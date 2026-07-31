#!/bin/sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

(
    cd "$repository_root/backend"
    .venv/bin/pip-audit --skip-editable --progress-spinner off
)

(
    cd "$repository_root/frontend"
    npm audit --audit-level=high
)

printf '%s\n' "Dependency audits passed."
