# AUDIT-02 deployment and rollback plan

Local implementation only. No server, production DB, AWS configuration, deployment,
or remote push is authorized by this file. Obtain approval for the concrete target
versions, maintenance window and recovery steps before executing this plan.

## Contract

1. Browser authenticates `POST /api/vm/terminal-ticket` with its existing login Bearer
   token. Body: `{ "probId": 9, "containerId": "<full 64-character Docker ID>" }`.
   Response: `{ "ticket": "<43-character random token>", "expiresAt": <epoch milliseconds> }`.
   Owner, problem, RUNNING status and non-null future expiry must match.
2. Browser opens `/ws/terminal?container_id=<ID>` with Origin `https://diveon.net`.
   No login token or ticket is present in the URL. The first text frame is
   `{ "type": "auth", "ticket": "..." }`. No shell runs before verification.
3. Terminal calls `POST /internal/terminal/connect` with the machine credential in
   `X-Terminal-Service-Key` and `{ "ticket": "...", "containerId": "..." }` in the body.
   Backend consumes the Redis ticket atomically (GETDEL), rechecks the DB session,
   and returns `{ "lease": "...", "containerId": "...", "expiresAt": ... }`.
4. Terminal queues `{ "type": "ready" }` before PTY output. The browser attaches
   xterm only after this frame. Subsequent frames preserve the existing raw PTY I/O.
5. Terminal calls `POST /internal/terminal/check` with the machine credential and
   `{ "lease": "..." }` every 5 seconds (start-to-start). Backend reads the lease
   and the current DB session. Leases are server-only and cannot be used as tickets.

Ticket TTL is at most 30 seconds. Lease TTL ends at the session's original expiry;
neither check nor reconnect extends a session. A stopped/deleted/reassigned session,
changed container/expiry, missing grant, Redis/DB failure or invalid response denies
access. Calls including response body reads time out after 2 seconds. With a responsive
event loop, revocation/outage is observed within about 7 seconds; this is not a hard
real-time guarantee under host suspension/overload. Known expiry has its own local
timer and input/output checks. Idle unauthenticated sockets terminate after 5 seconds.

The backend uses the same system timezone as existing `VmService` LocalDateTime
creation. Verify the production JVM timezone and existing expires_at interpretation
before deployment; do not change them incidentally.

## Configuration and artifacts

| Location | Configuration | Purpose |
| --- | --- | --- |
| Backend runtime | `TERMINAL_SERVICE_KEY` | Independent 32–256 character random secret; absent/short disables terminal grants |
| VM `/etc/diveon/terminal.env` | `TERMINAL_SERVICE_KEY` | Same machine credential, never the login JWT secret |
| VM environment | `TERMINAL_API_URL=http://127.0.0.1:18080` | Recommended deployment below uses an authenticated SSH tunnel |
| VM environment | `TERMINAL_ALLOWED_ORIGIN=https://diveon.net` | Exact browser origin; no wildcard |
| VM environment | `TERMINAL_BIND_HOST=10.0.3.30` | Observed VM private interface; verify before applying |

Only HTTPS or numeric loopback HTTP API URLs are accepted. Plain HTTP to a VPC IP
is deliberately rejected. No credential values belong in Git, generated JARs,
request/body tracing, terminal logs, screenshots or this document. Deliver a generated
secret through the approved runtime secret channel. Protect the VM environment file
with root ownership/mode 0600; systemd reads it before switching to ec2-user.

Backend adds three Java classes and two exact POST security exceptions; each internal
handler still requires the machine credential. There is no DB migration. Redis >= 6.2
is required; the earlier inspection found Redis 7.2, which must be reverified.
Redis keys use `terminal:v1:ticket:` and `terminal:v1:lease:` and expire automatically.
The application Redis instance must be trusted and inaccessible to practice containers.

VM release contains `server.js`, `auth.js`, `terminal.js`, `package.json` and
`package-lock.json`. Build/install native node-pty dependencies on the target Linux
architecture in an isolated release directory using `npm ci`; do not copy Windows
node_modules. Keep the observed `ws-terminal.service` name and working directory.
`systemd-setup.sh` now only prints the reviewed unit template; it does not install,
stop or start anything. Compare the existing unit before applying the template.

