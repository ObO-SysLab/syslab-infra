# DiveOn grading coordinator and isolated runner

AUDIT-01, linked AUDIT-09: local implementation and tests, **not deployed**.
Fargate compatibility, IAM/network policy and full submission integration remain unverified.

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
- Runner SG/routes must deny ingress, public internet and RDS/Redis/VM access. Restrict egress
  to approved S3/ECR/logs endpoints and DNS, and restrict bucket access to that endpoint.
  Worker transport accepts only regional S3 HTTPS without redirects/proxies. Submitted code
  cannot create sockets, including to metadata. Task-definition validation does not prove
  SG, IAM, endpoint or bucket policies; verify those independently.

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
monitor, which is not implemented. Crashes/deletion failures also need S3 lifecycle cleanup.
AUDIT-01 stays P0 open, with AUDIT-09 linked and unresolved, until these safeguards and AWS validation are complete.

## Local evidence

See [ISOLATION-VALIDATION.md](ISOLATION-VALIDATION.md) for approved execution/cleanup.
Pure Python 27, Node 8, disposable PostgreSQL 32, actual Linux sandbox 5 tests passed.
No production DB/AWS, deployment or remote push. Local Docker is not Fargate validation.
AUDIT-08: PostgreSQL harnesses now require DIVEON_GRADING_PG_TEST=approved and explicit PG_BIN;
executable suffixes support Windows and Unix. Default discovery skips all 35 DB cases.
Source packaging and CI details: [release procedure](../ops/RELEASE.md).

Sources: [Fargate task boundary](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-iam-roles.html),
[Fargate restrictions](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-tasks-services.html),
[S3 POST conditions](https://docs.aws.amazon.com/AmazonS3/latest/developerguide/sigv4-HTTPPOSTConstructPolicy.html),
[seccomp](https://docs.kernel.org/userspace-api/seccomp_filter.html).
