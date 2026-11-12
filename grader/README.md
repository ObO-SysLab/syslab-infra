# DiveOn grading coordinator and isolated runner

AUDIT-01, linked AUDIT-09: production grading rollout is **not complete**.
2026-11-04: the actual Fargate runner sandbox passed 10 synthetic tests, using a test
image with the production candidate's identical base layers. Signed S3 transport,
coordinator IAM/cleanup and full submission integration remain unverified.
See the workspace sibling [Fargate validation evidence](../../obo-harness/evidence/2026-11-04-fargate-isolation/REPORT.md).

## Execution boundary

```text
Spring → SQS → Lambda → trusted coordinator Fargate task → RDS / Redis
                           │ reads code, inputs and expected answers from trusted S3
                           │ sends code + inputs only through a separate exchange bucket
                           ▼
                       isolated runner Fargate task
                       no task role / DB credentials / expected answers
                       compiler + program → chroot / UID 10001 / seccomp / rlimits
                           │ returns stdout, exit category and measurements
                           └──────── coordinator compares answers and commits results
```

The coordinator never compiles or runs submitted code. It validates a pinned task revision
and image digest before launch. Missing/unsafe settings, bad or incomplete results and
sandbox setup failure are infrastructure failures. There is no unrestricted fallback.
Existing result transactions, score idempotency and attempt/deadline fencing remain.

The root runner worker holds two short-lived exchange capabilities. Submitted code has
neither: the launcher closes extra descriptors, resets environment, enters a toolchain-only
chroot, drops UID/GID/groups, sets no-new-privileges and installs a syscall allowlist.
The worker, parent files, /proc, answers and credentials are outside the jail. Network
syscalls and credential/process-group changes are denied. Compilation is confined too;
runtime fork and threads are denied. Each case starts with fresh writable directories.
Keep this security-sensitive native component and its compiler/kernel dependencies patched;
the tests are not proof against every kernel or compiler vulnerability.

## Protocol and limits

Strict version-1 fields bind the random request ID, language, source, inputs and limits.
Results must match that request and contain every case in order, or zero cases on compile
failure. Expected-answer/credential fields are invalid. Numeric and byte sizes are bounded.

| Limit | Implementation |
| --- | --- |
| Languages | Python 3.11, C, C++17 from Debian bookworm |
| Case time / memory | 1–30 seconds / 16–1024 MiB **virtual address space**, not RSS |
| Cases / aggregate budget | 1–100; `20 + count * (seconds + 2) <= 600` |
| Compiler | 10 seconds / 1024 MiB / 32 processes per UID |
| Runtime | 1 process; additional processes and threads denied |
| Source / each input / each expected answer | 1 MiB each |
| stdout + stderr | 1 MiB combined per execution, stderr discarded |
| Successful stdout aggregate | 8 MiB threshold; later cases return output-limit error |
| JSON request / result | 16 MiB each, including base64 overhead |
| File size / file descriptors | compile 16 MiB, runtime 1 MiB per file / 64 descriptors |
| Runner task | 1 vCPU / 2048 MiB / dedicated ephemeral work volume |
| Exchange expiry / polling budget | 780 seconds including startup allowance |
| Independent worker watchdog | at most 630 seconds after worker startup, shortened to request expiry |

Limits reject invalid requests rather than silently clamping. Existing problem settings,
reference solutions and case sizes need compatibility checks before rollout; backend
authoring screens do not enforce every new ceiling yet. Per-file limits are not a total
disk quota: many small files can exhaust task ephemeral storage. Docker local tests add
a 2 GiB tmpfs and PID cap, which are **not** Fargate task-definition options.

Normally reaped processes report their own wait4 peak RSS; forcibly terminated processes
may report zero. This is not precise accounting of every failed process's peak memory.
Persisted results stay CORRECT / WRONG / ERROR. Time limit stays WRONG as before;
compile/runtime/output-limit and invalid UTF-8 output become ERROR. Infrastructure failures
remain retryable failures, never judged ERROR.

