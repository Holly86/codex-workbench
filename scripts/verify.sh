#!/bin/sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_root"

(
    cd backend
    .venv/bin/ruff check .
    .venv/bin/mypy
    .venv/bin/pytest
)

(
    cd frontend
    npm run lint
    npm run typecheck
    npm test
    npm run build
)

./scripts/check-secrets.sh
./scripts/audit-dependencies.sh
./scripts/check-generated-schemas.sh
git diff --check
printf '%s\n' "All available verification steps passed."
