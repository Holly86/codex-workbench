#!/usr/bin/env python3
"""Secrets-safe scan of the working tree and every object reachable from local refs."""

from __future__ import annotations

import math
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator

ROOT = Path(__file__).resolve().parents[1]
SKIPPED_CURRENT_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    ".venv",
    "dist",
    "coverage",
}
SENSITIVE_ROOTS = {
    ".codex",
    ".ssh",
    "data",
    "uploads",
    "secrets",
    "security",
    "state",
    "logs",
    "cache",
    "workspace",
    "transfers",
    "tmp",
}
SENSITIVE_FILENAMES = {
    "auth.json",
    "config.json",
    "id_rsa",
    "id_ed25519",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}
PLACEHOLDER_VALUES = {
    "changeme",
    "dummy",
    "example",
    "fake",
    "fake-token",
    "not-a-secret",
    "password",
    "redacted",
    "replace-me",
    "secret",
    "secret-value",
    "test",
    "test-password",
    "token",
}


@dataclass(frozen=True)
class Finding:
    category: str
    path: str
    status: str
    commit: str | None = None


SIGNATURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private-key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ),
    ("openai-api-key", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b")),
    (
        "github-token",
        re.compile(r"\b(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,})\b"),
    ),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("stripe-live-key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b")),
    ("password-verifier", re.compile(r"\$argon2id\$v=\d+\$m=\d+,t=\d+,p=\d+\$")),
    (
        "bearer-token",
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{24,}", re.IGNORECASE),
    ),
)

CONTEXTUAL_SECRET = re.compile(
    r"(?im)\b(OPENAI_API_KEY|DOCKERHUB_TOKEN|GITHUB_TOKEN|GITLAB_TOKEN|"
    r"AWS_SECRET_ACCESS_KEY|CLIENT_SECRET|SESSION_SECRET|COOKIE_SECRET|"
    r"ACCESS_TOKEN|REFRESH_TOKEN|PRIVATE_KEY|PASSWORD_VERIFIER)\b"
    r"\s*[:=]\s*([\"']?)([^\s\"',}#]+)\2"
)
URL_CREDENTIAL = re.compile(
    r"(?i)\b[a-z][a-z0-9+.-]*://([^\s/:]+):([^\s/@]+)@([^\s/:]+)"
)
ENV_SENSITIVE_KEY = re.compile(
    r"(?:^|_)(?:API_KEY|TOKEN|SECRET|PASSWORD|PASSWD|VERIFIER|PRIVATE_KEY|CLIENT_SECRET)$"
)


def run(*args: str, input_bytes: bytes | None = None) -> bytes:
    return subprocess.check_output(args, cwd=ROOT, input=input_bytes, stderr=subprocess.DEVNULL)


def is_placeholder(value: str) -> bool:
    normalized = value.strip().strip("\"'").lower()
    if not normalized:
        return True
    if normalized in PLACEHOLDER_VALUES:
        return True
    if normalized.startswith(("${", "<", "replace-", "example-", "test-", "fake-")):
        return True
    if normalized.endswith((".invalid", ".example", ".test")):
        return True
    return "[redacted]" in normalized or "placeholder" in normalized


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def sensitive_path(path: str, *, tracked: bool) -> str | None:
    pure = PurePosixPath(path)
    parts = pure.parts
    lowered = tuple(part.lower() for part in parts)
    name = pure.name.lower()

    if parts and lowered[0] in SENSITIVE_ROOTS:
        return "sensitive-runtime-path"
    if len(lowered) >= 2 and lowered[:2] in {("config", ".codex"), ("docker", "config.json")}:
        return "credential-file"
    if name in SENSITIVE_FILENAMES or name.startswith("credentials"):
        return "credential-file"
    if pure.suffix.lower() in SENSITIVE_SUFFIXES:
        return "private-key-or-certificate"
    if tracked and name == ".env":
        return "tracked-env-file"
    if tracked and name.startswith(".env.") and name != ".env.example":
        return "tracked-env-file"
    return None


def scan_env(path: str, text: str) -> str | None:
    name = PurePosixPath(path).name.lower()
    if name != ".env" and not name.startswith(".env."):
        return None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        upper_key = key.strip().upper()
        if upper_key.endswith(("_PATH", "_FILE", "_DIR")):
            continue
        if ENV_SENSITIVE_KEY.search(upper_key) and not is_placeholder(value):
            return "env-secret"
    return None


