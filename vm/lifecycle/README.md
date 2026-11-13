# VM lifecycle helper (AUDIT-03/04)

Local implementation only; **not installed or enabled in production**.

The backend sends protocol version 1 JSON over stdin to a fixed SSH command:
`python3 /usr/local/libexec/diveon-vm-helper.py`.
Only `create`, `inspect`, `remove`, and `reconcile` are accepted. Per-session requests contain
`sessionId`, `userId`, `problemId`, `expiresAtEpochMs`; create also requires an image.
Legacy inspect/remove may carry a full `legacyContainerId` and require the exact old name.

The helper persists ownership manifests before creation and permanent closed markers before
removal. A per-session nonblocking flock serializes commands. Deterministic names and labels
are checked before inspecting or removing an existing resource. A failed Docker query is
never treated as absence. Removal requires a successful subsequent absence check.
Reconciliation rotates manifests, including closed sessions, to clean up Docker operations
that completed after a lost response. Progress is persisted before each attempt to avoid
starvation when an SSH command ends early. Unrecorded/mismatched containers require manual review.

Every practice container is created with `--memory 256m --memory-swap 256m` (no swap), `--pids-limit 256`,
`--cpus 0.3`, `--network none` and `--init`. no-new-privileges is not applied to unrestricted images, so
sudo/setuid escalation problems keep working.

Images restricted to an allowed-command list (practice build label `net.diveon.restriction=v1`) get extra
create options. The helper reads the label before creating, and pulls the image first if it is not local.
- `/tmp`, `/var/tmp` and `/home/lab/work` become `noexec,nosuid,nodev` tmpfs mounts.
- `no-new-privileges` is added.
- An unknown label value fails the create, so an unhardened container is never created.
- Unlabeled images get only the base options above.

Pure unit tests (no Docker, DB, SSH, network or application server):

```text
python -B -m unittest discover -s vm/lifecycle -p 'test_*.py' -v
```

Installation requires separate user approval. Use a persistent `/var/lib/diveon-vm` directory
owned by the SSH execution user, mode 0700. The helper and state directory must be inaccessible
to practice containers. Never mount the Docker socket or helper state into those containers.
Keep manifests, closed markers and the rotation cursor across upgrades and rollback.
Linux flock, actual Docker behavior and SSH key pinning still require native integration validation.

Backend settings: `vm.ssh-known-hosts` is mandatory for verified SSH, `vm.recovery.enabled`
defaults off, `vm.recovery.poll-ms` defaults to 30000. The interval is not a deletion SLA;
API/DB/SSH/Docker outages or batches may delay cleanup. Runtime version 1 sessions require the
new backend/frontend contract and the partial unique active-user DB index. Existing runtime
version 0 sessions must have verified full IDs and exact legacy names.

The coordinated installation, temporary PostgreSQL validation, rollback and approval plan is
in the sibling backend repository at `ops/2026-10-30-vm-lifecycle.md` and its SQL migration.
On rollback keep VM routes closed and disable recovery; do not let old code manage new sessions.
This helper does not change the node-pty terminal implementation in `vm/ws-server`.
