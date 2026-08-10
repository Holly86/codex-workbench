"""Composition root for password, persistence, session, and throttling security."""

from __future__ import annotations

import asyncio
import logging
import stat
import time
from collections.abc import Callable

from codex_workbench.config import Settings
from codex_workbench.persistence.database import Database
from codex_workbench.persistence.metadata import MetadataRepository
from codex_workbench.persistence.sessions import SessionRepository
from codex_workbench.security.passwords import PasswordStore
from codex_workbench.security.rate_limit import LoginRateLimiter
from codex_workbench.security.sessions import SessionManager, load_session_secret

logger = logging.getLogger("codex_workbench.security")


class SecurityService:
    """Expose initialized security primitives without a parallel auth path."""

    def __init__(
        self,
        settings: Settings,
        *,
        wall_clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.passwords = PasswordStore(settings.password_verifier_file)
        self.rate_limiter = LoginRateLimiter(
            address_limit=settings.login_rate_limit_attempts,
            global_limit=settings.login_rate_limit_global_attempts,
            window_seconds=settings.login_rate_limit_window_seconds,
            clock=monotonic_clock,
        )
        self._database = Database(settings.database_path)
        self._wall_clock = wall_clock
        self.sessions: SessionManager | None = None
        self.metadata: MetadataRepository | None = None
        self.ready = False
        self.initialization_error: str | None = None

    def initialize(self) -> None:
        if not self.settings.data_dir.is_dir():
            raise RuntimeError("security state root is unavailable")
        security_directories = {
            self.settings.database_path.parent,
            self.settings.password_verifier_file.parent,
        }
        if self.settings.deploy_keys_dir is None:
            raise RuntimeError("deploy key state directory is unavailable")
        security_directories.add(self.settings.deploy_keys_dir)
        for directory in security_directories:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            metadata = directory.lstat()
            if stat.S_ISLNK(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
                raise RuntimeError("security state directory is not restrictive")
        self._database.initialize()
        secret = load_session_secret(self.settings.session_secret_file)
        repository = SessionRepository(self._database)
        self.metadata = MetadataRepository(self._database)
        repository.revoke_expired(now=int(self._wall_clock()))
        self.sessions = SessionManager(
            repository,
            secret,
            idle_timeout_seconds=self.settings.session_idle_timeout_seconds,
            absolute_timeout_seconds=self.settings.session_absolute_timeout_seconds,
            clock=self._wall_clock,
        )
        _ = self.passwords.is_initialized
        self.ready = True
        self.initialization_error = None
        if self.settings.allow_insecure_http:
            logger.warning("security_insecure_http_enabled")

    @property
    def setup_required(self) -> bool:
        return self.ready and not self.passwords.is_initialized

    def require_sessions(self) -> SessionManager:
        if not self.ready or self.sessions is None:
            raise RuntimeError("security service is unavailable")
        return self.sessions

    def require_metadata(self) -> MetadataRepository:
        if not self.ready or self.metadata is None:
            raise RuntimeError("metadata repository is unavailable")
        return self.metadata

    async def initialize_password(self, password: str) -> bool:
        return await asyncio.to_thread(self.passwords.initialize, password)

    async def verify_password(self, password: str) -> bool:
        return await asyncio.to_thread(self.passwords.verify, password)
