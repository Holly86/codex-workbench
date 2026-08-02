# Local development

## Toolchain

The production target is Python 3.12. The inspected build host supplied Python 3.14.5, Node
24.18.1, npm 11.12.1, and Codex CLI 0.146.0. Python dependencies live in backend/.venv and frontend
dependencies in frontend/node_modules. Neither bootstrap script installs global packages.

## Bootstrap

    ./scripts/bootstrap-backend.sh
    ./scripts/bootstrap-frontend.sh

When Make is installed, `make bootstrap` runs the same scripts.

The backend script creates a virtual environment and installs the backend with the development
extra. The frontend script uses npm ci, so package-lock.json is authoritative.

## Run

The Synology-only `scripts/init-deployment.sh` must not be used for local development. Initialize
ignored repository-local state once (this creates a session key, not a password):

    mkdir -p data/state/uploads data/state/security data/secrets
    chmod 700 data data/state data/state/uploads data/state/security data/secrets
    if [ ! -e data/secrets/session.key ]; then
        openssl rand -hex 32 > data/secrets/session.key
    fi
    chmod 600 data/secrets/session.key

Terminal 1:

    cd backend
    export WORKBENCH_PUBLIC_ORIGIN=http://127.0.0.1:5173
    export WORKBENCH_SESSION_COOKIE_SECURE=false
    export WORKBENCH_ALLOW_INSECURE_HTTP=true
    export WORKBENCH_DATA_DIR=../data/state
    export WORKBENCH_UPLOADS_DIR=../data/state/uploads
    export WORKBENCH_DATABASE_PATH=../data/state/security/workbench.sqlite3
    export WORKBENCH_PASSWORD_VERIFIER_FILE=../data/state/security/password.argon2id
    export WORKBENCH_SESSION_SECRET_FILE=../data/secrets/session.key
    .venv/bin/uvicorn codex_workbench.main:app --reload --host 127.0.0.1 --port 8000

For a local checkout outside /workspace, either create the expected directories or provide
`WORKBENCH_WORKSPACE_ROOT` and `WORKBENCH_REPOSITORIES_ROOT`. All configured child roots, database,
and verifier paths must remain inside their corresponding parent.

Terminal 2:

    cd frontend
    npm run dev

Only loopback listeners are used by development defaults. Vite proxies same-origin API requests.
Open `http://127.0.0.1:5173` and complete first-start Setup. Do not reuse the explicit insecure HTTP
mode outside host-local development; Synology production requires its documented HTTPS reverse
proxy.

## Checks

Backend:

    cd backend
    .venv/bin/ruff check .
    .venv/bin/mypy
    .venv/bin/pytest

Frontend:

    cd frontend
    npm run lint
    npm run typecheck
    npm test
    npm run build

Repository:

    ./scripts/check-secrets.sh
    ./scripts/audit-dependencies.sh
    git diff --check

Or run all of the above with `./scripts/verify.sh`; `make verify` is an optional equivalent.

## Configuration principles

Configuration uses `WORKBENCH_` environment variables. The session-key **path** is configuration;
the key itself remains in a mode-0600 regular, non-symlink file. The Argon2id verifier is created
below the state directory. Do not place passwords, credentials, hashes, or keys in `.env`. The
application does not use cross-origin browser calls; the frontend proxy is the only public ingress.

## Schema updates

Only update schemas during an explicit Codex CLI upgrade. Update
schemas/codex-app-server/VERSION, install that exact CLI version, run make schemas, and review
method, request, response, and notification changes before adapting code. Never hand-edit
generated files.
