"""Path and filename validation for untrusted browser input."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path


class PathValidationError(ValueError):
    """Raised when a path would cross an allowed root boundary."""


def resolve_path_within(
    root: Path,
    untrusted_relative_path: str,
    *,
    must_exist: bool = True,
    allow_root: bool = False,
) -> Path:
    """Resolve a browser-supplied relative path below a trusted root.

    Existing symlinks are resolved before the containment check, so a symlink
    cannot be used to escape the root.
    """

    if not untrusted_relative_path or "\x00" in untrusted_relative_path:
        raise PathValidationError("path must not be empty or contain NUL")

    relative = Path(untrusted_relative_path)
    if relative.is_absolute():
        raise PathValidationError("absolute paths are not accepted")

    try:
        resolved_root = root.resolve(strict=True)
        candidate = (resolved_root / relative).resolve(strict=must_exist)
    except (OSError, RuntimeError) as exc:
        raise PathValidationError("path cannot be resolved") from exc

    if candidate == resolved_root:
        if allow_root:
            return candidate
        raise PathValidationError("the root itself is not an allowed selection")
    if not candidate.is_relative_to(resolved_root):
        raise PathValidationError("path escapes its allowed root")
    return candidate


_UNSAFE_FILENAME = re.compile(r"[\\/\x00-\x1f\x7f]")


def validate_upload_filename(filename: str, *, max_length: int = 180) -> str:
    """Normalize a display filename; storage names must still be server-generated."""

    normalized = unicodedata.normalize("NFKC", filename).strip()
    if not normalized or normalized in {".", ".."}:
        raise PathValidationError("filename is empty or reserved")
    if len(normalized) > max_length:
        raise PathValidationError("filename is too long")
    if _UNSAFE_FILENAME.search(normalized):
        raise PathValidationError("filename contains a path separator or control character")
    return normalized
