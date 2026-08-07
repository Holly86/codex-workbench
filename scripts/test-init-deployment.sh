#!/bin/sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
cd "$repository_root"

temporary_file=$(mktemp /tmp/codex-workbench-env-test.XXXXXX)
cleanup() {
    rm -f -- "$temporary_file"
}
trap cleanup 0 1 2 15

printf 'PUID=1026\r\nPGID = 100\r\nLITERAL=$(touch /must-not-run)\r\n' > "$temporary_file"
[ "$(./scripts/read-deployment-env.sh "$temporary_file" PUID)" = 1026 ]
[ "$(./scripts/read-deployment-env.sh "$temporary_file" PGID)" = 100 ]
[ "$(./scripts/read-deployment-env.sh "$temporary_file" LITERAL)" = '$(touch /must-not-run)' ]

printf 'PUID=1026\nPUID=1027\n' > "$temporary_file"
result=0
./scripts/read-deployment-env.sh "$temporary_file" PUID >/dev/null 2>&1 || result=$?
[ "$result" -eq 2 ] || {
    printf '%s\n' "Duplicate .env keys must return status 2." >&2
    exit 1
}

printf 'PGID=100\n' > "$temporary_file"
result=0
./scripts/read-deployment-env.sh "$temporary_file" PUID >/dev/null 2>&1 || result=$?
[ "$result" -eq 1 ] || {
    printf '%s\n' "Missing .env keys must return status 1." >&2
    exit 1
}

printf '%s\n' "Installer environment parser tests passed."