def scan_text(path: str, data: bytes) -> str | None:
    text = data.decode("utf-8", errors="ignore")
    env_finding = scan_env(path, text)
    if env_finding:
        return env_finding

    for category, pattern in SIGNATURES:
        for match in pattern.finditer(text):
            value = match.group(0)
            if not is_placeholder(value):
                return category

    for match in CONTEXTUAL_SECRET.finditer(text):
        value = match.group(3)
        if is_placeholder(value) or value.startswith(("${", "$", "{{")):
            continue
        if len(value) >= 16 and shannon_entropy(value) >= 3.0:
            return "assigned-secret"

    for match in URL_CREDENTIAL.finditer(text):
        username, password, host = match.groups()
        if host.lower() in {"example.com", "example.test", "example.invalid"}:
            continue
        if username.lower() == "user" and is_placeholder(password):
            continue
        return "credential-url"
    return None


def tracked_paths() -> set[str]:
    output = run("git", "ls-files", "-z")
    return {item.decode("utf-8", errors="surrogateescape") for item in output.split(b"\0") if item}


def current_files() -> Iterator[tuple[str, bytes, bool]]:
    tracked = tracked_paths()
    for directory, dirs, files in os.walk(ROOT):
        relative_dir = Path(directory).relative_to(ROOT)
        dirs[:] = [
            name
            for name in dirs
            if name not in SKIPPED_CURRENT_DIRS
            and not (relative_dir == Path("frontend") and name == "dist")
        ]
        for filename in files:
            absolute = Path(directory) / filename
            relative = absolute.relative_to(ROOT).as_posix()
            try:
                yield relative, absolute.read_bytes(), relative in tracked
            except OSError:
                yield relative, b"", relative in tracked


def scan_current() -> Finding | None:
    for path, data, tracked in current_files():
        category = sensitive_path(path, tracked=tracked)
        if category:
            return Finding(category, path, "current")
        category = scan_text(path, data)
        if category:
            return Finding(category, path, "current")
    return None


def read_batch_blob(stream: BinaryIO, output: BinaryIO, object_id: str) -> bytes:
    stream.write((object_id + "\n").encode())
    stream.flush()
    header = output.readline().decode("ascii", errors="replace").strip().split()
    if len(header) != 3 or header[1] != "blob":
        raise RuntimeError("unexpected git object")
    size = int(header[2])
    data = output.read(size)
    if output.read(1) != b"\n":
        raise RuntimeError("invalid git batch framing")
    return data


def history_entries() -> Iterator[tuple[str, str, bytes]]:
    if run("git", "rev-parse", "--is-shallow-repository").strip() == b"true":
        raise RuntimeError("shallow history")
    commits = run("git", "rev-list", "--all").decode().splitlines()
    seen: set[tuple[str, str]] = set()
    cache: dict[str, bytes] = {}
    process = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if process.stdin is None or process.stdout is None:
        raise RuntimeError("git batch unavailable")
    try:
        for commit in commits:
            tree = run("git", "ls-tree", "-r", "-z", "--full-tree", commit)
            for entry in tree.split(b"\0"):
                if not entry:
                    continue
                metadata, raw_path = entry.split(b"\t", 1)
                _, object_type, object_id_bytes = metadata.split()
                if object_type != b"blob":
                    continue
                path = raw_path.decode("utf-8", errors="surrogateescape")
                object_id = object_id_bytes.decode("ascii")
                key = (object_id, path)
                if key in seen:
                    continue
                seen.add(key)
                if object_id not in cache:
                    cache[object_id] = read_batch_blob(process.stdin, process.stdout, object_id)
                yield commit, path, cache[object_id]
    finally:
        process.stdin.close()
        process.stdout.close()
        process.wait(timeout=10)


def scan_history() -> Finding | None:
    for commit, path, data in history_entries():
        category = sensitive_path(path, tracked=True)
        if category:
            return Finding(category, path, "history", commit)
        category = scan_text(path, data)
        if category:
            return Finding(category, path, "history", commit)
    return None


def print_finding(finding: Finding) -> None:
    print("BLOCKED:")
    print(f"type={finding.category}")
    print(f"path={finding.path}")
    print(f"status={finding.status}")
    if finding.commit:
        print(f"commit={finding.commit}")


def main() -> int:
    try:
        mode = sys.argv[1] if len(sys.argv) == 2 else "--all"
        if len(sys.argv) > 2 or mode not in {"--all", "--current", "--history"}:
            raise ValueError("invalid mode")
        if mode in {"--all", "--current"}:
            finding = scan_current()
            if finding:
                print_finding(finding)
                return 1
        if mode in {"--all", "--history"}:
            finding = scan_history()
            if finding:
                print_finding(finding)
                return 1
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
        print_finding(Finding("scanner-error", ".", "current"))
        return 2
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
