"""Conservative text redaction for browser-visible server path fragments."""

from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath

_UNIX_ABSOLUTE = re.compile(r"(?<![/:A-Za-z0-9_])/(?:[^\s'\"`<>|;]+)")
_WINDOWS_ABSOLUTE = re.compile(r"(?i)\b[A-Z]:\\(?:[^\s'\"`<>|;]+)")


def sanitize_browser_text(
    value: str,
    *,
    known_path: str | None = None,
    known_label: str = "repository",
) -> str:
    if known_path:
        value = value.replace(known_path, known_label)

    def unix_label(match: re.Match[str]) -> str:
        name = PurePosixPath(match.group(0)).name
        return f"<path:{name}>" if name else "<absolute-path>"

    def windows_label(match: re.Match[str]) -> str:
        name = PureWindowsPath(match.group(0)).name
        return f"<path:{name}>" if name else "<absolute-path>"

    return _WINDOWS_ABSOLUTE.sub(windows_label, _UNIX_ABSOLUTE.sub(unix_label, value))
