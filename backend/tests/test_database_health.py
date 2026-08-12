from pathlib import Path

from codex_workbench.persistence.database import Database


def test_database_sets_explicit_busy_timeout_and_quick_check(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.sqlite3", busy_timeout_ms=4321)
    with database.connect() as connection:
        connection.execute("CREATE TABLE example (id INTEGER PRIMARY KEY)")
        timeout = connection.execute("PRAGMA busy_timeout").fetchone()
    assert timeout is not None and timeout[0] == 4321
    assert database.quick_check() is True


def test_database_quick_check_fails_closed_for_invalid_storage(tmp_path: Path) -> None:
    path = tmp_path / "broken.sqlite3"
    path.write_bytes(b"not a sqlite database")
    assert Database(path).quick_check() is False
