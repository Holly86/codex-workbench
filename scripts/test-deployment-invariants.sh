#!/bin/sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
cd "$repository_root"

fixture=tests/deployment/rendered-compose.yaml
temporary_file=$(mktemp /tmp/codex-workbench-compose-test.XXXXXX)
cleanup() {
    rm -f -- "$temporary_file"
}
trap cleanup 0 1 2 15

./scripts/validate-compose.sh "$fixture" test-release 1026 100 >/dev/null

expect_rejected() {
    label=$1
    if ./scripts/validate-compose.sh "$temporary_file" test-release 1026 100 >/dev/null 2>&1; then
        printf '%s\n' "Invariant fixture was unexpectedly accepted: $label" >&2
        exit 1
    fi
}

sed 's/host_ip: 127\.0\.0\.1/host_ip: 0.0.0.0/' "$fixture" > "$temporary_file"
expect_rejected wildcard-port

sed '/user: "1026:100"/a\    cpu_quota: 200000' "$fixture" > "$temporary_file"
expect_rejected cpu-cfs

sed 's/backend-net/docker-net/g' "$fixture" > "$temporary_file"
expect_rejected obsolete-network

sed 's/driver: bridge/driver: macvlan/' "$fixture" > "$temporary_file"
expect_rejected macvlan

sed '/workbench-private: null/a\        ipv4_address: 172.30.0.10' "$fixture" > "$temporary_file"
expect_rejected fixed-ip

sed 's/external: true/external: false/' "$fixture" > "$temporary_file"
expect_rejected nonexternal-backend-net

sed 's/codex-workbench-backend:test-release/codex-workbench-backend:wrong-release/' "$fixture" > "$temporary_file"
expect_rejected wrong-image-tag

printf '%s\n' "Deployment invariant parser tests passed."
