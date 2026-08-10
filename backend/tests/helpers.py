from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path
from typing import Literal

from codex_workbench.config import Settings

_EXECUTABLE_FIXTURE_ROOT = Path(
    tempfile.mkdtemp(prefix=".exec-fixtures-", dir=Path(__file__).parent)
)
atexit.register(shutil.rmtree, _EXECUTABLE_FIXTURE_ROOT, ignore_errors=True)


def executable_fixture(name: str, body: str, *, executable: bool = True) -> Path:
    """Create executable fixtures on the repository mount, not a potentially noexec /tmp."""
    path = _EXECUTABLE_FIXTURE_ROOT / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8", newline="\n")
    path.chmod(0o700 if executable else 0o600)
    return path


def build_settings(
    tmp_path: Path,
    *,
    create_data: bool = True,
    environment: Literal["test", "production"] = "test",
    public_origin: str = "http://testserver",
    secure_cookie: bool = False,
    login_attempts: int = 5,
    global_login_attempts: int = 30,
    idle_timeout: int = 3600,
    absolute_timeout: int = 7200,
) -> Settings:
    workspace = tmp_path / "workspace"
    repositories = workspace / "repositories"
    data = tmp_path / "data"
    secrets = tmp_path / "secrets"
    repositories.mkdir(parents=True, exist_ok=True)
    if create_data:
        data.mkdir(exist_ok=True)
        (data / "security").mkdir(mode=0o700, exist_ok=True)
    secrets.mkdir(exist_ok=True)
    session_secret = secrets / "session.key"
    session_secret.write_text("a" * 64, encoding="ascii")
    session_secret.chmod(0o600)
    return Settings(
        _env_file=None,
        environment=environment,
        public_origin=public_origin,
        workspace_root=workspace,
        repositories_root=repositories,
        data_dir=data,
        uploads_dir=data / "uploads",
        database_path=data / "security" / "workbench.sqlite3",
        password_verifier_file=data / "security" / "password.argon2id",
        session_secret_file=session_secret,
        session_idle_timeout_seconds=idle_timeout,
        session_absolute_timeout_seconds=absolute_timeout,
        session_cookie_secure=secure_cookie,
        allow_insecure_http=not secure_cookie,
        login_rate_limit_attempts=login_attempts,
        login_rate_limit_global_attempts=global_login_attempts,
        login_failure_delay_seconds=0,
    )
