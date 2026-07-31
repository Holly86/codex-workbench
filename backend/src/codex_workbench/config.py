"""Environment-backed application configuration."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, HttpUrl, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _absolute_without_resolving_symlinks(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


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
    public_origin: HttpUrl = HttpUrl("https://localhost:8080")
    workspace_root: Path = Path("/workspace")
    repositories_root: Path = Path("/workspace/repositories")
    data_dir: Path = Path("/var/lib/codex-workbench")
    uploads_dir: Path = Path("/var/lib/codex-workbench/uploads")
    database_path: Path = Path("/var/lib/codex-workbench/security/workbench.sqlite3")
    password_verifier_file: Path = Path(
        "/var/lib/codex-workbench/security/password.argon2id"
    )
    session_secret_file: Path = Path("/run/secrets/workbench/session.key")
    session_idle_timeout_seconds: int = Field(default=12 * 60 * 60, ge=300, le=30 * 24 * 60 * 60)
    session_absolute_timeout_seconds: int = Field(
        default=7 * 24 * 60 * 60,
        ge=300,
        le=90 * 24 * 60 * 60,
    )
    session_cookie_secure: bool = True
    session_cookie_samesite: Literal["strict", "lax"] = "strict"
    allow_insecure_http: bool = False
    password_min_length: int = Field(default=12, ge=12, le=128)
    login_rate_limit_attempts: int = Field(default=5, ge=1, le=100)
    login_rate_limit_global_attempts: int = Field(default=30, ge=1, le=1000)
    login_rate_limit_window_seconds: int = Field(default=5 * 60, ge=10, le=24 * 60 * 60)
    login_failure_delay_seconds: float = Field(default=0.25, ge=0, le=2)
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
        self.database_path = _absolute_without_resolving_symlinks(self.database_path)
        self.password_verifier_file = _absolute_without_resolving_symlinks(
            self.password_verifier_file
        )
        # Preserve the configured final path so the key loader can reject a symlink itself.
        self.session_secret_file = _absolute_without_resolving_symlinks(
            self.session_secret_file
        )

        _require_nested(self.repositories_root, self.workspace_root, "repositories_root")
        _require_nested(self.uploads_dir, self.data_dir, "uploads_dir")
        resolved_database_path = _resolved(self.database_path)
        resolved_verifier_path = _resolved(self.password_verifier_file)
        _require_nested(resolved_database_path, self.data_dir, "database_path")
        _require_nested(resolved_verifier_path, self.data_dir, "password_verifier_file")
        _require_nested(resolved_database_path.parent, self.data_dir, "database_path parent")
        _require_nested(
            resolved_verifier_path.parent,
            self.data_dir,
            "password_verifier_file parent",
        )

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
        if self.session_absolute_timeout_seconds < self.session_idle_timeout_seconds:
            raise ValueError(
                "session_absolute_timeout_seconds must be at least session_idle_timeout_seconds"
            )
        if self.login_rate_limit_global_attempts < self.login_rate_limit_attempts:
            raise ValueError(
                "login_rate_limit_global_attempts must be at least login_rate_limit_attempts"
            )

        is_https = self.public_origin.scheme == "https"
        if is_https and not self.session_cookie_secure:
            raise ValueError("HTTPS requires session_cookie_secure")
        if not is_https and (self.session_cookie_secure or not self.allow_insecure_http):
            raise ValueError(
                "HTTP requires session_cookie_secure=false and explicit allow_insecure_http=true"
            )
        return self

    @property
    def canonical_public_origin(self) -> str:
        """Return the configured origin without the URL serializer's root slash."""

        return str(self.public_origin).removesuffix("/")

    @property
    def session_cookie_name(self) -> str:
        """Use the __Host- prefix whenever the Secure cookie contract permits it."""

        if self.session_cookie_secure:
            return "__Host-codex_workbench_session"
        return "codex_workbench_session"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide immutable-by-convention settings instance."""

    return Settings()
