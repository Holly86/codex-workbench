#!/bin/sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
cd "$repository_root"
umask 077

persistent_root=/volume1/docker/codex
created_env=false
configuration_ready=true
temporary_file=

status() {
    printf '%s\n' "[OK] $*"
}

notice() {
    printf '%s\n' "[HINWEIS] $*"
}

fail() {
    printf '%s\n' "[FEHLER] $*" >&2
    exit 1
}

cleanup() {
    if [ -n "$temporary_file" ]; then
        rm -f -- "$temporary_file"
    fi
}

trap cleanup 0 1 2 15

require_file() {
    [ -f "$1" ] || fail "Erforderliche Datei fehlt: $1"
    [ ! -L "$1" ] || fail "Erforderliche Datei darf kein Symlink sein: $1"
}

require_lf_file() {
    require_file "$1"
    carriage_return=$(printf '\r')
    if grep -q "$carriage_return" "$1"; then
        fail "Datei muss LF-Zeilenenden verwenden: $1"
    fi
}

read_env() {
    key=$1
    value=$(./scripts/read-deployment-env.sh .env "$key") || {
        result=$?
        if [ "$result" -eq 2 ]; then
            fail "Variable $key ist in .env mehrfach definiert."
        fi
        fail "Variable $key fehlt in .env."
    }
    [ -n "$value" ] || fail "Variable $key ist in .env leer."
    case "$value" in
        *'"'*|*"'"*) fail "Variable $key darf nicht in Anführungszeichen stehen." ;;
    esac
    printf '%s' "$value"
}

validate_integer() {
    label=$1
    value=$2
    minimum=$3
    maximum=$4
    case "$value" in
        ''|*[!0-9]*) fail "$label muss eine ganze Zahl sein." ;;
    esac
    [ "$value" -ge "$minimum" ] && [ "$value" -le "$maximum" ] \
        || fail "$label muss zwischen $minimum und $maximum liegen."
}

validate_decimal() {
    label=$1
    value=$2
    minimum=$3
    maximum=$4
    awk -v value="$value" -v minimum="$minimum" -v maximum="$maximum" '
        BEGIN {
            if (value !~ /^[0-9]+([.][0-9]+)?$/ || value + 0 < minimum || value + 0 > maximum) {
                exit 1
            }
        }
    ' || fail "$label muss eine Dezimalzahl zwischen $minimum und $maximum sein."
}

validate_boolean() {
    label=$1
    value=$2
    case "$value" in
        true|false) ;;
        *) fail "$label muss exakt true oder false sein." ;;
    esac
}

validate_managed_path() {
    label=$1
    path=$2
    case "$path" in
        "$persistent_root"/*) ;;
        *) fail "$label muss ein absoluter Pfad unter $persistent_root sein: $path" ;;
    esac
    case "$path" in
        */|*'/../'*|*'/./'*|*'//'|*'/..'|*'/.')
            fail "$label enthält ein unsicheres Pfadsegment: $path"
            ;;
    esac
}

