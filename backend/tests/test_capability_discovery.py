from __future__ import annotations

import asyncio
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from codex_workbench.app_server.correlation import AppServerRequestError
from codex_workbench.app_server.supervisor import ProcessState
from codex_workbench.app_server.transport import JsonObject, JsonValue
from codex_workbench.persistence.metadata import MetadataRepository
from codex_workbench.runtime import WorkbenchRuntime
from tests.helpers import build_settings


def _runtime(tmp_path: Path) -> tuple[WorkbenchRuntime, str]:
    settings = build_settings(tmp_path)
    repository = settings.repositories_root / "sample"
    repository.mkdir()
    (repository / ".git").mkdir()
    metadata = cast(MetadataRepository, object())
    runtime = WorkbenchRuntime(settings, metadata)
    runtime.supervisor._state = ProcessState.READY  # noqa: SLF001
    runtime.supervisor._generation = 1  # noqa: SLF001
    repository_id = sha256(b"sample").hexdigest()[:16]
    return runtime, repository_id


def _status(result: JsonObject) -> JsonObject:
    status = result.get("status")
    assert isinstance(status, dict)
    return status


def _response(method: str, cwd: str) -> JsonObject:
    if method == "model/list":
        return {
            "data": [
                {
                    "model": "fixture-codex",
                    "displayName": "Fixture Codex",
                    "hidden": False,
                    "isDefault": True,
                    "defaultReasoningEffort": "medium",
                    "supportedReasoningEfforts": [],
                    "inputModalities": ["text"],
                }
            ]
        }
    if method == "skills/list":
        return {
            "data": [
                {
                    "cwd": cwd,
                    "skills": [
                        {"name": "fixture-skill", "shortDescription": "Skill", "enabled": True}
                    ],
                }
            ]
        }
    if method == "plugin/list":
        return {
            "marketplaces": [
                {
                    "plugins": [
                        {
                            "id": "fixture-plugin",
                            "name": "Fixture Plugin",
                            "installed": True,
                            "enabled": True,
                        }
                    ]
                }
            ]
        }
    raise AssertionError(method)


@pytest.mark.asyncio
async def test_capabilities_are_single_flight_cached_and_generation_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, repository_id = _runtime(tmp_path)
    calls: Counter[str] = Counter()
    release = asyncio.Event()

    async def call(
        method: str, params: JsonObject | None, *, owner: str
    ) -> JsonValue:
        del owner
        calls[method] += 1
        assert params is not None
        if method == "model/list":
            await release.wait()
            assert params == {"includeHidden": False}
        else:
            cwd = params["cwds"]
            assert isinstance(cwd, list) and len(cwd) == 1 and isinstance(cwd[0], str)
            if method == "skills/list":
                assert params == {"cwds": cwd, "forceReload": False}
            else:
                assert params == {"cwds": cwd}
        cwd_value = str(runtime.settings.repositories_root / "sample")
        return _response(method, cwd_value)

    monkeypatch.setattr(runtime, "_call", call)
    requests = [
        asyncio.create_task(runtime.capabilities(repository_id, f"session-{index}"))
        for index in range(5)
    ]
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(*requests)
    assert all(_status(result)["overall"] == "ready" for result in results)
    assert calls == Counter({"model/list": 1, "skills/list": 1, "plugin/list": 1})

    cached = await runtime.capabilities(repository_id, "session-cache")
    assert _status(cached)["cached"] is True
    assert calls["model/list"] == 1

    runtime.supervisor._generation = 2  # noqa: SLF001
    refreshed = await runtime.capabilities(repository_id, "session-generation")
    assert _status(refreshed)["generation"] == 2
    assert calls == Counter({"model/list": 2, "skills/list": 2, "plugin/list": 2})

    await runtime._on_notification(2, "skills/changed", {})  # noqa: SLF001
    await runtime.capabilities(repository_id, "session-skills-changed")
    assert calls == Counter({"model/list": 3, "skills/list": 3, "plugin/list": 3})

    await runtime.capabilities(repository_id, "session-refresh", refresh=True)
    assert calls == Counter({"model/list": 4, "skills/list": 4, "plugin/list": 4})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failed_method", "error", "reason"),
    [
        ("plugin/list", AppServerRequestError(-32601, "not found"), "unsupported"),
        ("plugin/list", AppServerRequestError(-32602, "invalid"), "invalid_params"),
        ("skills/list", AppServerRequestError(-32001, "failed"), "json_rpc_error"),
        ("model/list", AppServerRequestError(-32002, "failed"), "json_rpc_error"),
    ],
)
async def test_capability_request_errors_are_partial_and_do_not_restart_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_method: str,
    error: AppServerRequestError,
    reason: str,
) -> None:
    runtime, repository_id = _runtime(tmp_path)

    async def call(
        method: str, params: JsonObject | None, *, owner: str
    ) -> JsonValue:
        del owner
        if method == failed_method:
            raise error
        assert params is not None
        return _response(method, str(runtime.settings.repositories_root / "sample"))

    monkeypatch.setattr(runtime, "_call", call)
    result = await runtime.capabilities(repository_id, "session")
    capability = {"model/list": "models", "skills/list": "skills", "plugin/list": "plugins"}[
        failed_method
    ]
    status = _status(result)
    availability = status[capability]
    assert isinstance(availability, dict)
    assert status["overall"] == "partial"
    assert availability["reason"] == reason
    assert runtime.supervisor.status.state is ProcessState.READY
    assert runtime.supervisor.status.generation == 1
    assert runtime.supervisor.status.restart_attempts == 0
