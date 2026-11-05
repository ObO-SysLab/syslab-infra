from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stale_runner_monitor import Configuration, GROUP, sweep

CLUSTER = "arn:aws:ecs:ap-northeast-2:123456789012:cluster/test"
DEFINITION = "arn:aws:ecs:ap-northeast-2:123456789012:task-definition/runner:1"
ENV = {"RUNNER_MONITOR_CLUSTER": CLUSTER, "RUNNER_MONITOR_DEFINITIONS": DEFINITION}
NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)


class StaleRunnerMonitorTest(unittest.TestCase):
    def setUp(self):
        self.ecs = Mock()
        self.task = dict(taskArn="task-1", clusterArn=CLUSTER, taskDefinitionArn=DEFINITION,
                         launchType="FARGATE", group=GROUP, startedBy="a" * 32,
                         createdAt=NOW - timedelta(seconds=901),
                         lastStatus="RUNNING", desiredStatus="RUNNING")
        self.ecs.get_paginator.return_value.paginate.return_value = [{"taskArns": ["task-1"]}]
        self.ecs.describe_tasks.side_effect = lambda **kw: {"tasks": [deepcopy(self.task)]}
        self.ecs.stop_task.return_value = {"task": {"taskArn": "task-1"}}

    def sweep(self, apply=False):
        config = Configuration.from_env(dict(ENV, RUNNER_MONITOR_DRY_RUN="false" if apply else "true"))
        return sweep(self.ecs, config, NOW)

    def test_default_is_observation_only(self):
        self.assertTrue(Configuration.from_env(ENV).dry_run)
        self.assertEqual(self.sweep()["stale"], 1)
        self.ecs.stop_task.assert_not_called()

    def test_service_rejecting_combined_family_and_launch_type_is_supported(self):
        def paginate(**filters):
            if "family" in filters and "launchType" in filters:
                raise ValueError("ECS cannot combine family and launchType filters")
            return [{"taskArns": ["task-1"]}]

        self.ecs.get_paginator.return_value.paginate.side_effect = paginate
        self.assertEqual(self.sweep(True)["stopRequested"], 1)

    def test_old_running_pending_and_provisioning_are_stopped_after_recheck(self):
        for status in ("RUNNING", "PENDING", "PROVISIONING"):
            self.task["lastStatus"] = status
            self.assertEqual(self.sweep(True)["stopRequested"], 1)
        self.assertEqual(self.ecs.describe_tasks.call_count, 6)
        self.assertEqual(self.ecs.get_paginator.return_value.paginate.call_args.kwargs["desiredStatus"], "RUNNING")

    def test_other_cluster_definition_group_launch_type_or_owner_is_untouched(self):
        for field, value in (("clusterArn", "other"), ("taskDefinitionArn", DEFINITION + "0"),
                             ("group", "service:api"), ("launchType", "EC2"), ("startedBy", "not-request")):
            original = self.task[field]
            self.task[field] = value
            self.assertEqual(self.sweep(True)["stale"], 0)
            self.task[field] = original
        self.ecs.stop_task.assert_not_called()

    def test_fresh_or_future_task_is_untouched(self):
        for seconds in (899, -60):
            self.task["createdAt"] = NOW - timedelta(seconds=seconds)
            self.assertEqual(self.sweep(True)["stale"], 0)
        self.ecs.stop_task.assert_not_called()

    def test_finished_between_reads_is_not_stopped(self):
        stopped = dict(self.task, lastStatus="STOPPED")
        self.ecs.describe_tasks.side_effect = [{"tasks": [self.task]}, {"tasks": [stopped]}]
        self.assertEqual(self.sweep(True)["stopRequested"], 0)
        self.ecs.stop_task.assert_not_called()

    def test_stop_failure_is_retried_by_next_sweep(self):
        self.ecs.stop_task.side_effect = RuntimeError("access denied")
        with self.assertRaises(RuntimeError):
            self.sweep(True)
        self.ecs.stop_task.side_effect = None
        self.assertEqual(self.sweep(True)["stopRequested"], 1)

    def test_eventual_visibility_and_pagination_do_not_lose_or_duplicate_task(self):
        self.ecs.get_paginator.return_value.paginate.return_value = [{"taskArns": []}]
        self.assertEqual(self.sweep(True)["scanned"], 0)
        self.ecs.get_paginator.return_value.paginate.return_value = [{"taskArns": []}, {"taskArns": ["task-1", "task-1"]}]
        self.assertEqual(self.sweep(True)["stopRequested"], 1)

    def test_missing_task_or_timestamp_fails_without_stop(self):
        self.ecs.describe_tasks.side_effect = None
        for reply in ({"tasks": []}, {"tasks": [dict(self.task, taskArn="other")]},
                      {"tasks": [dict(self.task, createdAt=None)]}, {"failures": [{"reason": "MISSING"}]}):
            self.ecs.describe_tasks.return_value = reply
            with self.assertRaises(RuntimeError):
                self.sweep(True)
        self.ecs.stop_task.assert_not_called()

    def test_unconfirmed_stop_response_fails(self):
        self.ecs.stop_task.return_value = {}
        with self.assertRaises(RuntimeError):
            self.sweep(True)

    def test_unsafe_config_rejected(self):
        for change in ({"RUNNER_MONITOR_CLUSTER": "default"}, {"RUNNER_MONITOR_DEFINITIONS": "runner:latest"},
                       {"RUNNER_MONITOR_MAX_AGE_SECONDS": "780"}, {"RUNNER_MONITOR_DRY_RUN": "yes"},
                       {"RUNNER_MONITOR_DEFINITIONS": DEFINITION.replace("123456789012", "999999999999")}):
            with self.assertRaises(ValueError):
                Configuration.from_env(dict(ENV, **change))

    def test_exhausted_lambda_budget_never_stops_task(self):
        with self.assertRaises(RuntimeError):
            sweep(self.ecs, Configuration.from_env(ENV), NOW, remaining_ms=lambda: 19999)
        self.ecs.stop_task.assert_not_called()


if __name__ == "__main__":
    unittest.main()