create_managed_directory() {
    path=$1
    mode=$2
    if [ -L "$path" ]; then
        fail "Verwaltetes Verzeichnis darf kein Symlink sein: $path"
    fi
    mkdir -p -- "$path"
    [ -d "$path" ] && [ ! -L "$path" ] || fail "Unsicheres Verzeichnis: $path"
    resolved=$(CDPATH= cd -- "$path" && pwd -P)
    case "$resolved" in
        "$persistent_root_resolved"/*) ;;
        *) fail "Pfad verlässt nach Symlink-Auflösung den persistenten Root: $path -> $resolved" ;;
    esac
    chown "$puid:$pgid" "$path"
    chmod "$mode" "$path"
}

require_shared_directory() {
    path=$1
    [ -d "$path" ] && [ ! -L "$path" ] \
        || fail "Das bestehende gemeinsame Verzeichnis fehlt oder ist ein Symlink: $path"
    resolved=$(CDPATH= cd -- "$path" && pwd -P)
    case "$resolved" in
        "$persistent_root_resolved"/*) ;;
        *) fail "Gemeinsamer Pfad verlässt den persistenten Root: $path -> $resolved" ;;
    esac
}

secure_known_file() {
    path=$1
    [ ! -e "$path" ] && [ ! -L "$path" ] && return 0
    [ -f "$path" ] && [ ! -L "$path" ] \
        || fail "Persistente Datei ist kein reguläres Nicht-Symlink: $path"
    chown "$puid:$pgid" "$path"
    chmod 600 "$path"
}

check_host_tcp_port_available() {
    checked_port=$1
    port_hex=$(printf '%04X' "$checked_port")
    tcp_tables=/proc/net/tcp
    [ -r /proc/net/tcp ] \
        || fail "TCP-Portbelegung kann nicht über /proc/net/tcp geprüft werden."
    if [ -r /proc/net/tcp6 ]; then
        tcp_tables="$tcp_tables /proc/net/tcp6"
    fi
    # The word split is intentional: tcp_tables contains only the two fixed procfs paths above.
    if awk -v wanted="$port_hex" '
        FNR == 1 { next }
        {
            split($2, local_address, ":")
            if (toupper(local_address[2]) == wanted && $4 == "0A") {
                found = 1
            }
        }
        END { exit(found ? 0 : 1) }
    ' $tcp_tables; then
        fail "TCP-Port $checked_port ist auf dem Host bereits belegt."
    fi
    if command -v docker >/dev/null 2>&1 \
        && docker ps --format '{{.Ports}}' 2>/dev/null \
            | grep -Eq "(^|, )[^,]*:${checked_port}->"; then
        fail "Docker veröffentlicht Host-Port $checked_port bereits."
    fi
    status "TCP-Port $checked_port ist auf dem Host frei."
}

validate_rendered_ports() {
    rendered_compose=$1
    awk '
        function indentation(value, copy) {
            copy = value
            sub(/^ */, "", copy)
            return length(value) - length(copy)
        }
        function trim(value) {
            sub(/^[[:space:]]*/, "", value)
            sub(/[[:space:]]*$/, "", value)
            return value
        }
        function unquote(value, first, last) {
            value = trim(value)
            first = substr(value, 1, 1)
            last = substr(value, length(value), 1)
            if ((first == "\"" && last == "\"") || (first == single_quote && last == single_quote)) {
                return substr(value, 2, length(value) - 2)
            }
            return value
        }
        function mapping_value(value) {
            sub(/^[^:]*:[[:space:]]*/, "", value)
            return unquote(value)
        }
        BEGIN { single_quote = sprintf("%c", 39) }
        /^[[:space:]]*$/ { next }
        {
            raw = $0
            level = indentation(raw)
            stripped = raw
            sub(/^ */, "", stripped)

            if (level == 0) {
                in_services = (stripped == "services:")
                service = ""
            } else if (in_services && level == 2 && stripped ~ /^[A-Za-z0-9_.-]+:[[:space:]]*$/) {
                service = stripped
                sub(/:[[:space:]]*$/, "", service)
            }

            if (stripped == "ports:") {
                port_blocks += 1
                port_owner = service
                in_ports = 1
                ports_indent = level
                next
            }
            if (in_ports && (level < ports_indent || (level == ports_indent && stripped !~ /^-/))) {
                in_ports = 0
            }
            if (!in_ports) {
                next
            }

            port_line = stripped
            if (port_line ~ /^-[[:space:]]*/) {
                sub(/^-[[:space:]]*/, "", port_line)
                if (port_line !~ /^(host_ip|published|target|protocol|mode):/) {
                    short_count += 1
                    short_value = unquote(port_line)
                    next
                }
            }
            if (port_line ~ /^host_ip:/) {
                host_ip_count += 1
                host_ip = mapping_value(port_line)
            } else if (port_line ~ /^published:/) {
                published_count += 1
                published = mapping_value(port_line)
            } else if (port_line ~ /^target:/) {
                target_count += 1
                target = mapping_value(port_line)
            }
        }
        END {
            structured_ok = (short_count == 0 && host_ip_count == 1 && host_ip == "127.0.0.1" && published_count == 1 && published == "8085" && target_count == 1 && target == "8080")
            short_ok = (short_count == 1 && short_value == "127.0.0.1:8085:8080" && host_ip_count == 0 && published_count == 0 && target_count == 0)
            if (port_blocks != 1 || port_owner != "frontend" || (!structured_ok && !short_ok)) {
                exit 1
            }
        }
    ' "$rendered_compose" \
        || fail "Compose muss genau eine Portfreigabe 127.0.0.1:8085:8080 am Frontend rendern."
}

