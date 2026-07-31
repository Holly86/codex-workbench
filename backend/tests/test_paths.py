from pathlib import Path

import pytest

from codex_workbench.security.paths import (
    PathValidationError,
    resolve_path_within,
    validate_upload_filename,
)
from codex_workbench.security.redaction import sanitize_browser_text


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


def test_browser_text_redacts_absolute_paths_without_damaging_urls() -> None:
    value = sanitize_browser_text(
        "read /etc/passwd and C:\\Users\\name\\secret.txt; see https://example.test/path",
        known_path="/workspace/repositories/sample",
        known_label="sample",
    )
    assert "/etc/passwd" not in value
    assert "C:\\Users" not in value
    assert "<path:passwd>" in value
    assert "<path:secret.txt>" in value
    assert "https://example.test/path" in value
