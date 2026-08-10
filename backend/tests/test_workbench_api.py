from __future__ import annotations

import asyncio
import base64
import os
import time
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient

from codex_workbench.config import Settings
from codex_workbench.main import create_app
from codex_workbench.runtime import WorkbenchRuntime
from codex_workbench.security.service import SecurityService
from codex_workbench.uploads import UploadService
from tests.helpers import build_settings

_PASSWORD = "correct horse battery staple"
_FIXTURE = Path(__file__).parent / "fixtures" / "fake_codex.py"
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _settings(tmp_path: Path) -> Settings:
    base = build_settings(tmp_path)
    values = base.model_dump()
    values.update(
        {
            "codex_executable": str(_FIXTURE),
            "app_server_start_enabled": True,
            "app_server_backoff_initial_seconds": 0.05,
            "app_server_backoff_max_seconds": 0.1,
            "upload_max_frames": 1,
        }
    )
    return Settings(_env_file=None, **values)


def _auth(client: TestClient, settings: Settings) -> dict[str, str]:
    response = client.post(
        "/api/auth/setup",
        json={"password": _PASSWORD},
        headers={"Origin": settings.canonical_public_origin},
    )
    csrf = cast(str, response.json()["csrfToken"])
    return {
        "Origin": settings.canonical_public_origin,
        "Sec-Fetch-Site": "same-origin",
        "Content-Type": "application/json",
        "X-CSRF-Token": csrf,
    }


def _repository(settings: Settings) -> None:
    repository = settings.repositories_root / "sample"
    repository.mkdir()
    (repository / ".git").mkdir()


