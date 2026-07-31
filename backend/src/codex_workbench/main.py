"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from codex_workbench import __version__
from codex_workbench.api.health import router as health_router
from codex_workbench.config import Settings, get_settings
from codex_workbench.logging import configure_logging
from codex_workbench.middleware import ApiBoundaryMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    effective_settings = settings or get_settings()
    configure_logging(effective_settings.log_level)
    logger = logging.getLogger("codex_workbench.lifecycle")
    expose_api_schema = effective_settings.environment != "production"

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "application_started",
            extra={
                "environment": effective_settings.environment,
                "app_server_enabled": effective_settings.app_server_start_enabled,
            },
        )
        yield
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
    app.add_middleware(ApiBoundaryMiddleware)
    app.include_router(health_router)
    return app


app = create_app()
