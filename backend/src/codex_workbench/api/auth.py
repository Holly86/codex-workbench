"""Single-user setup, login, status, and logout API."""

from __future__ import annotations

import asyncio
import logging
from http import HTTPStatus
from typing import Annotated, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from codex_workbench.security.http import api_error, clear_session_cookie, set_session_cookie
from codex_workbench.security.service import SecurityService
from codex_workbench.security.sessions import SessionValidation

logger = logging.getLogger("codex_workbench.security.auth")
router = APIRouter(prefix="/api/auth", tags=["authentication"])


class SetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: Annotated[str, Field(min_length=12, max_length=1024)]


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: Annotated[str, Field(min_length=1, max_length=1024)]


class AuthStateResponse(BaseModel):
    state: Literal["setup_required", "unauthenticated", "authenticated"]
    csrfToken: str | None = None
    expiresAt: int | None = None
    reason: Literal["session_expired"] | None = None


def _client_address(request: Request) -> str | None:
    return None if request.client is None else request.client.host


def _issue_session_response(request: Request, security: SecurityService) -> JSONResponse:
    previous_token = request.cookies.get(security.settings.session_cookie_name)
    issued = security.require_sessions().issue(
        previous_token=previous_token,
        client_address=_client_address(request),
        user_agent=request.headers.get("user-agent"),
    )
    response = JSONResponse(
        status_code=HTTPStatus.OK,
        content=AuthStateResponse(
            state="authenticated",
            csrfToken=issued.authenticated.csrf_token,
            expiresAt=issued.authenticated.expires_at,
        ).model_dump(),
    )
    set_session_cookie(response, security.settings, issued.token)
    return response


@router.get("/status", response_model=AuthStateResponse)
async def auth_status(request: Request, response: Response) -> AuthStateResponse:
    security: SecurityService = request.app.state.security
    if security.setup_required:
        return AuthStateResponse(state="setup_required")

    raw_token = request.cookies.get(security.settings.session_cookie_name)
    authentication = security.require_sessions().authenticate(raw_token)
    if authentication.status is SessionValidation.VALID and authentication.session is not None:
        return AuthStateResponse(
            state="authenticated",
            csrfToken=authentication.session.csrf_token,
            expiresAt=authentication.session.expires_at,
        )
    if authentication.expired_session_id is not None:
        runtime = getattr(request.app.state, "runtime", None)
        invalidate = getattr(runtime, "invalidate_session", None)
        if invalidate is not None:
            await invalidate(authentication.expired_session_id)
    if raw_token is not None:
        clear_session_cookie(response, security.settings)
        return AuthStateResponse(state="unauthenticated", reason="session_expired")
    return AuthStateResponse(state="unauthenticated")


@router.post("/setup", response_model=AuthStateResponse)
async def setup(payload: SetupRequest, request: Request) -> JSONResponse:
    security: SecurityService = request.app.state.security
    if len(payload.password) < security.settings.password_min_length:
        return api_error(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            "password_policy_failed",
            f"Das Passwort muss mindestens {security.settings.password_min_length} Zeichen haben.",
        )
    if not security.setup_required:
        return api_error(
            HTTPStatus.CONFLICT,
            "setup_unavailable",
            "Die Ersteinrichtung ist nicht mehr verfügbar.",
        )
    initialized = await security.initialize_password(payload.password)
    if not initialized:
        logger.warning("security_setup_race_rejected")
        return api_error(
            HTTPStatus.CONFLICT,
            "setup_unavailable",
            "Die Ersteinrichtung ist nicht mehr verfügbar.",
        )
    logger.info("security_setup_completed")
    return _issue_session_response(request, security)


@router.post("/login", response_model=AuthStateResponse)
async def login(payload: LoginRequest, request: Request) -> JSONResponse:
    security: SecurityService = request.app.state.security
    sessions = security.require_sessions()
    address_key = sessions.fingerprint(_client_address(request))
    decision = security.rate_limiter.consume(address_key)
    if not decision.allowed:
        logger.warning("security_login_rate_limited")
        return api_error(
            HTTPStatus.TOO_MANY_REQUESTS,
            "login_rate_limited",
            "Zu viele Anmeldeversuche. Bitte später erneut versuchen.",
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )

    verified = await security.verify_password(payload.password)
    if not verified or security.setup_required:
        delay = min(
            security.settings.login_failure_delay_seconds * decision.address_attempts,
            2.0,
        )
        if delay:
            await asyncio.sleep(delay)
        logger.info("security_login_failed")
        return api_error(
            HTTPStatus.UNAUTHORIZED,
            "invalid_credentials",
            "Anmeldung fehlgeschlagen.",
        )

    logger.info("security_login_succeeded")
    return _issue_session_response(request, security)


@router.post("/logout", response_model=AuthStateResponse)
async def logout(
    request: Request,
    response: Response,
) -> AuthStateResponse:
    security: SecurityService = request.app.state.security
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is not None:
        await runtime.invalidate_session(request.state.authenticated_session.id)
    raw_token = request.cookies.get(security.settings.session_cookie_name)
    security.require_sessions().revoke(raw_token)
    clear_session_cookie(response, security.settings)
    logger.info(
        "security_logout_completed",
        extra={"session_id": request.state.authenticated_session.id},
    )
    return AuthStateResponse(state="unauthenticated")
