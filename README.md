# Codex Workbench

Codex Workbench is a self-hosted, browser-based, single-user interface for the OpenAI Codex CLI
and its App Server. It provides conversation, repository, and Git workflows while keeping Codex
authentication and filesystem authority in a backend process that the browser cannot access
directly.

This is an independent community open-source project. It is not an official OpenAI product and is
not affiliated with or endorsed by OpenAI. “OpenAI”, “Codex”, and related names belong to their
respective owners. The project does not bundle ChatGPT artwork or other proprietary product assets.

> **Early release:** `v0.1.0` is the intended first public version. Publication remains blocked
> until a private security-report channel is configured. Treat interfaces, persistence formats,
> and operating procedures as subject to change.

## What it is for

Workbench is for an operator who wants to run Codex against repositories on a trusted workstation
or NAS, retain threads across browser reconnects, and use a narrow same-origin web application
instead of exposing Codex credentials or raw App Server transport to a browser.

It is not a multi-user SaaS platform, an IDE, a generic remote shell, a Codex authentication proxy,
or a replacement for repository hosting. It does not expose Docker Engine, Codex App Server stdio,
arbitrary filesystem APIs, force-push/reset/clean operations, or deployment automation.

## Architecture and trust boundary

```text
Browser
  │ same-origin HTTPS / WebSocket
  ▼
Frontend (unprivileged nginx)
  │ private HTTP / WebSocket proxy
  ▼
Backend (FastAPI, one worker)
  ├── supervised stdio JSONL ──► Codex App Server / Codex CLI 0.146.0
  ├── constrained native Git ──► selected repositories
  ├── metadata only ───────────► SQLite
  └── private mounts ──────────► CODEX_HOME, workspace, uploads, state
```

Only the backend can read the Codex home, launch the pinned App Server child, or operate on the
workspace. Browser requests use opaque repository identities and authenticated, CSRF-protected,
same-origin routes. The backend is never assigned a host port; the frontend is the only ingress.
See [architecture](docs/architecture.md), the [security model](docs/security-model.md), and the
[repository-scoped deploy-key guide](docs/deploy-keys.md).

## Main capabilities

- Codex-authoritative thread listing, start, resume, streaming turns, interruption, and restart
  reconciliation.
- Live assistant text, reasoning summaries, plans, commands, file activity, usage, rate limits,
  errors, and reconnect-safe event replay.
- Model and reasoning-effort selection from `model/list`.
- Schema-backed skills and plugins exposed by Codex.
- PNG, JPEG, WebP, and single-frame GIF handoff by paste, drag/drop, or file picker, with private
  uploads, progress, limits, ownership checks, and expiry.
- Background turns and native jobs that survive browser disconnection.
- Native Git status, diff, log, stage, commit, fetch, fast-forward-only pull, push, branch,
  repository initialization, and clone operations without starting a model turn.
- Capability-gated GitHub publication with explicit public-repository confirmation. Workbench never
  publishes automatically.
- Explicit `read-only`, `workspace-write`, and confirmed `danger-full-access` modes with separate
  approval policy controls and visible approval state.

Full Access intentionally removes the Codex sandbox. Code running in that mode can exercise the
backend user's workspace authority and may read backend mounts available to that user, including
Codex authentication material. Use it only for trusted work and review
[permissions and approvals](docs/permissions-and-approvals.md).

## Container distribution

The planned `v0.1.0` release publishes two public, `linux/amd64` images:

- `<your-dockerhub-namespace>/codex-workbench-backend:0.1.0`
- `<your-dockerhub-namespace>/codex-workbench-frontend:0.1.0`

No ARM64 support is claimed until it is built and tested. `compose.release.yaml` references these
images and contains no `build:` directives. The source-oriented `compose.yaml` remains available
for local image builds.

### Install from Docker Hub

After the images have been published, clone or download the release files and prepare configuration:

```sh
cp .env.example .env
# Set DOCKERHUB_NAMESPACE, the public origin, persistent host paths, PUID, and PGID.
# Create backend-net if it does not already exist.
sudo ./scripts/init-deployment.sh
docker compose -f compose.release.yaml pull
docker compose -f compose.release.yaml up -d
```

