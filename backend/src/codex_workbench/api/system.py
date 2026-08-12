"""Authenticated operator status and bounded recovery actions."""

from __future__ import annotations

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from codex_workbench.api.workbench import _mutation_allowed, _safe_error
from codex_workbench.runtime import RuntimeUnavailableError, WorkbenchRuntime
from codex_workbench.security.sessions import AuthenticatedSession
from codex_workbench.service_status import SystemStatus

router = APIRouter(prefix="/api/system", tags=["system"])


class RuntimeRestartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: Annotated[str, Field(min_length=1, max_length=64)]


def _runtime(request: Request) -> WorkbenchRuntime:
    runtime = getattr(request.app.state, "runtime", None)
    if not isinstance(runtime, WorkbenchRuntime):
        raise RuntimeUnavailableError("workbench runtime is unavailable")
    return runtime


def _session(request: Request) -> AuthenticatedSession:
    session = getattr(request.state, "authenticated_session", None)
    if not isinstance(session, AuthenticatedSession):
        raise RuntimeUnavailableError("authenticated session is unavailable")
    return session


@router.get("/status", response_model=SystemStatus)
async def system_status(request: Request) -> SystemStatus | JSONResponse:
    try:
        return await _runtime(request).system_status()
    except Exception as exc:
        return _safe_error(exc)


@router.post("/runtime/restart")
async def restart_runtime(payload: RuntimeRestartRequest, request: Request) -> JSONResponse:
    try:
        runtime, session = _runtime(request), _session(request)
        limited = _mutation_allowed(runtime, session.id)
        if limited is not None:
            return limited
        result = await runtime.restart_app_server(session.id, payload.confirmation)
        return JSONResponse(
            {"status": "accepted", "result": result},
            status_code=HTTPStatus.ACCEPTED,
        )
    except Exception as exc:
        return _safe_error(exc)
