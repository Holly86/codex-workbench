#!/bin/sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_root/frontend"

npm ci
printf '%s\n' "Frontend dependencies ready: $repository_root/frontend/node_modules"