[ "$(id -u)" -eq 0 ] || fail "Das Skript muss als root ausgeführt werden, damit numerische Eigentümer sicher gesetzt werden können."

require_file .env.example
require_file compose.yaml
require_file deploy/backend.Dockerfile
require_file deploy/frontend.Dockerfile
require_file deploy/nginx.conf
require_file schemas/codex-app-server/VERSION
require_file scripts/validate-compose.sh
require_file scripts/read-deployment-env.sh
require_lf_file .env.example
for shell_script in scripts/*.sh; do
    require_lf_file "$shell_script"
done
status "Compose-Datei, Image-Definitionen, Nginx-Konfiguration und Schema-Pin sind vorhanden."

if [ -L .env ]; then
    fail ".env darf kein Symlink sein."
fi
if [ ! -e .env ]; then
    cp .env.example .env
    chmod 600 .env
    created_env=true
    configuration_ready=false
    notice ".env wurde aus .env.example angelegt und muss vor dem Start angepasst werden."
else
    require_file .env
    chmod 600 .env
fi

carriage_return=$(printf '\r')
if grep -q "$carriage_return" .env; then
    original_owner=$(stat -c '%u:%g' .env) \
        || fail "Eigentümer von .env konnte nicht gelesen werden."
    temporary_file=.env.normalized.$$
    [ ! -e "$temporary_file" ] && [ ! -L "$temporary_file" ] \
        || fail "Temporäre Datei existiert bereits: $temporary_file"
    tr -d '\r' < .env > "$temporary_file"
    chown "$original_owner" "$temporary_file"
    chmod 600 "$temporary_file"
    mv -f -- "$temporary_file" .env
    temporary_file=
    status ".env wurde atomar von CRLF/CR auf LF normalisiert."
fi

project_name=$(read_env COMPOSE_PROJECT_NAME)
awk -v value="$project_name" '
    BEGIN { if (value !~ /^[a-z0-9][a-z0-9_-]*$/) { exit 1 } }
' || fail "COMPOSE_PROJECT_NAME darf nur Kleinbuchstaben, Ziffern, Unterstriche und Bindestriche enthalten."
[ "$project_name" = codex-workbench ] \
    || fail "COMPOSE_PROJECT_NAME muss zum Schutz fremder Container exakt codex-workbench sein."

image_tag=$(read_env WORKBENCH_IMAGE_TAG)
awk -v value="$image_tag" '
    BEGIN {
        if (length(value) > 128 || value !~ /^[A-Za-z0-9_][A-Za-z0-9_.-]*$/) { exit 1 }
    }
' || fail "WORKBENCH_IMAGE_TAG ist kein gültiger Docker-Tag."
case "$image_tag" in
    local|latest|replace-with-unique-release-id)
        configuration_ready=false
        notice "WORKBENCH_IMAGE_TAG benötigt vor dem Build eine eindeutige Release-ID."
        ;;
esac

origin=$(read_env WORKBENCH_PUBLIC_ORIGIN)
cookie_secure=$(read_env WORKBENCH_SESSION_COOKIE_SECURE)
allow_http=$(read_env WORKBENCH_ALLOW_INSECURE_HTTP)
validate_boolean WORKBENCH_SESSION_COOKIE_SECURE "$cookie_secure"
validate_boolean WORKBENCH_ALLOW_INSECURE_HTTP "$allow_http"
case "$origin" in
    http://*)
        [ "$origin" = http://127.0.0.1:8085 ] \
            || fail "HTTP ist ausschließlich für den lokalen Test-Origin http://127.0.0.1:8085 zulässig."
        [ "$cookie_secure" = false ] && [ "$allow_http" = true ] \
            || fail "HTTP benötigt SESSION_COOKIE_SECURE=false und ALLOW_INSECURE_HTTP=true."
        notice "Lokaler HTTP-Testmodus ist aktiv; regulärer Betrieb erfordert eine HTTPS-Origin hinter dem Synology Reverse Proxy."
        ;;
    https://*)
        [ "$cookie_secure" = true ] && [ "$allow_http" = false ] \
            || fail "HTTPS benötigt SESSION_COOKIE_SECURE=true und ALLOW_INSECURE_HTTP=false."
        ;;
    *) fail "WORKBENCH_PUBLIC_ORIGIN muss mit http:// oder https:// beginnen." ;;
esac
authority=${origin#*://}
case "$authority" in
    ''|*/*|*'?'*|*'#'*|*@*|*[[:space:]]*)
        fail "WORKBENCH_PUBLIC_ORIGIN muss ein exakter Origin ohne Pfad, Credentials, Query oder Fragment sein."
        ;;
