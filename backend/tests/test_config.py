from pathlib import Path

import pytest
from pydantic import ValidationError

from codex_workbench.config import Settings


def test_settings_normalize_and_accept_nested_roots(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    data = tmp_path / "data"
    settings = Settings(
        _env_file=None,
        workspace_root=workspace,
        repositories_root=workspace / "repositories",
        data_dir=data,
        uploads_dir=data / "uploads",
        database_path=data / "security" / "workbench.sqlite3",
        password_verifier_file=data / "security" / "password.argon2id",
    )

    assert settings.workspace_root == workspace.resolve()
    assert settings.repositories_root == (workspace / "repositories").resolve()
    assert settings.max_upload_bytes == 20 * 1024 * 1024
    assert settings.app_server_start_enabled is False
    assert settings.git_command_timeout_seconds == 30
    assert settings.git_network_timeout_seconds == 300
    assert settings.git_clone_timeout_seconds == 900


@pytest.mark.parametrize(
    ("command_timeout", "network_timeout", "clone_timeout"),
    [
        (4, 300, 900),
        (301, 300, 900),
        (30, 29, 900),
        (30, 1801, 900),
        (30, 300, 59),
        (30, 300, 3601),
    ],
)
def test_settings_reject_git_timeouts_outside_safe_bounds(
    tmp_path: Path,
    command_timeout: float,
    network_timeout: float,
    clone_timeout: float,
) -> None:
    workspace = tmp_path / "workspace"
    data = tmp_path / "data"
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            workspace_root=workspace,
            repositories_root=workspace / "repositories",
            data_dir=data,
            uploads_dir=data / "uploads",
            database_path=data / "security" / "workbench.sqlite3",
            password_verifier_file=data / "security" / "password.argon2id",
            git_command_timeout_seconds=command_timeout,
            git_network_timeout_seconds=network_timeout,
            git_clone_timeout_seconds=clone_timeout,
        )


def test_settings_reject_repository_root_outside_workspace(tmp_path: Path) -> None:
    data = tmp_path / "data"
    with pytest.raises(ValidationError, match="repositories_root"):
        Settings(
            _env_file=None,
            workspace_root=tmp_path / "workspace",
            repositories_root=tmp_path / "elsewhere",
            data_dir=data,
            uploads_dir=data / "uploads",
            database_path=data / "security" / "workbench.sqlite3",
            password_verifier_file=data / "security" / "password.argon2id",
        )


def test_settings_reject_upload_root_outside_data(tmp_path: Path) -> None:
    data = tmp_path / "data"
    with pytest.raises(ValidationError, match="uploads_dir"):
        Settings(
            _env_file=None,
            workspace_root=tmp_path / "workspace",
            repositories_root=tmp_path / "workspace" / "repositories",
            data_dir=data,
            uploads_dir=tmp_path / "uploads",
            database_path=data / "security" / "workbench.sqlite3",
            password_verifier_file=data / "security" / "password.argon2id",
        )


@pytest.mark.parametrize(
    "public_origin",
    [
        "https://workbench.example/path",
        "https://user@workbench.example",
        "https://workbench.example?source=untrusted",
        "https://workbench.example#fragment",
    ],
)
def test_settings_reject_non_origin_public_urls(tmp_path: Path, public_origin: str) -> None:
    data = tmp_path / "data"
    with pytest.raises(ValidationError, match="public_origin"):
        Settings(
            _env_file=None,
            public_origin=public_origin,
            workspace_root=tmp_path / "workspace",
            repositories_root=tmp_path / "workspace" / "repositories",
            data_dir=data,
            uploads_dir=data / "uploads",
            database_path=data / "security" / "workbench.sqlite3",
            password_verifier_file=data / "security" / "password.argon2id",
        )


def test_settings_require_explicit_insecure_http_mode(tmp_path: Path) -> None:
    data = tmp_path / "data"
    with pytest.raises(ValidationError, match="HTTP requires"):
        Settings(
            _env_file=None,
            public_origin="http://workbench.example",
            session_cookie_secure=False,
            workspace_root=tmp_path / "workspace",
            repositories_root=tmp_path / "workspace" / "repositories",
            data_dir=data,
            uploads_dir=data / "uploads",
            database_path=data / "security" / "workbench.sqlite3",
            password_verifier_file=data / "security" / "password.argon2id",
        )


def test_settings_reject_insecure_cookie_for_https(tmp_path: Path) -> None:
    data = tmp_path / "data"
    with pytest.raises(ValidationError, match="HTTPS requires"):
        Settings(
            _env_file=None,
            public_origin="https://workbench.example",
            session_cookie_secure=False,
            allow_insecure_http=True,
            workspace_root=tmp_path / "workspace",
            repositories_root=tmp_path / "workspace" / "repositories",
            data_dir=data,
            uploads_dir=data / "uploads",
            database_path=data / "security" / "workbench.sqlite3",
            password_verifier_file=data / "security" / "password.argon2id",
        )
