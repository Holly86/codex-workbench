from __future__ import annotations

import re
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast

_ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (_ROOT / path).read_text(encoding="utf-8")


def test_release_compose_uses_distribution_images_without_builds() -> None:
    compose = _read("compose.release.yaml")

    assert "\n    build:" not in compose
    assert (
        "${DOCKERHUB_NAMESPACE:?Set DOCKERHUB_NAMESPACE in .env}/"
        "codex-workbench-backend:${WORKBENCH_VERSION:-0.1.0}"
    ) in compose
    assert (
        "${DOCKERHUB_NAMESPACE:?Set DOCKERHUB_NAMESPACE in .env}/"
        "codex-workbench-frontend:${WORKBENCH_VERSION:-0.1.0}"
    ) in compose
    assert compose.count("\n    ports:\n") == 1
    assert '      - "127.0.0.1:8085:8080"' in compose
    assert compose.count("      - no-new-privileges:true") == 2
    assert compose.count("    cap_drop:\n      - ALL") == 2
    assert compose.count("    read_only: true") == 2
    assert "docker.sock" not in compose


def test_docker_release_workflow_has_safe_triggers_tags_and_credentials() -> None:
    workflow = _read(".github/workflows/docker-release.yml")

    assert '- "v*.*.*"' in workflow
    assert "pull_request:" not in workflow
    assert "github.event_name == 'push'" in workflow
    assert "file: deploy/backend.Dockerfile" in workflow
    assert "file: deploy/frontend.Dockerfile" in workflow
    assert workflow.count("push: true") == 2
    assert "type=semver,pattern={{version}}" in workflow
    assert "type=semver,pattern={{major}}.{{minor}}" in workflow
    assert "type=raw,value=latest,enable=${{ !contains(github.ref_name, '-') }}" in workflow
    assert workflow.count("secrets.DOCKERHUB_TOKEN") == 1
    assert "vars.DOCKERHUB_USERNAME" in workflow
    assert "build-args:" not in workflow
    assert workflow.count("sbom: true") == 2
    assert workflow.count("provenance: mode=max") == 2
    assert workflow.count("org.opencontainers.image.licenses=Apache-2.0") == 2


def test_public_build_context_and_example_configuration_exclude_secrets() -> None:
    dockerignore = _read(".dockerignore")
    env_example = _read(".env.example")
    dockerfiles = _read("deploy/backend.Dockerfile") + _read("deploy/frontend.Dockerfile")

    assert re.search(r"(?m)^\*\*$", dockerignore)
    assert "**/security/deploy-keys/" in _read(".gitignore")
    assert "!backend/src/**" in dockerignore
    assert "!frontend/src/**" in dockerignore
    assert "COPY . " not in dockerfiles
    assert not re.search(r"(?im)^\s*ADD\s", dockerfiles)
    assert not re.search(
        r"(?im)^\s*(?:ARG|ENV)\s+[^\n]*(?:SECRET|TOKEN|PASSWORD|CREDENTIAL)",
        dockerfiles,
    )
    assert "DOCKERHUB_NAMESPACE=<your-dockerhub-namespace>" in env_example
    for forbidden in (
        "DOCKERHUB_TOKEN=",
        "GITHUB_TOKEN=",
        "OPENAI_API_KEY=",
        "SESSION_SECRET=",
    ):
        assert forbidden not in env_example


def test_project_license_metadata_is_consistently_apache_2() -> None:
    license_text = _read("LICENSE")
    assert "Apache License\n                           Version 2.0, January 2004" in license_text
    assert 'license = "Apache-2.0"' in _read("backend/pyproject.toml")
    assert '"license": "Apache-2.0"' in _read("frontend/package.json")
    assert "original Codex Workbench source is licensed under the Apache License 2.0" in _read(
        "THIRD_PARTY_NOTICES.md"
    )


def test_secret_scanner_detects_openssh_private_key_material() -> None:
    module = ModuleType("secret_scanner_policy_test")
    module.__file__ = str(_ROOT / "scripts/check-secrets.py")
    source = _read("scripts/check-secrets.py")
    sys.modules[module.__name__] = module
    try:
        exec(compile(source, "scripts/check-secrets.py", "exec"), module.__dict__)
    finally:
        sys.modules.pop(module.__name__, None)
    scan_text = cast(Callable[[str, bytes], str | None], module.__dict__["scan_text"])
    assert scan_text(
        "accidentally-committed-key",
        b"-----BEGIN " + b"OPENSSH PRIVATE KEY-----\nnot-real-key-material\n",
    ) == "private-key"


def test_public_docs_do_not_contain_known_private_infrastructure_markers() -> None:
    public_docs = "\n".join(
        _read(path)
        for path in (
            "README.md",
            "SECURITY.md",
            "CONTRIBUTING.md",
            "docs/deployment.md",
            "docs/synology-test-plan.md",
        )
    )

    assert not re.search(
        r"[A-Za-z0-9._%+-]+@(?:gmail|googlemail|outlook|yahoo)\.[A-Za-z]{2,}",
        public_docs,
        re.IGNORECASE,
    )
    assert not re.search(r"\b(?:alivo|Pool Community|Signage|Homebridge)\b", public_docs, re.I)