def test_thread_turn_stream_approval_usage_and_interrupt_flow(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _repository(settings)
    with TestClient(create_app(settings)) as client:
        headers = _auth(client, settings)
        ready = client.get("/api/health/ready")
        assert ready.status_code == 200
        assert ready.json()["checks"]["app_server"] is True

        repositories = client.get("/api/repositories").json()["repositories"]
        repository_id = repositories[0]["id"]
        created = client.post(
            "/api/threads",
            json={
                "repositoryId": repository_id,
                "sandboxMode": "workspace-write",
                "approvalPolicy": "on-request",
            },
            headers=headers,
        )
        assert created.status_code == 201
        thread = created.json()["thread"]
        thread_id = thread["id"]
        assert thread["fullAccess"] is False
        assert "cwd" not in created.text

        turn = client.post(
            f"/api/threads/{thread_id}/turns",
            json={
                "message": "Run the deterministic fixture",
                "images": [],
                "sandboxMode": "workspace-write",
                "approvalPolicy": "on-request",
                "idempotencyKey": "fixture-message-0001",
            },
            headers=headers,
        )
        assert turn.status_code == 202

        with client.websocket_connect(
            "/ws/events", headers={"Origin": settings.canonical_public_origin}
        ) as websocket:
            websocket.send_json(
                {
                    "type": "subscribe",
                    "csrfToken": headers["X-CSRF-Token"],
                    "lastSequence": 0,
                }
            )
            snapshot = websocket.receive_json()
            assert snapshot["type"] == "connection.snapshot"
            approvals = snapshot["snapshot"]["pendingApprovals"]
            assert len(approvals) == 1
            approval = approvals[0]
            assert approval["kind"] == "command_execution"
            assert approval["command"] == "git status --short"
            assert approval["cwdDisplay"] == "sample"
            serialized_threads = str(snapshot["snapshot"]["threads"])
            assert "Hello from the fake server." in serialized_threads
            assert "Fixture reasoning summary." in serialized_threads
            assert "Fixture plan" in serialized_threads
            assert str(settings.repositories_root) not in serialized_threads
            usage = snapshot["snapshot"]["threads"][0]["usage"]
            assert usage["context"]["usedTokens"] == 12
            assert usage["context"]["usedPercent"] == 1.2
            assert "remainingQuota" not in usage
            approval_id = approval["id"]

        # Closing the browser transport must not interrupt the backend-owned turn.
        with client.websocket_connect(
            "/ws/events", headers={"Origin": settings.canonical_public_origin}
        ) as reconnected:
            reconnected.send_json(
                {
                    "type": "subscribe",
                    "csrfToken": headers["X-CSRF-Token"],
                    "lastSequence": 0,
                }
            )
            resumed_snapshot = reconnected.receive_json()["snapshot"]
            assert resumed_snapshot["threads"][0]["activeTurnId"] is not None
            assert resumed_snapshot["pendingApprovals"][0]["id"] == approval_id

        decision = client.post(
            f"/api/approvals/{approval_id}/decision",
            json={"decision": "decline"},
            headers=headers,
        )
        assert decision.status_code == 200
        replay = client.post(
            f"/api/approvals/{approval_id}/decision",
            json={"decision": "accept"},
            headers=headers,
        )
        assert replay.status_code == 409

        limits = client.get("/api/usage/rate-limits")
        assert limits.status_code == 200
        assert limits.json()["primary"]["usedPercent"] == 25.0

        second_turn = client.post(
            f"/api/threads/{thread_id}/turns",
            json={
                "message": "Start again",
                "images": [],
                "sandboxMode": "workspace-write",
                "approvalPolicy": "on-request",
                "idempotencyKey": "fixture-message-0002",
            },
            headers=headers,
        )
        assert second_turn.status_code == 202
        interrupted = client.post(f"/api/threads/{thread_id}/interrupt", json={}, headers=headers)
        assert interrupted.status_code == 200
        assert interrupted.json()["status"] == "interrupting"


def test_full_access_is_one_use_bound_and_allows_never_policy(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _repository(settings)
    with TestClient(create_app(settings)) as client:
        headers = _auth(client, settings)
        repository = client.get("/api/repositories").json()["repositories"][0]
        prepared = client.post(
            "/api/permissions/full-access/prepare",
            json={
                "repositoryId": repository["id"],
                "approvalPolicy": "never",
                "threadId": None,
            },
            headers=headers,
        )
        assert prepared.status_code == 200
        capability = prepared.json()
        created = client.post(
            "/api/threads",
            json={
                "repositoryId": repository["id"],
                "sandboxMode": "danger-full-access",
                "approvalPolicy": "never",
                "confirmationId": capability["confirmationId"],
                "fullAccessScopeId": capability["scopeId"],
            },
            headers=headers,
        )
        assert created.status_code == 201
        assert created.json()["thread"]["fullAccess"] is True

        replay = client.post(
            "/api/threads",
            json={
                "repositoryId": repository["id"],
                "sandboxMode": "danger-full-access",
                "approvalPolicy": "never",
                "confirmationId": capability["confirmationId"],
                "fullAccessScopeId": capability["scopeId"],
            },
            headers=headers,
        )
        assert replay.status_code == 403

        thread_id = created.json()["thread"]["id"]
        changed_policy_without_confirmation = client.post(
            f"/api/threads/{thread_id}/turns",
            json={
                "message": "This policy transition must be confirmed",
                "images": [],
                "sandboxMode": "danger-full-access",
                "approvalPolicy": "on-request",
                "idempotencyKey": "fixture-full-access-policy-change",
            },
            headers=headers,
        )
        assert changed_policy_without_confirmation.status_code == 403

        manipulated = client.post(
            "/api/threads",
            json={
                "repositoryId": repository["id"],
                "sandboxMode": "not-a-sandbox",
                "approvalPolicy": "never",
            },
            headers=headers,
        )
        assert manipulated.status_code == 422


def test_image_upload_validation_multiple_images_and_symlink_recheck(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _repository(settings)
    with TestClient(create_app(settings)) as client:
        headers = _auth(client, settings)
        upload_headers = {
            "Origin": settings.canonical_public_origin,
            "Sec-Fetch-Site": "same-origin",
            "X-CSRF-Token": headers["X-CSRF-Token"],
        }
        uploaded = [
            client.post(
                "/api/uploads",
                files={"file": (f"screen-{index}.png", _PNG, "image/png")},
                headers=upload_headers,
            )
            for index in range(2)
        ]
        assert [response.status_code for response in uploaded] == [201, 201]
        upload_ids = [response.json()["upload"]["id"] for response in uploaded]

        invalid = client.post(
            "/api/uploads",
            files={"file": ("fake.png", b"not an image", "image/png")},
            headers=upload_headers,
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "upload_invalid_image"

        repository_id = client.get("/api/repositories").json()["repositories"][0]["id"]
        thread = client.post(
            "/api/threads",
            json={
                "repositoryId": repository_id,
                "sandboxMode": "read-only",
                "approvalPolicy": "on-request",
            },
            headers=headers,
        ).json()["thread"]
        third = client.post(
            "/api/uploads",
            files={"file": ("race.png", _PNG, "image/png")},
            headers=upload_headers,
        )
        upload_id = third.json()["upload"]["id"]
        runtime = cast(WorkbenchRuntime, cast(FastAPI, client.app).state.runtime)
        record = runtime.metadata.get_upload(upload_id)
        assert record is not None
        stored_path = settings.uploads_dir / record.storage_name
        stored_path.unlink()
        stored_path.symlink_to(settings.session_secret_file)
        rejected = client.post(
            f"/api/threads/{thread['id']}/turns",
            json={
                "message": "This must fail closed",
                "images": [{"uploadId": upload_id, "detail": "auto"}],
                "sandboxMode": "read-only",
                "approvalPolicy": "on-request",
                "idempotencyKey": "fixture-images-0002",
            },
            headers=headers,
        )
        assert rejected.status_code == 422

        removed = client.request("DELETE", f"/api/uploads/{upload_id}", json={}, headers=headers)
        assert removed.status_code == 200
        assert not os.path.lexists(stored_path)

        turn = client.post(
            f"/api/threads/{thread['id']}/turns",
            json={
                "message": "Inspect both screenshots",
                "images": [{"uploadId": upload_id, "detail": "auto"} for upload_id in upload_ids],
                "sandboxMode": "read-only",
                "approvalPolicy": "on-request",
                "idempotencyKey": "fixture-images-0001",
            },
            headers=headers,
        )
        assert turn.status_code == 202


class _SlowUpload:
    filename = "cancelled.png"

    def __init__(self) -> None:
        self.waiting = asyncio.Event()
        self.closed = False
        self._first = True

    async def read(self, _size: int = -1) -> bytes:
        if self._first:
            self._first = False
            return _PNG[:16]
        self.waiting.set()
        await asyncio.Event().wait()
        return b""

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_upload_cancellation_removes_staging_file_and_records_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    security = SecurityService(settings)
    security.initialize()
    service = UploadService(settings, security.require_metadata())
    await service.initialize()
    monkeypatch.setattr("codex_workbench.uploads.secrets.token_urlsafe", lambda _length: "A" * 24)
    slow = _SlowUpload()
    task = asyncio.create_task(service.store(cast(UploadFile, slow), session_id="session-cancel"))
    await asyncio.wait_for(slow.waiting.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    record = security.require_metadata().get_upload("upload_" + "A" * 24)
    assert record is not None
    assert record.state == "rejected"
    assert record.error_code == "upload_cancelled"
    assert slow.closed is True
    assert not (settings.uploads_dir / record.storage_name).exists()


def test_session_expiry_invalidates_pending_approvals(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    values = settings.model_dump()
    values.update(
        {
            "session_idle_timeout_seconds": 300,
            "session_absolute_timeout_seconds": 600,
        }
    )
    expiring = Settings(_env_file=None, **values)
    _repository(expiring)
    clock = {"now": 1_000.0}
    with TestClient(create_app(expiring, wall_clock=lambda: clock["now"])) as client:
        headers = _auth(client, expiring)
        repository_id = client.get("/api/repositories").json()["repositories"][0]["id"]
        thread = client.post(
            "/api/threads",
            json={
                "repositoryId": repository_id,
                "sandboxMode": "workspace-write",
                "approvalPolicy": "on-request",
            },
            headers=headers,
        ).json()["thread"]
        started = client.post(
            f"/api/threads/{thread['id']}/turns",
            json={
                "message": "Create a pending approval",
                "images": [],
                "sandboxMode": "workspace-write",
                "approvalPolicy": "on-request",
                "idempotencyKey": "fixture-session-expiration",
            },
            headers=headers,
        )
        assert started.status_code == 202
        runtime = cast(WorkbenchRuntime, cast(FastAPI, client.app).state.runtime)
        session_id = runtime.projection.require_thread(thread["id"]).session_id
        assert client.portal is not None
        assert len(client.portal.call(runtime.approvals.list_pending, session_id)) == 1

        clock["now"] += 301
        expired = client.get("/api/usage/rate-limits")
        assert expired.status_code == 401
        assert client.portal.call(runtime.approvals.list_pending, session_id) == ()


def test_fake_server_projects_command_and_file_change_without_absolute_paths(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _repository(settings)
    with TestClient(create_app(settings)) as client:
        headers = _auth(client, settings)
        repository_id = client.get("/api/repositories").json()["repositories"][0]["id"]
        thread = client.post(
            "/api/threads",
            json={"repositoryId": repository_id},
            headers=headers,
        ).json()["thread"]
        client.post(
            f"/api/threads/{thread['id']}/turns",
            json={
                "message": "Exercise activities",
                "idempotencyKey": "fixture-command-file-change",
            },
            headers=headers,
        )
        runtime = cast(WorkbenchRuntime, cast(FastAPI, client.app).state.runtime)
        session_id = runtime.projection.require_thread(thread["id"]).session_id
        assert client.portal is not None
        pending = client.portal.call(runtime.approvals.list_pending, session_id)
        for _ in range(100):
            if pending:
                break
            time.sleep(0.01)
            pending = client.portal.call(runtime.approvals.list_pending, session_id)
        assert pending
        response = client.post(
            f"/api/approvals/{pending[0].id}/decision",
            json={"decision": "accept"},
            headers=headers,
        )
        assert response.status_code == 200
        projected = runtime.projection.require_thread(thread["id"])
        for _ in range(100):
            if projected.status == "completed":
                break
            time.sleep(0.01)
        browser = runtime.projection.thread_browser_dict(projected)
        serialized = str(browser)
        assert "commandExecution" in serialized
        assert "fileChange" in serialized
        assert str(settings.repositories_root) not in serialized
