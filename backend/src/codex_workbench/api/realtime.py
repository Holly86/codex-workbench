"""Authenticated normalized WebSocket with snapshots, replay, heartbeat, and backpressure."""

from __future__ import annotations

import asyncio
import json
import time
from typing import cast

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from codex_workbench.realtime.broker import SlowRealtimeClientError
from codex_workbench.runtime import WorkbenchRuntime
from codex_workbench.security.service import SecurityService
from codex_workbench.security.sessions import SessionValidation
from codex_workbench.security.websocket import authenticate_websocket

router = APIRouter()


@router.websocket("/ws/events")
async def event_boundary(websocket: WebSocket) -> None:
    security: SecurityService = websocket.app.state.security
    authenticated = await authenticate_websocket(websocket, security)
    if authenticated is None:
        return
    runtime = getattr(websocket.app.state, "runtime", None)
    if not isinstance(runtime, WorkbenchRuntime):
        await websocket.close(code=1013)
        return
    raw_token = websocket.cookies.get(security.settings.session_cookie_name)
    await websocket.accept()
    subscription = None
    try:
        try:
            raw_initial = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        except TimeoutError:
            await websocket.close(code=4408)
            return
        if len(raw_initial.encode("utf-8")) > 4096:
            await websocket.close(code=4400)
            return
        try:
            initial: object = json.loads(raw_initial)
        except json.JSONDecodeError:
            await websocket.close(code=4400)
            return
        if not isinstance(initial, dict) or initial.get("type") != "subscribe":
            await websocket.close(code=4400)
            return
        supplied_csrf = initial.get("csrfToken")
        if (
            raw_token is None
            or not isinstance(supplied_csrf, str)
            or not security.require_sessions().validate_csrf(raw_token, supplied_csrf)
        ):
            await websocket.close(code=4403)
            return
        raw_sequence = initial.get("lastSequence")
        last_sequence = (
            raw_sequence
            if isinstance(raw_sequence, int)
            and not isinstance(raw_sequence, bool)
            and raw_sequence >= 0
            else None
        )
        subscription, replay, missed, latest = await runtime.broker.subscribe(
            authenticated.id, last_sequence
        )
        if last_sequence is not None and last_sequence > 0 and not missed:
            for event in replay:
                await websocket.send_json(event.browser_dict())
        snapshot = await runtime.snapshot(authenticated.id)
        await websocket.send_json(
            {
                "type": "connection.snapshot",
                "latestSequence": latest,
                "missedEvents": missed,
                "snapshot": snapshot,
            }
        )
        sender = asyncio.create_task(
            _send_events(websocket, subscription, security, raw_token),
            name="workbench-websocket-sender",
        )
        receiver = asyncio.create_task(
            _receive_liveness(websocket, security.settings.realtime_heartbeat_seconds),
            name="workbench-websocket-receiver",
        )
        done, pending = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*done, *pending, return_exceptions=True)
    except WebSocketDisconnect:
        return
    finally:
        if subscription is not None:
            await subscription.close()


async def _send_events(
    websocket: WebSocket,
    subscription: object,
    security: SecurityService,
    raw_token: str | None,
) -> None:
    from codex_workbench.realtime.broker import RealtimeSubscription

    active = cast(RealtimeSubscription, subscription)
    interval = security.settings.realtime_heartbeat_seconds
    while True:
        try:
            value = await asyncio.wait_for(active.queue.get(), timeout=interval)
        except TimeoutError:
            authentication = security.require_sessions().authenticate(raw_token)
            if authentication.status is not SessionValidation.VALID:
                await websocket.close(code=4401)
                return
            await websocket.send_json({"type": "heartbeat", "serverTime": int(time.time())})
            continue
        if isinstance(value, SlowRealtimeClientError):
            await websocket.close(code=4408)
            return
        await websocket.send_json(value.browser_dict())


async def _receive_liveness(websocket: WebSocket, interval: int) -> None:
    while True:
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=interval * 3)
        except TimeoutError:
            await websocket.close(code=4408)
            return
        if len(raw.encode("utf-8")) > 1024:
            await websocket.close(code=4400)
            return
        try:
            message: object = json.loads(raw)
        except json.JSONDecodeError:
            await websocket.close(code=4400)
            return
        if not isinstance(message, dict) or message.get("type") != "pong":
            await websocket.close(code=4400)
            return
