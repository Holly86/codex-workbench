from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from codex_workbench.app_server.supervisor import ProcessState
from codex_workbench.persistence.metadata import MetadataRepository
from codex_workbench.runtime import RuntimeConflictError, WorkbenchRuntime
from codex_workbench.service_status import ServiceState
from tests.helpers import build_settings


class _HealthyMetadata:
    def database_quick_check(self) -> bool:
        return True


def _runtime(tmp_path: Path) -> WorkbenchRuntime:
    settings = build_settings(tmp_path).model_copy(
        update={"app_server_start_enabled": True}
    )
    settings.uploads_dir.mkdir(parents=True)
    runtime = WorkbenchRuntime(settings, cast(MetadataRepository, _HealthyMetadata()))
    runtime.supervisor._state = ProcessState.READY  # noqa: SLF001
    runtime.supervisor._generation = 3  # noqa: SLF001
    runtime.supervisor._version = "0.146.0"  # noqa: SLF001
    runtime.supervisor._last_ready_at = 1234  # noqa: SLF001
    runtime.supervisor._last_recovery_at = 1200  # noqa: SLF001
    return runtime


@pytest.mark.asyncio
async def test_system_status_is_typed_safe_and_keeps_optional_plugins_nonfatal(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    status = await runtime.system_status()
    payload = status.model_dump(mode="json")
    assert status.status is ServiceState.DEGRADED
    assert payload["workbenchVersion"] == "0.1.0"
    assert payload["codexVersion"] == "0.146.0"
    components = {item["component"]: item for item in payload["components"]}
    assert components["codex_app_server"]["generation"] == 3
    assert components["codex_app_server"]["lastSuccessAt"] == 1234
    assert components["codex_app_server"]["lastRecoveryAt"] == 1200
    assert components["plugin_discovery"]["optional"] is True
    assert components["plugin_discovery"]["state"] == "blocked"
    serialized = status.model_dump_json()
    for forbidden in ("session", "owner", "cwd", "prompt", "token", "cookie"):
        assert forbidden not in serialized.lower()


@pytest.mark.asyncio
async def test_manual_restart_rejects_active_turn_before_touching_supervisor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    monkeypatch.setattr(runtime.projection, "has_active_turns", lambda: True)
    called = False

    async def restart() -> str:
        nonlocal called
        called = True
        return "started"

    monkeypatch.setattr(runtime.supervisor, "request_restart", restart)
    with pytest.raises(RuntimeConflictError):
        await runtime.restart_app_server("opaque-session", "CODEX RUNTIME NEU STARTEN")
    assert called is False
