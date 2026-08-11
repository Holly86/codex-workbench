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


@dataclass(frozen=True, slots=True)
class DeployKeyRecord:
    credential_id: str
    repository_id: str | None
    github_slug: str
    active_created_at: int
    candidate_created_at: int | None
    retired_created_at: int | None
    last_tested_at: int | None
    last_success_at: int | None
    last_test_succeeded: bool | None
    last_test_key: str | None
    created_at: int
    updated_at: int


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


def _deploy_key_from_row(row: sqlite3.Row) -> DeployKeyRecord:
    tested = cast(int | None, row["last_test_succeeded"])
    return DeployKeyRecord(
        credential_id=cast(str, row["credential_id"]),
        repository_id=cast(str | None, row["repository_id"]),
        github_slug=cast(str, row["github_slug"]),
        active_created_at=cast(int, row["active_created_at"]),
        candidate_created_at=cast(int | None, row["candidate_created_at"]),
        retired_created_at=cast(int | None, row["retired_created_at"]),
        last_tested_at=cast(int | None, row["last_tested_at"]),
        last_success_at=cast(int | None, row["last_success_at"]),
        last_test_succeeded=None if tested is None else bool(tested),
        last_test_key=cast(str | None, row["last_test_key"]),
        created_at=cast(int, row["created_at"]),
        updated_at=cast(int, row["updated_at"]),
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

    def list_repository_threads(self, repository_id: str) -> tuple[ThreadReference, ...]:
        return tuple(
            reference for reference in self.list_threads()
            if reference.repository_id == repository_id
        )

    def delete_thread(self, thread_id: str) -> None:
        with self._database.connect() as connection:
            connection.execute("DELETE FROM workbench_threads WHERE thread_id = ?", (thread_id,))

    def create_deploy_key(self, record: DeployKeyRecord) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO deploy_key_credentials(
                    credential_id, repository_id, github_slug, active_created_at,
                    candidate_created_at, retired_created_at, last_tested_at,
                    last_success_at, last_test_succeeded, last_test_key, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.credential_id,
                    record.repository_id,
                    record.github_slug,
                    record.active_created_at,
                    record.candidate_created_at,
                    record.retired_created_at,
                    record.last_tested_at,
                    record.last_success_at,
                    None if record.last_test_succeeded is None else int(record.last_test_succeeded),
                    record.last_test_key,
                    record.created_at,
                    record.updated_at,
                ),
            )

    def deploy_key_by_credential(self, credential_id: str) -> DeployKeyRecord | None:
        return self._deploy_key_where("credential_id", credential_id)

    def deploy_key_by_repository(self, repository_id: str) -> DeployKeyRecord | None:
        return self._deploy_key_where("repository_id", repository_id)

    def deploy_key_by_slug(self, github_slug: str) -> DeployKeyRecord | None:
        return self._deploy_key_where("github_slug", github_slug)

    def _deploy_key_where(self, field: str, value: str) -> DeployKeyRecord | None:
        if field not in {"credential_id", "repository_id", "github_slug"}:
            raise ValueError("invalid deploy key lookup")
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT * FROM deploy_key_credentials WHERE {field} = ?", (value,)
            ).fetchone()
        return None if row is None else _deploy_key_from_row(row)

    def bind_deploy_key(self, credential_id: str, repository_id: str, *, updated_at: int) -> None:
        with self._database.connect() as connection:
            cursor = connection.execute(
                "UPDATE deploy_key_credentials SET repository_id = ?, updated_at = ? "
                "WHERE credential_id = ? AND repository_id IS NULL",
                (repository_id, updated_at, credential_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("deploy key cannot be bound")

    def update_deploy_key_test(
        self,
        credential_id: str,
        *,
        key_kind: str,
        succeeded: bool,
        tested_at: int,
    ) -> None:
        if key_kind not in {"active", "candidate"}:
            raise ValueError("invalid deploy key kind")
        with self._database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE deploy_key_credentials
                SET last_tested_at = ?,
                    last_success_at = CASE WHEN ? THEN ? ELSE last_success_at END,
                    last_test_succeeded = ?, last_test_key = ?, updated_at = ?
                WHERE credential_id = ?
                """,
                (
                    tested_at,
                    int(succeeded),
                    tested_at,
                    int(succeeded),
                    key_kind,
                    tested_at,
                    credential_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("deploy key is unavailable")

    def begin_deploy_key_rotation(self, credential_id: str, *, created_at: int) -> None:
        with self._database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE deploy_key_credentials
                SET candidate_created_at = ?, last_tested_at = NULL,
                    last_test_succeeded = NULL, last_test_key = NULL, updated_at = ?
                WHERE credential_id = ? AND candidate_created_at IS NULL
                """,
                (created_at, created_at, credential_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("deploy key rotation is already pending")

    def promote_deploy_key_candidate(self, credential_id: str, *, promoted_at: int) -> None:
        with self._database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE deploy_key_credentials
                SET retired_created_at = active_created_at,
                    active_created_at = candidate_created_at,
                    candidate_created_at = NULL,
                    last_tested_at = ?, last_success_at = ?, last_test_succeeded = 1,
                    last_test_key = 'active', updated_at = ?
                WHERE credential_id = ? AND candidate_created_at IS NOT NULL
                    AND last_test_succeeded = 1 AND last_test_key = 'candidate'
                """,
                (promoted_at, promoted_at, promoted_at, credential_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("deploy key candidate has not been verified")

    def clear_deploy_key_retired(self, credential_id: str, *, updated_at: int) -> None:
        with self._database.connect() as connection:
            connection.execute(
                "UPDATE deploy_key_credentials SET retired_created_at = NULL, updated_at = ? "
                "WHERE credential_id = ?",
                (updated_at, credential_id),
            )

    def delete_deploy_key(self, credential_id: str) -> None:
        with self._database.connect() as connection:
            connection.execute(
                "DELETE FROM deploy_key_credentials WHERE credential_id = ?", (credential_id,)
            )

    def hidden_repository_ids(self) -> frozenset[str]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT repository_id FROM repository_registrations"
            ).fetchall()
        return frozenset(cast(str, row[0]) for row in rows)

    def repository_registration_state(self, repository_id: str) -> str | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT state FROM repository_registrations WHERE repository_id = ?",
                (repository_id,),
            ).fetchone()
        return None if row is None else cast(str, row[0])

    def mark_repository_unavailable(
        self, repository_id: str, relative_path: str, *, state: str, updated_at: int
    ) -> None:
        if state not in {"removed", "deleted"}:
            raise ValueError("invalid repository registration state")
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO repository_registrations(
                    repository_id, relative_path, state, updated_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(repository_id) DO UPDATE SET
                    relative_path = excluded.relative_path,
                    state = excluded.state,
                    updated_at = excluded.updated_at
                """,
                (repository_id, relative_path, state, updated_at),
            )

    def mark_repository_available(self, repository_id: str, relative_path: str) -> bool:
        """Remove only the exact historical tombstone for a verified repository."""
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT relative_path FROM repository_registrations WHERE repository_id = ?",
                (repository_id,),
            ).fetchone()
            if row is None:
                return False
            if cast(str, row["relative_path"]) != relative_path:
                raise ValueError("repository registration path does not match")
            cursor = connection.execute(
                "DELETE FROM repository_registrations "
                "WHERE repository_id = ? AND relative_path = ?",
                (repository_id, relative_path),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("repository registration could not be reactivated")
        return True

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
