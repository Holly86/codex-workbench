from __future__ import annotations

import asyncio
import json
import socket
import threading
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, WebSocket
from starlette.websockets import WebSocketDisconnect
from websockets.sync.client import ClientConnection, connect
from websockets.typing import Origin

from codex_workbench.api import realtime
from codex_workbench.config import Settings
from codex_workbench.main import create_app
from codex_workbench.realtime.broker import RealtimeSubscription
from codex_workbench.runtime import WorkbenchRuntime
from codex_workbench.security.service import SecurityService
from tests.helpers import build_settings

_PASSWORD = "correct horse battery staple"
_FIXTURE = Path(__file__).parent / "fixtures" / "fake_codex.py"
_WAIT_SECONDS = 10.0


class _LifecycleTracker:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._events: list[str] = []
        self.subscription_close_was_already_closed: list[bool] = []

    def record(self, event: str) -> None:
        with self._condition:
            self._events.append(event)
            self._condition.notify_all()

    def record_subscription_close(self, *, already_closed: bool) -> None:
        with self._condition:
            self.subscription_close_was_already_closed.append(already_closed)
            self._events.append("subscription_closed")
            self._condition.notify_all()

    def wait_for(self, event: str, count: int) -> None:
        with self._condition:
            observed = self._condition.wait_for(
                lambda: self._events.count(event) >= count,
                timeout=_WAIT_SECONDS,
            )
        assert observed, f"timed out waiting for {event} occurrence {count}"

    def events(self) -> tuple[str, ...]:
        with self._condition:
            return tuple(self._events)


class _ObservedServer(uvicorn.Server):
    def __init__(
        self,
        config: uvicorn.Config,
        *,
        ready: threading.Event,
        shutdown_started: threading.Event,
    ) -> None:
        super().__init__(config)
        self._ready = ready
        self._shutdown_started = shutdown_started

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        self._ready.set()

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        self._shutdown_started.set()
        await super().shutdown(sockets=sockets)


class _NetworkServer:
    def __init__(self, app: FastAPI, listener: socket.socket) -> None:
        self.ready = threading.Event()
        self.shutdown_started = threading.Event()
        self.stopped = threading.Event()
        self.error: Exception | None = None
        self.leaked_tasks: tuple[str, ...] = ()
        self._listener = listener
        self._loop: asyncio.AbstractEventLoop | None = None
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            access_log=False,
            log_level="warning",
            loop="asyncio",
            lifespan="on",
            ws="websockets",
        )
        self._server = _ObservedServer(
            config,
            ready=self.ready,
            shutdown_started=self.shutdown_started,
        )
        self._thread = threading.Thread(
            target=self._run,
            name="workbench-test-uvicorn",
            daemon=True,
        )

    def __enter__(self) -> _NetworkServer:
        self._thread.start()
        assert self.ready.wait(timeout=_WAIT_SECONDS), "Uvicorn did not become ready"
        assert self.error is None
        return self

    def __exit__(self, *_: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=_WAIT_SECONDS)
        assert not self._thread.is_alive(), "Uvicorn did not stop cleanly"
        assert self.stopped.is_set()
        assert self.error is None
        assert self.leaked_tasks == ()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._server.serve(sockets=[self._listener]))
        except Exception as exc:
            self.error = exc
        finally:
            pending = tuple(task for task in asyncio.all_tasks(loop) if not task.done())
            self.leaked_tasks = tuple(sorted(task.get_name() for task in pending))
            if pending:
                for task in pending:
                    task.cancel()
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            self.stopped.set()

    def websocket_tasks(self) -> tuple[str, ...]:
        return self._submit(_active_websocket_tasks())

    def _submit(self, operation: Coroutine[Any, Any, tuple[str, ...]]) -> tuple[str, ...]:
        loop = self._loop
        assert loop is not None
        future = asyncio.run_coroutine_threadsafe(operation, loop)
        return future.result(timeout=_WAIT_SECONDS)


async def _active_websocket_tasks() -> tuple[str, ...]:
    return tuple(
        sorted(
            task.get_name()
            for task in asyncio.all_tasks()
            if not task.done() and task.get_name().startswith("workbench-websocket-")
        )
    )


