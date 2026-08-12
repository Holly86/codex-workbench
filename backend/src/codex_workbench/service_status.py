"""Safe, typed operator-facing component health models."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ServiceState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    RECOVERING = "recovering"
    BLOCKED = "blocked"
    FAILED = "failed"
    UNKNOWN = "unknown"


type ComponentName = Literal[
    "backend_http",
    "codex_app_server",
    "realtime_websocket",
    "sqlite_metadata",
    "repository_root",
    "upload_storage",
    "background_jobs",
    "plugin_discovery",
]


class ComponentStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component: ComponentName
    state: ServiceState
    observedAt: int
    errorCode: str | None = None
    lastSuccessAt: int | None = None
    lastRecoveryAt: int | None = None
    nextRecoveryAt: int | None = None
    generation: int | None = None
    restartAttempts: int | None = None
    restartLimit: int | None = None
    activeCount: int | None = None
    queuedCount: int | None = None
    failedCount: int | None = None
    connectionCount: int | None = None
    optional: bool = False
    enabled: bool = True


class SystemStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ServiceState
    workbenchVersion: str
    codexVersion: str | None
    observedAt: int
    components: tuple[ComponentStatus, ...]
