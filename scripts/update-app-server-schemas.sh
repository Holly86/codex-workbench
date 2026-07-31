#!/bin/sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_root"

expected=$(sed -n 's/^codex-cli //p' schemas/codex-app-server/VERSION)
actual=$(codex --version | awk '{print $2}')

if [ "$actual" != "$expected" ]; then
    printf '%s\n' "Refusing schema update: expected Codex $expected, found $actual." >&2
    exit 1
fi

codex app-server generate-ts --out schemas/codex-app-server/typescript
codex app-server generate-json-schema --out schemas/codex-app-server/json
printf '%s\n' "Regenerated app-server schemas with Codex $actual."