## Configuration

Lambda: ECS_CLUSTER, pinned TASK_DEFINITION, SUBNET_ID, SECURITY_GROUP.
Package lambda_trigger.mjs as index.mjs with launch.mjs at ZIP root; handler index.handler.
Messages must include memoryLimit. Overrides carry submission/contest/attempt context and
MEMORY_LIMIT, **no DB credentials**.

Coordinator retains DB_HOST/DB_NAME/DB_USER/DB_PORT/DB_PASSWORD, S3_BUCKET and Redis settings.
Inject DB_PASSWORD through coordinator ECS runtime secrets. New mandatory settings:
RUNNER_BUCKET, RUNNER_CLUSTER, RUNNER_TASK_DEFINITION (full ARN/revision), RUNNER_SUBNET_ID,
RUNNER_SECURITY_GROUP.

- Use a separate private, DNS-compatible, dot-free exchange bucket in ap-northeast-2.
  Block public access, require HTTPS, use SSE-S3, and apply short retention for abandoned
  executions/ objects. If versioning is enabled, expire noncurrent versions too.
- Coordinator IAM needs trusted input reads, exchange Get/Put/Delete, pinned runner RunTask
  and DescribeTaskDefinition, owned-task Describe/Stop, cluster ListTasks for uncertain
  launch recovery, and PassRole for the runner execution role only. Scope resource ARNs and
  conditions during deployment review; policies are not created or proven by this code.
- runner/task-definition.example.json has **placeholders**. Pin a reviewed image digest,
  use a dedicated ECR/logs execution role, no task role, secrets, sidecars, shared storage or
  ports. Preserve read-only root, init, capability drops and only the /work volume. Root
  performs jail setup; submitted code runs as UID 10001.
- REQUEST_URL is an exact-object GET; RESULT_POST is an exact-object, expiring, size-bounded
  POST. They are the only runner overrides. Do not log their values or expose task inspection.
  The S3 client pins regional HTTPS, virtual addressing and SigV4.
- The selected deployment uses the existing NAT EC2, not paid interface endpoints:
  see [NAT network contract](runner/NAT_NETWORK.md). The runner has no public IP or ingress;
  a dedicated subnet ACL denies RFC1918 traffic, while TCP443 to public destinations is
  allowed through NAT for trusted image/log transport. This is not a public-internet deny
  or an AWS-domain allowlist. S3 uses the existing free Gateway endpoint.
  Worker transport still accepts only regional S3 HTTPS without redirects/proxies.
  Compiler and submitted code cannot create sockets, including to metadata, under seccomp.
  Task-definition validation does not prove SG, ACL, IAM, bucket policy or sandbox isolation;
  verify these independently on Fargate before opening submissions.

### Lambda coordinator (2026-11-12, not yet deployed)

Measured production latency is 92–99 s per coding submission. Two sequential Fargate cold
starts dominate: the coordinator task takes 17–25 s and the runner task 24–30 s. The Lambda
coordinator removes the first cold start. The runner stays a fresh, credential-free Fargate
task per submission.

- Image: `Dockerfile.lambda`. It uses the same base and trusted files as `Dockerfile` and adds
  `awslambdaric`. The handler is `lambda_coordinator.handler`, attached to the existing grading
  SQS queue with batch size 1.
- Each record is checked with the admission rules of `launch.mjs`, then passed to
  `grader.configure()` and `grader.main()`. In the ECS task, `configure(os.environ)` does the
  same thing once per task.
- `DB_SECRET_ARN` holds the grading DB secret (JSON with DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD).
  It is read once per warm container. S3_BUCKET, the runner settings and the Redis settings are
  the same environment variables the coordinator task uses.
- Run it in the coordinator's VPC subnet and security group, so it reaches RDS, Redis, ECS and
  S3 through the existing NAT and S3 Gateway path. Timeout is 900 s: the runner budget is
  780 s, including the startup allowance. The SQS visibility timeout must be longer than the
  function timeout. Cap reserved concurrency so parallel runners and DB connections stay bounded.
