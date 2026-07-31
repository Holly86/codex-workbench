# Deployment

## Target topology

Docker Compose builds two images. The frontend Nginx container is the only service with a host
port. The FastAPI backend is reachable only on the Compose network and owns one
`codex app-server --stdio` child. This preserves one public web origin while keeping separate
frontend and backend build stages.

The Synology DS918+ target is x86-64 with four CPU cores and 12 GB RAM. The initial topology uses
one backend replica, bounded queues/uploads, and no resource-intensive editor or terminal bundle in
the critical path.

## Persistent mounts

| Host setting | Container target | Access | Purpose |
| --- | --- | --- | --- |
| WORKSPACE_PATH | /workspace | read/write | configured Git repositories available to Codex |
| WORKBENCH_DATA_PATH/state | /var/lib/codex-workbench | read/write | SQLite, Argon2id verifier, uploads, runtime metadata |
| CODEX_HOME_PATH | /home/workbench/.codex | read/write | targeted existing Codex authentication/config |
| WORKBENCH_DATA_PATH/secrets | /run/secrets/workbench | read-only | Workbench session HMAC key |

The Codex-home source must be a dedicated persistent directory with the authenticated CLI state
needed by this Workbench. Do not mount an entire user home. The frontend receives none of these
mounts. Host ownership must match WORKBENCH_UID/GID or use an intentional NAS ACL.

The existing Codex container is unrelated to this Compose project and must not be restarted,
replaced, joined, or mutated.

## Initialize without deployment

    ./scripts/init-deployment.sh

This copies `.env.example` to ignored `.env` only when absent, creates ignored
state/upload/security/secret directories with restrictive permissions, and generates a random
session key. It does not create a default password, build, or start containers. Set
`CODEX_HOME_PATH`, canonical public origin, and the explicit cookie/HTTP mode afterward. Never put
a password, Codex token, API key, or session key in `.env`. The session key must remain a regular,
non-symlink file readable only by its owner; startup fails closed otherwise.

On first authenticated-boundary startup, browse to the canonical origin and complete Setup. The
backend exclusively creates `state/security/password.argon2id`; that file closes Setup permanently.
Back up state and session-key files together. Password recovery/change is not implemented, so loss
needs an operator recovery decision rather than ad-hoc deletion on a running deployment.

## Build and validate

On a Docker-capable host:

    docker compose config --quiet
    docker compose build

Before any functional deployment, confirm the image architecture is linux/amd64, codex --version
inside the backend reports 0.146.0, the non-root uid can write required mounts, and both healthchecks
pass.

This repository's inspected environment has no docker executable, so Compose config, image build,
runtime user, bundled Codex executable, and healthchecks were not executed here.

## Hardening

Both services use read-only image filesystems, dropped Linux capabilities, no-new-privileges, and
non-root runtime users. Temporary paths are size-bounded tmpfs mounts. The backend alone receives
workspace/data/auth mounts. No Docker socket, host PID/network namespace, devices, privileged mode,
or broad home mount is present. The Compose network permits backend egress required for Codex model
access but publishes no backend or app-server host port.

The gateway limits bodies to 21 MiB while the application limit is 20 MiB. This leaves multipart
overhead, while streaming byte, dimension, pixel, per-turn and per-session limits remain
authoritative. Backend /api responses are
no-store.

Pin base images by digest before a production release and establish dependency/image vulnerability
scanning. Tags are version-pinned in the foundation but are not immutable digests.

## Health and readiness

Frontend `/healthz` proves Nginx can serve. Backend `/api/health/live` proves the process event loop
can respond. `/api/health/ready` verifies workspace/repository roots, writable data, successful
SQLite/security initialization, and a live app-server generation after exact CLI version and
initialize/initialized handshake. A missing password verifier means Setup is required and does not
make security unready. Liveness remains independent so an orchestrator does not create a restart
loop merely because the child reached its bounded restart limit.

## Resource guidance

Use exactly one backend worker because app-server process ownership and in-memory JSON-RPC,
approval, projection, and event state cannot be shared. Compose defaults for a DS918+-class host
are 2 CPU and 2 GiB for backend, 0.5 CPU and 256 MiB for frontend, 256/64 PID limits, bounded tmpfs,
and bounded file descriptors. Treat these as starting points: observe large-context/build turns
before lowering them and leave headroom for NAS services. Application queue/history/output/upload
limits remain enforced when container limits are raised.

## HTTPS reverse proxy

For later HTTPS:

- terminate TLS at a trusted reverse proxy and forward only to the frontend port;
- set `WORKBENCH_PUBLIC_ORIGIN` to the exact HTTPS origin;
- set `WORKBENCH_SESSION_COOKIE_SECURE=true` and `WORKBENCH_ALLOW_INSECURE_HTTP=false`;
- preserve Host, X-Forwarded-Proto, WebSocket Upgrade, and Origin;
- configure the application to trust forwarded headers only from the internal proxy address;
- set HSTS at the TLS edge after validation;
- do not cache /api or /ws;
- close direct HTTP access or redirect it before using Secure cookies.

A concrete domain/certificate proxy configuration is intentionally not part of this phase.

The sample `.env.example` opts explicitly into degraded localhost HTTP with a non-Secure cookie.
Application validation refuses implicit HTTP and refuses insecure cookies for an HTTPS origin.
Compose requires the origin and both mode flags. Do not expose the sample HTTP configuration to the
internet.

Login's per-address bucket currently sees the backend's direct peer, normally Nginx inside Compose;
the global bucket remains effective. Do not trust arbitrary forwarded addresses. Narrow proxy
trust and tested client-address extraction are a production-readiness task.

## Upgrade and rollback

Back up Workbench state and the dedicated Codex home before upgrades. Stop new turns, shut down the
Workbench Compose project gracefully, build pinned images, run forward-only metadata migrations,
and verify health. Codex schema upgrades require the documented regeneration/review process.

A rollback may restore the previous images only when the metadata migration declares backward
compatibility; otherwise restore the coordinated state backup. Uploaded file and SQLite backups
must be consistent. Never restore or overwrite the host /workspace repositories as part of an
application rollback.

## Remaining deployment gates

Compose enables `WORKBENCH_APP_SERVER_START_ENABLED=true` and pins the image/expected version to
0.146.0. This implementation host has no Docker executable, so Compose resolution, image build,
non-root mount ownership, Codex egress/authentication, healthchecks, resource limits, graceful child
shutdown, and linux/amd64 NAS behavior must be validated on the Docker host before production use.
Pin image digests and add an SBOM after that validation.