esac
case "$authority" in
    example|example:*|*.example|*.example:*|*.example.com|*.example.com:*|\
    *.example.net|*.example.net:*|*.example.org|*.example.org:*|*.invalid|*.invalid:*|\
    192.0.2.*|198.51.100.*|203.0.113.*|*'<'*|*'>'*|replace-*|change-me*)
        configuration_ready=false
        notice "WORKBENCH_PUBLIC_ORIGIN enthält noch einen Dokumentationsplatzhalter."
        ;;
esac
case "$origin" in
    https://localhost|https://localhost:*|https://127.*|https://0.0.0.0*|https://\[::1\]*)
        fail "Regulärer HTTPS-Betrieb erwartet den externen Reverse-Proxy-Origin, keine Loopback-Origin."
        ;;
esac

samesite=$(read_env WORKBENCH_SESSION_COOKIE_SAMESITE)
case "$samesite" in
    strict|lax) ;;
    *) fail "WORKBENCH_SESSION_COOKIE_SAMESITE muss strict oder lax sein." ;;
esac

log_level=$(read_env WORKBENCH_LOG_LEVEL)
case "$log_level" in
    DEBUG|INFO|WARNING|ERROR|CRITICAL) ;;
    *) fail "WORKBENCH_LOG_LEVEL ist ungültig." ;;
esac

