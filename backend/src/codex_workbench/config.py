"""Environment-backed application configuration."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

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
    password_verifier_file: Path = Path("/var/lib/codex-workbench/security/password.argon2id")
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
    max_json_request_bytes: int = Field(default=1024 * 1024, ge=1024, le=10 * 1024 * 1024)
    max_uploads_per_turn: int = Field(default=8, ge=1, le=32)
    max_uploads_per_session: int = Field(default=64, ge=1, le=512)
    max_upload_session_bytes: int = Field(default=100 * 1024 * 1024, ge=1, le=1024 * 1024 * 1024)
    upload_ttl_seconds: int = Field(default=24 * 60 * 60, ge=300, le=30 * 24 * 60 * 60)
    upload_max_dimension: int = Field(default=12000, ge=256, le=50000)
    upload_max_pixels: int = Field(default=40_000_000, ge=65_536, le=250_000_000)
    upload_max_frames: int = Field(default=1, ge=1, le=100)
    codex_executable: str = "codex"
    expected_codex_version: str = "0.146.0"
    app_server_start_enabled: bool = False
    app_server_version_check_timeout_seconds: float = Field(default=15.0, ge=0.1, le=60.0)
    app_server_request_timeout_seconds: float = Field(default=30.0, ge=1.0, le=600.0)
    app_server_shutdown_timeout_seconds: float = Field(default=5.0, ge=0.5, le=60.0)
    app_server_max_line_bytes: int = Field(
        default=4 * 1024 * 1024, ge=64 * 1024, le=32 * 1024 * 1024
    )
    app_server_restart_limit: int = Field(default=5, ge=0, le=50)
    app_server_restart_window_seconds: int = Field(default=300, ge=10, le=3600)
    app_server_backoff_initial_seconds: float = Field(default=0.5, ge=0.05, le=60)
    app_server_backoff_max_seconds: float = Field(default=30.0, ge=0.1, le=300)
    capability_cache_ttl_seconds: float = Field(default=60.0, ge=1.0, le=3600.0)
    capability_failure_cooldown_seconds: float = Field(default=300.0, ge=10.0, le=3600.0)
    realtime_history_size: int = Field(default=512, ge=16, le=10000)
    realtime_client_queue_size: int = Field(default=128, ge=8, le=2048)
    realtime_heartbeat_seconds: int = Field(default=20, ge=5, le=120)
    approval_ttl_seconds: int = Field(default=300, ge=15, le=1800)
    full_access_confirmation_ttl_seconds: int = Field(default=120, ge=15, le=600)
    mutation_rate_limit_requests: int = Field(default=120, ge=10, le=5000)
    mutation_rate_limit_global_requests: int = Field(default=1000, ge=10, le=20000)
    mutation_rate_limit_window_seconds: int = Field(default=60, ge=10, le=3600)
    max_command_output_bytes: int = Field(default=256 * 1024, ge=1024, le=4 * 1024 * 1024)
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
        self.session_secret_file = _absolute_without_resolving_symlinks(self.session_secret_file)

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
        if "\x00" in self.codex_executable:
            raise ValueError("codex_executable contains NUL")
        if self.expected_codex_version != "0.146.0":
            raise ValueError("this build supports exactly Codex 0.146.0")
        if self.app_server_backoff_initial_seconds > self.app_server_backoff_max_seconds:
            raise ValueError("initial app-server backoff must not exceed its maximum")
        if self.max_upload_bytes > self.max_upload_session_bytes:
            raise ValueError("per-file upload limit must not exceed the session byte limit")
        if self.mutation_rate_limit_global_requests < self.mutation_rate_limit_requests:
            raise ValueError("global mutation rate limit must be at least the per-session limit")
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
    def public_scheme(self) -> str:
        return urlsplit(self.canonical_public_origin).scheme

    @property
    def public_authority(self) -> str:
        return urlsplit(self.canonical_public_origin).netloc

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
