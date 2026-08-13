#!/usr/bin/env python3
"""Static policy gate for public documentation and distribution artifacts."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_BACKEND_IMAGE = (
    "${DOCKERHUB_NAMESPACE:?Set DOCKERHUB_NAMESPACE in .env}/"
    "codex-workbench-backend:${WORKBENCH_VERSION:-0.2.1}"
)
EXPECTED_FRONTEND_IMAGE = (
    "${DOCKERHUB_NAMESPACE:?Set DOCKERHUB_NAMESPACE in .env}/"
    "codex-workbench-frontend:${WORKBENCH_VERSION:-0.2.1}"
)


def fail(message: str) -> None:
    raise ValueError(message)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        fail(f"{label} must be a string-keyed mapping")
    return value


def require_hardening(service: dict[str, Any], label: str) -> None:
    if service.get("read_only") is not True:
        fail(f"{label} must have a read-only root filesystem")
    if service.get("cap_drop") != ["ALL"]:
        fail(f"{label} must drop all capabilities")
    if "no-new-privileges:true" not in service.get("security_opt", []):
        fail(f"{label} must set no-new-privileges")
    if service.get("privileged") is True:
        fail(f"{label} must not be privileged")
    if not service.get("tmpfs"):
        fail(f"{label} must define bounded tmpfs paths")
    if not mapping(service.get("healthcheck"), f"{label}.healthcheck").get("test"):
        fail(f"{label} must define a healthcheck")


def check_release_compose() -> None:
    compose = mapping(yaml.safe_load(read("compose.release.yaml")), "release compose")
    services = mapping(compose.get("services"), "release services")
    if set(services) != {"backend", "frontend"}:
        fail("release compose must define exactly backend and frontend")
    backend = mapping(services["backend"], "release backend")
    frontend = mapping(services["frontend"], "release frontend")
    for name, service in (("backend", backend), ("frontend", frontend)):
        if "build" in service:
            fail(f"release {name} must not contain build")
        if service.get("platform") != "linux/amd64":
            fail(f"release {name} must target linux/amd64")
        if service.get("user") != "${PUID:-1000}:${PGID:-1000}":
            fail(f"release {name} must use the configured non-root identity")
        require_hardening(service, name)
    if backend.get("image") != EXPECTED_BACKEND_IMAGE:
        fail("release backend image reference is not versioned through the public variables")
    if frontend.get("image") != EXPECTED_FRONTEND_IMAGE:
        fail("release frontend image reference is not versioned through the public variables")
    if "ports" in backend:
        fail("release backend must remain internal")
    if frontend.get("ports") != ["127.0.0.1:8085:8080"]:
        fail("release frontend must publish only loopback port 8085")
    if backend.get("networks") != ["workbench-private"]:
        fail("release backend network topology changed")
    if set(mapping(frontend.get("networks"), "release frontend networks")) != {
        "workbench-private",
        "backend-net",
    }:
        fail("release frontend network topology changed")
    networks = mapping(compose.get("networks"), "release networks")
    if mapping(networks.get("backend-net"), "backend-net").get("external") is not True:
        fail("backend-net must remain external")
    if "docker.sock" in read("compose.release.yaml"):
        fail("release compose must not mount the Docker socket")
    source = mapping(yaml.safe_load(read("compose.yaml")), "source compose")
    source_backend = mapping(
        mapping(source.get("services"), "source services").get("backend"),
        "source backend",
    )
    source_environment = mapping(source_backend.get("environment"), "source environment")
    release_environment = mapping(backend.get("environment"), "release environment")
    if set(source_environment) != set(release_environment):
        fail("source and release backend environment keys must remain in parity")


def check_build_context() -> None:
    dockerignore = read(".dockerignore")
    if not re.search(r"(?m)^\*\*$", dockerignore):
        fail(".dockerignore must default-deny the build context")
    allowed = {
        "!backend/pyproject.toml",
        "!backend/README.md",
        "!backend/src/**",
        "!frontend/package.json",
        "!frontend/package-lock.json",
        "!frontend/index.html",
        "!frontend/src/**",
        "!deploy/backend.Dockerfile",
        "!deploy/frontend.Dockerfile",
        "!deploy/nginx.conf",
        "!deploy/verify-codex-runtime.sh",
    }
    lines = {line.strip() for line in dockerignore.splitlines()}
    missing = allowed - lines
    if missing:
        fail(".dockerignore is missing required allowlist entries")

    for name in ("deploy/backend.Dockerfile", "deploy/frontend.Dockerfile"):
        dockerfile = read(name)
        if re.search(r"(?im)^\s*ADD\s", dockerfile):
            fail(f"{name} must not use ADD")
        if re.search(r"(?im)^\s*(?:ARG|ENV)\s+[^\n]*(?:SECRET|TOKEN|PASSWORD|CREDENTIAL)", dockerfile):
            fail(f"{name} must not accept credential ARG/ENV values")
        if re.search(r"(?im)^\s*COPY\s+(?:--[^\s]+\s+)*\.\s", dockerfile):
            fail(f"{name} must not copy the repository root")
    backend = read("deploy/backend.Dockerfile")
    frontend = read("deploy/frontend.Dockerfile")
    for fragment in (
        "ARG CODEX_VERSION=0.146.0",
        "ARG NODE_VERSION=24.18.1",
        "ARG PYTHON_VERSION=3.12.11",
        'npm install --global --omit=dev "@openai/codex@${CODEX_VERSION}"',
        "USER workbench",
        "git",
    ):
        if fragment not in backend:
            fail(f"backend Dockerfile invariant is missing: {fragment}")
    for fragment in (
        "ARG NODE_VERSION=24.18.1",
        "RUN npm ci",
        "nginxinc/nginx-unprivileged:1.28.0-alpine",
        "USER nginx",
    ):
        if fragment not in frontend:
            fail(f"frontend Dockerfile invariant is missing: {fragment}")


def check_workflows() -> None:
    ci = read(".github/workflows/ci.yml")
    release = read(".github/workflows/docker-release.yml")
    if "pull_request_target" in ci or "pull_request_target" in release:
        fail("pull_request_target is forbidden")
    for fragment in (
        "pull_request:",
        "push:",
        "- main",
        "fetch-depth: 0",
        "persist-credentials: false",
        "run: make verify",
    ):
        if fragment not in ci:
            fail(f"CI workflow is missing: {fragment}")
    action_refs = re.findall(r"uses:\s+[^@\s]+@([^\s#]+)", ci + release)
    if not action_refs or any(not re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs):
        fail("all GitHub Actions must be pinned to full commit SHAs")

    for fragment in (
        '- "v*.*.*"',
        "github.event_name == 'push'",
        "deploy/backend.Dockerfile",
        "deploy/frontend.Dockerfile",
        "vars.DOCKERHUB_USERNAME",
        "secrets.DOCKERHUB_TOKEN",
        "type=semver,pattern={{version}}",
        "type=semver,pattern={{major}}.{{minor}}",
        "type=raw,value=latest,enable=${{ !contains(github.ref_name, '-') }}",
        "platforms: linux/amd64",
        "push: true",
        "sbom: true",
        "provenance: mode=max",
    ):
        if fragment not in release:
            fail(f"Docker release workflow is missing: {fragment}")
    if "pull_request:" in release or "build-args:" in release:
        fail("Docker release workflow has an unsafe trigger or build args")
    if release.count("secrets.DOCKERHUB_TOKEN") != 1:
        fail("Docker Hub token must be used only by the login action")
    if release.count("flavor: latest=false") != 2:
        fail("both image metadata steps must disable implicit latest tags")
    for label in (
        "org.opencontainers.image.source",
        "org.opencontainers.image.revision",
        "org.opencontainers.image.version",
        "org.opencontainers.image.description",
        "org.opencontainers.image.licenses",
    ):
        if release.count(label) != 2:
            fail(f"both images must define OCI label {label}")


def check_versions_and_docs() -> None:
    version_files = {
        "backend/pyproject.toml": 'version = "0.2.1"',
        "backend/src/codex_workbench/__init__.py": '__version__ = "0.2.1"',
        "frontend/package.json": '"version": "0.2.1"',
        ".env.example": "WORKBENCH_VERSION=0.2.1",
        "README.md": "`v0.2.1`",
    }
    for path, fragment in version_files.items():
        if fragment not in read(path):
            fail(f"0.2.1 version is inconsistent in {path}")
    if read("schemas/codex-app-server/VERSION").strip() != "codex-cli 0.146.0":
        fail("Codex schema pin must remain 0.146.0")
    for path in ("README.md", "SECURITY.md", "CONTRIBUTING.md", "THIRD_PARTY_NOTICES.md"):
        if not (ROOT / path).is_file():
            fail(f"public document is missing: {path}")

    public_text = "\n".join(
        read(path)
        for path in [
            "README.md",
            "SECURITY.md",
            "CONTRIBUTING.md",
            ".env.example",
            *[str(path.relative_to(ROOT)) for path in sorted((ROOT / "docs").glob("*.md"))],
        ]
    )
    forbidden_privacy = {
        "private-email": r"[A-Za-z0-9._%+-]+@(?:gmail|googlemail|outlook|yahoo)\.[A-Za-z]{2,}",
        "personal-windows-path": r"[A-Za-z]:\\Users\\[^\\\s]+",
        "private-ip": r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b",
        "private-service-name": r"\b(?:alivo|Pool Community|Signage|Homebridge)\b",
    }
    for label, pattern in forbidden_privacy.items():
        if re.search(pattern, public_text, re.IGNORECASE):
            fail(f"public documentation contains {label}")

    env = read(".env.example")
    for forbidden in ("DOCKERHUB_TOKEN=", "GITHUB_TOKEN=", "OPENAI_API_KEY=", "SESSION_SECRET="):
        if forbidden in env:
            fail(".env.example must not contain credential variables or values")
    if "DOCKERHUB_NAMESPACE=<your-dockerhub-namespace>" not in env:
        fail(".env.example must use a generic Docker Hub namespace placeholder")

    licenses = [path for name in ("LICENSE", "LICENSE.md", "COPYING") if (path := ROOT / name).exists()]
    release_workflow = read(".github/workflows/docker-release.yml")
    if [path.name for path in licenses] != ["LICENSE"]:
        fail("the canonical Apache-2.0 LICENSE file is required")
    license_text = read("LICENSE")
    if "Apache License\n                           Version 2.0, January 2004" not in license_text:
        fail("LICENSE must contain the official Apache License 2.0 text")
    if "No project license has been selected" in read("README.md"):
        fail("README still reports an unselected project license")
    if 'license = "Apache-2.0"' not in read("backend/pyproject.toml"):
        fail("backend metadata must declare Apache-2.0")
    if '"license": "Apache-2.0"' not in read("frontend/package.json"):
        fail("frontend metadata must declare Apache-2.0")
    if release_workflow.count("org.opencontainers.image.licenses=Apache-2.0") != 2:
        fail("both OCI images must declare Apache-2.0")
    if "NOASSERTION" in release_workflow:
        fail("OCI license metadata must not use NOASSERTION")
    gitignore = read(".gitignore")
    if "**/security/deploy-keys/" not in gitignore:
        fail("repository-local deploy key storage must be ignored")


def main() -> int:
    check_release_compose()
    check_build_context()
    check_workflows()
    check_versions_and_docs()
    print("Public release artifact policy checks passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"Public release artifact policy failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
