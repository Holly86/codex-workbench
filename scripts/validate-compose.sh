#!/bin/sh
set -eu

[ "$#" -ge 1 ] && [ "$#" -le 4 ] || {
    printf '%s\n' "usage: validate-compose.sh RENDERED_COMPOSE [IMAGE_TAG PUID PGID]" >&2
    exit 2
}

rendered_compose=$1
expected_tag=${2-}
expected_puid=${3-}
expected_pgid=${4-}

[ -f "$rendered_compose" ] && [ ! -L "$rendered_compose" ] || {
    printf '%s\n' "Rendered Compose input must be a regular non-symlink file." >&2
    exit 2
}

fail() {
    printf '%s\n' "Compose invariant failed: $*" >&2
    exit 1
}

if grep -Eq '^[[:space:]]*(cpus|cpu_count|cpu_percent|cpu_period|cpu_quota|cpuset|cpuset_cpus|cpuset_mems):' "$rendered_compose"; then
    fail "CPU CFS or CPU-set controls are unsupported on the target Synology kernel."
fi
if grep -Eq '^[[:space:]]*(container_name|ipv4_address|ipv6_address|network_mode):' "$rendered_compose"; then
    fail "fixed container identity, fixed IP, or host network mode is forbidden."
fi
if grep -Eq '(^|[^A-Za-z0-9_-])docker-net([^A-Za-z0-9_-]|$)' "$rendered_compose"; then
    fail "the obsolete docker-net assumption is forbidden."
fi
if grep -Eq '^[[:space:]]*driver:[[:space:]]*macvlan[[:space:]]*$' "$rendered_compose"; then
    fail "macvlan is forbidden."
fi
if grep -Eq 'docker\.sock|^[[:space:]]*privileged:[[:space:]]*true([[:space:]]|$)' "$rendered_compose"; then
    fail "privileged mode and Docker socket mounts are forbidden."
fi

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
' "$rendered_compose" || fail "exactly frontend 127.0.0.1:8085:8080 must be published."

awk -v expected_tag="$expected_tag" -v expected_puid="$expected_puid" -v expected_pgid="$expected_pgid" '
    function indentation(value, copy) {
        copy = value
        sub(/^ */, "", copy)
        return length(value) - length(copy)
    }
    function scalar(value, first, last) {
        sub(/^[^:]*:[[:space:]]*/, "", value)
        first = substr(value, 1, 1)
        last = substr(value, length(value), 1)
        if ((first == "\"" && last == "\"") || (first == single_quote && last == single_quote)) {
            value = substr(value, 2, length(value) - 2)
        }
        return value
    }
    BEGIN { single_quote = sprintf("%c", 39) }
    /^[[:space:]]*$/ { next }
    {
        raw = $0
        level = indentation(raw)
        stripped = raw
        sub(/^ */, "", stripped)

        if (level == 0) {
            section = stripped
            service = ""
            service_network_block = 0
            top_network = ""
            next
        }

        if (section == "services:" && level == 2 && stripped ~ /^[A-Za-z0-9_.-]+:[[:space:]]*$/) {
            service = stripped
            sub(/:[[:space:]]*$/, "", service)
            services[service] = 1
            service_count += 1
            service_network_block = 0
            next
        }
        if (section == "services:" && service != "") {
            if (level == 4 && stripped == "networks:") {
                service_network_block = 1
                next
            }
            if (level == 4 && stripped != "networks:") {
                service_network_block = 0
            }
            if (service_network_block && level == 6) {
                network = stripped
                sub(/^-[[:space:]]*/, "", network)
                sub(/:.*/, "", network)
                service_networks[service SUBSEP network] = 1
                service_network_count[service] += 1
            }
            if (level == 4 && stripped ~ /^image:/) {
                images[service] = scalar(stripped)
            } else if (level == 4 && stripped ~ /^user:/) {
                users[service] = scalar(stripped)
            } else if (level == 4 && stripped ~ /^platform:/) {
                platforms[service] = scalar(stripped)
            }
        }

        if (section == "networks:" && level == 2 && stripped ~ /^[A-Za-z0-9_.-]+:[[:space:]]*$/) {
            top_network = stripped
            sub(/:[[:space:]]*$/, "", top_network)
            top_networks[top_network] = 1
            top_network_count += 1
            next
        }
        if (section == "networks:" && top_network != "" && level == 4) {
            if (stripped ~ /^external:/) {
                external[top_network] = scalar(stripped)
            } else if (stripped ~ /^driver:/) {
                driver[top_network] = scalar(stripped)
            } else if (stripped ~ /^internal:/) {
                internal[top_network] = scalar(stripped)
            }
        }
    }
    END {
        if (service_count != 2 || !services["backend"] || !services["frontend"]) exit 1
        if (top_network_count != 2 || !top_networks["backend-net"] || !top_networks["workbench-private"]) exit 1
        if (external["backend-net"] != "true" || external["workbench-private"] == "true") exit 1
        if (driver["workbench-private"] != "bridge" || internal["workbench-private"] == "true") exit 1
        if (service_network_count["backend"] != 1 || !service_networks["backend" SUBSEP "workbench-private"]) exit 1
        if (service_network_count["frontend"] != 2 || !service_networks["frontend" SUBSEP "workbench-private"] || !service_networks["frontend" SUBSEP "backend-net"]) exit 1
        if (platforms["backend"] != "linux/amd64" || platforms["frontend"] != "linux/amd64") exit 1
        if (expected_tag != "" && (images["backend"] != "codex-workbench-backend:" expected_tag || images["frontend"] != "codex-workbench-frontend:" expected_tag)) exit 1
        if (expected_puid != "" && expected_pgid != "" && (users["backend"] != expected_puid ":" expected_pgid || users["frontend"] != expected_puid ":" expected_pgid)) exit 1
    }
' "$rendered_compose" || fail "service, image, identity, platform, or network topology differs from policy."

printf '%s\n' "Compose invariants passed."
