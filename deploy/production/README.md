# Permanent read-only HTTP MCP

This is a new permanent deployment, separate from `deploy/synthetic`. It never
runs synthetic fixtures, extends a synthetic deadline, imports the accounting
application, or opens the financial database. Export only its file allowlist:

```sh
python -B -m deploy.production.build_context /absolute/new/build-context
```

The export preserves the dedicated Dockerfile path. Set Railway's config file to
`deploy/production/railway.toml`, clear any previous synthetic start-command
override, and use port 8080. Keep one replica and the approved service resource
limits. Do not use the accounting worker's root Dockerfile for this service.

Before deploying, configure the explicit HTTP backend and the single approved
instance/chat in `docs/furniture_mcp.env.example`. The owner, audience, issuer,
client ID and read scopes must match the approved OAuth connection. The user
enters the separate archive read-token in Railway's protected variables. The
MCP receives no GREEN-API, Poster, Gemini, Telegram or financial credentials.

The public `/healthz` check proves only that the MCP process started, including
when reading is explicitly disabled. It does not
prove that the source archive is reachable, persisted, or scoped correctly.
Before reporting ready, verify the source and MCP deployment commits, unauthenticated
rejection, the exact permitted chat, a real stored message, attachment availability
if present, and rejection of other chat IDs. Observe the shared webhook delivery;
do not claim or clear GREEN-API queues as a test.

The archive remains on the existing webhook worker's own persistent volume. An
MCP rollback stops this service; a collector rollback disables
`FURNITURE_READER_ENABLED` on the worker. Preserve the accounting group, webhook
URL and all financial configuration. Do not delete the archive during rollback.
Permanent retention has no automatic deletion or backup in this minimal launch;
resource limits are not a dollar spending cap.
