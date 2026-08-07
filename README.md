# Codex Workbench

Codex Workbench is a self-hosted, browser-based, single-user client for the official OpenAI Codex
app-server. It runs Codex CLI 0.146.0 as one supervised backend child and provides a same-origin,
authenticated conversation UI without exposing Codex authentication, raw protocol messages, or
local filesystem paths to the browser.

## Implemented capabilities

- Direct `codex app-server --stdio` supervision with an exact, single-line
  `codex-cli 0.146.0` compatibility check,
  initialize/initialized handshake, request correlation, deadlines, cancellation, EOF detection,
  bounded exponential restart, readiness integration, and graceful shutdown.
- Opaque repository selection for direct non-symlink Git repositories, Codex-authoritative thread
  list/start/resume/read, turn start/interrupt, and post-restart reconciliation.
- Separate `read-only`, `workspace-write`, and confirmed `danger-full-access` sandbox choices with
  schema-backed `untrusted`, `on-request`, and `never` approval policies.
- One-use, short-lived, session/repository/thread-or-new-scope/policy-bound Full Access
  confirmations and permanently visible effective-mode labelling.
- Generation-, session-, thread-, turn-, and item-bound command, file-change, legacy, and permission
  approval registry with expiry, replay protection, invalidation, safe denial, and audit events.
- Authenticated same-origin WebSocket snapshots, monotonic per-session sequence ids, bounded replay,
  gap recovery, heartbeat, slow-client disconnection, and normalized incremental events.
- Live assistant text, reasoning summaries, plans, command/file activity, deterministic Working
  states, interruption, errors, usage, context derivation, rate limits, credits, and exhaustion.
- Decoded single-frame PNG, JPEG, WebP, and GIF image uploads through paste, drag/drop, or picker with
  real transfer progress, cancellation, private mode-0600 storage, decode/dimension/pixel limits,
  ownership/expiry checks, and schema-conformant `localImage` inputs.
- Argon2id first-start setup, digest-only server sessions, exact Origin/CSRF boundaries, mutation
  rate limiting, no-store APIs, structured redacted logging, and a responsive accessible React UI.

The main design documents are [architecture](docs/architecture.md),
[security model](docs/security-model.md), [app-server integration](docs/app-server-integration.md),
and the [capability matrix](docs/app-server-capability-matrix.md).

## Prerequisites

- Python 3.12 (the local verification environment uses compatible Python 3.14.5)
- Node.js 24 and npm 11
- Codex CLI 0.146.0
- Docker with Compose only for container build/runtime validation

Project dependencies stay local to this repository.

## Local development

    make bootstrap

Create restrictive local state as described in `docs/development.md`, enable the app server with
`WORKBENCH_APP_SERVER_START_ENABLED=true`, then run:

    cd backend
    .venv/bin/uvicorn codex_workbench.main:app --reload --host 127.0.0.1 --port 8000

    cd frontend
    npm run dev

Vite listens on 127.0.0.1:5173 and proxies same-origin API traffic to the backend.

## Verification

    make verify

This runs Ruff, strict MyPy, 83 backend tests including the deterministic fake app server, ESLint,
strict TypeScript, 9 frontend tests, the production build, secret scanning, Python/npm audits, the
generated-schema unchanged check, deployment policy and installer parser tests, shell syntax, and
`git diff --check`.

On the Synology deployment host additionally run from the repository after preparing `.env`:

    sudo ./scripts/init-deployment.sh
    sudo docker-compose config
    sudo docker-compose build --pull --no-cache
    sudo docker-compose up -d

If Docker is unavailable on a development host, these Docker checks remain a deployment-host gate
and must not be emulated by exposing another port.

## Deployment preparation

`./scripts/init-deployment.sh` is an idempotent, Synology-only installer for the fixed
`/volume1/docker/codex` topology. It validates or creates only Workbench data/upload/secret
directories, normalizes `.env` line endings, and creates a session key only when absent; it never
builds or starts containers and never changes the existing Codex home or workspace. Follow the
[deployment runbook](docs/deployment.md) and execute the real-host
[acceptance plan](docs/synology-test-plan.md). Compose starts the managed app server but never
publishes an app-server or backend host port. The frontend is attached to the existing external
`backend-net` without a fixed container or LAN IP. Its sole host publication is fixed at
`127.0.0.1:8085:8080`; production access is exclusively through the Synology HTTPS reverse proxy
targeting `http://127.0.0.1:8085`.

The DS918+ kernel cannot enforce Docker CPU CFS quotas. Compose deliberately contains no `cpus`,
`cpu_period`, or `cpu_quota`; memory, PID, file-descriptor, queue, upload, tmpfs, capability,
read-only-root, and health boundaries remain enforced. A Buildx warning that host Git is unavailable
is harmless: `.git` is excluded from the build context, images contain no fabricated commit label,
and runtime behavior does not depend on Git provenance. Run `sudo ./scripts/diagnose-deployment.sh`
for a secrets-free, read-only check of the Workbench containers after startup.

## Protocol schemas

Everything below `schemas/codex-app-server/typescript` and `schemas/codex-app-server/json` is
generated by Codex CLI 0.146.0 and is the protocol authority. Do not edit it manually. Use
`make schemas` only for a deliberate Codex upgrade and review the complete diff.

## Scope limits

There is no generic browser shell, arbitrary filesystem API, Git write API, terminal, editor,
multi-user administration, or deployment automation. Codex remains authoritative for threads;
SQLite stores only Workbench metadata. No license has been selected.
