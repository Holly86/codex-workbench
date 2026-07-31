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
