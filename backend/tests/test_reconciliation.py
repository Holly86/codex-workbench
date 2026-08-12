from __future__ import annotations

import logging
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from codex_workbench.app_server.correlation import AppServerRequestError
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
