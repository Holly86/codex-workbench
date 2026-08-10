#!/bin/sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
exec python3 "$repository_root/scripts/check-secrets.py" "${1:---all}"