## Proposed encrypted verification path (approval required)

Use a dedicated restricted SSH credential for VM -> API forwarding, not the user's
personal key. Verify the API private address and host key from a trusted source first.
On the API, restrict its authorized key to forwarding to `127.0.0.1:8080`, no PTY,
agent forwarding or X11; use a dedicated OS account and allow only local forwarding
with `PermitOpen 127.0.0.1:8080`. Restrict SSH network ingress to the VM security group.
Do not expose `/internal/terminal/*` through CloudFront or public Nginx routes.

Prepare a separate tunnel unit with the following ExecStart (replace reviewed paths
and API address before approval):

```text
/usr/bin/ssh -NT -o BatchMode=yes -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/etc/diveon/api_known_hosts -o ServerAliveInterval=15 -o ServerAliveCountMax=2 -i /etc/diveon/terminal_tunnel_key -L 127.0.0.1:18080:127.0.0.1:8080 terminal-forward@<verified-api-private-ip>
```

The tunnel service needs `Restart=on-failure`, protected key files, and startup ordering
before ws-terminal. A tunnel outage fails closed. A private HTTPS endpoint with proper
certificate verification can replace this tunnel, but must be reviewed as a separate
deployment configuration. These OS/network changes are prepared, not executed.

## Approved execution sequence

1. Record exact backend/frontend/terminal commit IDs, existing image digests, VM release
   file hashes, service units, Nginx configuration and runtime configuration names.
   Store protected rollback copies on the target hosts; do not copy secrets into notes.
2. Confirm AUDIT-01 remains P0 and unresolved. This terminal change does not approve
   enabling or deploying the unisolated grading service. Agree on deployment scope.
3. Announce terminal maintenance. Block `/ws/terminal` in API Nginx, validate config,
   then stop the old ws-terminal service so already-open unauthenticated sockets end.
   Snapshot/list existing lab sessions for verification without altering their data.
4. Install the encrypted verification path and runtime key after approval. Deploy the
   reviewed backend version, then verify anonymous ticket rejection, owner issuance,
   wrong-owner/expired/STOPPED rejection, one-use enforcement and internal key rejection.
   Do not start the full application merely to run unit tests.
5. Install a new Linux terminal release and the matching frontend build. Validate native
   node-pty with a designated non-sensitive test lab. Confirm 80x24 interactive input,
   output, Ctrl-C, exit/disconnect cleanup, and full 64-character container IDs.
6. Start the new ws-terminal, verify authorized access and expiry/stop/outage closure,
   restore the Nginx route only after these pass. Retest both ordinary and contest labs
   in a real browser; inspect logs for accidental credential capture without printing keys.
7. Record exact deployed versions, checks and resource final states. If temporary server
   starts were approved, stop only those resources approved for cleanup.

## Rollback / failure containment

First block the public terminal route and stop ws-terminal, ending all terminal PTYs.
Keep that route blocked while diagnosing or restoring the backed-up backend/frontend
and service configuration. **Do not reopen the old unauthenticated terminal.** Remove
or stop the newly introduced tunnel only after dependent terminal access is disabled.
Existing lab containers/data are not deleted by this rollback. Tickets/leases expire
without a Redis flush; do not flush the shared Redis database. Reopening requires a
validated authenticated release and approval. Other existing grading commits are
preserved; do not reset branches or drop grading tables as part of this rollback.

## Verification limits

Local unit tests exercise real authorization code with mocked DB/Redis and mocked
WebSocket/PTY boundaries. Atomic consumption uses Redis GETDEL, but these tests do
not start a real Redis server. No production connectivity or complete browser-to-Docker
test is implied. Native Linux node-pty build/runtime and SSH forwarding must be verified
in the approved deployment environment. Killing a PTY ends this connection; container
expiry/deletion and arbitrary descendant process cleanup remain AUDIT-03/04 scope.

Implementation references: [node-pty API](https://github.com/microsoft/node-pty/blob/main/typings/node-pty.d.ts),
[Spring Redis ValueOperations](https://docs.spring.io/spring-data/redis/docs/current/api/org/springframework/data/redis/core/ValueOperations.html).