The Synology initializer creates only the documented state/upload/secret directories and generates
a session key when absent. It does not build, start, stop, or modify containers. The first browser
visit on a trusted network establishes the Argon2id login verifier.

For a later version, edit `WORKBENCH_VERSION` in `.env` (or export it for both commands), then run:

```sh
docker compose -f compose.release.yaml pull
docker compose -f compose.release.yaml up -d
```

Updates do not delete data, uploads, repositories, Codex state, or previous images. No prune step is
required or recommended.

## Install from source

Requirements are Python 3.12, Node.js 24, npm 11, and Codex CLI 0.146.0. Project dependencies stay
inside this repository.

```sh
make bootstrap
make verify
```

For development servers and safe local state, follow [development.md](docs/development.md). To build
the production images locally, configure `.env`, run the Synology initializer only on its supported
host layout, then use `docker compose build`. The backend Dockerfile installs and verifies the exact
Codex CLI/App Server version twice; generated protocol schemas are pinned to the same version.

## Persistence

The release Compose file bind-mounts five operator-controlled locations:

- Workbench metadata/database and private uploads;
- password-verifier and session-key directories;
- the Codex home containing Codex authentication, configuration, and threads;
- the workspace containing repositories.

Keep these paths outside the repository, restrict their host permissions, and back them up while all
writers are quiesced. Never put their contents in Git, build contexts, diagnostics, or support
issues. SQLite is used only for Workbench metadata; Codex remains authoritative for Codex threads.

## Reverse proxy and Synology

The supplied topology targets `linux/amd64` Synology deployments. The frontend binds only
`127.0.0.1:8085`; expose it through a narrowly configured HTTPS reverse proxy with WebSocket support.
Set `WORKBENCH_PUBLIC_ORIGIN` to the exact external HTTPS origin. Do not publish the backend or App
Server, do not mount the Docker socket, and do not expose the frontend directly to an untrusted LAN
or the internet. The detailed runbook is [docs/deployment.md](docs/deployment.md).

Some older Synology kernels do not provide unprivileged user namespaces. Codex sandbox modes that
depend on the packaged Linux sandbox can then be unavailable; Workbench detects that capability and
does not silently substitute Full Access. `read-only` remains available, while Full Access remains
an explicit high-risk choice. The same hosts may lack Docker CPU CFS quotas, so the provided Compose
does not claim CPU isolation.

## Security

The application uses one backend worker, an Argon2id first-start login, digest-only server sessions,
exact Origin/CSRF checks, a private App Server stdio channel, bounded uploads and queues, redacted
structured logging, read-only container roots, dropped capabilities, `no-new-privileges`, PID/memory
limits, and health checks. These controls do not replace network isolation, HTTPS, host permissions,
patching, backups, or careful Full Access decisions. Read [SECURITY.md](SECURITY.md) before exposing
an instance.

## Troubleshooting

- **Backend remains unhealthy:** verify that workspace, data, security, and Codex-home bind mounts
  exist, are owned by the configured numeric user/group, and that Codex CLI 0.146.0 can start.
- **Frontend starts but API calls fail:** verify the exact public origin, HTTPS proxy headers, Host,
  WebSocket forwarding, and that the frontend can reach the private backend service.
- **Sandbox mode is unavailable:** the host likely lacks the required user-namespace capability.
  Do not work around this by making the container privileged; use `read-only` or explicitly assess
  Full Access.
- **Release image cannot be pulled:** confirm the Docker Hub namespace, repository visibility,
  version, and `linux/amd64` platform.
- **Synology deployment check:** run `sudo ./scripts/diagnose-deployment.sh`; it is read-only and
  deliberately omits secret values.

## Development and release verification

```sh
make verify
```

Verification covers Ruff, strict MyPy, backend tests, ESLint with zero warnings, TypeScript,
frontend tests, the production build, dependency audits, current/history secret scanning, generated
schema integrity, deployment and release policies, shell checks, Compose fixtures, and
`git diff --check`.

The generated JSON/TypeScript protocol files below `schemas/codex-app-server` come from Codex CLI
0.146.0 and must be regenerated only through `make schemas`. See [CONTRIBUTING.md](CONTRIBUTING.md)
and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## License

Codex Workbench is licensed under the [Apache License 2.0](LICENSE) (`Apache-2.0`). Third-party
components retain their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
