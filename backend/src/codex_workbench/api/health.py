"""Liveness and readiness probes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from codex_workbench import __version__
from codex_workbench.config import Settings
from codex_workbench.security.service import SecurityService

router = APIRouter(prefix="/api/health", tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok", "not_ready"]
    version: str
    checks: dict[str, bool] | None = None


def _directory_ready(path: Path, *, writable: bool = False) -> bool:
    mode = os.R_OK | os.X_OK
    if writable:
        mode |= os.W_OK
    return path.is_dir() and os.access(path, mode)


@router.get("/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@router.get("/ready", response_model=HealthResponse)
async def ready(request: Request, response: Response) -> HealthResponse:
    settings: Settings = request.app.state.settings
    security: SecurityService = request.app.state.security
    checks = {
        "workspace": _directory_ready(settings.workspace_root),
        "repositories": _directory_ready(settings.repositories_root),
        "data": (
            _directory_ready(settings.data_dir, writable=True)
            if settings.readiness_require_data_dir
            else True
        ),
        "security": security.ready,
        "app_server_safe_default": not settings.app_server_start_enabled,
    }
    if not all(checks.values()):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="not_ready", version=__version__, checks=checks)
    return HealthResponse(status="ok", version=__version__, checks=checks)
