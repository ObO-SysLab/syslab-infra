# AUDIT-01/09 local validation execution and recovery plan

Prepared and executed 2026-10-30 after explicit approval: **로컬 Docker·임시 DB 검증 승인**.
No AWS credentials, production databases, remote hosts or pushed images are used.

## Scope to approve

1. Start the installed local Docker Desktop engine if it is still stopped. Record the
   pre-start state and any existing running containers after connection; do not delete,
   stop, restart or modify unrelated containers. Do not change global Docker settings.
2. Download Debian packages/base images and build two local images from the checked-out
   `grader` context: the production runner candidate and the Linux test image.
3. Run only `diveon-audit-isolation-test` with no network, no host mounts, bounded memory,
   CPU/process count, a read-only root filesystem and a temporary work filesystem.
   The probes contain fake markers only. They test Python/C/C++ I/O, absent secrets and
   host files, prohibited network/fork, fresh case files and memory/time/output limits.
4. Run the existing grader persistence regression using its disposable PostgreSQL
   cluster on loopback with a randomly chosen port, no production configuration. The
   test harness creates a new temp data directory and stops that cluster in cleanup.

## Exact commands (workspace root unless stated)

```powershell
# Only after approval, if Docker Desktop is stopped:
Start-Process -FilePath 'C:/Program Files/Docker/Docker/Docker Desktop.exe' -WindowStyle Hidden
# Poll docker version with bounded waits; do not restart or reconfigure it on failure.
docker version

docker build --tag diveon-runner:audit-local --file syslab-infra/grader/runner/Dockerfile syslab-infra/grader
docker build --tag diveon-runner-test:audit-local --file syslab-infra/grader/runner/Dockerfile.test syslab-infra/grader
docker run --name diveon-audit-isolation-test --network none --read-only --memory 2g --memory-swap 2g --cpus 1 --pids-limit 64 --cap-drop NET_RAW --cap-drop NET_BIND_SERVICE --cap-drop MKNOD --cap-drop SETFCAP --cap-drop SETPCAP --cap-drop SYS_PTRACE --tmpfs /work:rw,exec,nosuid,nodev,size=2g --init diveon-runner-test:audit-local

$env:PYTHONPATH=Join-Path $env:TEMP 'syslab-grader-test-deps'
python -B -m unittest discover -s syslab-infra/grader/tests -p test_contest_result.py -v
```

Do not run the Docker test with AWS credentials, host source mounts, a Docker socket,
privileged mode, host networking or disabled seccomp. Confirm the named test container
does not already exist before running; choose a unique suffix if it does.

## Failure containment and recovery

- Stop only this newly created test container on a bounded overall timeout (10 minutes),
  capture its exit code and generic test output, then remove only that container.
  Its temporary filesystem disappears on removal. Never use Docker prune or bulk delete.
- Keep failed-test evidence in `산출물/근거`; do not save credentials or program contents
  from production. Local images can remain as identified test artifacts for review.
- Stop only the PostgreSQL instance whose absolute temp data path was created by this
  invocation if its harness cleanup failed. Verify that path before any stop/remove.
- If Docker Desktop was started solely for this test, shut it down using its supported
  quit/stop operation after confirming that doing so will not interrupt unrelated work.
  Do not force-kill Docker processes or stop unrelated containers. Report if it must stay on.
- Build/native-test failure keeps AUDIT-01/09 open. Never switch back to unrestricted
  subprocess execution to make tests pass.

## Distinguish evidence

Pure Python and Node mock tests need no engine/server and have already been run locally.
Only the commands above can establish actual Linux sandbox behavior. Passing them still
does not establish Fargate kernel/profile compatibility or validate production IAM,
security groups, signed S3 transport and deployment version combinations.

## Recorded result

- Docker Desktop was initially stopped and was started for this approved test. Engine
  29.3.0 reported no pre-existing running containers. No global settings were changed.
- Runner/test images built. Native launcher compiled with `-Wall -Wextra -Werror`.
  Linux tests: **5 passed**, 21.977 seconds, container exit 0. Python/C/C++ output,
  absent fake DB environment, UID 10001, inaccessible parent/worker/proc files,
  socket/fork denial, fresh case files and time/output/memory limits were exercised.
- Tests had no host mounts, Docker socket, AWS credentials or network. Socket denial
  was asserted as PermissionError inside the jail; Docker's network/PID/tmpfs restrictions
  are additional local controls, not evidence of identical Fargate behavior.
- Pure Python **27**, Node **8**, final disposable PostgreSQL **32** tests passed.
- Retained local runner image ID:
  `sha256:ba9a154d2757f9d61164a9e72fb61a7e6cf2a24a3ed609adde4c5ed508f204c4`.
  Test image ID: `sha256:861cd554301053905f774d7400dd0fbbffeb6d601c5b7d85a62e7bdd3c4a4e06`.
- Container `5208dfe534a1b25d96e8f79a0192691ae53499715a36123cf19da419dff14eab`
  (`diveon-audit-isolation-test`) exited 0 and was removed by exact name. No running
  containers remained; `docker desktop stop --timeout 30` succeeded. A subsequent
  status request confirmed Desktop was no longer available.
- Both temporary PostgreSQL runs stopped. Final data path:
  `C:/Users/CHOHYU~1/AppData/Local/Temp/syslab-grader-pg-j0x_35tb/data`;
  `pg_ctl status` returned `no server running`. Logs retained; unrelated DBs untouched.
- Production DB/AWS, deployment and remote push were not performed.
