from pathlib import Path

import pytest
from pydantic import ValidationError

from codex_workbench.config import Settings


def test_settings_normalize_and_accept_nested_roots(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    settings = Settings(
        _env_file=None,
        workspace_root=workspace,
        repositories_root=workspace / "repositories",
        data_dir=tmp_path / "data",
        uploads_dir=tmp_path / "data" / "uploads",
    )

    assert settings.workspace_root == workspace.resolve()
    assert settings.repositories_root == (workspace / "repositories").resolve()
    assert settings.max_upload_bytes == 20 * 1024 * 1024
    assert settings.app_server_start_enabled is False


def test_settings_reject_repository_root_outside_workspace(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="repositories_root"):
        Settings(
            _env_file=None,
            workspace_root=tmp_path / "workspace",
            repositories_root=tmp_path / "elsewhere",
            data_dir=tmp_path / "data",
            uploads_dir=tmp_path / "data" / "uploads",
        )


def test_settings_reject_upload_root_outside_data(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="uploads_dir"):
        Settings(
            _env_file=None,
            workspace_root=tmp_path / "workspace",
            repositories_root=tmp_path / "workspace" / "repositories",
            data_dir=tmp_path / "data",
            uploads_dir=tmp_path / "uploads",
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
    with pytest.raises(ValidationError, match="public_origin"):
        Settings(
            _env_file=None,
            public_origin=public_origin,
            workspace_root=tmp_path / "workspace",
            repositories_root=tmp_path / "workspace" / "repositories",
            data_dir=tmp_path / "data",
            uploads_dir=tmp_path / "data" / "uploads",
        )
