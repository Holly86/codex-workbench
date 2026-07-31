# Deployment

## Target topology

Docker Compose builds two images. The frontend Nginx container is the only service with a host
port. The FastAPI backend is reachable only on the Compose internal network and later owns one
codex app-server child over stdio. This preserves one public web origin while keeping separate
frontend and backend build stages.

The Synology DS918+ target is x86-64 with four CPU cores and 12 GB RAM. The initial topology uses
one backend replica, bounded queues/uploads, and no resource-intensive editor or terminal bundle in
the critical path.

## Persistent mounts

| Host setting | Container target | Access | Purpose |
| --- | --- | --- | --- |
| WORKSPACE_PATH | /workspace | read/write | repositories, screenshots, documents, transfers |
| WORKBENCH_DATA_PATH/state | /var/lib/codex-workbench | read/write | SQLite, uploads, runtime metadata |
| CODEX_HOME_PATH | /home/workbench/.codex | read/write | targeted existing Codex authentication/config |
| WORKBENCH_DATA_PATH/secrets | /run/secrets/workbench | read-only | Workbench password/session secret files |

The Codex-home source must be a dedicated persistent directory with the authenticated CLI state
needed by this Workbench. Do not mount an entire user home. The frontend receives none of these
mounts. Host ownership must match WORKBENCH_UID/GID or use an intentional NAS ACL.

The existing Codex container is unrelated to this Compose project and must not be restarted,
replaced, joined, or mutated.

## Initialize without deployment

    ./scripts/init-deployment.sh

This copies .env.example to ignored .env only when absent, creates ignored state/upload/secret
directories with restrictive permissions, and generates a random session key. It does not build or
start a container. Set CODEX_HOME_PATH and the canonical public origin afterward. Never put a
password, Codex token, API key, or session key in .env.

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
or broad home mount is present.

The gateway limits bodies to 21 MiB while the application limit is 20 MiB. This leaves protocol
overhead but application streaming and quotas remain authoritative. Backend /api responses are
no-store.

Pin base images by digest before a production release and establish dependency/image vulnerability
scanning. Tags are version-pinned in the foundation but are not immutable digests.

## Health and readiness

Frontend /healthz proves Nginx can serve. Backend /api/health/live proves the process event loop can
respond. /api/health/ready verifies allowed workspace/repository roots and writable data plus the
safe foundation app-server default.

When productive app-server management is added, readiness must require successful CLI version
check and initialize/initialized handshake. Liveness must remain independent so an orchestrator
does not create a restart loop for an external account or mount problem.

## Resource guidance

Start with one backend worker because app-server process ownership and in-memory JSON-RPC
correlation cannot be shared between Uvicorn workers. Apply container memory/CPU limits only after
measuring Codex and build workloads; overly low limits can kill valid turns. Bound application
queues, upload concurrency, log retention, and frontend caches regardless.

## HTTPS reverse proxy

For later HTTPS:

- terminate TLS at a trusted reverse proxy and forward only to the frontend port;
- set WORKBENCH_PUBLIC_ORIGIN to the exact https origin;
- preserve Host, X-Forwarded-Proto, WebSocket Upgrade, and Origin;
- configure the application to trust forwarded headers only from the internal proxy address;
- set HSTS at the TLS edge after validation;
- do not cache /api or /ws;
- close direct HTTP access or redirect it before using Secure cookies.

A concrete domain/certificate proxy configuration is intentionally not part of this phase.

## Upgrade and rollback

Back up Workbench state and the dedicated Codex home before upgrades. Stop new turns, shut down the
Workbench Compose project gracefully, build pinned images, run forward-only metadata migrations,
and verify health. Codex schema upgrades require the documented regeneration/review process.

A rollback may restore the previous images only when the metadata migration declares backward
compatibility; otherwise restore the coordinated state backup. Uploaded file and SQLite backups
must be consistent. Never restore or overwrite the host /workspace repositories as part of an
application rollback.

## Foundation limitation

Compose intentionally sets WORKBENCH_APP_SERVER_START_ENABLED=false. There is no Workbench login
route yet. Starting this Compose file provides a UI/health foundation only; it does not authorize
or run agent work.
