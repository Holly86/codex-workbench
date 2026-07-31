"""Private, authenticated image upload lifecycle and localImage resolution."""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import stat
import time
from pathlib import Path

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError

from codex_workbench.config import Settings
from codex_workbench.persistence.metadata import MetadataRepository, UploadRecord
from codex_workbench.security.paths import resolve_path_within, validate_upload_filename

_FORMAT_MIME = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}


class UploadValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class UploadUnavailableError(ValueError):
    pass


class UploadService:
    def __init__(self, settings: Settings, repository: MetadataRepository) -> None:
        self._settings = settings
        self._repository = repository
        self._session_store_locks: dict[str, asyncio.Lock] = {}

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_directory)
        await self.cleanup_expired()

    async def store(self, upload: UploadFile, *, session_id: str) -> UploadRecord:
        lock = self._session_store_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            return await self._store(upload, session_id=session_id)

    async def _store(self, upload: UploadFile, *, session_id: str) -> UploadRecord:
        await self.cleanup_expired()
        display_name = validate_upload_filename(upload.filename or "screenshot")
        now = int(time.time())
        count, total = await asyncio.to_thread(
            self._repository.session_upload_totals, session_id, now
        )
        if count >= self._settings.max_uploads_per_session:
            raise UploadValidationError("upload_count_exceeded", "Zu viele aktive Uploads.")
        if total >= self._settings.max_upload_session_bytes:
            raise UploadValidationError(
                "upload_quota_exceeded", "Das Upload-Kontingent ist erreicht."
            )

        upload_id = "upload_" + secrets.token_urlsafe(24)
        storage_name = secrets.token_hex(24)
        path = self._settings.uploads_dir / storage_name
        record = UploadRecord(
            id=upload_id,
            owner_session_id=session_id,
            display_name=display_name,
            storage_name=storage_name,
            detected_mime=None,
            size_bytes=None,
            bytes_received=0,
            sha256=None,
            state="staging",
            created_at=now,
            expires_at=now + self._settings.upload_ttl_seconds,
            consumed_at=None,
            error_code=None,
        )
        await asyncio.to_thread(self._repository.create_upload, record)
        bytes_received = 0
        digest = hashlib.sha256()
        descriptor = -1
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(path, flags, 0o600)
            while True:
                chunk = await upload.read(256 * 1024)
                if not chunk:
                    break
                bytes_received += len(chunk)
                if bytes_received > self._settings.max_upload_bytes:
                    raise UploadValidationError(
                        "upload_too_large", "Die Bilddatei überschreitet das Größenlimit."
                    )
                if total + bytes_received > self._settings.max_upload_session_bytes:
                    raise UploadValidationError(
                        "upload_quota_exceeded", "Das Upload-Kontingent ist erreicht."
                    )
                digest.update(chunk)
                await asyncio.to_thread(self._write_all, descriptor, chunk)
            if bytes_received == 0:
                raise UploadValidationError("upload_empty", "Die Bilddatei ist leer.")
            await asyncio.to_thread(os.fsync, descriptor)
            os.close(descriptor)
            descriptor = -1
            await asyncio.to_thread(
                self._repository.update_upload,
                upload_id,
                state="validating",
                bytes_received=bytes_received,
                size_bytes=bytes_received,
            )
            detected_mime = await asyncio.to_thread(self._validate_image, path)
            await asyncio.to_thread(
                self._repository.update_upload,
                upload_id,
                state="ready",
                bytes_received=bytes_received,
                detected_mime=detected_mime,
                size_bytes=bytes_received,
                sha256=digest.hexdigest(),
            )
            stored = await asyncio.to_thread(self._repository.get_upload, upload_id)
            if stored is None:
                raise UploadUnavailableError("upload metadata disappeared")
            return stored
        except asyncio.CancelledError:
            await self._reject(upload_id, path, bytes_received, "upload_cancelled")
            raise
        except UploadValidationError as exc:
            await self._reject(upload_id, path, bytes_received, exc.code)
            raise
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            await self._reject(upload_id, path, bytes_received, "upload_invalid_image")
            raise UploadValidationError(
                "upload_invalid_image", "Die Bilddatei konnte nicht sicher verarbeitet werden."
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            await upload.close()

    async def resolve_images(
        self,
        descriptors: tuple[tuple[str, str], ...],
        *,
        session_id: str,
    ) -> tuple[tuple[str, str], ...]:
        if len(descriptors) > self._settings.max_uploads_per_turn:
            raise UploadUnavailableError("too many images for one turn")
        resolved: list[tuple[str, str]] = []
        for upload_id, detail in descriptors:
            if detail not in {"auto", "low", "high", "original"}:
                raise UploadUnavailableError("image detail is invalid")
            record = await asyncio.to_thread(self._repository.get_upload, upload_id)
            if (
                record is None
                or record.owner_session_id != session_id
                or record.state != "ready"
                or record.expires_at <= int(time.time())
                or record.sha256 is None
                or record.size_bytes is None
            ):
                raise UploadUnavailableError("upload is unavailable")
            path = await asyncio.to_thread(self._revalidate_ready_file, record)
            resolved.append((str(path), detail))
        return tuple(resolved)

    async def mark_consumed(self, upload_ids: tuple[str, ...], *, session_id: str) -> None:
        now = int(time.time())
        for upload_id in upload_ids:
            record = await asyncio.to_thread(self._repository.get_upload, upload_id)
            if (
                record is not None
                and record.owner_session_id == session_id
                and record.state == "ready"
            ):
                await asyncio.to_thread(
                    self._repository.update_upload,
                    upload_id,
                    state="consumed",
                    bytes_received=record.bytes_received,
                    detected_mime=record.detected_mime,
                    size_bytes=record.size_bytes,
                    sha256=record.sha256,
                    consumed_at=now,
                )

    async def remove(self, upload_id: str, *, session_id: str) -> None:
        record = await asyncio.to_thread(self._repository.get_upload, upload_id)
        if record is None or record.owner_session_id != session_id:
            raise UploadUnavailableError("upload is unavailable")
        path = self._storage_entry_path(record.storage_name)
        if os.path.lexists(path):
            metadata = path.lstat()
            if stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                path.unlink()
        await asyncio.to_thread(
            self._repository.update_upload,
            upload_id,
            state="expired",
            bytes_received=record.bytes_received,
            detected_mime=record.detected_mime,
            size_bytes=record.size_bytes,
            sha256=record.sha256,
            error_code="removed",
        )

    async def cleanup_expired(self) -> None:
        records = await asyncio.to_thread(self._repository.expired_uploads, int(time.time()))
        for record in records:
            try:
                await self.remove(record.id, session_id=record.owner_session_id)
            except (OSError, UploadUnavailableError):
                continue

    async def _reject(
        self, upload_id: str, path: Path, bytes_received: int, error_code: str
    ) -> None:
        try:
            path.unlink(missing_ok=True)
        finally:
            await asyncio.to_thread(
                self._repository.update_upload,
                upload_id,
                state="rejected",
                bytes_received=bytes_received,
                error_code=error_code,
            )

    def _initialize_directory(self) -> None:
        self._settings.uploads_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = self._settings.uploads_dir.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError("uploads directory is not a regular directory")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise RuntimeError("uploads directory permissions are too broad")

    @staticmethod
    def _write_all(descriptor: int, chunk: bytes) -> None:
        offset = 0
        while offset < len(chunk):
            written = os.write(descriptor, chunk[offset:])
            if written <= 0:
                raise OSError("upload file write did not make progress")
            offset += written

    def _validate_image(self, path: Path) -> str:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                with Image.open(handle) as image:
                    image_format = image.format
                    if image_format not in _FORMAT_MIME:
                        raise UploadValidationError(
                            "upload_type_rejected", "Dieses Bildformat wird nicht unterstützt."
                        )
                    width, height = image.size
                    if (
                        width <= 0
                        or height <= 0
                        or width > self._settings.upload_max_dimension
                        or height > self._settings.upload_max_dimension
                        or width * height > self._settings.upload_max_pixels
                    ):
                        raise UploadValidationError(
                            "upload_dimensions_exceeded", "Die Bildabmessungen sind zu groß."
                        )
                    frames = int(getattr(image, "n_frames", 1))
                    if frames > self._settings.upload_max_frames:
                        raise UploadValidationError(
                            "upload_animation_rejected",
                            "Animierte oder mehrteilige Bilder werden nicht unterstützt.",
                        )
                    image.verify()
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        # Reopen and decode pixels; verify() alone does not force full decoding.
        descriptor = os.open(path, flags)
        try:
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                with Image.open(handle) as image:
                    image.load()
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if image_format is None:
            raise UploadValidationError("upload_invalid_image", "Das Bildformat fehlt.")
        return _FORMAT_MIME[image_format]

    def _revalidate_ready_file(self, record: UploadRecord) -> Path:
        path = self._contained_storage_path(record.storage_name, must_exist=True)
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise UploadUnavailableError("upload file is unsafe")
        if stat.S_IMODE(metadata.st_mode) & 0o077 or metadata.st_size != record.size_bytes:
            raise UploadUnavailableError("upload file metadata changed")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size != record.size_bytes:
                raise UploadUnavailableError("upload file changed during validation")
            digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 256 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            if digest.hexdigest() != record.sha256:
                raise UploadUnavailableError("upload digest changed")
        finally:
            os.close(descriptor)
        return path

    def _contained_storage_path(self, storage_name: str, *, must_exist: bool) -> Path:
        if len(storage_name) != 48 or any(
            character not in "0123456789abcdef" for character in storage_name
        ):
            raise UploadUnavailableError("upload storage identity is invalid")
        try:
            return resolve_path_within(
                self._settings.uploads_dir,
                storage_name,
                must_exist=must_exist,
            )
        except ValueError as exc:
            raise UploadUnavailableError("upload storage escaped containment") from exc

    def _storage_entry_path(self, storage_name: str) -> Path:
        if len(storage_name) != 48 or any(
            character not in "0123456789abcdef" for character in storage_name
        ):
            raise UploadUnavailableError("upload storage identity is invalid")
        root = self._settings.uploads_dir.resolve(strict=True)
        metadata = root.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise UploadUnavailableError("upload root is unsafe")
        return root / storage_name
