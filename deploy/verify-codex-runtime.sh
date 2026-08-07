#!/bin/sh
set -eu

expected_codex_version=${1:?expected Codex version is required}
expected_node_version=${2:?expected Node version is required}
codex_launcher=/usr/local/bin/codex
node_binary=/usr/local/bin/node
codex_package=/usr/local/lib/node_modules/@openai/codex
platform_package=$codex_package/node_modules/@openai/codex-linux-x64
target_root=$platform_package/vendor/x86_64-unknown-linux-musl
expected_launcher_target=../lib/node_modules/@openai/codex/bin/codex.js
check_directory=/tmp/codex-runtime-verification

fail() {
    printf '%s\n' "[codex-runtime-check] FAILED: $*" >&2
    exit 1
}

pass() {
    printf '%s\n' "[codex-runtime-check] OK: $*"
}

cleanup() {
    rm -rf -- "$check_directory"
}

trap cleanup 0 1 2 15
rm -rf -- "$check_directory"
install -d -m 700 \
    "$check_directory/home" \
    "$check_directory/codex-home" \
    "$check_directory/tmp"

[ -L "$codex_launcher" ] || fail "Codex launcher is missing or is not a symbolic link"
launcher_target=$(readlink "$codex_launcher") \
    || fail "Codex launcher target cannot be read"
[ "$launcher_target" = "$expected_launcher_target" ] \
    || fail "Codex launcher points to an unexpected target"
[ -x "$codex_package/bin/codex.js" ] \
    || fail "Codex JavaScript launcher is missing or not executable"
pass "Codex launcher layout"

[ -x "$node_binary" ] || fail "Node runtime is missing or not executable"
if ! ldd "$node_binary" > "$check_directory/node.ldd" 2>&1; then
    fail "Node shared-library inspection failed"
fi
if grep -Fq 'not found' "$check_directory/node.ldd"; then
    fail "Node has an unresolved runtime library"
fi
set +e
HOME="$check_directory/home" \
CODEX_HOME="$check_directory/codex-home" \
TMPDIR="$check_directory/tmp" \
    "$node_binary" --version \
    > "$check_directory/node.stdout" \
    2> "$check_directory/node.stderr"
node_status=$?
set -e
[ "$node_status" -eq 0 ] \
    || fail "node --version exited with status $node_status (runtime file or library missing)"
[ ! -s "$check_directory/node.stderr" ] \
    || fail "node --version wrote unexpected stderr ($(wc -c < "$check_directory/node.stderr") bytes)"
printf 'v%s\n' "$expected_node_version" > "$check_directory/node.expected"
cmp -s "$check_directory/node.expected" "$check_directory/node.stdout" \
    || fail "node --version wrote unexpected stdout"
pass "Node v$expected_node_version and shared libraries"

[ -r "$codex_package/package.json" ] || fail "Codex package manifest is missing"
package_version=$(sed -n \
    's/^[[:space:]]*"version":[[:space:]]*"\([^"]*\)"[,]*[[:space:]]*$/\1/p' \
    "$codex_package/package.json")
[ "$package_version" = "$expected_codex_version" ] \
    || fail "Codex package manifest has the wrong version"
[ -r "$platform_package/package.json" ] \
    || fail "linux/amd64 Codex platform package is missing"
for executable in \
    "$target_root/bin/codex" \
    "$target_root/bin/codex-code-mode-host" \
    "$target_root/codex-resources/bwrap" \
    "$target_root/codex-resources/zsh/bin/zsh" \
    "$target_root/codex-path/rg"
do
    [ -x "$executable" ] \
        || fail "required native Codex runtime executable is missing"
done
[ -r "$target_root/codex-package.json" ] \
    || fail "native Codex runtime manifest is missing"
pass "complete @openai/codex linux/amd64 runtime artifacts"

set +e
HOME="$check_directory/home" \
CODEX_HOME="$check_directory/codex-home" \
TMPDIR="$check_directory/tmp" \
    "$codex_launcher" --version \
    > "$check_directory/version.stdout" \
    2> "$check_directory/version.stderr"
version_status=$?
set -e
[ "$version_status" -eq 0 ] \
    || fail "codex --version exited with status $version_status"
[ ! -s "$check_directory/version.stderr" ] \
    || fail "codex --version wrote unexpected stderr ($(wc -c < "$check_directory/version.stderr") bytes)"
printf 'codex-cli %s\n' "$expected_codex_version" > "$check_directory/version.expected"
if ! cmp -s "$check_directory/version.expected" "$check_directory/version.stdout"; then
    version_line=$(sed -n '1p' "$check_directory/version.stdout")
    version_lines=$(wc -l < "$check_directory/version.stdout")
    if [ "$version_lines" -eq 1 ] \
        && printf '%s\n' "$version_line" \
            | grep -Eq '^codex-cli (0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'; then
        fail "codex --version reported the wrong version"
    fi
    fail "codex --version wrote unexpected stdout"
fi
pass "exact codex-cli $expected_codex_version stdout with empty stderr"

set +e
HOME="$check_directory/home" \
CODEX_HOME="$check_directory/codex-home" \
TMPDIR="$check_directory/tmp" \
    "$codex_launcher" app-server --help \
    > "$check_directory/app-server-help.stdout" \
    2> "$check_directory/app-server-help.stderr"
help_status=$?
set -e
[ "$help_status" -eq 0 ] \
    || fail "codex app-server --help exited with status $help_status"
[ ! -s "$check_directory/app-server-help.stderr" ] \
    || fail "codex app-server --help wrote unexpected stderr ($(wc -c < "$check_directory/app-server-help.stderr") bytes)"
grep -Fq 'Usage: codex app-server' "$check_directory/app-server-help.stdout" \
    || fail "codex app-server --help wrote unexpected stdout"
grep -Fq -- '--stdio' "$check_directory/app-server-help.stdout" \
    || fail "codex app-server --help does not advertise --stdio"
pass "codex app-server --help and --stdio support"
