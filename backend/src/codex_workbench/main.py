"""FastAPI application factory."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse

from codex_workbench import __version__
from codex_workbench.api.auth import router as auth_router
from codex_workbench.api.health import router as health_router
from codex_workbench.api.realtime import router as realtime_router
from codex_workbench.api.system import router as system_router
from codex_workbench.api.uploads import router as uploads_router
from codex_workbench.api.workbench import router as workbench_router
from codex_workbench.config import Settings, get_settings
from codex_workbench.logging import configure_logging
from codex_workbench.middleware import ApiBoundaryMiddleware, RequestBodyLimitMiddleware
from codex_workbench.runtime import WorkbenchRuntime
from codex_workbench.security.http import SecurityBoundaryMiddleware, api_error
from codex_workbench.security.service import SecurityService


def create_app(
    settings: Settings | None = None,
    *,
    wall_clock: Callable[[], float] = time.time,
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    effective_settings = settings or get_settings()
    configure_logging(effective_settings.log_level)
    logger = logging.getLogger("codex_workbench.lifecycle")
    expose_api_schema = effective_settings.environment != "production"
    security = SecurityService(
        effective_settings,
        wall_clock=wall_clock,
        monotonic_clock=monotonic_clock,
    )

    @asynccontextmanager
    async def lifespan(lifespan_app: FastAPI) -> AsyncIterator[None]:
        runtime: WorkbenchRuntime | None = None
        try:
            security.initialize()
        except RuntimeError as exc:
            security.initialization_error = type(exc).__name__
            logger.error(
                "security_initialization_failed",
                extra={"error_type": type(exc).__name__},
            )
        if security.ready:
            try:
                runtime = WorkbenchRuntime(effective_settings, security.require_metadata())
                lifespan_app.state.runtime = runtime
                await runtime.start()
            except Exception as exc:
                logger.error(
                    "runtime_initialization_failed",
                    extra={"error_type": type(exc).__name__},
                )
        logger.info(
            "application_started",
            extra={
                "environment": effective_settings.environment,
                "app_server_enabled": effective_settings.app_server_start_enabled,
                "security_ready": security.ready,
            },
        )
        yield
        if runtime is not None:
            await runtime.stop()
        logger.info("application_stopped")

    app = FastAPI(
        title="Codex Workbench API",
        version=__version__,
        docs_url="/api/docs" if expose_api_schema else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if expose_api_schema else None,
        lifespan=lifespan,
    )
    app.state.settings = effective_settings
    app.state.security = security
    app.state.runtime = None
    app.add_middleware(SecurityBoundaryMiddleware)
    app.add_middleware(ApiBoundaryMiddleware)
    app.add_middleware(RequestBodyLimitMiddleware)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(realtime_router)
    app.include_router(system_router)
    app.include_router(uploads_router)
    app.include_router(workbench_router)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, __: RequestValidationError) -> JSONResponse:
        return api_error(
            422,
            "validation_failed",
            "Die Anfrage ist ungültig.",
        )

    return app


app = create_app()
