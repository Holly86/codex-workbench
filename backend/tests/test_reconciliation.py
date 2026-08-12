from __future__ import annotations

import logging
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from codex_workbench.app_server.client import AppServerClient
from codex_workbench.app_server.correlation import (
    AppServerRequestError,
    AppServerRequestTimeoutError,
)
from codex_workbench.app_server.supervisor import ProcessState
from codex_workbench.app_server.transport import JsonObject, JsonValue
from codex_workbench.persistence.metadata import MetadataRepository, ThreadReference, ThreadTitle
from codex_workbench.runtime import WorkbenchRuntime
from tests.helpers import build_settings


class ReconciliationMetadata:
    def __init__(self, references: tuple[ThreadReference, ...]) -> None:
        self.references = references
        self.upserts: list[ThreadReference] = []

    def list_threads(self) -> tuple[ThreadReference, ...]:
        return self.references

    def upsert_thread(self, reference: ThreadReference) -> None:
        self.upserts.append(reference)

    def repository_registration_state(self, _repository_id: str) -> str | None:
        return None

    def resolve_thread_title(
        self, thread_id: str, *, native_title: str | None, fallback_title: str, observed_at: int
    ) -> ThreadTitle:
        return ThreadTitle(
            thread_id,
            native_title or fallback_title,
            "native" if native_title else "fallback",
            observed_at,
            "succeeded" if native_title else "idle",
            None,
        )


@pytest.mark.asyncio
async def test_stale_thread_is_isolated_and_warning_is_deduplicated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = build_settings(tmp_path)
    repository = settings.repositories_root / "sample"
    repository.mkdir()
    (repository / ".git").mkdir()
    repository_id = sha256(b"sample").hexdigest()[:16]
    references = tuple(
        ThreadReference(
            thread_id=thread_id,
            repository_id=repository_id,
            repository_relative_path="sample",
            last_session_id="session",
            sandbox_mode="workspace-write",
            approval_policy="on-request",
            created_at=1,
            updated_at=1,
        )
        for thread_id in ("stale-thread", "healthy-thread")
    )
    metadata = ReconciliationMetadata(references)
    runtime = WorkbenchRuntime(settings, cast(MetadataRepository, metadata))

    async def call(method: str, params: JsonObject | None, *, owner: str) -> JsonValue:
        del method, owner
        assert params is not None
        if params["threadId"] == "stale-thread":
            raise AppServerRequestError(-32001, "historical thread unavailable")
        return {
            "thread": {
                "id": "healthy-thread",
                "cwd": str(repository),
                "preview": "Healthy",
                "status": {"type": "idle"},
                "createdAt": 1,
                "updatedAt": 2,
                "turns": [],
            }
        }

    monkeypatch.setattr(runtime, "_call", call)
    caplog.set_level(logging.WARNING, logger="codex_workbench.runtime")
    await runtime._reconcile()  # noqa: SLF001
    await runtime._reconcile()  # noqa: SLF001

    assert runtime.projection.require_thread("healthy-thread").id == "healthy-thread"
    snapshot = await runtime.snapshot("session")
    unavailable = snapshot["unavailableThreads"]
    assert isinstance(unavailable, list) and len(unavailable) == 1
    other_snapshot = await runtime.snapshot("other-session")
    assert other_snapshot["unavailableThreads"] == []
    assert caplog.messages.count("thread_reconciliation_failed") == 1


@pytest.mark.asyncio
async def test_unresponsive_active_turn_recovers_without_inventing_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = build_settings(tmp_path).model_copy(
        update={"app_server_watchdog_failure_threshold": 3}
    )
    repository = settings.repositories_root / "sample"
    repository.mkdir()
    (repository / ".git").mkdir()
    repository_id = sha256(b"sample").hexdigest()[:16]
    reference = ThreadReference(
        thread_id="active-thread",
        repository_id=repository_id,
        repository_relative_path="sample",
        last_session_id="owner-session",
        sandbox_mode="workspace-write",
        approval_policy="on-request",
        created_at=1,
        updated_at=1,
    )
    metadata = ReconciliationMetadata((reference,))
    runtime = WorkbenchRuntime(settings, cast(MetadataRepository, metadata))
    await runtime.projection.register_thread(
        {
            "id": reference.thread_id,
            "cwd": str(repository),
            "status": {"type": "active"},
            "createdAt": 1,
            "updatedAt": 1,
            "turns": [{"id": "active-turn", "status": "inProgress", "items": []}],
        },
        repository_id=repository_id,
        repository_name="sample",
        repository_path=repository,
        repository_relative_path="sample",
        session_id="owner-session",
        sandbox_mode="workspace-write",
        approval_policy="on-request",
    )

    class UnresponsiveClient:
        async def call(
            self, method: str, params: JsonObject | None, *, owner: str
        ) -> JsonValue:
            del method, params, owner
            raise AppServerRequestTimeoutError("synthetic watchdog timeout")

    supervisor = runtime.supervisor
    supervisor._state = ProcessState.READY  # noqa: SLF001
    supervisor._generation = 4  # noqa: SLF001
    supervisor._client = cast(AppServerClient, UnresponsiveClient())  # noqa: SLF001
    scheduled: list[bool] = []

    async def schedule(*, immediate: bool = False, request_followup: bool = True) -> bool:
        del immediate, request_followup
        scheduled.append(True)
        return True

    monkeypatch.setattr(supervisor, "_schedule_restart", schedule)
    for _ in range(3):
        await supervisor._run_watchdog_probe(4)  # noqa: SLF001

    reconnecting = runtime.projection.snapshot("owner-session", ())["connection"]
    assert isinstance(reconnecting, dict)
    assert reconnecting["state"] == "reconnecting"
    projected = runtime.projection.require_thread("active-thread")
    assert projected.status == "running"
    assert projected.active_turn_id == "active-turn"
    assert scheduled == [True]

    reconciliation_calls = 0

    async def call(method: str, params: JsonObject | None, *, owner: str) -> JsonValue:
        nonlocal reconciliation_calls
        assert method == "thread/read"
        assert params == {"threadId": "active-thread", "includeTurns": True}
        assert owner == "owner-session"
        reconciliation_calls += 1
        return {
            "thread": {
                "id": "active-thread",
                "cwd": str(repository),
                "status": {"type": "active"},
                "createdAt": 1,
                "updatedAt": 2,
                "turns": [
                    {"id": "active-turn", "status": "inProgress", "items": []}
                ],
            }
        }

    monkeypatch.setattr(runtime, "_call", call)
    await runtime._on_ready(5)  # noqa: SLF001

    connected = runtime.projection.snapshot("owner-session", ())["connection"]
    assert isinstance(connected, dict)
    assert connected["state"] == "connected"
    reconciled = runtime.projection.require_thread("active-thread")
    assert reconciled.status == "running"
    assert reconciled.active_turn_id == "active-turn"
    assert reconciliation_calls == 1
