from __future__ import annotations

import re
from pathlib import Path

from codex_workbench.app_server.protocol import EXPECTED_CODEX_VERSION
from codex_workbench.app_server.supervisor import APP_SERVER_ARGUMENTS
from codex_workbench.config import Settings

_ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (_ROOT / path).read_text(encoding="utf-8")


def _env_value(name: str) -> str:
    prefix = f"{name}="
    matches = [
        line.removeprefix(prefix)
        for line in _read(".env.example").splitlines()
        if line.startswith(prefix)
    ]
    assert len(matches) == 1
    return matches[0]


def test_codex_version_and_app_server_command_are_consistent() -> None:
    schema_version = _read("schemas/codex-app-server/VERSION").strip()
    settings = Settings(_env_file=None)

    assert EXPECTED_CODEX_VERSION == "0.146.0"
    assert settings.expected_codex_version == EXPECTED_CODEX_VERSION
    assert _env_value("CODEX_VERSION") == EXPECTED_CODEX_VERSION
    assert schema_version == f"codex-cli {EXPECTED_CODEX_VERSION}"
    assert APP_SERVER_ARGUMENTS == ("app-server", "--stdio")


def test_backend_image_preserves_the_complete_pinned_codex_layout() -> None:
    dockerfile = _read("deploy/backend.Dockerfile")
    verifier = _read("deploy/verify-codex-runtime.sh")

    assert "ARG CODEX_VERSION=0.146.0" in dockerfile
    assert "ARG NODE_VERSION=24.18.1" in dockerfile
    assert 'npm install --global --omit=dev "@openai/codex@${CODEX_VERSION}"' in dockerfile
    assert "COPY --chmod=755 deploy/verify-codex-runtime.sh" in dockerfile
    assert (
        "COPY --from=codex-cli /usr/local/lib/node_modules /usr/local/lib/node_modules"
        in dockerfile
    )
    assert "COPY --from=codex-cli /usr/local/bin/codex" not in dockerfile
    assert "libstdc++6" in dockerfile
    assert "ln -s ../lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex" in dockerfile
    assert not re.search(r"@openai/codex@latest(?:\s|\"|$)", dockerfile)
    verifier_call = '/usr/local/sbin/verify-codex-runtime "${CODEX_VERSION}" "${NODE_VERSION}"'
    assert dockerfile.count(verifier_call) == 2

    for runtime_path in (
        "bin/codex",
        "bin/codex-code-mode-host",
        "codex-resources/bwrap",
        "codex-resources/zsh/bin/zsh",
        "codex-path/rg",
        "codex-package.json",
    ):
        assert runtime_path in verifier
    assert '"$codex_launcher" app-server --help' in verifier
    assert "grep -Fq -- '--stdio'" in verifier


def test_synology_compose_boundaries_remain_unchanged() -> None:
    compose = _read("compose.yaml")

    assert compose.count("\n    ports:\n") == 1
    assert '      - "127.0.0.1:8085:8080"' in compose
    assert compose.count('    user: "${PUID:-1000}:${PGID:-1000}"') == 2
    assert "  backend-net:\n    external: true" in compose
    assert "  workbench-private:\n    driver: bridge" in compose
