#!/bin/sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_root"

if [ -n "$(git status --porcelain -- schemas/codex-app-server)" ]; then
    printf '%s\n' "Generated Codex app-server schemas differ from the pinned tree." >&2
    exit 1
fi

expected=$(sed -n 's/^codex-cli //p' schemas/codex-app-server/VERSION)
if [ "$expected" != "0.146.0" ]; then
    printf '%s\n' "Unexpected generated-schema version: $expected" >&2
    exit 1
fi

printf '%s\n' "Generated Codex 0.146.0 schemas are unchanged."
