from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from codex_workbench.app_server.transport import JsonObject, JsonValue
from codex_workbench.persistence.metadata import MetadataRepository, ThreadReference, ThreadTitle
from codex_workbench.realtime.broker import RealtimeEnvelope
from codex_workbench.runtime import (
    RuntimeConflictError,
    ThreadUnavailableError,
    WorkbenchRuntime,
)
from tests.helpers import build_settings


class MemoryThreadMetadata:
    def __init__(self) -> None:
        self.threads: dict[str, ThreadReference] = {}
        self.audits: list[str] = []
        self.titles: dict[str, ThreadTitle] = {}

    def upsert_thread(self, reference: ThreadReference) -> None:
        self.threads[reference.thread_id] = reference

    def get_thread(self, thread_id: str) -> ThreadReference | None:
        return self.threads.get(thread_id)

    def get_thread_title(self, thread_id: str) -> ThreadTitle | None:
        return self.titles.get(thread_id)

    def delete_thread(self, thread_id: str) -> None:
        self.threads.pop(thread_id, None)

    def audit(self, event_type: str, **_values: object) -> None:
        self.audits.append(event_type)

    def resolve_thread_title(
        self, thread_id: str, *, native_title: str | None, fallback_title: str, observed_at: int
    ) -> ThreadTitle:
        existing = self.titles.get(thread_id)
        if existing is not None and existing.source == "manual":
            return existing
        title = ThreadTitle(
            thread_id,
            native_title or fallback_title,
            "native" if native_title else "fallback",
            observed_at,
            "succeeded" if native_title else "idle",
            None,
        )
        self.titles[thread_id] = title
        return title

    def set_thread_title(
        self,
        thread_id: str,
        display_title: str,
        *,
        source: str,
        updated_at: int,
        generation_state: str = "succeeded",
        error_code: str | None = None,
    ) -> ThreadTitle:
        title = ThreadTitle(
            thread_id, display_title, source, updated_at, generation_state, error_code
        )
        self.titles[thread_id] = title
        return title


async def runtime_with_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, active: bool = False
) -> tuple[WorkbenchRuntime, str, list[tuple[str, JsonObject | None]]]:
    settings = build_settings(tmp_path)
    repository_path = settings.repositories_root / "sample"
    repository_path.mkdir()
    (repository_path / ".git").mkdir()
    memory = MemoryThreadMetadata()
    metadata = cast(MetadataRepository, memory)
    runtime = WorkbenchRuntime(settings, metadata)
    repository = (await runtime.repositories.list_repositories())[0]
    reference = ThreadReference(
        thread_id="thread-delete-me",
        repository_id=repository.id,
        repository_relative_path=repository.relative_path,
        last_session_id="owner-session",
        sandbox_mode="workspace-write",
        approval_policy="on-request",
        created_at=1,
        updated_at=1,
    )
    metadata.upsert_thread(reference)
    metadata.upsert_thread(
        ThreadReference(
            thread_id="thread-keep-me",
            repository_id=repository.id,
            repository_relative_path=repository.relative_path,
            last_session_id="owner-session",
            sandbox_mode="workspace-write",
            approval_policy="on-request",
            created_at=1,
            updated_at=1,
        )
    )
    projected = await runtime.projection.register_thread(
        {
            "id": reference.thread_id,
            "cwd": str(repository_path),
            "status": "running" if active else "idle",
            "turns": (
                [{"id": "active-turn", "status": "inProgress", "items": []}] if active else []
            ),
        },
        repository_id=repository.id,
        repository_name=repository.name,
        repository_path=repository_path,
        repository_relative_path=repository.relative_path,
        session_id="owner-session",
        sandbox_mode="workspace-write",
        approval_policy="on-request",
    )
    assert (projected.active_turn_id is not None) is active
    calls: list[tuple[str, JsonObject | None]] = []

    async def call(method: str, params: JsonObject | None, *, owner: str) -> JsonValue:
        assert owner == "owner-session"
        calls.append((method, params))
        return {}

    monkeypatch.setattr(runtime, "_call", call)
    return runtime, repository.id, calls


