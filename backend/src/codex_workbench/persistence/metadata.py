"""SQLite metadata for thread references, uploads, and coarse security audit events."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import cast

from codex_workbench.persistence.database import Database


@dataclass(frozen=True, slots=True)
class ThreadReference:
    thread_id: str
    repository_id: str
    repository_relative_path: str
    last_session_id: str
    sandbox_mode: str
    approval_policy: str
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class UploadRecord:
    id: str
    owner_session_id: str
    display_name: str
    storage_name: str
    detected_mime: str | None
    size_bytes: int | None
    bytes_received: int
    sha256: str | None
    state: str
    created_at: int
    expires_at: int
    consumed_at: int | None
    error_code: str | None


def _upload_from_row(row: sqlite3.Row) -> UploadRecord:
    return UploadRecord(
        id=cast(str, row["id"]),
        owner_session_id=cast(str, row["owner_session_id"]),
        display_name=cast(str, row["display_name"]),
        storage_name=cast(str, row["storage_name"]),
        detected_mime=cast(str | None, row["detected_mime"]),
        size_bytes=cast(int | None, row["size_bytes"]),
        bytes_received=cast(int, row["bytes_received"]),
        sha256=cast(str | None, row["sha256"]),
        state=cast(str, row["state"]),
        created_at=cast(int, row["created_at"]),
        expires_at=cast(int, row["expires_at"]),
        consumed_at=cast(int | None, row["consumed_at"]),
        error_code=cast(str | None, row["error_code"]),
    )


class MetadataRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def upsert_thread(self, reference: ThreadReference) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO workbench_threads(
                    thread_id, repository_id, repository_relative_path, last_session_id,
                    sandbox_mode, approval_policy, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(thread_id) DO UPDATE SET
                    repository_id = excluded.repository_id,
                    repository_relative_path = excluded.repository_relative_path,
                    last_session_id = excluded.last_session_id,
                    sandbox_mode = excluded.sandbox_mode,
                    approval_policy = excluded.approval_policy,
                    updated_at = excluded.updated_at
                """,
                (
                    reference.thread_id,
                    reference.repository_id,
                    reference.repository_relative_path,
                    reference.last_session_id,
                    reference.sandbox_mode,
                    reference.approval_policy,
                    reference.created_at,
                    reference.updated_at,
                ),
            )

    def get_thread(self, thread_id: str) -> ThreadReference | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM workbench_threads WHERE thread_id = ?", (thread_id,)
            ).fetchone()
        if row is None:
            return None
        return ThreadReference(
            thread_id=cast(str, row["thread_id"]),
            repository_id=cast(str, row["repository_id"]),
            repository_relative_path=cast(str, row["repository_relative_path"]),
            last_session_id=cast(str, row["last_session_id"]),
            sandbox_mode=cast(str, row["sandbox_mode"]),
            approval_policy=cast(str, row["approval_policy"]),
            created_at=cast(int, row["created_at"]),
            updated_at=cast(int, row["updated_at"]),
        )

    def list_threads(self) -> tuple[ThreadReference, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM workbench_threads ORDER BY updated_at DESC"
            ).fetchall()
        return tuple(
            ThreadReference(
                thread_id=cast(str, row["thread_id"]),
                repository_id=cast(str, row["repository_id"]),
                repository_relative_path=cast(str, row["repository_relative_path"]),
                last_session_id=cast(str, row["last_session_id"]),
                sandbox_mode=cast(str, row["sandbox_mode"]),
                approval_policy=cast(str, row["approval_policy"]),
                created_at=cast(int, row["created_at"]),
                updated_at=cast(int, row["updated_at"]),
            )
            for row in rows
        )

    def repository_preferences(self, repository_id: str) -> dict[str, str | None] | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT relative_path, display_name, model, reasoning_effort "
                "FROM repository_preferences WHERE repository_id = ?",
                (repository_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "relative_path": cast(str, row["relative_path"]),
            "display_name": cast(str | None, row["display_name"]),
            "model": cast(str | None, row["model"]),
            "reasoning_effort": cast(str | None, row["reasoning_effort"]),
        }

    def upsert_repository_preferences(
        self,
        repository_id: str,
        relative_path: str,
        *,
        display_name: str | None,
        model: str | None,
        reasoning_effort: str | None,
        updated_at: int,
    ) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO repository_preferences(
                    repository_id, relative_path, display_name, model, reasoning_effort, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(repository_id) DO UPDATE SET
                    relative_path = excluded.relative_path,
                    display_name = excluded.display_name,
                    model = excluded.model,
                    reasoning_effort = excluded.reasoning_effort,
                    updated_at = excluded.updated_at
                """,
                (
                    repository_id,
                    relative_path,
                    display_name,
                    model,
                    reasoning_effort,
                    updated_at,
                ),
            )

    def create_upload(self, record: UploadRecord) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO uploads(
                    id, owner_session_id, display_name, storage_name, detected_mime,
                    size_bytes, bytes_received, sha256, state, created_at, expires_at,
                    consumed_at, error_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.owner_session_id,
                    record.display_name,
                    record.storage_name,
                    record.detected_mime,
                    record.size_bytes,
                    record.bytes_received,
                    record.sha256,
                    record.state,
                    record.created_at,
                    record.expires_at,
                    record.consumed_at,
                    record.error_code,
                ),
            )

    def update_upload(
        self,
        upload_id: str,
        *,
        state: str,
        bytes_received: int,
        detected_mime: str | None = None,
        size_bytes: int | None = None,
        sha256: str | None = None,
        consumed_at: int | None = None,
        error_code: str | None = None,
    ) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                UPDATE uploads SET state = ?, bytes_received = ?, detected_mime = ?,
                    size_bytes = ?, sha256 = ?, consumed_at = ?, error_code = ?
                WHERE id = ?
                """,
                (
                    state,
                    bytes_received,
                    detected_mime,
                    size_bytes,
                    sha256,
                    consumed_at,
                    error_code,
                    upload_id,
                ),
            )

    def get_upload(self, upload_id: str) -> UploadRecord | None:
        with self._database.connect() as connection:
            row = connection.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone()
        return None if row is None else _upload_from_row(row)

    def session_upload_totals(self, session_id: str, now: int) -> tuple[int, int]:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(size_bytes), 0)
                FROM uploads
                WHERE owner_session_id = ? AND expires_at > ?
                  AND state IN ('staging', 'validating', 'ready')
                """,
                (session_id, now),
            ).fetchone()
        if row is None:
            return (0, 0)
        return cast(int, row[0]), cast(int, row[1])

    def expired_uploads(self, now: int) -> tuple[UploadRecord, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM uploads
                WHERE expires_at <= ? AND state != 'expired'
                """,
                (now,),
            ).fetchall()
        return tuple(_upload_from_row(row) for row in rows)

    def audit(
        self,
        event_type: str,
        *,
        occurred_at: int,
        session_id: str | None = None,
        thread_id: str | None = None,
        repository_id: str | None = None,
    ) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO security_audit_events(
                    event_type, session_id, thread_id, repository_id, occurred_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (event_type, session_id, thread_id, repository_id, occurred_at),
            )