for integer_rule in \
    WORKBENCH_SESSION_IDLE_TIMEOUT_SECONDS:300:2592000 \
    WORKBENCH_SESSION_ABSOLUTE_TIMEOUT_SECONDS:300:7776000 \
    WORKBENCH_LOGIN_RATE_LIMIT_ATTEMPTS:1:100 \
    WORKBENCH_LOGIN_RATE_LIMIT_GLOBAL_ATTEMPTS:1:1000 \
    WORKBENCH_LOGIN_RATE_LIMIT_WINDOW_SECONDS:10:86400 \
    WORKBENCH_APP_SERVER_MAX_LINE_BYTES:65536:33554432 \
    WORKBENCH_APP_SERVER_RESTART_LIMIT:0:50 \
    WORKBENCH_APP_SERVER_RESTART_WINDOW_SECONDS:10:3600 \
    WORKBENCH_REALTIME_HISTORY_SIZE:16:10000 \
    WORKBENCH_REALTIME_CLIENT_QUEUE_SIZE:8:2048 \
    WORKBENCH_REALTIME_HEARTBEAT_SECONDS:5:120 \
    WORKBENCH_APPROVAL_TTL_SECONDS:15:1800 \
    WORKBENCH_FULL_ACCESS_CONFIRMATION_TTL_SECONDS:15:600 \
    WORKBENCH_MUTATION_RATE_LIMIT_REQUESTS:10:5000 \
    WORKBENCH_MUTATION_RATE_LIMIT_GLOBAL_REQUESTS:10:20000 \
    WORKBENCH_MUTATION_RATE_LIMIT_WINDOW_SECONDS:10:3600 \
    WORKBENCH_MAX_COMMAND_OUTPUT_BYTES:1024:4194304 \
    WORKBENCH_MAX_JSON_REQUEST_BYTES:1024:10485760 \
    WORKBENCH_MAX_UPLOAD_BYTES:1:104857600 \
    WORKBENCH_MAX_UPLOADS_PER_TURN:1:32 \
    WORKBENCH_MAX_UPLOADS_PER_SESSION:1:512 \
    WORKBENCH_MAX_UPLOAD_SESSION_BYTES:1:1073741824 \
    WORKBENCH_UPLOAD_TTL_SECONDS:300:2592000 \
    WORKBENCH_UPLOAD_MAX_DIMENSION:256:50000 \
    WORKBENCH_UPLOAD_MAX_PIXELS:65536:250000000
