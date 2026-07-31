"""Authenticated placeholder for the future normalized event WebSocket."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket

from codex_workbench.security.service import SecurityService
from codex_workbench.security.websocket import authenticate_websocket

router = APIRouter()


@router.websocket("/ws/events")
async def event_boundary(websocket: WebSocket) -> None:
    security: SecurityService = websocket.app.state.security
    authenticated = await authenticate_websocket(websocket, security)
    if authenticated is None:
        return
    await websocket.accept()
    await websocket.send_json({"type": "connection.boundary_ready"})
    await websocket.close(code=1000)
