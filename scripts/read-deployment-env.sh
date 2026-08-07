#!/bin/sh
set -eu

[ "$#" -eq 2 ] || exit 2
environment_file=$1
wanted=$2

case "$wanted" in
    ''|*[!A-Z0-9_]*) exit 2 ;;
esac
[ -f "$environment_file" ] && [ ! -L "$environment_file" ] || exit 2

awk -v wanted="$wanted" '
    /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
    {
        line = $0
        sub(/^[[:space:]]*/, "", line)
        separator = index(line, "=")
        if (separator == 0) { next }
        name = substr(line, 1, separator - 1)
        sub(/[[:space:]]*$/, "", name)
        if (name != wanted) { next }
        found += 1
        result = substr(line, separator + 1)
        sub(/^[[:space:]]*/, "", result)
        sub(/[[:space:]]*$/, "", result)
    }
    END {
        if (found == 1) { print result; exit 0 }
        if (found > 1) { exit 2 }
        exit 1
    }
' "$environment_file"
