"""Opaque cookie sessions backed by keyed digests in SQLite."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from codex_workbench.persistence.sessions import SessionRecord, SessionRepository


class SessionConfigurationError(RuntimeError):
    """Raised when the session secret cannot be loaded safely."""


class SessionValidation(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    id: str
    csrf_token: str
    expires_at: int


@dataclass(frozen=True, slots=True)
class SessionAuthentication:
    status: SessionValidation
    session: AuthenticatedSession | None = None


@dataclass(frozen=True, slots=True)
class IssuedSession:
    token: str
    authenticated: AuthenticatedSession


def load_session_secret(path: Path) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SessionConfigurationError("session secret file is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SessionConfigurationError("session secret must be a regular non-symlink file")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise SessionConfigurationError("session secret permissions are too broad")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SessionConfigurationError("session secret could not be opened safely") from exc
    try:
        opened_metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_metadata.st_mode)
            or stat.S_IMODE(opened_metadata.st_mode) & 0o077
        ):
            raise SessionConfigurationError("session secret file is not restrictive")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            secret = handle.read(257).strip()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not 32 <= len(secret) <= 256:
        raise SessionConfigurationError("session secret must contain 32 to 256 bytes")
    return secret


class SessionManager:
    """Issue, rotate, authenticate, expire, and revoke server-side sessions."""

    def __init__(
        self,
        repository: SessionRepository,
        secret: bytes,
        *,
        idle_timeout_seconds: int,
        absolute_timeout_seconds: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repository = repository
        self._secret = secret
        self._idle_timeout = idle_timeout_seconds
        self._absolute_timeout = absolute_timeout_seconds
        self._clock = clock

    def issue(
        self,
        *,
        previous_token: str | None,
        client_address: str | None,
        user_agent: str | None,
    ) -> IssuedSession:
        now = int(self._clock())
        if previous_token:
            self._repository.revoke_by_digest(self._token_digest(previous_token), now=now)

        token = secrets.token_urlsafe(32)
        absolute_expires_at = now + self._absolute_timeout
        idle_expires_at = min(now + self._idle_timeout, absolute_expires_at)
        record = self._repository.create(
            session_id=secrets.token_urlsafe(18),
            token_digest=self._token_digest(token),
            now=now,
            idle_expires_at=idle_expires_at,
            absolute_expires_at=absolute_expires_at,
            client_address_digest=self._metadata_digest("address", client_address),
            user_agent_digest=self._metadata_digest("user-agent", user_agent),
        )
        return IssuedSession(token=token, authenticated=self._authenticated(record, token))

    def authenticate(self, token: str | None) -> SessionAuthentication:
        if token is None or not 20 <= len(token) <= 512:
            return SessionAuthentication(SessionValidation.INVALID)
        record = self._repository.get_by_digest(self._token_digest(token))
        if record is None or record.revoked_at is not None:
            return SessionAuthentication(SessionValidation.INVALID)

        now = int(self._clock())
        if record.idle_expires_at <= now or record.absolute_expires_at <= now:
            self._repository.revoke_by_id(record.id, now=now)
            return SessionAuthentication(SessionValidation.EXPIRED)

        idle_expires_at = min(now + self._idle_timeout, record.absolute_expires_at)
        self._repository.touch(record.id, now=now, idle_expires_at=idle_expires_at)
        updated = replace(record, last_seen_at=now, idle_expires_at=idle_expires_at)
        return SessionAuthentication(
            SessionValidation.VALID,
            self._authenticated(updated, token),
        )

    def validate_csrf(self, token: str, supplied: str | None) -> bool:
        if supplied is None or len(supplied) > 512:
            return False
        expected = self._csrf_token(token)
        return hmac.compare_digest(expected, supplied)

    def revoke(self, token: str | None) -> bool:
        if token is None or len(token) > 512:
            return False
        return self._repository.revoke_by_digest(
            self._token_digest(token),
            now=int(self._clock()),
        )

    def fingerprint(self, value: str | None) -> str:
        digest = self._metadata_digest("rate-limit", value or "unknown")
        if digest is None:
            raise AssertionError("a non-null value must produce a digest")
        return digest.hex()

    def _token_digest(self, token: str) -> bytes:
        return hmac.new(self._secret, b"session\0" + token.encode(), hashlib.sha256).digest()

    def _csrf_token(self, token: str) -> str:
        digest = hmac.new(self._secret, b"csrf\0" + token.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def _metadata_digest(self, namespace: str, value: str | None) -> bytes | None:
        if value is None:
            return None
        return hmac.new(
            self._secret,
            namespace.encode() + b"\0" + value.encode(errors="replace"),
            hashlib.sha256,
        ).digest()

    def _authenticated(self, record: SessionRecord, token: str) -> AuthenticatedSession:
        return AuthenticatedSession(
            id=record.id,
            csrf_token=self._csrf_token(token),
            expires_at=min(record.idle_expires_at, record.absolute_expires_at),
        )
