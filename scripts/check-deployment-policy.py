#!/usr/bin/env python3
"""Static, secrets-free validation of the deployment source model."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_CPU_KEYS = {
    "cpus",
    "cpu_count",
    "cpu_percent",
    "cpu_period",
    "cpu_quota",
    "cpuset",
    "cpuset_cpus",
    "cpuset_mems",
}
FORBIDDEN_NETWORK_KEYS = {"container_name", "ipv4_address", "ipv6_address", "network_mode"}


def fail(message: str) -> None:
    raise ValueError(message)


def mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        fail(f"{label} must be a mapping")
    return value


def walk_forbidden(value: object, path: str = "compose") -> None:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key)
            if key in FORBIDDEN_CPU_KEYS:
                fail(f"unsupported CPU control found at {path}.{key}")
            if key in FORBIDDEN_NETWORK_KEYS:
                fail(f"forbidden network/container control found at {path}.{key}")
            walk_forbidden(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            walk_forbidden(child, f"{path}[{index}]")


def require_hardening(service: dict[str, Any], label: str) -> None:
    if service.get("read_only") is not True:
        fail(f"{label} must have a read-only root")
    if service.get("privileged") is True:
        fail(f"{label} must not be privileged")
    if service.get("cap_drop") != ["ALL"]:
        fail(f"{label} must drop all capabilities")
    if "no-new-privileges:true" not in service.get("security_opt", []):
        fail(f"{label} must set no-new-privileges")
    expected_memory = "2g" if label == "backend" else "128m"
    if service.get("mem_limit") != expected_memory:
        fail(f"{label} must define the conservative fixed memory limit")
    if not isinstance(service.get("pids_limit"), int):
        fail(f"{label} must define a PID limit")
    nofile = mapping(
        mapping(service.get("ulimits"), f"{label}.ulimits").get("nofile"),
        f"{label}.nofile",
    )
    if not isinstance(nofile.get("soft"), int) or not isinstance(nofile.get("hard"), int):
        fail(f"{label} must define nofile soft/hard limits")
    if not service.get("tmpfs"):
        fail(f"{label} must define bounded tmpfs runtime paths")
    if not mapping(service.get("healthcheck"), f"{label}.healthcheck").get("test"):
        fail(f"{label} must define a healthcheck")


def load_env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            fail(".env.example contains a non-assignment line")
        key, value = line.split("=", 1)
        if key in values:
            fail(f".env.example contains duplicate key {key}")
        values[key] = value
    return values


def main() -> int:
    raw = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    compose = mapping(raw, "compose")
    walk_forbidden(compose)
    services = mapping(compose.get("services"), "services")
    if set(services) != {"backend", "frontend"}:
        fail("Compose must define exactly backend and frontend")
    backend = mapping(services["backend"], "backend")
    frontend = mapping(services["frontend"], "frontend")

    for name, service in (("backend", backend), ("frontend", frontend)):
        require_hardening(service, name)
        if service.get("platform") != "linux/amd64":
            fail(f"{name} must target linux/amd64")
        if service.get("user") != "${PUID:-1000}:${PGID:-1000}":
            fail(f"{name} must use the configured numeric identity")

    if "ports" in backend:
        fail("backend must not publish a host port")
    if frontend.get("ports") != ["127.0.0.1:8085:8080"]:
        fail("frontend must publish exactly 127.0.0.1:8085:8080")
    if backend.get("networks") != ["workbench-private"]:
        fail("backend must use only workbench-private")
    if set(mapping(frontend.get("networks"), "frontend.networks")) != {
        "workbench-private",
        "backend-net",
    }:
        fail("frontend network topology is invalid")

    networks = mapping(compose.get("networks"), "networks")
    if set(networks) != {"workbench-private", "backend-net"}:
        fail("Compose must define exactly workbench-private and backend-net")
    private = mapping(networks["workbench-private"], "workbench-private")
    external = mapping(networks["backend-net"], "backend-net")
    if private.get("driver") != "bridge" or private.get("internal") is True:
        fail("workbench-private must be a project bridge with backend egress")
    if external.get("external") is not True:
        fail("backend-net must be external")

    if backend.get("init") is not True:
        fail("the Python backend requires Docker init for child reaping")
    if frontend.get("init") is True:
        fail("the nginx frontend does not require an additional Docker init")
    health_text = " ".join(str(part) for part in mapping(backend["healthcheck"], "health")["test"])
    if "/api/health/ready" not in health_text or "/api/health/live" in health_text:
        fail("backend healthcheck must retain readiness semantics")
    depends = mapping(
        mapping(frontend.get("depends_on"), "depends_on").get("backend"),
        "depends_on.backend",
    )
    if depends.get("condition") != "service_healthy":
        fail("frontend must wait for backend readiness")

    environment = mapping(backend.get("environment"), "backend.environment")
    required_environment = {
        "HOME": "/home/workbench",
        "CODEX_HOME": "/home/workbench/.codex",
        "TMPDIR": "/tmp",
        "WORKBENCH_CODEX_EXECUTABLE": "/usr/local/bin/codex",
        "WORKBENCH_EXPECTED_CODEX_VERSION": "${CODEX_VERSION:-0.146.0}",
        "WORKBENCH_APP_SERVER_START_ENABLED": "true",
        "WORKBENCH_GIT_COMMAND_TIMEOUT_SECONDS": "${WORKBENCH_GIT_COMMAND_TIMEOUT_SECONDS:-30}",
        "WORKBENCH_GIT_NETWORK_TIMEOUT_SECONDS": "${WORKBENCH_GIT_NETWORK_TIMEOUT_SECONDS:-300}",
        "WORKBENCH_GIT_CLONE_TIMEOUT_SECONDS": "${WORKBENCH_GIT_CLONE_TIMEOUT_SECONDS:-900}",
    }
    for key, expected in required_environment.items():
        if environment.get(key) != expected:
            fail(f"backend environment invariant differs for {key}")

    env = load_env_example()
    if env.get("COMPOSE_PROJECT_NAME") != "codex-workbench":
        fail(".env.example must retain the isolated Compose project name")
    if env.get("PUID") != "1000" or env.get("PGID") != "1000":
        fail(".env.example must use generic non-root UID/GID defaults")
    if env.get("CODEX_VERSION") != "0.146.0":
        fail(".env.example must pin Codex 0.146.0")
    expected_git_timeouts = {
        "WORKBENCH_GIT_COMMAND_TIMEOUT_SECONDS": "30",
        "WORKBENCH_GIT_NETWORK_TIMEOUT_SECONDS": "300",
        "WORKBENCH_GIT_CLONE_TIMEOUT_SECONDS": "900",
    }
    for key, expected in expected_git_timeouts.items():
        if env.get(key) != expected:
            fail(f".env.example must retain the safe default for {key}")
    if any(key in env for key in FORBIDDEN_CPU_KEYS) or any("CPU_QUOTA" in key for key in env):
        fail(".env.example must not expose unsupported CPU quotas")

    backend_dockerfile = (ROOT / "deploy/backend.Dockerfile").read_text(encoding="utf-8")
    frontend_dockerfile = (ROOT / "deploy/frontend.Dockerfile").read_text(encoding="utf-8")
    if "s6-overlay" in backend_dockerfile or "s6-overlay" in frontend_dockerfile:
        fail("Docker init must not be combined with s6-overlay")
    if "/api/health/ready" not in backend_dockerfile:
        fail("backend image healthcheck must use readiness")
    if re.search(r"--workers(?:=|\s)", backend_dockerfile):
        fail("backend image must retain one Uvicorn worker")

    required_backend_fragments = {
        'ARG CODEX_VERSION=0.146.0': "backend image must pin Codex 0.146.0",
        'ARG NODE_VERSION=24.18.1': "backend image must pin Node 24.18.1",
        'npm install --global --omit=dev "@openai/codex@${CODEX_VERSION}"': (
            "backend image must install the exact configured Codex package"
        ),
        "COPY --chmod=755 deploy/verify-codex-runtime.sh": (
            "backend image must make the Codex runtime verifier executable"
        ),
        "libstdc++6": "backend image must install the copied Node runtime dependency",
        (
            "COPY --from=codex-cli /usr/local/lib/node_modules "
            "/usr/local/lib/node_modules"
        ): "backend image must copy the complete global npm module layout",
        (
            "ln -s ../lib/node_modules/@openai/codex/bin/codex.js "
            "/usr/local/bin/codex"
        ): "backend image must recreate the Codex npm bin link explicitly",
    }
    for fragment, message in required_backend_fragments.items():
        if fragment not in backend_dockerfile:
            fail(message)
    if "COPY --from=codex-cli /usr/local/bin/codex" in backend_dockerfile:
        fail("backend image must not dereference-copy the Codex npm bin symlink")
    if re.search(r"@openai/codex@(?:latest|\$\{?[^} ]*:-latest)", backend_dockerfile):
        fail("backend image must never install an unpinned latest Codex package")
    verifier_call = (
        '/usr/local/sbin/verify-codex-runtime "${CODEX_VERSION}" "${NODE_VERSION}"'
    )
    if backend_dockerfile.count(verifier_call) != 2:
        fail("Codex runtime verification must run in both Node and final runtime stages")

    verifier = (ROOT / "deploy/verify-codex-runtime.sh").read_text(encoding="utf-8")
    required_verifier_fragments = {
        "Codex launcher is missing or is not a symbolic link",
        "Node has an unresolved runtime library",
        "codex --version exited with status",
        "codex --version wrote unexpected stdout",
        "codex --version wrote unexpected stderr",
        "codex --version reported the wrong version",
        "codex app-server --help exited with status",
        "required native Codex runtime executable is missing",
        "grep -Fq -- '--stdio'",
    }
    for fragment in required_verifier_fragments:
        if fragment not in verifier:
            fail(f"Codex runtime verifier is missing diagnostic: {fragment}")

    executable_assets = [ROOT / "Makefile", ROOT / "compose.yaml"]
    executable_assets.extend((ROOT / "scripts").glob("*"))
    prune_pattern = re.compile(r"docker(?:-compose)?\s+(?:system|image|network|volume)\s+prune")
    for path in executable_assets:
        if path.is_file() and prune_pattern.search(path.read_text(encoding="utf-8")):
            fail(f"global Docker prune command found in executable asset {path.name}")

    for path in [ROOT / ".env.example", *sorted((ROOT / "scripts").glob("*.sh"))]:
        if b"\r" in path.read_bytes():
            fail(f"CR byte found in LF-only file {path.name}")

    print("Deployment source policy checks passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"Deployment source policy failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
