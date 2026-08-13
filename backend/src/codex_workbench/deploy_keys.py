"""Repository-scoped GitHub deploy-key lifecycle and SSH execution policy."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import os
import re
import secrets
import shutil
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from codex_workbench.config import Settings
from codex_workbench.persistence.metadata import DeployKeyRecord, MetadataRepository

_SLUG_PART = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})$")
_CREDENTIAL_ID = re.compile(r"^deploy_[A-Za-z0-9_-]{32,100}$")


class DeployKeyError(RuntimeError):
    """Safe deploy-key failure whose message is never returned to the browser."""


class DeployKeyRuntimeUnavailableError(DeployKeyError):
    """The local OpenSSH runtime cannot execute safely."""


class DeployKeyGenerationError(DeployKeyError):
    """A deploy-key pair could not be generated."""


class DeployKeyAccessError(DeployKeyError):
    """A deploy-key access probe could not be started."""


class InvalidGitHubRepositoryError(ValueError):
    """A repository identifier is not a supported GitHub repository."""


class DeployKeyAlreadyBoundError(ValueError):
    """A private-connect credential already belongs to a local repository."""


@dataclass(frozen=True, slots=True)
class GitHubRepository:
    slug: str

    @property
    def ssh_url(self) -> str:
        return f"git@github.com:{self.slug}.git"


def normalize_github_repository(value: str) -> GitHubRepository:
    """Accept only github.com repository identifiers without embedded credentials."""
    candidate = value.strip()
    if not candidate or len(candidate) > 2048 or any(char in candidate for char in "\x00\r\n"):
        raise InvalidGitHubRepositoryError("invalid GitHub repository")
    path = candidate
    if "://" in candidate:
        parsed = urlsplit(candidate)
        if parsed.scheme not in {"https", "ssh"} or parsed.hostname != "github.com":
            raise InvalidGitHubRepositoryError("only github.com repositories are supported")
        if parsed.password is not None or (
            parsed.scheme == "https" and parsed.username is not None
        ):
            raise InvalidGitHubRepositoryError(
                "credentials must not be embedded in repository URLs"
            )
        if parsed.query or parsed.fragment:
            raise InvalidGitHubRepositoryError("repository URL data is not supported")
        if parsed.scheme == "ssh" and parsed.username not in {None, "git"}:
            raise InvalidGitHubRepositoryError("GitHub SSH user must be git")
        path = unquote(parsed.path).strip("/")
    else:
        scp = re.fullmatch(r"git@github\.com:([^\s]+)", candidate)
        if scp is not None:
            path = scp.group(1)
        elif ":" in candidate or "@" in candidate:
            raise InvalidGitHubRepositoryError("only github.com repositories are supported")
    path = path.removesuffix(".git").strip("/")
    parts = path.split("/")
    if len(parts) != 2 or not all(_SLUG_PART.fullmatch(part) for part in parts):
        raise InvalidGitHubRepositoryError("GitHub repository must be OWNER/REPO")
    return GitHubRepository(f"{parts[0].casefold()}/{parts[1].casefold()}")


class DeployKeyService:
    """Own private key files; SQLite stores only browser-safe lifecycle metadata."""

    def __init__(self, settings: Settings, metadata: MetadataRepository) -> None:
        if settings.deploy_keys_dir is None:
            raise RuntimeError("deploy key directory is not configured")
        self._root = settings.deploy_keys_dir
        self._workspace_root = settings.workspace_root
        self._metadata = metadata
        security_dir = Path(__file__).parent / "security"
        self._known_hosts = security_dir / "github_known_hosts"
        self._ssh_wrapper = security_dir / "repository_ssh_wrapper"

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        if self._root.is_symlink():
            raise RuntimeError("deploy key directory must not be a symlink")
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root = self._root.resolve(strict=True)
        if root.is_relative_to(self._workspace_root.resolve(strict=True)):
            raise RuntimeError("deploy key directory must be outside the workspace")
        metadata = self._root.lstat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError("deploy key path is not a directory")
        os.chmod(self._root, 0o700)
        if not self._known_hosts.is_file() or self._known_hosts.is_symlink():
            raise RuntimeError("GitHub known-hosts policy is unavailable")
        if not self._ssh_wrapper.is_file() or self._ssh_wrapper.is_symlink():
            raise RuntimeError("repository SSH wrapper is unavailable")

    async def create(self, github: str, *, repository_id: str | None) -> dict[str, object]:
        repository = normalize_github_repository(github)
        existing_slug = await asyncio.to_thread(
            self._metadata.deploy_key_by_slug, repository.slug
        )
        if existing_slug is not None:
            raise ValueError("a deploy key already exists for this GitHub repository")
        if repository_id is not None:
            existing_repository = await asyncio.to_thread(
                self._metadata.deploy_key_by_repository, repository_id
            )
            if existing_repository is not None:
                raise ValueError("this repository already has a deploy key")
        now = int(time.time())
        record = DeployKeyRecord(
            credential_id="deploy_" + secrets.token_urlsafe(32),
            repository_id=repository_id,
            github_slug=repository.slug,
            active_created_at=now,
            candidate_created_at=None,
            retired_created_at=None,
            last_tested_at=None,
            last_success_at=None,
            last_test_succeeded=None,
            last_test_key=None,
            created_at=now,
            updated_at=now,
        )
        slot = await asyncio.to_thread(self._create_slot, record)
        try:
            await self._generate_pair(slot / "active", repository.slug)
            await asyncio.to_thread(self._metadata.create_deploy_key, record)
        except BaseException:
            await asyncio.to_thread(self._remove_slot, slot)
            raise
        return self.browser_dict(record)

    async def connect_private(self, github: str) -> tuple[dict[str, object], bool]:
        """Create or safely resume one unbound credential for a normalized GitHub slug."""
        repository = normalize_github_repository(github)
        existing = await asyncio.to_thread(
            self._metadata.deploy_key_by_slug, repository.slug
        )
        if existing is not None:
            if existing.repository_id is not None:
                raise DeployKeyAlreadyBoundError("deploy key is already bound")
            return self.browser_dict(existing), False
        return await self.create(repository.slug, repository_id=None), True

    async def view_for_repository(self, repository_id: str) -> dict[str, object] | None:
        record = await asyncio.to_thread(
            self._metadata.deploy_key_by_repository, repository_id
        )
        return None if record is None else self.browser_dict(record)

    async def view_for_credential(self, credential_id: str) -> dict[str, object]:
        record = await self._require_credential(credential_id)
        return self.browser_dict(record)

    async def test_access(
        self, credential_id: str, *, candidate: bool = False
    ) -> dict[str, object]:
        record = await self._require_credential(credential_id)
        key_kind = "candidate" if candidate else "active"
        if candidate and record.candidate_created_at is None:
            raise ValueError("no deploy key rotation is pending")
        identity = self._key_path(record, key_kind)
        environment = self._ssh_environment(identity)
        try:
            process = await asyncio.create_subprocess_exec(
                "git",
                "ls-remote",
                "--",
                GitHubRepository(record.github_slug).ssh_url,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=environment,
            )
        except OSError as exc:
            raise DeployKeyAccessError("deploy key access probe unavailable") from exc
        try:
            await asyncio.wait_for(process.wait(), timeout=30)
        except TimeoutError:
            process.kill()
            await process.wait()
            succeeded = False
        else:
            succeeded = process.returncode == 0
        tested_at = int(time.time())
        await asyncio.to_thread(
            self._metadata.update_deploy_key_test,
            credential_id,
            key_kind=key_kind,
            succeeded=succeeded,
            tested_at=tested_at,
        )
        current = await self._require_credential(credential_id)
        return {"succeeded": succeeded, "deployKey": self.browser_dict(current)}

    async def rotate(self, credential_id: str) -> dict[str, object]:
        record = await self._require_credential(credential_id)
        if record.candidate_created_at is not None:
            raise ValueError("deploy key rotation is already pending")
        slot = self._slot(record)
        candidate = slot / "candidate"
        now = int(time.time())
        await self._generate_pair(candidate, record.github_slug)
        try:
            await asyncio.to_thread(
                self._metadata.begin_deploy_key_rotation, credential_id, created_at=now
            )
        except BaseException:
            self._unlink_pair(candidate)
            raise
        return self.browser_dict(await self._require_credential(credential_id))

    async def promote_rotation(self, credential_id: str) -> dict[str, object]:
        record = await self._require_credential(credential_id)
        if (
            record.candidate_created_at is None
            or record.last_test_key != "candidate"
            or record.last_test_succeeded is not True
        ):
            raise ValueError("deploy key candidate has not been verified")
        slot = self._slot(record)
        retired = slot / "retired"
        if retired.exists() or retired.with_suffix(".pub").exists():
            raise ValueError("remove the previous retired key before promoting another")
        (slot / "active").replace(retired)
        (slot / "active.pub").replace(retired.with_suffix(".pub"))
        (slot / "candidate").replace(slot / "active")
        (slot / "candidate.pub").replace(slot / "active.pub")
        now = int(time.time())
        await asyncio.to_thread(
            self._metadata.promote_deploy_key_candidate, credential_id, promoted_at=now
        )
        return self.browser_dict(await self._require_credential(credential_id))

    async def remove_retired(self, credential_id: str) -> dict[str, object]:
        record = await self._require_credential(credential_id)
        if record.retired_created_at is None:
            raise ValueError("no retired deploy key exists")
        retired = self._slot(record) / "retired"
        self._unlink_pair(retired)
        await asyncio.to_thread(
            self._metadata.clear_deploy_key_retired,
            credential_id,
            updated_at=int(time.time()),
        )
        return self.browser_dict(await self._require_credential(credential_id))

    async def remove(self, credential_id: str) -> None:
        record = await self._require_credential(credential_id)
        slot = self._slot(record)
        await asyncio.to_thread(self._remove_slot, slot)
        await asyncio.to_thread(self._metadata.delete_deploy_key, credential_id)

    async def bind_after_clone(self, credential_id: str, repository_id: str) -> None:
        record = await self._require_credential(credential_id)
        if record.repository_id is not None:
            raise ValueError("deploy key is already bound")
        await asyncio.to_thread(
            self._metadata.bind_deploy_key,
            credential_id,
            repository_id,
            updated_at=int(time.time()),
        )

    async def clone_environment(self, credential_id: str) -> tuple[str, dict[str, str]]:
        record = await self._require_credential(credential_id)
        if record.repository_id is not None:
            raise ValueError("deploy key is already bound")
        if record.last_test_key != "active" or record.last_test_succeeded is not True:
            raise ValueError("deploy key access has not been verified")
        return GitHubRepository(record.github_slug).ssh_url, self._ssh_environment(
            self._key_path(record, "active")
        )

    async def repository_environment(self, repository_id: str) -> dict[str, str] | None:
        record = await asyncio.to_thread(
            self._metadata.deploy_key_by_repository, repository_id
        )
        if record is None:
            return None
        return self._ssh_environment(self._key_path(record, "active"))

    async def verified_repository_url(self, repository_id: str) -> str:
        record = await asyncio.to_thread(
            self._metadata.deploy_key_by_repository, repository_id
        )
        if record is None:
            raise ValueError("repository deploy key is unavailable")
        if record.last_test_key != "active" or record.last_test_succeeded is not True:
            raise ValueError("repository deploy key access has not been verified")
        return GitHubRepository(record.github_slug).ssh_url

    def browser_dict(self, record: DeployKeyRecord) -> dict[str, object]:
        active = self._public_key_view(record, "active", record.active_created_at)
        candidate = (
            self._public_key_view(record, "candidate", record.candidate_created_at)
            if record.candidate_created_at is not None
            else None
        )
        if record.last_tested_at is None:
            status = "configured"
        elif record.last_test_succeeded:
            status = "verified" if record.last_test_key == "active" else "rotation_verified"
        else:
            status = "failed"
        return {
            "credentialId": record.credential_id,
            "repositoryId": record.repository_id,
            "githubRepository": record.github_slug,
            "sshUrl": GitHubRepository(record.github_slug).ssh_url,
            "status": status,
            "active": active,
            "candidate": candidate,
            "retiredKeyAvailable": record.retired_created_at is not None,
            "lastTestedAt": record.last_tested_at,
            "lastSuccessfulTestAt": record.last_success_at,
        }

    async def _require_credential(self, credential_id: str) -> DeployKeyRecord:
        if not _CREDENTIAL_ID.fullmatch(credential_id):
            raise ValueError("invalid deploy key credential")
        record = await asyncio.to_thread(
            self._metadata.deploy_key_by_credential, credential_id
        )
        if record is None:
            raise ValueError("deploy key is unavailable")
        return record

    def _create_slot(self, record: DeployKeyRecord) -> Path:
        slot = self._slot(record, must_exist=False)
        if slot.exists() or slot.is_symlink():
            raise DeployKeyError("deploy key storage collision")
        slot.mkdir(mode=0o700)
        os.chmod(slot, 0o700)
        return slot

    def _slot(self, record: DeployKeyRecord, *, must_exist: bool = True) -> Path:
        root = self._root.resolve(strict=True)
        slot_name = hashlib.sha256(record.credential_id.encode("ascii")).hexdigest()
        slot = root / slot_name
        if slot.parent != root or slot.is_symlink():
            raise DeployKeyError("unsafe deploy key storage path")
        if must_exist:
            resolved = slot.resolve(strict=True)
            if resolved.parent != root or not resolved.is_dir():
                raise DeployKeyError("unsafe deploy key storage path")
        return slot

    def _key_path(self, record: DeployKeyRecord, kind: str) -> Path:
        if kind not in {"active", "candidate", "retired"}:
            raise ValueError("invalid deploy key kind")
        path = self._slot(record) / kind
        if path.is_symlink() or not path.is_file():
            raise DeployKeyError("deploy key material is unavailable")
        resolved = path.resolve(strict=True)
        if resolved.parent != self._slot(record).resolve(strict=True):
            raise DeployKeyError("unsafe deploy key material path")
        return resolved

    async def _generate_pair(self, private_path: Path, slug: str) -> None:
        if private_path.exists() or private_path.is_symlink():
            raise DeployKeyError("deploy key material already exists")
        executable = shutil.which("ssh-keygen")
        if executable is None:
            raise DeployKeyRuntimeUnavailableError("ssh-keygen is unavailable")
        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                f"codex-workbench:{slug}",
                "-f",
                str(private_path),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as exc:
            raise DeployKeyRuntimeUnavailableError("ssh-keygen is unavailable") from exc
        try:
            await asyncio.wait_for(process.wait(), timeout=15)
        except TimeoutError:
            process.kill()
            await process.wait()
            self._unlink_pair(private_path)
            raise DeployKeyGenerationError("deploy key generation timed out") from None
        if process.returncode != 0:
            self._unlink_pair(private_path)
            raise DeployKeyGenerationError("deploy key generation failed")
        public_path = private_path.with_suffix(".pub")
        if private_path.is_symlink() or public_path.is_symlink():
            self._unlink_pair(private_path)
            raise DeployKeyError("deploy key generation produced unsafe paths")
        os.chmod(private_path, 0o600)
        os.chmod(public_path, 0o644)

    def _public_key_view(
        self, record: DeployKeyRecord, kind: str, created_at: int
    ) -> dict[str, object]:
        private_path = self._key_path(record, kind)
        public_path = private_path.with_suffix(".pub")
        if public_path.is_symlink() or not public_path.is_file():
            raise DeployKeyError("deploy key public material is unavailable")
        public_key = public_path.read_text(encoding="ascii").strip()
        fields = public_key.split()
        if len(fields) < 2 or fields[0] != "ssh-ed25519" or len(public_key) > 4096:
            raise DeployKeyError("deploy key public material is invalid")
        try:
            blob = base64.b64decode(fields[1], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise DeployKeyError("deploy key public material is invalid") from exc
        fingerprint = base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii").rstrip("=")
        return {
            "publicKey": public_key,
            "fingerprint": f"SHA256:{fingerprint}",
            "createdAt": created_at,
        }

    def _ssh_environment(self, identity: Path) -> dict[str, str]:
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_SSH": str(self._ssh_wrapper.resolve(strict=True)),
            "GIT_SSH_VARIANT": "ssh",
            "WORKBENCH_SSH_IDENTITY": str(identity),
            "WORKBENCH_SSH_KNOWN_HOSTS": str(self._known_hosts.resolve(strict=True)),
        }
        home = os.environ.get("HOME")
        if home:
            environment["HOME"] = home
        for name in ("LD_PRELOAD", "NSS_WRAPPER_PASSWD", "NSS_WRAPPER_GROUP"):
            value = os.environ.get(name)
            if value:
                environment[name] = value
        return environment

    def _remove_slot(self, slot: Path) -> None:
        root = self._root.resolve(strict=True)
        resolved = slot.resolve(strict=True)
        if resolved.parent != root or resolved == root or slot.is_symlink():
            raise DeployKeyError("unsafe deploy key removal target")
        shutil.rmtree(resolved)

    @staticmethod
    def _unlink_pair(private_path: Path) -> None:
        for path in (private_path, private_path.with_suffix(".pub")):
            if path.exists() and not path.is_symlink():
                path.unlink()
