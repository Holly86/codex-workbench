"""Argon2id password verifier storage outside SQLite."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError


class PasswordStoreError(RuntimeError):
    """Raised when the verifier file cannot be used safely."""


def _assert_restrictive_regular_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PasswordStoreError("password verifier metadata is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PasswordStoreError("password verifier must be a regular non-symlink file")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise PasswordStoreError("password verifier permissions are too broad")


def _read_restrictive_file(path: Path, *, max_bytes: int) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PasswordStoreError("password verifier could not be opened safely") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise PasswordStoreError("password verifier file is not restrictive")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            return handle.read(max_bytes + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


class PasswordStore:
    """Create once and verify an encoded Argon2id password hash."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._hasher = PasswordHasher(
            time_cost=3,
            memory_cost=64 * 1024,
            parallelism=4,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )
        self._dummy_verifier = self._hasher.hash(os.urandom(32))

    @property
    def is_initialized(self) -> bool:
        if not self._path.exists():
            return False
        _assert_restrictive_regular_file(self._path)
        self._read_verifier()
        return True

    def initialize(self, password: str) -> bool:
        """Write the first verifier atomically; return false after initial setup."""

        encoded = self._hasher.hash(password)
        parent = self._path.parent
        try:
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if stat.S_IMODE(parent.stat().st_mode) & 0o077:
                raise PasswordStoreError("password verifier directory permissions are too broad")
        except OSError as exc:
            raise PasswordStoreError("password verifier directory is unavailable") from exc

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self._path, flags, 0o600)
        except FileExistsError:
            return False
        except OSError as exc:
            raise PasswordStoreError("password verifier could not be created") from exc

        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            _assert_restrictive_regular_file(self._path)
        except BaseException:
            self._path.unlink(missing_ok=True)
            raise
        return True

    def verify(self, password: str) -> bool:
        verifier = self._read_verifier() if self.is_initialized else self._dummy_verifier
        try:
            return self._hasher.verify(verifier, password)
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            return False

    def _read_verifier(self) -> str:
        _assert_restrictive_regular_file(self._path)
        raw = _read_restrictive_file(self._path, max_bytes=1024)
        if len(raw) > 1024:
            raise PasswordStoreError("password verifier is oversized")
        try:
            verifier = raw.decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise PasswordStoreError("password verifier encoding is invalid") from exc
        if not verifier.startswith("$argon2id$"):
            raise PasswordStoreError("password verifier is not Argon2id")
        return verifier
