"""Request tracing and baseline API response headers."""

from __future__ import annotations

import logging
from time import monotonic
from typing import cast
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("codex_workbench.http")


class RequestBodyLimitMiddleware:
    """Count actual ASGI body bytes; Content-Length is only an early rejection hint."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        method = cast(str, scope.get("method", "GET"))
        path = cast(str, scope.get("path", ""))
        if method not in {"POST", "PUT", "PATCH", "DELETE"} or not path.startswith("/api/"):
            await self._app(scope, receive, send)
            return
        application = scope.get("app")
        if application is None:
            raise RuntimeError("ASGI application state is unavailable")
        settings = application.state.settings
        limit = (
            settings.max_upload_bytes + 1024 * 1024
            if path == "/api/uploads"
            else settings.max_json_request_bytes
        )
        received = 0
        too_large = False

        async def limited_receive() -> Message:
            nonlocal received, too_large
            if too_large:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    too_large = True
                    return {"type": "http.disconnect"}
            return message

        async def limited_send(message: Message) -> None:
            if not too_large:
                await send(message)

        try:
            await self._app(scope, limited_receive, limited_send)
        except Exception:
            if not too_large:
                raise
        if too_large:
            response = JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "code": "request_too_large",
                        "message": "Die Anfrage überschreitet das Größenlimit.",
                    }
                },
            )
            await response(scope, receive, send)


class ApiBoundaryMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Never reflect or log an untrusted header that may contain sensitive data.
        request_id = str(uuid4())
        request.state.request_id = request_id
        started = monotonic()
        if request.url.path.startswith("/api/") and request.method in {
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        }:
            raw_length = request.headers.get("content-length")
            if raw_length is not None:
                try:
                    content_length = int(raw_length)
                except ValueError:
                    content_length = -1
                settings = request.app.state.settings
                limit = (
                    settings.max_upload_bytes + 1024 * 1024
                    if request.url.path == "/api/uploads"
                    else settings.max_json_request_bytes
                )
                if content_length < 0 or content_length > limit:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "error": {
                                "code": "request_too_large",
                                "message": "Die Anfrage überschreitet das Größenlimit.",
                            }
                        },
                    )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        logger.info(
            "request_complete",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((monotonic() - started) * 1000, 2),
            },
        )
        return response
