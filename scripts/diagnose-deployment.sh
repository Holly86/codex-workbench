#!/bin/sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
cd "$repository_root"

status() {
    printf '%s\n' "[DIAG] $*"
}

fail() {
    printf '%s\n' "[FEHLER] $*" >&2
    exit 1
}

command -v docker >/dev/null 2>&1 || fail "Docker ist auf diesem Host nicht verfügbar."
command -v docker-compose >/dev/null 2>&1 \
    || fail "docker-compose ist auf diesem Host nicht verfügbar."
[ -f .env ] && [ ! -L .env ] || fail ".env fehlt oder ist ein Symlink."

image_tag=$(./scripts/read-deployment-env.sh .env WORKBENCH_IMAGE_TAG) \
    || fail "WORKBENCH_IMAGE_TAG konnte nicht sicher gelesen werden."
project_name=$(./scripts/read-deployment-env.sh .env COMPOSE_PROJECT_NAME) \
    || fail "COMPOSE_PROJECT_NAME konnte nicht sicher gelesen werden."
[ "$project_name" = codex-workbench ] \
    || fail "COMPOSE_PROJECT_NAME muss exakt codex-workbench sein."
puid=$(./scripts/read-deployment-env.sh .env PUID) \
    || fail "PUID konnte nicht sicher gelesen werden."
pgid=$(./scripts/read-deployment-env.sh .env PGID) \
    || fail "PGID konnte nicht sicher gelesen werden."

temporary_file=$(mktemp /tmp/codex-workbench-diagnostic.XXXXXX) \
    || fail "Temporäre Diagnosedatei konnte nicht angelegt werden."
cleanup() {
    rm -f -- "$temporary_file"
}
trap cleanup 0 1 2 15

status "Docker: $(docker --version)"
status "Docker Compose: $(docker-compose version --short 2>/dev/null || printf 'unbekannt')"
docker-compose config > "$temporary_file" \
    || fail "docker-compose config konnte nicht gerendert werden."
./scripts/validate-compose.sh "$temporary_file" "$image_tag" "$puid" "$pgid"

network_driver=$(docker network inspect --format '{{.Driver}}' backend-net 2>/dev/null) \
    || fail "backend-net ist nicht verfügbar."
status "backend-net driver=$network_driver"

status "Workbench-eigene Containerzustände:"
docker-compose ps

backend_id=$(docker-compose ps -q backend)
frontend_id=$(docker-compose ps -q frontend)
[ -n "$backend_id" ] || fail "Der Workbench-Backend-Container läuft nicht."
[ -n "$frontend_id" ] || status "Der Frontend-Container läuft noch nicht."

status "Effektive Workbench-Portbindungen:"
docker inspect --format '{{json .NetworkSettings.Ports}}' "$backend_id"
if [ -n "$frontend_id" ]; then
    docker inspect --format '{{json .NetworkSettings.Ports}}' "$frontend_id"
fi

status "Effektive Workbench-Netzwerke:"
docker inspect --format '{{range $name, $_ := .NetworkSettings.Networks}}{{println $name}}{{end}}' "$backend_id"
if [ -n "$frontend_id" ]; then
    docker inspect --format '{{range $name, $_ := .NetworkSettings.Networks}}{{println $name}}{{end}}' "$frontend_id"
fi

status "Backend-Mounts (nur Quelle, Ziel und Schreibmodus; keine Inhalte):"
docker inspect --format '{{range .Mounts}}{{println .Source "->" .Destination "rw=" .RW}}{{end}}' "$backend_id"

status "Codex-Version, App-Server-Unterstützung, Identität und Pfadberechtigungen:"
docker-compose exec -T backend sh -c '
    set -eu
    printf "codex_path=%s\n" "$(command -v codex)"
    codex --version
    codex app-server --help >/dev/null
    printf "app_server_help=available\n"
    printf "uid=%s gid=%s\n" "$(id -u)" "$(id -g)"
    printf "HOME=%s\nCODEX_HOME=%s\n" "$HOME" "$CODEX_HOME"
    for directory in /home/workbench/.codex /workspace /workspace/repositories /var/lib/codex-workbench /var/lib/codex-workbench/state /var/lib/codex-workbench/uploads /var/lib/codex-workbench/security /tmp; do
        readable=false
        writable=false
        [ -r "$directory" ] && readable=true
        [ -w "$directory" ] && writable=true
        metadata=$(stat -c "%u:%g %a" "$directory")
        printf "path=%s owner_mode=%s readable=%s writable=%s\n" "$directory" "$metadata" "$readable" "$writable"
    done
    secret=/run/secrets/workbench/session.key
    secret_readable=false
    secret_writable=false
    [ -r "$secret" ] && secret_readable=true
    [ -w "$secret" ] && secret_writable=true
    printf "session_secret owner_mode=%s readable=%s writable=%s\n" "$(stat -c "%u:%g %a" "$secret")" "$secret_readable" "$secret_writable"
    ps -eo pid=,ppid=,uid=,comm=,args= | awk '\''$4 ~ /^codex/ && $0 ~ /app-server/ { printf "app_server pid=%s ppid=%s uid=%s comm=%s\n", $1, $2, $3, $4; found=1 } END { if (!found) print "app_server=not_found" }'\''
'

status "Backend Live/Ready (Status und sicherer Health-Body):"
docker-compose exec -T backend python -c '
import urllib.error
import urllib.request
for path in ("live", "ready"):
    url = f"http://127.0.0.1:8000/api/health/{path}"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            print(path, response.status, response.read(4096).decode("utf-8", "strict"))
    except urllib.error.HTTPError as error:
        print(path, error.code, error.read(4096).decode("utf-8", "replace"))
'

status "Diagnose abgeschlossen; es wurden keine Container oder fremden Ressourcen verändert."