@pytest.mark.asyncio
async def test_manual_title_uses_native_set_and_wins_over_later_native_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _repository_id, calls = await runtime_with_thread(tmp_path, monkeypatch)
    browser = await runtime.set_thread_title(
        "thread-delete-me", "owner-session", "  Manual   release title  "
    )
    assert calls[-1] == (
        "thread/name/set",
        {"threadId": "thread-delete-me", "name": "Manual release title"},
    )
    assert browser["name"] == "Manual release title"
    assert browser["titleSource"] == "manual"

    await runtime.projection.ingest_notification(
        1,
        "thread/name/updated",
        {"threadId": "thread-delete-me", "threadName": "Stale native title"},
    )
    projected = runtime.projection.thread_browser_dict(
        runtime.projection.require_thread("thread-delete-me")
    )
    assert projected["name"] == "Manual release title"
    assert projected["titleSource"] == "manual"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "native_title",
    [
        "Resiliente Runtime-Wiederherstellung absichern",
        "Harden release recovery and navigation",
        "Workspace-Pfade sicher validieren",
        "Mehrstufigen Reliability-Sprint strukturiert abschließen",
        "Release-Readiness nach Recovery prüfen",
    ],
)
async def test_completed_turn_refreshes_native_title_without_visible_title_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native_title: str,
) -> None:
    runtime, _repository_id, calls = await runtime_with_thread(tmp_path, monkeypatch)

    async def call(method: str, params: JsonObject | None, *, owner: str) -> JsonValue:
        assert owner == "owner-session"
        calls.append((method, params))
        return {
            "thread": {
                "id": "thread-delete-me",
                "name": native_title,
            }
        }

    monkeypatch.setattr(runtime, "_call", call)
    await runtime._refresh_native_thread_title("thread-delete-me")  # noqa: SLF001

    assert calls == [
        ("thread/read", {"threadId": "thread-delete-me", "includeTurns": False})
    ]
    browser = runtime.projection.thread_browser_dict(
        runtime.projection.require_thread("thread-delete-me")
    )
    assert browser["name"] == native_title
    assert browser["titleSource"] == "native"
    assert browser["titleGenerationState"] == "succeeded"


@pytest.mark.asyncio
async def test_delete_idle_thread_cleans_local_state_and_emits_realtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, repository_id, calls = await runtime_with_thread(tmp_path, monkeypatch)
    subscription, _replay, _missed, _latest = await runtime.broker.subscribe("owner-session", None)

    result = await runtime.delete_thread(
        thread_id="thread-delete-me",
        repository_id=repository_id,
        session_id="owner-session",
    )
    event = await subscription.queue.get()

    assert result == {"deleted": True, "threadId": "thread-delete-me"}
    assert calls == [("thread/delete", {"threadId": "thread-delete-me"})]
    assert all(method != "thread/read" for method, _params in calls)
    assert runtime.metadata.get_thread("thread-delete-me") is None
    assert runtime.metadata.get_thread("thread-keep-me") is not None
    with pytest.raises(ValueError):
        runtime.projection.require_thread("thread-delete-me")
    assert isinstance(event, RealtimeEnvelope)
    assert event.type == "thread.deleted"
    assert event.thread_id == "thread-delete-me"
    await subscription.close()


@pytest.mark.asyncio
async def test_delete_active_thread_is_blocked_before_native_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, repository_id, calls = await runtime_with_thread(tmp_path, monkeypatch, active=True)
    with pytest.raises(RuntimeConflictError):
        await runtime.delete_thread(
            thread_id="thread-delete-me",
            repository_id=repository_id,
            session_id="owner-session",
        )
    assert calls == []
    assert runtime.metadata.get_thread("thread-delete-me") is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["thread", "repository", "session"])
async def test_delete_rejects_unknown_repository_and_session_mismatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mismatch: str
) -> None:
    runtime, repository_id, calls = await runtime_with_thread(tmp_path, monkeypatch)
    with pytest.raises(ThreadUnavailableError):
        await runtime.delete_thread(
            thread_id="unknown" if mismatch == "thread" else "thread-delete-me",
            repository_id="f" * 16 if mismatch == "repository" else repository_id,
            session_id="other-session" if mismatch == "session" else "owner-session",
        )
    assert calls == []
    remaining = cast(ThreadReference, runtime.metadata.get_thread("thread-delete-me"))
    assert remaining.thread_id == "thread-delete-me"
