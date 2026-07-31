"""Server-side session metadata repository."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import cast

from codex_workbench.persistence.database import Database


@dataclass(frozen=True, slots=True)
class SessionRecord:
    id: str
    token_digest: bytes
    created_at: int
    last_seen_at: int
    idle_expires_at: int
    absolute_expires_at: int
    revoked_at: int | None


def _record_from_row(row: sqlite3.Row) -> SessionRecord:
    return SessionRecord(
        id=cast(str, row["id"]),
        token_digest=cast(bytes, row["token_digest"]),
        created_at=cast(int, row["created_at"]),
        last_seen_at=cast(int, row["last_seen_at"]),
        idle_expires_at=cast(int, row["idle_expires_at"]),
        absolute_expires_at=cast(int, row["absolute_expires_at"]),
        revoked_at=cast(int | None, row["revoked_at"]),
    )


class SessionRepository:
    """Persist only non-reusable session digests and lifecycle metadata."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def create(
        self,
        *,
        session_id: str,
        token_digest: bytes,
        now: int,
        idle_expires_at: int,
        absolute_expires_at: int,
        client_address_digest: bytes | None,
        user_agent_digest: bytes | None,
    ) -> SessionRecord:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions(
                    id, token_digest, created_at, last_seen_at, idle_expires_at,
                    absolute_expires_at, client_address_digest, user_agent_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    token_digest,
                    now,
                    now,
                    idle_expires_at,
                    absolute_expires_at,
                    client_address_digest,
                    user_agent_digest,
                ),
            )
        return SessionRecord(
            id=session_id,
            token_digest=token_digest,
            created_at=now,
            last_seen_at=now,
            idle_expires_at=idle_expires_at,
            absolute_expires_at=absolute_expires_at,
            revoked_at=None,
        )

    def get_by_digest(self, token_digest: bytes) -> SessionRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT id, token_digest, created_at, last_seen_at, idle_expires_at,
                       absolute_expires_at, revoked_at
                FROM sessions
                WHERE token_digest = ?
                """,
                (token_digest,),
            ).fetchone()
        return None if row is None else _record_from_row(row)

    def touch(self, session_id: str, *, now: int, idle_expires_at: int) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                UPDATE sessions
                SET last_seen_at = ?, idle_expires_at = ?
                WHERE id = ? AND revoked_at IS NULL
                """,
                (now, idle_expires_at, session_id),
            )

    def revoke_by_digest(self, token_digest: bytes, *, now: int) -> bool:
        with self._database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE sessions
                SET revoked_at = ?
                WHERE token_digest = ? AND revoked_at IS NULL
                """,
                (now, token_digest),
            )
        return cursor.rowcount == 1

    def revoke_by_id(self, session_id: str, *, now: int) -> None:
        with self._database.connect() as connection:
            connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (now, session_id),
            )

    def revoke_expired(self, *, now: int) -> int:
        with self._database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE sessions
                SET revoked_at = ?
                WHERE revoked_at IS NULL
                  AND (idle_expires_at <= ? OR absolute_expires_at <= ?)
                """,
                (now, now, now),
            )
        return cursor.rowcount