def _listener() -> tuple[socket.socket, int]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(2048)
    listener.setblocking(False)
    return listener, cast(int, listener.getsockname()[1])


def _settings(tmp_path: Path, public_origin: str) -> Settings:
    base = build_settings(tmp_path, public_origin=public_origin)
    values = base.model_dump()
    values.update(
        {
            "codex_executable": str(_FIXTURE),
            "app_server_start_enabled": True,
            "app_server_backoff_initial_seconds": 0.05,
            "app_server_backoff_max_seconds": 0.1,
        }
    )
    return Settings(_env_file=None, **values)


def _repository(settings: Settings) -> None:
    repository = settings.repositories_root / "sample"
    repository.mkdir()
    (repository / ".git").mkdir()


def _install_lifecycle_tracking(
    monkeypatch: pytest.MonkeyPatch,
    tracker: _LifecycleTracker,
) -> None:
    original_send = realtime._send_events
    original_receive = realtime._receive_liveness
    original_close = RealtimeSubscription.close

    async def tracked_send(
        websocket: WebSocket,
        subscription: object,
        security: SecurityService,
        raw_token: str | None,
    ) -> None:
        tracker.record("sender_started")
        try:
            await original_send(websocket, subscription, security, raw_token)
        except asyncio.CancelledError:
            tracker.record("sender_cancelled")
            raise
        finally:
            tracker.record("sender_finished")

    async def tracked_receive(websocket: WebSocket, interval: int) -> None:
        tracker.record("receiver_started")
        try:
            await original_receive(websocket, interval)
        except WebSocketDisconnect:
            tracker.record("receiver_disconnected")
            raise
        except asyncio.CancelledError:
            tracker.record("receiver_cancelled")
            raise
        finally:
            tracker.record("receiver_finished")

    async def tracked_close(subscription: RealtimeSubscription) -> None:
        already_closed = subscription.closed
        await original_close(subscription)
        tracker.record_subscription_close(already_closed=already_closed)

    monkeypatch.setattr(realtime, "_send_events", tracked_send)
    monkeypatch.setattr(realtime, "_receive_liveness", tracked_receive)
    monkeypatch.setattr(RealtimeSubscription, "close", tracked_close)


def _receive_snapshot(connection: ClientConnection, csrf_token: str) -> dict[str, Any]:
    connection.send(
        json.dumps(
            {
                "type": "subscribe",
                "csrfToken": csrf_token,
                "lastSequence": 0,
            }
        )
    )
    raw = connection.recv(timeout=_WAIT_SECONDS)
    assert isinstance(raw, str)
    envelope = json.loads(raw)
    assert isinstance(envelope, dict)
    assert envelope["type"] == "connection.snapshot"
    snapshot = envelope["snapshot"]
    assert isinstance(snapshot, dict)
    return snapshot


