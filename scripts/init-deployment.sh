#!/bin/sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_root"
umask 077

if [ ! -f .env ]; then
    cp .env.example .env
    printf '%s\n' "Created .env. Set CODEX_HOME_PATH and WORKBENCH_PUBLIC_ORIGIN before Compose."
fi

mkdir -p data/state/uploads data/secrets
chmod 700 data data/state data/state/uploads data/secrets

if [ ! -f data/secrets/session.key ]; then
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 32 > data/secrets/session.key
    else
        dd if=/dev/urandom bs=32 count=1 2>/dev/null | od -An -tx1 | tr -d ' \n' > data/secrets/session.key
    fi
    chmod 600 data/secrets/session.key
fi

printf '%s\n' "Deployment directories initialized. No container was started."
