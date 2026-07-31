"""Environment-backed application configuration."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, HttpUrl, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _require_nested(child: Path, parent: Path, label: str) -> None:
    if child == parent or not child.is_relative_to(parent):
        raise ValueError(f"{label} must be a strict descendant of {parent}")


class Settings(BaseSettings):
    """Validated settings. Secrets are intentionally absent from this model."""

    model_config = SettingsConfigDict(
        env_prefix="WORKBENCH_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    public_origin: HttpUrl = HttpUrl("http://localhost:8080")
    workspace_root: Path = Path("/workspace")
    repositories_root: Path = Path("/workspace/repositories")
    data_dir: Path = Path("/var/lib/codex-workbench")
    uploads_dir: Path = Path("/var/lib/codex-workbench/uploads")
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1, le=100 * 1024 * 1024)
    codex_executable: str = "codex"
    app_server_start_enabled: bool = False
    readiness_require_data_dir: bool = True

    @model_validator(mode="after")
    def validate_path_boundaries(self) -> Self:
        self.workspace_root = _resolved(self.workspace_root)
        self.repositories_root = _resolved(self.repositories_root)
        self.data_dir = _resolved(self.data_dir)
        self.uploads_dir = _resolved(self.uploads_dir)

        _require_nested(self.repositories_root, self.workspace_root, "repositories_root")
        _require_nested(self.uploads_dir, self.data_dir, "uploads_dir")

        if (
            self.public_origin.username is not None
            or self.public_origin.password is not None
            or self.public_origin.query is not None
            or self.public_origin.fragment is not None
            or self.public_origin.path not in {None, "", "/"}
        ):
            raise ValueError("public_origin must be an HTTP origin without credentials or URL data")
        if not self.codex_executable.strip():
            raise ValueError("codex_executable must not be empty")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide immutable-by-convention settings instance."""

    return Settings()
