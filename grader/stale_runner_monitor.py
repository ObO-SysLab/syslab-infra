"""External sweep for orphan runner tasks; dry-run unless explicitly configured.

No source, signed URL, task overrides, or submission data is logged or returned.
Schedule and IAM are deployment prerequisites, not provisioned by this module.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import os
import re

GROUP = "diveon:isolated-runner"


@dataclass(frozen=True)
class Configuration:
    cluster: str
    definitions: frozenset
    max_age: int = 900  # Greater than the coordinator's full 780-second budget.
    dry_run: bool = True

    @classmethod
    def from_env(cls, env):
        cluster = env.get("RUNNER_MONITOR_CLUSTER", "")
        match = re.fullmatch(r"arn:aws:ecs:([\w-]+):(\d{12}):cluster/([\w-]+)", cluster)
        definitions = frozenset(env.get("RUNNER_MONITOR_DEFINITIONS", "").split(","))
        if not match or not definitions:
            raise ValueError("Monitor requires an explicit cluster ARN and pinned runner revisions")
        prefix = "arn:aws:ecs:" + match[1] + ":" + match[2] + ":task-definition/"
        if any(not re.fullmatch(re.escape(prefix) + r"[\w-]+:[1-9]\d*", value) for value in definitions):
            raise ValueError("Monitor revisions must pin the same account and region")
        age = int(env.get("RUNNER_MONITOR_MAX_AGE_SECONDS", "900"))
        dry = env.get("RUNNER_MONITOR_DRY_RUN", "true")
        if not 900 <= age <= 3600 or dry not in ("true", "false"):
            raise ValueError("Unsafe monitor age or dry-run setting")
        return cls(cluster, definitions, age, dry == "true")


def owned(task, config):
    # Group and exact revisions separate runners from coordinator/service/other tasks.
    return (task.get("clusterArn") == config.cluster
            and task.get("taskDefinitionArn") in config.definitions
            and task.get("launchType") == "FARGATE"
            and task.get("group") == GROUP
            and re.fullmatch(r"[a-f0-9]{32}", task.get("startedBy", "")) is not None)


def expired(task, config, now):
    created = task.get("createdAt")
    if not isinstance(created, datetime) or created.tzinfo is None:
        raise ValueError("Owned runner has no trustworthy creation time")
    return (now - created).total_seconds() >= config.max_age


def describe(ecs, config, arn):
    response = ecs.describe_tasks(cluster=config.cluster, tasks=[arn])
    tasks = response.get("tasks", [])
    if response.get("failures") or len(tasks) != 1 or tasks[0].get("taskArn") != arn:
        raise RuntimeError("Cannot verify listed task identity")
    return tasks[0]


def sweep(ecs, config, now=None, remaining_ms=None):
    now = now or datetime.now(timezone.utc)
    counts = dict(scanned=0, stale=0, stopRequested=0, dryRun=config.dry_run, errors=0)
    seen = set()
    families = sorted({definition.rsplit("/", 1)[1].rsplit(":", 1)[0] for definition in config.definitions})
    for family in families:
        # desiredStatus RUNNING includes provisioning/pending; desired PENDING returns no tasks.
        for page in ecs.get_paginator("list_tasks").paginate(
                cluster=config.cluster, family=family, launchType="FARGATE", desiredStatus="RUNNING"):
            for arn in page.get("taskArns", []):
                if arn in seen:
                    continue
                seen.add(arn)
                if remaining_ms is not None and remaining_ms() < 20000:
                    raise RuntimeError("Monitor ran out of scan budget; retry required")
                counts["scanned"] += 1
                try:
                    task = describe(ecs, config, arn)
                    if not owned(task, config) or task.get("lastStatus") == "STOPPED":
                        continue
                    if not expired(task, config, now):
                        continue
                    counts["stale"] += 1
                    if config.dry_run:
                        continue
                    # Recheck immediately before mutation; a task may finish during the sweep.
                    current = describe(ecs, config, arn)
                    if (not owned(current, config) or current.get("lastStatus") == "STOPPED"
                            or current.get("desiredStatus") != "RUNNING" or not expired(current, config, now)):
                        continue
                    reply = ecs.stop_task(cluster=config.cluster, task=arn, reason="Isolated runner exceeded maximum lifetime")
                    if reply.get("task", {}).get("taskArn") != arn:
                        raise RuntimeError("Stop request acknowledgement missing")
                    counts["stopRequested"] += 1
                except Exception:
                    # Continue independent tasks, but fail the invocation for retry/alarm.
                    # Do not include SDK exception text: task overrides may contain signed URLs.
                    counts["errors"] += 1
    if counts["errors"]:
        raise RuntimeError("Runner monitor scan failed for " + str(counts["errors"]) + " task(s); retry required")
    return counts


def handler(event, context):
    config = Configuration.from_env(os.environ)  # Event payload cannot enable mutation or widen scope.
    import boto3
    from botocore.config import Config
    ecs = boto3.client("ecs", region_name=config.cluster.split(":")[3],
                       config=Config(connect_timeout=2, read_timeout=3,
                                     retries={"total_max_attempts": 2, "mode": "standard"}))
    result = sweep(ecs, config, remaining_ms=context.get_remaining_time_in_millis)
    print(result)
    return result