def test_browser_disconnect_reconnect_preserves_backend_turn_over_real_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recwarn: pytest.WarningsRecorder,
) -> None:
    listener, port = _listener()
    public_origin = f"http://127.0.0.1:{port}"
    settings = _settings(tmp_path, public_origin)
    _repository(settings)
    tracker = _LifecycleTracker()
    _install_lifecycle_tracking(monkeypatch, tracker)
    app = create_app(settings)

    with _NetworkServer(app, listener) as server:  # noqa: SIM117
        with httpx.Client(base_url=public_origin, timeout=_WAIT_SECONDS, trust_env=False) as client:
            auth = client.post(
                "/api/auth/setup",
                json={"password": _PASSWORD},
                headers={"Origin": public_origin},
            )
            assert auth.status_code == 200
            csrf_token = cast(str, auth.json()["csrfToken"])
            request_headers = {
                "Origin": public_origin,
                "Sec-Fetch-Site": "same-origin",
                "Content-Type": "application/json",
                "X-CSRF-Token": csrf_token,
            }
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
                headers=request_headers,
            )
            assert created.status_code == 201
            thread_id = created.json()["thread"]["id"]
            turn = client.post(
                f"/api/threads/{thread_id}/turns",
                json={
                    "message": "Run the deterministic fixture",
                    "images": [],
                    "sandboxMode": "workspace-write",
                    "approvalPolicy": "on-request",
                    "idempotencyKey": "network-fixture-message-0001",
                },
                headers=request_headers,
            )
            assert turn.status_code == 202

            raw_token = client.cookies.get(settings.session_cookie_name)
            assert raw_token is not None
            websocket_headers = {
                "Cookie": f"{settings.session_cookie_name}={raw_token}",
            }
            websocket_url = f"ws://127.0.0.1:{port}/ws/events"
            with connect(
                websocket_url,
                origin=cast(Origin, public_origin),
                additional_headers=websocket_headers,
                proxy=None,
                open_timeout=_WAIT_SECONDS,
                close_timeout=_WAIT_SECONDS,
            ) as browser_a:
                snapshot = _receive_snapshot(browser_a, csrf_token)
                approvals = snapshot["pendingApprovals"]
                assert len(approvals) == 1
                approval = approvals[0]
                assert approval["kind"] == "command_execution"
                assert approval["command"] == "git status --short"
                assert approval["cwdDisplay"] == "sample"
                serialized_threads = str(snapshot["threads"])
                assert "Hello from the fake server." in serialized_threads
                assert "Fixture reasoning summary." in serialized_threads
                assert "Fixture plan" in serialized_threads
                assert str(settings.repositories_root) not in serialized_threads
                usage = snapshot["threads"][0]["usage"]
                assert usage["context"]["usedTokens"] == 12
                assert usage["context"]["usedPercent"] == 1.2
                assert "remainingQuota" not in usage
                approval_id = approval["id"]
                assert server.websocket_tasks() == (
                    "workbench-websocket-receiver",
                    "workbench-websocket-sender",
                )

            tracker.wait_for("subscription_closed", 1)
            assert cast(WorkbenchRuntime, app.state.runtime).broker.status_counts()[1] == 0
            assert server.websocket_tasks() == ()
            assert not server.shutdown_started.is_set()
            active_thread = client.get(f"/api/threads/{thread_id}")
            assert active_thread.status_code == 200
            assert active_thread.json()["thread"]["activeTurnId"] is not None

            with connect(
                websocket_url,
                origin=cast(Origin, public_origin),
                additional_headers=websocket_headers,
                proxy=None,
                open_timeout=_WAIT_SECONDS,
                close_timeout=_WAIT_SECONDS,
            ) as browser_b:
                resumed = _receive_snapshot(browser_b, csrf_token)
                assert resumed["threads"][0]["activeTurnId"] is not None
                assert resumed["pendingApprovals"][0]["id"] == approval_id

            tracker.wait_for("subscription_closed", 2)
            assert cast(WorkbenchRuntime, app.state.runtime).broker.status_counts()[1] == 0
            assert server.websocket_tasks() == ()
            assert not server.shutdown_started.is_set()

            decision = client.post(
                f"/api/approvals/{approval_id}/decision",
                json={"decision": "decline"},
                headers=request_headers,
            )
            assert decision.status_code == 200
            replay = client.post(
                f"/api/approvals/{approval_id}/decision",
                json={"decision": "accept"},
                headers=request_headers,
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
                    "idempotencyKey": "network-fixture-message-0002",
                },
                headers=request_headers,
            )
            assert second_turn.status_code == 202
            interrupted = client.post(
                f"/api/threads/{thread_id}/interrupt",
                json={},
                headers=request_headers,
            )
            assert interrupted.status_code == 200
            assert interrupted.json()["status"] == "interrupting"

            events = tracker.events()
            assert events.count("receiver_disconnected") == 2
            assert events.count("receiver_finished") == 2
            assert events.count("sender_cancelled") == 2
            assert events.count("sender_finished") == 2
            assert tracker.subscription_close_was_already_closed == [False, False]
            first_close = events.index("subscription_closed") + 1
            lifecycles = (events[:first_close], events[first_close:])
            for lifecycle in lifecycles:
                assert lifecycle.index("receiver_disconnected") < lifecycle.index(
                    "sender_cancelled"
                )

    assert not any(item.category is ResourceWarning for item in recwarn)
