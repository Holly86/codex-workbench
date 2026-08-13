#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
    printf '%s\n' "usage: test-runtime-identity.sh BACKEND_IMAGE" >&2
    exit 64
fi

image=$1

verify_identity() {
    runtime_uid=$1
    runtime_gid=$2
    docker run --rm \
        --user "$runtime_uid:$runtime_gid" \
        --read-only \
        --tmpfs /tmp:size=32m,mode=1777,uid="$runtime_uid",gid="$runtime_gid" \
        --network none \
        "$image" \
        sh -ec '
            test "$(id -u)" = "$1"
            test "$(id -g)" = "$2"
            test "$(getent passwd "$1" | cut -d: -f6)" = /home/workbench
            getent group "$2" >/dev/null
            test "$(stat -c %a "$NSS_WRAPPER_PASSWD")" = 600
            test "$(stat -c %a "$NSS_WRAPPER_GROUP")" = 600
            test "$(stat -c %u "$NSS_WRAPPER_PASSWD")" = "$1"
            test "$(stat -c %u "$NSS_WRAPPER_GROUP")" = "$1"
            python -c "import os, pwd; record = pwd.getpwuid(os.getuid()); assert record.pw_dir == '\''/home/workbench'\''"
            key_dir=$(mktemp -d)
            trap '\''rm -f "$key_dir/key" "$key_dir/key.pub"; rmdir "$key_dir"'\'' EXIT HUP INT TERM
            ssh-keygen -q -t ed25519 -N "" -C runtime-identity-test -f "$key_dir/key"
            test -s "$key_dir/key"
            test -s "$key_dir/key.pub"
            ssh -G -F /dev/null github.com >/dev/null
        ' runtime-identity-test "$runtime_uid" "$runtime_gid"
    printf 'runtime identity verified: %s:%s\n' "$runtime_uid" "$runtime_gid"
}

verify_identity 1000 1000
verify_identity 1026 100
verify_identity 12345 23456