do
    integer_name=${integer_rule%%:*}
    remainder=${integer_rule#*:}
    integer_minimum=${remainder%%:*}
    integer_maximum=${remainder#*:}
    integer_value=$(read_env "$integer_name")
    validate_integer "$integer_name" "$integer_value" "$integer_minimum" "$integer_maximum"
done

for decimal_rule in \
    WORKBENCH_APP_SERVER_REQUEST_TIMEOUT_SECONDS:1:600 \
    WORKBENCH_APP_SERVER_SHUTDOWN_TIMEOUT_SECONDS:0.5:60 \
    WORKBENCH_APP_SERVER_BACKOFF_INITIAL_SECONDS:0.05:60 \
    WORKBENCH_APP_SERVER_BACKOFF_MAX_SECONDS:0.1:300
do
    decimal_name=${decimal_rule%%:*}
    remainder=${decimal_rule#*:}
    decimal_minimum=${remainder%%:*}
    decimal_maximum=${remainder#*:}
    decimal_value=$(read_env "$decimal_name")
    validate_decimal "$decimal_name" "$decimal_value" "$decimal_minimum" "$decimal_maximum"
done

session_idle=$(read_env WORKBENCH_SESSION_IDLE_TIMEOUT_SECONDS)
session_absolute=$(read_env WORKBENCH_SESSION_ABSOLUTE_TIMEOUT_SECONDS)
[ "$session_absolute" -ge "$session_idle" ] \
    || fail "WORKBENCH_SESSION_ABSOLUTE_TIMEOUT_SECONDS darf nicht kleiner als das Idle-Limit sein."
login_limit=$(read_env WORKBENCH_LOGIN_RATE_LIMIT_ATTEMPTS)
login_global_limit=$(read_env WORKBENCH_LOGIN_RATE_LIMIT_GLOBAL_ATTEMPTS)
[ "$login_global_limit" -ge "$login_limit" ] \
    || fail "Das globale Login-Limit darf nicht kleiner als das Einzel-Limit sein."
mutation_limit=$(read_env WORKBENCH_MUTATION_RATE_LIMIT_REQUESTS)
mutation_global_limit=$(read_env WORKBENCH_MUTATION_RATE_LIMIT_GLOBAL_REQUESTS)
[ "$mutation_global_limit" -ge "$mutation_limit" ] \
    || fail "Das globale Mutationslimit darf nicht kleiner als das Sitzungslimit sein."
upload_limit=$(read_env WORKBENCH_MAX_UPLOAD_BYTES)
upload_session_limit=$(read_env WORKBENCH_MAX_UPLOAD_SESSION_BYTES)
[ "$upload_session_limit" -ge "$upload_limit" ] \
    || fail "Das Upload-Sitzungslimit darf nicht kleiner als das Dateilimit sein."
upload_turn_count=$(read_env WORKBENCH_MAX_UPLOADS_PER_TURN)
upload_session_count=$(read_env WORKBENCH_MAX_UPLOADS_PER_SESSION)
[ "$upload_session_count" -ge "$upload_turn_count" ] \
    || fail "Das Upload-Sitzungsanzahllimit darf nicht kleiner als das Turn-Limit sein."
backoff_initial=$(read_env WORKBENCH_APP_SERVER_BACKOFF_INITIAL_SECONDS)
backoff_maximum=$(read_env WORKBENCH_APP_SERVER_BACKOFF_MAX_SECONDS)
awk -v initial="$backoff_initial" -v maximum="$backoff_maximum" '
    BEGIN { if (initial + 0 > maximum + 0) { exit 1 } }
' || fail "Der initiale App-Server-Backoff darf nicht größer als sein Maximum sein."

puid=$(read_env PUID)
pgid=$(read_env PGID)
validate_integer PUID "$puid" 1 60000
validate_integer PGID "$pgid" 1 60000
[ "$puid" = 1026 ] || fail "PUID muss auf dieser Synology exakt 1026 sein."
[ "$pgid" = 100 ] || fail "PGID muss auf dieser Synology exakt 100 sein."
status "PUID/PGID validiert: $puid:$pgid."
if [ "$created_env" = true ]; then
    chown "$puid:$pgid" .env
    chmod 600 .env
    status "Neu angelegte .env gehört $puid:$pgid und bleibt Modus 0600."
fi

workbench_data=$(read_env WORKBENCH_DATA_PATH)
workbench_uploads=$(read_env WORKBENCH_UPLOADS_PATH)
workbench_password=$(read_env WORKBENCH_PASSWORD_PATH)
workbench_session=$(read_env WORKBENCH_SESSION_SECRET_PATH)
codex_home=$(read_env CODEX_HOME_PATH)
workspace=$(read_env WORKSPACE_PATH)

validate_managed_path WORKBENCH_DATA_PATH "$workbench_data"
validate_managed_path WORKBENCH_UPLOADS_PATH "$workbench_uploads"
validate_managed_path WORKBENCH_PASSWORD_PATH "$workbench_password"
validate_managed_path WORKBENCH_SESSION_SECRET_PATH "$workbench_session"
validate_managed_path CODEX_HOME_PATH "$codex_home"
validate_managed_path WORKSPACE_PATH "$workspace"

[ "$workbench_data" = "$persistent_root/workbench/data" ] \
    || fail "WORKBENCH_DATA_PATH muss für dieses Deployment exakt $persistent_root/workbench/data sein."
[ "$workbench_uploads" = "$persistent_root/workbench/uploads" ] \
    || fail "WORKBENCH_UPLOADS_PATH muss für dieses Deployment exakt $persistent_root/workbench/uploads sein."
[ "$workbench_password" = "$persistent_root/workbench/secrets/password" ] \
    || fail "WORKBENCH_PASSWORD_PATH muss exakt $persistent_root/workbench/secrets/password sein."
[ "$workbench_session" = "$persistent_root/workbench/secrets/session" ] \
    || fail "WORKBENCH_SESSION_SECRET_PATH muss exakt $persistent_root/workbench/secrets/session sein."
[ "$codex_home" = "$persistent_root/config/.codex" ] \
    || fail "CODEX_HOME_PATH muss für dieses Deployment exakt $persistent_root/config/.codex sein."
[ "$workspace" = "$persistent_root/workspace" ] \
    || fail "WORKSPACE_PATH muss für dieses Deployment exakt $persistent_root/workspace sein."
[ "$workbench_data" != "$workbench_uploads" ] \
    || fail "Daten und Uploads benötigen getrennte Hostpfade."
[ "$workbench_password" != "$workbench_session" ] \
    || fail "Passwort-Verifier und read-only Sitzungsschlüssel benötigen getrennte Hostpfade."

codex_version=$(read_env CODEX_VERSION)
node_version=$(read_env NODE_VERSION)
python_version=$(read_env PYTHON_VERSION)
[ "$codex_version" = 0.146.0 ] \
    || fail "CODEX_VERSION muss zur generierten Protokollversion exakt 0.146.0 sein."
[ "$node_version" = 24.18.1 ] \
    || fail "NODE_VERSION muss für das geprüfte Deployment exakt 24.18.1 sein."
[ "$python_version" = 3.12.11 ] \
    || fail "PYTHON_VERSION muss für das geprüfte Deployment exakt 3.12.11 sein."
grep -qx 'codex-cli 0.146.0' schemas/codex-app-server/VERSION \
    || fail "schemas/codex-app-server/VERSION stimmt nicht mit Codex 0.146.0 überein."
status "Origin-Modus, Limits, Pfade und gepinnte Build-Versionen sind konsistent."

check_host_tcp_port_available 8085

[ -d "$persistent_root" ] && [ ! -L "$persistent_root" ] \
    || fail "Der bestehende persistente Root fehlt oder ist ein Symlink: $persistent_root"
persistent_root_resolved=$(CDPATH= cd -- "$persistent_root" && pwd -P)

require_shared_directory "$codex_home"
require_shared_directory "$workspace"
require_shared_directory "$workspace/repositories"
status "Bestehendes Codex-Home, Workspace und Repositories-Root wurden validiert und nicht verändert."

create_managed_directory "$persistent_root/workbench" 700
create_managed_directory "$workbench_data" 700
create_managed_directory "$workbench_data/state" 700
create_managed_directory "$workbench_uploads" 700
create_managed_directory "$persistent_root/workbench/secrets" 700
create_managed_directory "$workbench_password" 700
create_managed_directory "$workbench_session" 700

unexpected_password_entry=$(find "$workbench_password" -mindepth 1 ! -name password.argon2id -print -quit)
[ -z "$unexpected_password_entry" ] \
    || fail "Passwort-Mount enthält eine unerwartete Datei: $unexpected_password_entry"
unexpected_session_entry=$(find "$workbench_session" -mindepth 1 ! -name session.key -print -quit)
[ -z "$unexpected_session_entry" ] \
    || fail "Sitzungs-Secret-Mount enthält eine unerwartete Datei: $unexpected_session_entry"

unsafe_upload_entry=$(find "$workbench_uploads" -mindepth 1 ! -type f -print -quit)
[ -z "$unsafe_upload_entry" ] \
    || fail "Uploads-Verzeichnis enthält einen unerwarteten Nicht-Datei-Eintrag: $unsafe_upload_entry"
find "$workbench_uploads" -mindepth 1 -type f -exec chown "$puid:$pgid" {} \;
find "$workbench_uploads" -mindepth 1 -type f -exec chmod 600 {} \;

session_key=$workbench_session/session.key
if [ -L "$session_key" ]; then
    fail "Sitzungsschlüssel darf kein Symlink sein: $session_key"
fi
if [ ! -e "$session_key" ]; then
    temporary_file=$workbench_session/.session.key.tmp.$$
    [ ! -e "$temporary_file" ] && [ ! -L "$temporary_file" ] \
        || fail "Temporäre Schlüsseldatei existiert bereits: $temporary_file"
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 32 > "$temporary_file"
    else
        dd if=/dev/urandom bs=32 count=1 2>/dev/null \
            | od -An -tx1 | tr -d ' \n' > "$temporary_file"
        printf '\n' >> "$temporary_file"
    fi
    chown "$puid:$pgid" "$temporary_file"
    chmod 600 "$temporary_file"
    if ln "$temporary_file" "$session_key" 2>/dev/null; then
        rm -f -- "$temporary_file"
        temporary_file=
        status "Neuer Sitzungsschlüssel wurde atomar erzeugt."
    else
        fail "Sitzungsschlüssel entstand parallel; keine Datei wurde überschrieben."
    fi
else
    status "Vorhandener Sitzungsschlüssel bleibt inhaltlich unverändert."
fi
secure_known_file "$session_key"

password_file=$workbench_password/password.argon2id
if [ -e "$password_file" ] || [ -L "$password_file" ]; then
    secure_known_file "$password_file"
    status "Vorhandener Passwort-Verifier bleibt inhaltlich unverändert."
else
    notice "Kein Passwort-Verifier vorhanden; er wird ausschließlich beim ersten Browser-Bootstrap erzeugt."
fi

for database_file in \
    "$workbench_data/state/workbench.sqlite3" \
    "$workbench_data/state/workbench.sqlite3-wal" \
    "$workbench_data/state/workbench.sqlite3-shm" \
    "$workbench_data/state/workbench.sqlite3-journal"
do
    secure_known_file "$database_file"
done

codex_owner=$(stat -c '%u:%g' "$codex_home" 2>/dev/null || printf 'unbekannt')
workspace_owner=$(stat -c '%u:%g' "$workspace" 2>/dev/null || printf 'unbekannt')
notice "Codex-Home Eigentümer: $codex_owner; Workspace Eigentümer: $workspace_owner; Containeridentität: $puid:$pgid."
[ "$codex_owner" = "$puid:$pgid" ] \
    || fail "Codex-Home muss $puid:$pgid gehören; der Installer ändert es nicht."
[ "$workspace_owner" = "$puid:$pgid" ] \
    || fail "Workspace muss $puid:$pgid gehören; der Installer ändert ihn nicht."

command -v docker >/dev/null 2>&1 \
    || fail "Docker ist nicht verfügbar; backend-net kann nicht geprüft werden."
docker network inspect backend-net >/dev/null 2>&1 \
    || fail "Das erforderliche externe Netzwerk backend-net ist nicht verfügbar."
network_driver=$(docker network inspect --format '{{.Driver}}' backend-net 2>/dev/null || printf 'unbekannt')
[ "$network_driver" = bridge ] \
    || fail "backend-net muss den Treiber bridge verwenden; gefunden: $network_driver"
status "Externes Netzwerk backend-net ist vorhanden (Treiber: $network_driver); Compose setzt bewusst keine feste IP."

command -v docker-compose >/dev/null 2>&1 \
    || fail "docker-compose ist nicht verfügbar; das gerenderte Deployment kann nicht geprüft werden."
compose_version=$(docker-compose version --short 2>/dev/null || printf 'unbekannt')
notice "Erkannte Docker-Compose-Version: $compose_version"
temporary_file=$(mktemp /tmp/codex-workbench-compose.XXXXXX) \
    || fail "Temporäre Compose-Datei konnte nicht sicher angelegt werden."
docker-compose config > "$temporary_file" \
    || fail "docker-compose config konnte nicht gerendert werden."
validate_rendered_ports "$temporary_file"
./scripts/validate-compose.sh "$temporary_file" "$image_tag" "$puid" "$pgid" >/dev/null \
    || fail "Die gerenderte Compose-Konfiguration verletzt Deployment-Invarianten."
rm -f -- "$temporary_file"
temporary_file=
status "docker-compose config ist gültig und veröffentlicht exakt 127.0.0.1:8085:8080 (Version: $compose_version)."

if [ "$created_env" = true ] || [ "$configuration_ready" != true ]; then
    fail "Persistenz ist vorbereitet, aber die markierten Platzhalter in .env müssen vor einem Start ersetzt und das Skript erneut ausgeführt werden."
fi

trap - 0 1 2 15
printf '%s\n' "[FERTIG] Synology-Persistenz und Konfiguration sind vorbereitet. Es wurde kein Container gebaut oder gestartet."
