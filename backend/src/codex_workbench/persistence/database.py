"""Small explicit SQLite migration and connection boundary."""

from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            token_digest BLOB NOT NULL UNIQUE CHECK(length(token_digest) = 32),
            created_at INTEGER NOT NULL,
            last_seen_at INTEGER NOT NULL,
            idle_expires_at INTEGER NOT NULL,
            absolute_expires_at INTEGER NOT NULL,
            revoked_at INTEGER,
            client_address_digest BLOB CHECK(
                client_address_digest IS NULL OR length(client_address_digest) = 32
            ),
            user_agent_digest BLOB CHECK(
                user_agent_digest IS NULL OR length(user_agent_digest) = 32
            ),
            CHECK(idle_expires_at <= absolute_expires_at)
        );

        CREATE INDEX sessions_expiry_idx
            ON sessions(revoked_at, idle_expires_at, absolute_expires_at);
        """,
    ),
    (
        2,
        """
        CREATE TABLE workbench_threads (
            thread_id TEXT PRIMARY KEY,
            repository_id TEXT NOT NULL,
            repository_relative_path TEXT NOT NULL,
            last_session_id TEXT NOT NULL,
            sandbox_mode TEXT NOT NULL CHECK(
                sandbox_mode IN ('read-only', 'workspace-write', 'danger-full-access')
            ),
            approval_policy TEXT NOT NULL CHECK(
                approval_policy IN ('untrusted', 'on-request', 'never')
            ),
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );

        CREATE INDEX workbench_threads_repository_idx
            ON workbench_threads(repository_id, updated_at DESC);

        CREATE TABLE uploads (
            id TEXT PRIMARY KEY,
            owner_session_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            storage_name TEXT NOT NULL UNIQUE,
            detected_mime TEXT,
            size_bytes INTEGER,
            bytes_received INTEGER NOT NULL DEFAULT 0,
            sha256 TEXT,
            state TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            consumed_at INTEGER,
            error_code TEXT,
            CHECK(bytes_received >= 0),
            CHECK(size_bytes IS NULL OR size_bytes >= 0)
        );

        CREATE INDEX uploads_owner_state_idx
            ON uploads(owner_session_id, state, expires_at);

        CREATE TABLE security_audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            session_id TEXT,
            thread_id TEXT,
            repository_id TEXT,
            occurred_at INTEGER NOT NULL
        );
        """,
    ),
)


class DatabaseError(RuntimeError):
    """Raised when Workbench metadata storage cannot be initialized safely."""


class Database:
    """Own SQLite connection setup and forward-only schema migrations."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        if not self.path.parent.is_dir():
            raise DatabaseError("database parent directory is unavailable")
        if not os.access(self.path.parent, os.R_OK | os.W_OK | os.X_OK):
            raise DatabaseError("database parent directory is not writable")
        if stat.S_IMODE(self.path.parent.stat().st_mode) & 0o077:
            raise DatabaseError("database parent directory permissions are too broad")
        if self.path.is_symlink():
            raise DatabaseError("database path must not be a symlink")

        try:
            with self.connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version INTEGER PRIMARY KEY,
                        applied_at INTEGER NOT NULL DEFAULT (unixepoch())
                    )
                    """
                )
                applied = {
                    int(row[0])
                    for row in connection.execute("SELECT version FROM schema_migrations")
                }
                for version, sql in _MIGRATIONS:
                    if version in applied:
                        continue
                    connection.executescript(
                        f"BEGIN IMMEDIATE;\n{sql}\n"
                        f"INSERT INTO schema_migrations(version) VALUES ({version});\nCOMMIT;"
                    )
        except sqlite3.Error as exc:
            raise DatabaseError("database initialization failed") from exc

        try:
            self.path.chmod(0o600)
        except OSError as exc:
            raise DatabaseError("database permissions could not be restricted") from exc

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
