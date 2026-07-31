"""Short-lived one-use Full Access confirmation capabilities."""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime


class FullAccessConfirmationError(RuntimeError):
    pass


@dataclass(slots=True)
class FullAccessConfirmation:
    id: str
    session_id: str
    repository_id: str
    scope_id: str
    approval_policy: str
    expires_at: float
    consumed: bool = False

    @property
    def expires_at_iso(self) -> str:
        return datetime.fromtimestamp(self.expires_at, UTC).isoformat()


class FullAccessRegistry:
    def __init__(self, *, ttl_seconds: int = 120) -> None:
        self._ttl = ttl_seconds
        self._records: dict[str, FullAccessConfirmation] = {}
        self._lock = asyncio.Lock()

    async def issue(
        self,
        *,
        session_id: str,
        repository_id: str,
        scope_id: str,
        approval_policy: str,
    ) -> FullAccessConfirmation:
        now = time.time()
        record = FullAccessConfirmation(
            id="full_" + secrets.token_urlsafe(24),
            session_id=session_id,
            repository_id=repository_id,
            scope_id=scope_id,
            approval_policy=approval_policy,
            expires_at=now + self._ttl,
        )
        async with self._lock:
            self._records[record.id] = record
        return record

    async def consume(
        self,
        confirmation_id: str | None,
        *,
        session_id: str,
        repository_id: str,
        scope_id: str,
        approval_policy: str,
    ) -> None:
        if confirmation_id is None:
            raise FullAccessConfirmationError("full access confirmation is required")
        async with self._lock:
            record = self._records.get(confirmation_id)
            valid = (
                record is not None
                and not record.consumed
                and record.expires_at > time.time()
                and record.session_id == session_id
                and record.repository_id == repository_id
                and record.scope_id == scope_id
                and record.approval_policy == approval_policy
            )
            if not valid or record is None:
                raise FullAccessConfirmationError("full access confirmation is invalid")
            record.consumed = True

    async def invalidate_session(self, session_id: str) -> None:
        async with self._lock:
            for record in self._records.values():
                if record.session_id == session_id:
                    record.consumed = True
