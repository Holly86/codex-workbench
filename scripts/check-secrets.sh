#!/bin/sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_root"

if rg --hidden --line-number --ignore-case \
    --glob '!.git/**' \
    --glob '!frontend/node_modules/**' \
    --glob '!scripts/check-secrets.sh' \
    '(sk-[a-z0-9_-]{20,}|OPENAI_API_KEY[[:space:]]*=[[:space:]]*[^$<{[:space:]]|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY)' .; then
    printf '%s\n' "Potential secret material found." >&2
    exit 1
fi

printf '%s\n' "No obvious secret patterns found."
