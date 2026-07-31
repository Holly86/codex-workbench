"""Same-session and exact-Origin WebSocket handshake authentication."""

from __future__ import annotations

import logging

from fastapi import WebSocket

from codex_workbench.security.service import SecurityService
from codex_workbench.security.sessions import AuthenticatedSession, SessionValidation

logger = logging.getLogger("codex_workbench.security.websocket")


async def authenticate_websocket(
    websocket: WebSocket,
    security: SecurityService,
) -> AuthenticatedSession | None:
    """Authenticate before accept; return None after a policy close."""

    origins = websocket.headers.getlist("origin")
    if len(origins) != 1 or origins[0] != security.settings.canonical_public_origin:
        logger.warning("security_websocket_origin_rejected")
        await websocket.close(code=4403)
        return None
    if not security.ready:
        await websocket.close(code=1013)
        return None

    raw_token = websocket.cookies.get(security.settings.session_cookie_name)
    authentication = security.require_sessions().authenticate(raw_token)
    if authentication.status is not SessionValidation.VALID or authentication.session is None:
        logger.info("security_websocket_session_rejected")
        await websocket.close(code=4401)
        return None
    websocket.state.authenticated_session = authentication.session
    return authentication.session
