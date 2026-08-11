from __future__ import annotations

import asyncio
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from codex_workbench.app_server.correlation import (
    AppServerDisconnectedError,
    AppServerRequestError,
)
from codex_workbench.app_server.supervisor import ProcessState
from codex_workbench.app_server.transport import JsonObject, JsonValue
from codex_workbench.persistence.metadata import MetadataRepository
from codex_workbench.runtime import WorkbenchRuntime
from tests.helpers import build_settings


def _runtime(tmp_path: Path) -> tuple[WorkbenchRuntime, str]:
    base = build_settings(tmp_path)
    settings = base.model_copy(update={"plugin_discovery_enabled": True})
    repository = settings.repositories_root / "sample"
    repository.mkdir()
    (repository / ".git").mkdir()
    metadata = cast(MetadataRepository, object())
    runtime = WorkbenchRuntime(settings, metadata)
    runtime.host_capabilities = {
        "sandbox": {
            "workspaceWrite": {"available": True, "reason": None},
            "readOnly": {"available": True, "reason": None},
            "fullAccess": {"available": True, "reason": None},
        }
    }
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
async def test_core_discovery_skips_optional_tools_and_disabled_is_not_a_health_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, repository_id = _runtime(tmp_path)
    runtime.settings.plugin_discovery_enabled = False
    calls: list[str] = []

    async def call(
        generation: int, method: str, params: JsonObject | None, *, owner: str
    ) -> JsonValue:
        del generation, params, owner
        calls.append(method)
        return _response(method, str(runtime.settings.repositories_root / "sample"))

    monkeypatch.setattr(runtime, "_call_for_generation", call)
    core = await runtime.capabilities(repository_id, "browser", tools="none")
    status = _status(core)

    assert calls == ["model/list"]
    assert status["overall"] == "ready"
    assert status["skills"] == {
        "state": "not_loaded", "available": False, "reason": None
    }
    assert status["plugins"] == {
        "state": "disabled", "available": False, "reason": None
    }

    skills = await runtime.capabilities(repository_id, "browser", tools="skills")
    assert calls == ["model/list", "model/list", "skills/list"]
    assert _status(skills)["overall"] == "ready"
    skill_status = _status(skills)["skills"]
    assert isinstance(skill_status, dict)
    assert skill_status["state"] == "ready"


@pytest.mark.asyncio
async def test_capabilities_are_single_flight_cached_and_generation_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, repository_id = _runtime(tmp_path)
    calls: Counter[str] = Counter()
    release = asyncio.Event()

    async def call(
        generation: int, method: str, params: JsonObject | None, *, owner: str
    ) -> JsonValue:
        del owner
        assert generation == runtime.supervisor.status.generation
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

    monkeypatch.setattr(runtime, "_call_for_generation", call)
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
        generation: int, method: str, params: JsonObject | None, *, owner: str
    ) -> JsonValue:
        del owner
        assert generation == 1
        if method == failed_method:
            raise error
        assert params is not None
        return _response(method, str(runtime.settings.repositories_root / "sample"))

    monkeypatch.setattr(runtime, "_call_for_generation", call)
    result = await runtime.capabilities(repository_id, "session")
    capability = {"model/list": "models", "skills/list": "skills", "plugin/list": "plugins"}[
        failed_method
    ]
    status = _status(result)
    availability = status[capability]
    assert isinstance(availability, dict)
    assert status["overall"] == (
        "unavailable" if failed_method == "model/list" else "ready"
    )
    assert availability["reason"] == reason
    assert runtime.supervisor.status.state is ProcessState.READY
    assert runtime.supervisor.status.generation == 1
    assert runtime.supervisor.status.restart_attempts == 0


@pytest.mark.asyncio
async def test_plugin_transport_circuit_survives_generation_refresh_and_request_storm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, repository_id = _runtime(tmp_path)
    calls: Counter[tuple[int, str]] = Counter()
    plugin_entered = asyncio.Event()
    release_plugin = asyncio.Event()
    generation_two_entered = asyncio.Event()
    release_generation_two = asyncio.Event()

    async def call(
        generation: int, method: str, params: JsonObject | None, *, owner: str
    ) -> JsonValue:
        del owner
        calls[(generation, method)] += 1
        assert params is not None
        if generation == 2 and method == "model/list":
            generation_two_entered.set()
            await release_generation_two.wait()
        if method == "plugin/list":
            plugin_entered.set()
            await release_plugin.wait()
            runtime.supervisor._state = ProcessState.RESTARTING  # noqa: SLF001
            raise AppServerDisconnectedError("generation 1 EOF")
        return _response(method, str(runtime.settings.repositories_root / "sample"))

    monkeypatch.setattr(runtime, "_call_for_generation", call)
    first = asyncio.create_task(runtime.capabilities(repository_id, "browser-a"))
    await plugin_entered.wait()
    overlapping = [
        asyncio.create_task(runtime.capabilities(repository_id, owner))
        for owner in ("browser-b", "browser-reload", "websocket-reconnect")
    ]
    release_plugin.set()
    initial = await asyncio.gather(first, *overlapping)
    assert all(_status(result)["overall"] == "ready" for result in initial)
    assert calls[(1, "plugin/list")] == 1

    runtime.supervisor._generation = 2  # noqa: SLF001
    runtime.supervisor._state = ProcessState.READY  # noqa: SLF001
    generation_two_first = asyncio.create_task(
        runtime.capabilities(repository_id, "browser-b", refresh=True)
    )
    await generation_two_entered.wait()
    generation_two_others = [
        asyncio.create_task(runtime.capabilities(repository_id, owner, refresh=True))
        for owner in ("browser-c", "websocket-reconnect")
    ]
    for _ in range(10):
        await asyncio.sleep(0)
    release_generation_two.set()
    after_restart = await asyncio.gather(generation_two_first, *generation_two_others)
    assert calls[(2, "plugin/list")] == 0
    assert calls[(2, "model/list")] >= 1
    assert calls[(2, "skills/list")] >= 1
    assert all(_status(result)["overall"] == "ready" for result in after_restart)
    plugin_state = _status(after_restart[0])["plugins"]
    assert isinstance(plugin_state, dict)
    assert plugin_state == {
        "state": "unavailable",
        "available": False,
        "reason": "temporarily_disabled",
    }
    assert runtime.app_server_ready is True


@pytest.mark.asyncio
async def test_generation_fence_prevents_old_discovery_from_using_new_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, repository_id = _runtime(tmp_path)
    calls: list[tuple[int, str]] = []

    async def call(
        generation: int, method: str, params: JsonObject | None, *, owner: str
    ) -> JsonValue:
        del owner
        calls.append((generation, method))
        assert params is not None
        if method == "model/list":
            runtime.supervisor._generation = 2  # noqa: SLF001
            raise AppServerDisconnectedError("generation changed")
        raise AssertionError("old discovery continued after generation change")

    monkeypatch.setattr(runtime, "_call_for_generation", call)
    result = await runtime.capabilities(repository_id, "browser")
    assert calls == [(1, "model/list"), (1, "skills/list"), (1, "plugin/list")]
    assert _status(result)["generation"] == 1
    assert _status(result)["overall"] == "unavailable"