- The execution role needs the coordinator task role's permissions,
  `secretsmanager:GetSecretValue` on that one secret, and VPC ENI access.
- Failure semantics are unchanged:
  - A grading infrastructure failure is recorded in `coding_grade_job`, and the backend
    re-dispatches it as the next attempt.
  - Only messages that could not start (bad body, unreachable DB or secret) return to SQS.
  - Attempt fencing in `begin_grading` keeps a redelivery from grading twice.
- Rollback: point the SQS event source mapping back at `syslab-grader-trigger`. That restores
  the Fargate coordinator, whose task definition is untouched.

`runner_client` also reads the result once the runner container has exited, using its exit
code. It no longer waits for the task to reach `STOPPED`; Fargate deprovisioning added
15–26 s after the program ended.

## Rollout and recovery — separate approval required

1. Inventory actual queue/task revisions/digests, IAM, secret references, networking,
   retry deadlines and problem limits. Record rollback references without secret values.
   Back up DB before the separate recovery migration.
2. Prepare exact exchange bucket/retention, ECR, task, role, network, coordinator secret
   and image changes. Obtain approval before AWS mutations, image push or deployment.
3. In an approved dedicated AWS test scope, verify Fargate chroot/seccomp compatibility,
   signed GET/POST, trusted-bucket denial, no task-role endpoint, stop and orphan handling.
4. Pause grading dispatch and identify/drain legacy queue/tasks. Deploy compatible backend
   recovery schema/code, Lambda, coordinator and pinned runner revision. Keep
   grading.recovery.enabled=false until its migration and integration checks pass.
   Old messages with missing/invalid memory limits need explicit handling.
5. Check normal/contest Python/C/C++, incorrect/compile/runtime/time/output cases, duplicate
   and stale attempts, missing result, lost launch response, failed StopTask and coordinator
   death. Confirm DB/score consistency and task/object cleanup before reopening dispatch.
6. On failure, pause dispatch, stop only identified test/new tasks, preserve evidence and
   restore compatible version references while grading stays closed. **Do not reopen the
   old unrestricted grader.** Do not undo committed results or drop recovery tables.

A known task ARN is stopped on coordinator failure. Lost RunTask responses use unique
startedBy lookup, but ECS eventual consistency makes one lookup insufficient to guarantee
discovery. The worker watchdog bounds execution once started. Coordinator death before
worker startup, prolonged provisioning and failed StopTask still need an external stale-task
monitor. It is now implemented locally in stale_runner_monitor.py with a default dry-run,
exact cluster/revision/group ownership and repeated scans; see [monitor contract](runner/MONITOR.md).
It has NOT been deployed or verified on AWS. Crashes/deletion failures also need S3 lifecycle cleanup.
AUDIT-01 stays P0 open, with AUDIT-09 linked and unresolved, until these safeguards and AWS validation are complete.

## Local evidence

Historical execution/cleanup records are preserved in the workspace sibling
[validation archive](../../obo-harness/evidence/2026-11-04-infra-layout/isolation-validation-history.md).
Current repeatable CI commands remain in this repository's workflow and test sources.
Pure Python 27, Node 8, disposable PostgreSQL 32, actual Linux sandbox 5 tests passed.
No production DB/AWS, deployment or remote push. Local Docker is not Fargate validation.
AUDIT-08: PostgreSQL harnesses now require DIVEON_GRADING_PG_TEST=approved and explicit PG_BIN;
executable suffixes support Windows and Unix. Default discovery skips all 35 DB cases.
Source packaging and CI details: [release procedure](../ops/RELEASE.md).

Sources: [Fargate task boundary](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-iam-roles.html),
[Fargate restrictions](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-tasks-services.html),
[S3 POST conditions](https://docs.aws.amazon.com/AmazonS3/latest/developerguide/sigv4-HTTPPOSTConstructPolicy.html),
[seccomp](https://docs.kernel.org/userspace-api/seccomp_filter.html).
