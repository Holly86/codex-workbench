from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest

from codex_workbench.app_server.supervisor import ProcessState
from codex_workbench.git_service import GitResult
from codex_workbench.jobs import BackgroundJobRegistry
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
    assert status.status is ServiceState.HEALTHY
    assert payload["workbenchVersion"] == "0.2.2"
    assert payload["codexVersion"] == "0.146.0"
    components = {item["component"]: item for item in payload["components"]}
    assert components["codex_app_server"]["generation"] == 3
    assert components["codex_app_server"]["lastSuccessAt"] == 1234
    assert components["codex_app_server"]["lastRecoveryAt"] == 1200
    assert components["plugin_discovery"]["optional"] is True
    assert components["plugin_discovery"]["state"] == "blocked"
    assert components["realtime_websocket"]["state"] == "unknown"
    serialized = status.model_dump_json()
    for forbidden in ("session", "owner", "cwd", "prompt", "token", "cookie"):
        assert forbidden not in serialized.lower()


@pytest.mark.asyncio
async def test_failed_job_history_remains_visible_without_degrading_job_subsystem(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    completed = asyncio.Event()

    async def update(_session_id: str, payload: object) -> None:
        if isinstance(payload, dict) and payload.get("status") == "failed":
            completed.set()

    runtime.jobs = BackgroundJobRegistry(update_handler=update)

    async def failed_fetch() -> GitResult:
        return GitResult("git fetch --prune", 1, "", 5, False)

    await runtime.jobs.start(
        owner_session_id="session",
        repository_id="0123456789abcdef",
        repository_name="repo",
        job_type="git_fetch",
        operation=failed_fetch,
    )
    await completed.wait()

    status = await runtime.system_status()
    jobs = next(item for item in status.components if item.component == "background_jobs")
    assert jobs.state is ServiceState.HEALTHY
    assert jobs.failedCount == 1
    assert jobs.errorCode is None


@pytest.mark.asyncio
async def test_successful_protocol_activity_normalizes_watchdog_health_only(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    runtime.supervisor._error_code = "watchdog_probe_rejected"  # noqa: SLF001
    runtime.supervisor._consecutive_probe_failures = 1  # noqa: SLF001

    runtime.supervisor.note_activity(3)
    status = await runtime.system_status()
    app_server = next(item for item in status.components if item.component == "codex_app_server")
    assert app_server.state is ServiceState.HEALTHY
    assert app_server.errorCode is None

    runtime.supervisor._error_code = "unsupported_version"  # noqa: SLF001
    runtime.supervisor.note_activity(3)
    permanent = await runtime.system_status()
    app_server = next(item for item in permanent.components if item.component == "codex_app_server")
    assert app_server.state is ServiceState.DEGRADED
    assert app_server.errorCode == "unsupported_version"


@pytest.mark.asyncio
async def test_current_job_infrastructure_failure_degrades_then_recovers(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    reject_updates = True
    failed_update = asyncio.Event()
    successful_completion = asyncio.Event()

    async def update(_session_id: str, payload: object) -> None:
        if reject_updates:
            failed_update.set()
            raise RuntimeError("private broker detail")
        if isinstance(payload, dict) and payload.get("status") == "succeeded":
            successful_completion.set()

    runtime.jobs = BackgroundJobRegistry(update_handler=update)
    release = asyncio.Event()

    async def operation() -> GitResult:
        await release.wait()
        return GitResult("git fetch --prune", 0, "", 5, False)

    await runtime.jobs.start(
        owner_session_id="session",
        repository_id="0123456789abcdef",
        repository_name="repo",
        job_type="git_fetch",
        operation=operation,
    )
    await failed_update.wait()
    degraded = await runtime.system_status()
    jobs = next(item for item in degraded.components if item.component == "background_jobs")
    assert jobs.state is ServiceState.DEGRADED
    assert jobs.errorCode == "job_realtime_update_failed"

    reject_updates = False
    release.set()
    await successful_completion.wait()
    recovered = await runtime.system_status()
    jobs = next(item for item in recovered.components if item.component == "background_jobs")
    assert jobs.state is ServiceState.HEALTHY
    assert jobs.errorCode is None


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
