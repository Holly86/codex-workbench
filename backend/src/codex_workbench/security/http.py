"""Central authenticated-session, Origin, content-type, and CSRF HTTP boundary."""

from __future__ import annotations

import logging
from http import HTTPStatus

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from codex_workbench.config import Settings
from codex_workbench.security.service import SecurityService
from codex_workbench.security.sessions import SessionValidation

logger = logging.getLogger("codex_workbench.security.http")

_PUBLIC_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/health/live"),
        ("GET", "/api/health/ready"),
        ("GET", "/api/auth/status"),
        ("POST", "/api/auth/setup"),
        ("POST", "/api/auth/login"),
    }
)
_PUBLIC_MUTATIONS = frozenset({"/api/auth/setup", "/api/auth/login"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def api_error(
    status_code: int,
    code: str,
    message: str,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=headers,
    )


def exact_origin_matches(request: Request, expected: str) -> bool:
    origins = request.headers.getlist("origin")
    return len(origins) == 1 and origins[0] == expected


def mutation_metadata_is_safe(
    request: Request,
    expected_origin: str,
    *,
    allowed_content_types: frozenset[str] = frozenset({"application/json"}),
) -> bool:
    if not exact_origin_matches(request, expected_origin):
        return False
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site != "same-origin":
        return False
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    return content_type in allowed_content_types


class SecurityBoundaryMiddleware(BaseHTTPMiddleware):
    """Fail closed for every non-public HTTP API route."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        route_key = (request.method, request.url.path)
        if route_key in {
            ("GET", "/api/health/live"),
            ("GET", "/api/health/ready"),
        }:
            return await call_next(request)

        security: SecurityService = request.app.state.security
        if not security.ready:
            return api_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "security_unavailable",
                "Die Sicherheitskonfiguration ist nicht verfügbar.",
            )

        if route_key in _PUBLIC_ROUTES:
            if request.url.path in _PUBLIC_MUTATIONS and not mutation_metadata_is_safe(
                request, security.settings.canonical_public_origin
            ):
                logger.warning(
                    "security_origin_or_metadata_rejected",
                    extra={"path": request.url.path},
                )
                return api_error(
                    HTTPStatus.FORBIDDEN,
                    "request_origin_rejected",
                    "Die Anfrage wurde aus Sicherheitsgründen abgelehnt.",
                )
            return await call_next(request)

        raw_token = request.cookies.get(security.settings.session_cookie_name)
        authentication = security.require_sessions().authenticate(raw_token)
        if authentication.status is not SessionValidation.VALID or authentication.session is None:
            if authentication.expired_session_id is not None:
                runtime = getattr(request.app.state, "runtime", None)
                invalidate = getattr(runtime, "invalidate_session", None)
                if invalidate is not None:
                    await invalidate(authentication.expired_session_id)
            logger.info(
                "security_unauthenticated_request_rejected",
                extra={"path": request.url.path},
            )
            response = api_error(
                HTTPStatus.UNAUTHORIZED,
                "session_expired",
                "Die Sitzung ist abgelaufen. Bitte erneut anmelden.",
            )
            clear_session_cookie(response, security.settings)
            return response

        request.state.authenticated_session = authentication.session
        if request.method not in _SAFE_METHODS:
            allowed_content_types = (
                frozenset({"multipart/form-data"})
                if request.method == "POST" and request.url.path == "/api/uploads"
                else frozenset({"application/json"})
            )
            if not mutation_metadata_is_safe(
                request,
                security.settings.canonical_public_origin,
                allowed_content_types=allowed_content_types,
            ):
                logger.warning(
                    "security_origin_or_metadata_rejected",
                    extra={"path": request.url.path, "session_id": authentication.session.id},
                )
                return api_error(
                    HTTPStatus.FORBIDDEN,
                    "request_origin_rejected",
                    "Die Anfrage wurde aus Sicherheitsgründen abgelehnt.",
                )
            supplied_csrf = request.headers.get("x-csrf-token")
            if raw_token is None or not security.require_sessions().validate_csrf(
                raw_token, supplied_csrf
            ):
                logger.warning(
                    "security_csrf_rejected",
                    extra={"path": request.url.path, "session_id": authentication.session.id},
                )
                return api_error(
                    HTTPStatus.FORBIDDEN,
                    "csrf_rejected",
                    "Die Anfrage wurde aus Sicherheitsgründen abgelehnt.",
                )
        return await call_next(request)


def set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_absolute_timeout_seconds,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite=settings.session_cookie_samesite,
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite=settings.session_cookie_samesite,
    )
