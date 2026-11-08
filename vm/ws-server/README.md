# DiveOn terminal (AUDIT-02)

The repository now preserves the observed production `node-pty`, `container_id`,
80x24 terminal and raw interactive I/O, and adds authorization before Docker exec.
The production service observed on 2026-10-29 was `ws-terminal.service` under
`/home/ec2-user/ws-server`, reached through API Nginx at VM `10.0.3.30:8081`.
These are observations, not confirmation that this version has been deployed.

The browser obtains a one-use 30-second ticket from the authenticated backend.
It sends the ticket in its first WebSocket frame, never in the URL. The terminal
exchanges it for a server-only lease and rechecks the session every five seconds.
Missing/invalid grants, different targets, stopped/expired sessions, and verification
outages fail closed. The browser waits for a ready frame before attaching xterm.

Run isolated tests (no listeners, Docker, AWS, Redis or DB servers started):

```text
node --test auth.test.js terminal.test.js server.test.js
```

Production installation, environment names, protocol, encrypted backend transport,
validation and rollback are documented in [DEPLOYMENT.md](DEPLOYMENT.md).
`systemd-setup.sh` only renders a service template. Do not run `npm start`, install
units, or restart services without the separately approved execution plan.

AUDIT-01 grading isolation remains unresolved and P0. Terminal tests do not establish
that the overall platform is ready for production deployment.
