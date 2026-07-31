from pathlib import Path

import pytest

from codex_workbench.security.paths import (
    PathValidationError,
    resolve_path_within,
    validate_upload_filename,
)


def test_resolves_existing_child(tmp_path: Path) -> None:
    root = tmp_path / "root"
    child = root / "project"
    child.mkdir(parents=True)

    assert resolve_path_within(root, "project") == child


@pytest.mark.parametrize("candidate", ["../outside", "/etc/passwd", "project/../../outside"])
def test_rejects_path_escape(tmp_path: Path, candidate: str) -> None:
    root = tmp_path / "root"
    root.mkdir()

    with pytest.raises(PathValidationError):
        resolve_path_within(root, candidate, must_exist=False)


def test_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PathValidationError, match="escapes"):
        resolve_path_within(root, "escape")


@pytest.mark.parametrize("filename", ["../secret", "folder/file.png", "bad\\name.png", "\x00.png"])
def test_rejects_unsafe_upload_filename(filename: str) -> None:
    with pytest.raises(PathValidationError):
        validate_upload_filename(filename)


def test_normalizes_safe_upload_filename() -> None:
    assert validate_upload_filename("  screenshot 01.png ") == "screenshot 01.png"
