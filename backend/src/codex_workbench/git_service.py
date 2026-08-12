"""Native, bounded Git operations behind opaque repository identities."""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import shutil
import signal
import stat
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

from codex_workbench.app_server.transport import JsonObject, JsonValue
from codex_workbench.repositories import RepositoryDiscovery

if TYPE_CHECKING:
    from codex_workbench.deploy_keys import DeployKeyService

_REF = re.compile(r"^(?![-./])(?!.*(?:\.\.|//|@\{|\\))[A-Za-z0-9._/-]{1,200}(?<![./])$")
_REPOSITORY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class GitOperationError(RuntimeError):
    pass


class GitOperationTimeoutError(GitOperationError):
    """A bounded Git or GitHub operation exceeded its configured deadline."""


class RepositoryNameConflictError(ValueError):
    """A requested direct-child clone target already exists."""


class BranchLocalNameConflictError(GitOperationError):
    """A remote branch cannot replace an existing local branch."""


class BranchTargetUnavailableError(GitOperationError):
    """A selected branch target is no longer present or cannot be switched."""


@dataclass(frozen=True, slots=True)
class GitResult:
    command: str
    exit_code: int
    output: str
    duration_ms: int
    truncated: bool

    def browser_dict(self) -> dict[str, object]:
        return {
            "command": self.command,
            "exitCode": self.exit_code,
            "output": self.output,
            "durationMs": self.duration_ms,
            "truncated": self.truncated,
        }


@dataclass(frozen=True, slots=True)
class CloneTargetReservation:
    """Opaque proof that this service atomically created a clone target."""

    token: str
    name: str
    target: Path
    device: int
    inode: int


class GitService:
    def __init__(
        self,
        repositories: RepositoryDiscovery,
        root: Path,
        *,
        output_limit: int,
        command_timeout_seconds: float = 30.0,
        network_timeout_seconds: float = 300.0,
        clone_timeout_seconds: float = 900.0,
        deploy_keys: DeployKeyService | None = None,
    ) -> None:
        self._repositories = repositories
        self._root = root
        self._output_limit = output_limit
        self._command_timeout = command_timeout_seconds
        self._network_timeout = network_timeout_seconds
        self._clone_timeout = clone_timeout_seconds
        self._deploy_keys = deploy_keys
        self._locks: dict[str, asyncio.Lock] = {}
        self._reservation_lock = threading.Lock()
        self._clone_reservations: dict[str, CloneTargetReservation] = {}

    async def run(
        self,
        repository_id: str,
        action: str,
        *,
        files: tuple[str, ...] = (),
        message: str | None = None,
        branch: str | None = None,
    ) -> GitResult:
        _summary, cwd = await asyncio.to_thread(
            self._repositories.resolve_repository_id, repository_id
        )
        args, mutating = self._arguments(action, files=files, message=message, branch=branch)
        environment = (
            await self._deploy_keys.repository_environment(repository_id)
            if self._deploy_keys is not None and action in {"fetch", "pull", "push"}
            else None
        )
        lock = self._locks.setdefault(repository_id, asyncio.Lock())
        timeout_seconds = (
            self._network_timeout
            if action in {"fetch", "pull", "push"}
            else self._command_timeout
        )
        if mutating:
            async with lock:
                return await self._execute(
                    cwd, args, environment=environment, timeout_seconds=timeout_seconds
                )
        return await self._execute(
            cwd, args, environment=environment, timeout_seconds=timeout_seconds
        )

    async def create(self, name: str, *, readme: bool) -> GitResult:
        self.validate_name(name)
        root = self._root.resolve(strict=True)
        target = root / name
        if target.exists() or target.is_symlink():
            raise GitOperationError("repository already exists")
        target.mkdir(mode=0o750)
        try:
            result = await self._execute(
                target,
                ("init", "--initial-branch=main"),
                timeout_seconds=self._command_timeout,
            )
            if result.exit_code != 0:
                raise GitOperationError("git init failed")
            if readme:
                readme_path = target / "README.md"
                readme_path.write_text(f"# {name}\n", encoding="utf-8")
                os.chmod(readme_path, 0o640)
            return result
        except BaseException:
            if target.exists() and not any(target.iterdir()):
                target.rmdir()
            raise

    async def clone(self, name: str, url: str) -> GitResult:
        self.validate_name(name)
        safe_url = self.validate_clone_url(url)
        root = self._root.resolve(strict=True)
        target = root / name
        if target.exists() or target.is_symlink():
            raise GitOperationError("repository already exists")
        preflight = await self.clone_preflight(safe_url)
        if preflight["allowed"] is not True:
            raise GitOperationError("clone authentication preflight failed")
        if preflight["mode"] == "github_cli":
            executable = shutil.which("gh")
            slug = self._github_slug(safe_url)
            if executable is None or slug is None:
                raise GitOperationError("GitHub CLI clone is unavailable")
            return await self._execute_external(
                (executable, "repo", "clone", slug, name),
                cwd=root,
                timeout_seconds=self._clone_timeout,
            )
        return await self._execute(
            root,
            ("clone", "--", safe_url, name),
            display=("clone", "--", self.redact_url(safe_url), name),
            timeout_seconds=self._clone_timeout,
        )

    def reserve_clone_target(self, name: str) -> CloneTargetReservation:
        """Atomically reserve one validated direct-child name before a job is created."""
        self.validate_name(name)
        root = self._root.resolve(strict=True)
        target = root / name
        if target.parent != root or target.exists() or target.is_symlink():
            raise RepositoryNameConflictError("repository name is already in use")
        try:
            target.mkdir(mode=0o750)
        except FileExistsError:
            raise RepositoryNameConflictError("repository name is already in use") from None
        try:
            metadata = target.lstat()
            resolved = target.resolve(strict=True)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or target.is_symlink()
                or resolved.parent != root
                or resolved == root
            ):
                raise GitOperationError("clone target reservation is unsafe")
            reservation = CloneTargetReservation(
                token=secrets.token_urlsafe(24),
                name=name,
                target=target,
                device=metadata.st_dev,
                inode=metadata.st_ino,
            )
            with self._reservation_lock:
                self._clone_reservations[reservation.token] = reservation
            return reservation
        except BaseException:
            if target.exists() and not target.is_symlink() and not any(target.iterdir()):
                target.rmdir()
            raise

    def release_clone_target(self, reservation: CloneTargetReservation) -> None:
        """Remove only the still-identical target created by this exact reservation."""
        with self._reservation_lock:
            registered = self._clone_reservations.get(reservation.token)
        if registered != reservation:
            raise GitOperationError("clone target reservation is unavailable")
        if not reservation.target.exists() and not reservation.target.is_symlink():
            with self._reservation_lock:
                self._clone_reservations.pop(reservation.token, None)
            return
        self._require_clone_reservation(reservation)
        shutil.rmtree(reservation.target)
        with self._reservation_lock:
            self._clone_reservations.pop(reservation.token, None)

    async def clone_with_deploy_key(
        self, reservation: CloneTargetReservation, credential_id: str
    ) -> GitResult:
        root = self._root.resolve(strict=True)
        completed = False
        try:
            if self._deploy_keys is None:
                raise GitOperationError("deploy key service is unavailable")
            self._require_clone_reservation(reservation, require_empty=True)
            url, environment = await self._deploy_keys.clone_environment(credential_id)
            result = await self._execute(
                root,
                ("clone", "--", url, reservation.name),
                display=("clone", "--", "github.com:repository", reservation.name),
                environment=environment,
                timeout_seconds=self._clone_timeout,
            )
            if result.exit_code != 0:
                return result
            summary, _path = await asyncio.to_thread(
                self._repositories.resolve_repository_id,
                self.repository_id_for_name(reservation.name),
            )
            await self._deploy_keys.bind_after_clone(credential_id, summary.id)
            self._require_clone_reservation(reservation)
            with self._reservation_lock:
                self._clone_reservations.pop(reservation.token, None)
            completed = True
            return result
        finally:
            if not completed:
                await asyncio.to_thread(self.release_clone_target, reservation)

    async def clone_preflight(self, url: str) -> dict[str, object]:
        safe_url = self.validate_clone_url(url)
        root = self._root.resolve(strict=True)
        parsed = urlsplit(safe_url)
        is_ssh = parsed.scheme == "ssh" or (not parsed.scheme and ":" in safe_url)
        probe = await self._execute(
            root,
            ("ls-remote", "--exit-code", safe_url, "HEAD"),
            display=("ls-remote", "--exit-code", self.redact_url(safe_url), "HEAD"),
            timeout_seconds=self._command_timeout,
        )
        if probe.exit_code == 0:
            return {
                "allowed": True,
                "mode": "ssh" if is_ssh else "public_https",
                "message": "Zugriff geprüft.",
            }
        slug = self._github_slug(safe_url)
        executable = shutil.which("gh")
        if not is_ssh and slug is not None and executable is not None:
            capability = await self.github_capability()
            if capability.get("authenticated") is True:
                private_probe = await self._execute_external(
                    (executable, "repo", "view", slug, "--json", "name"),
                    timeout_seconds=self._command_timeout,
                )
                if private_probe.exit_code == 0:
                    return {
                        "allowed": True,
                        "mode": "github_cli",
                        "message": (
                            "Privater GitHub-Zugriff über die bestehende "
                            "gh-Anmeldung geprüft."
                        ),
                    }
        return {
            "allowed": False,
            "mode": "ssh" if is_ssh else "unavailable",
            "message": (
                "Die konfigurierten SSH-Zugangsdaten erlauben keinen Zugriff."
                if is_ssh
                else (
                    "Repository nicht öffentlich erreichbar und keine passende "
                    "sichere Anmeldung gefunden."
                )
            ),
        }

    async def status(self, repository_id: str) -> dict[str, object]:
        result = await self.run(repository_id, "status")
        if result.truncated:
            raise GitOperationError("status inventory failed")
        branch: str | None = None
        upstream: str | None = None
        ahead = behind = staged = modified = untracked = conflicts = 0
        detached = False
        changes: list[dict[str, object]] = []
        for record in result.output.split("\x00"):
            if record.startswith("# branch.head "):
                value = record.removeprefix("# branch.head ")
                detached = value == "(detached)"
                branch = None if detached else value
            elif record.startswith("# branch.upstream "):
                upstream = record.removeprefix("# branch.upstream ")
            elif record.startswith("# branch.ab "):
                match = re.fullmatch(r"# branch\.ab \+(\d+) -(\d+)", record)
                if match:
                    ahead, behind = int(match.group(1)), int(match.group(2))
            elif record.startswith("? "):
                untracked += 1
                changes.append(
                    {
                        "path": record[2:],
                        "staged": False,
                        "unstaged": True,
                        "kind": "untracked",
                    }
                )
            elif record.startswith("1 "):
                # --no-renames guarantees one NUL record per path. A bounded split
                # preserves every byte after the fixed Porcelain-v2 metadata fields.
                parts = record.split(" ", 8)
                if len(parts) != 9:
                    raise GitOperationError("invalid status inventory")
                xy, path = parts[1], parts[8]
                is_conflict = "U" in xy
                index_changed = xy[0] not in {".", "?"}
                worktree_changed = xy[1] not in {".", "?"}
                conflicts += int(is_conflict)
                staged += int(index_changed)
                modified += int(worktree_changed)
                changes.append(
                    {
                        "path": path,
                        "staged": index_changed,
                        "unstaged": worktree_changed,
                        "kind": (
                            "conflict"
                            if is_conflict
                            else "deleted"
                            if "D" in xy
                            else "modified"
                        ),
                    }
                )
            elif record.startswith("u "):
                parts = record.split(" ", 10)
                if len(parts) != 11:
                    raise GitOperationError("invalid status inventory")
                xy, path = parts[1], parts[10]
                index_changed = xy[0] not in {".", "?"}
                worktree_changed = xy[1] not in {".", "?"}
                conflicts += 1
                staged += int(index_changed)
                modified += int(worktree_changed)
                changes.append(
                    {
                        "path": path,
                        "staged": index_changed,
                        "unstaged": worktree_changed,
                        "kind": "conflict",
                    }
                )
            elif record.startswith("2 "):
                raise GitOperationError("unexpected renamed status record")
        return {
            "branch": branch,
            "detached": detached,
            "upstream": upstream,
            "upstreamMissing": upstream is None,
            "ahead": ahead,
            "behind": behind,
            "staged": staged,
            "modified": modified,
            "untracked": untracked,
            "conflicts": conflicts,
            "clean": not changes,
            "changes": changes,
            "observedAt": int(time.time()),
        }

    async def branches(self, repository_id: str) -> JsonObject:
        """Return a typed local/remote branch inventory without command output."""

        _summary, cwd = await asyncio.to_thread(
            self._repositories.resolve_repository_id, repository_id
        )
        local, remote = await self._load_branch_inventory(cwd)
        return {"local": local, "remote": remote}

    async def switch_branch(
        self,
        repository_id: str,
        target_id: str,
        *,
        local_branch_name: str | None = None,
    ) -> GitResult:
        """Switch only to a target from a fresh, server-owned branch inventory."""

        _summary, cwd = await asyncio.to_thread(
            self._repositories.resolve_repository_id, repository_id
        )
        lock = self._locks.setdefault(repository_id, asyncio.Lock())
        async with lock:
            local, remote = await self._load_branch_inventory(cwd)
            targets = {
                str(item["id"]): item
                for item in (*local, *remote)
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
            target = targets.get(target_id)
            if target is None or target.get("disabledReason") is not None:
                raise BranchTargetUnavailableError("branch target unavailable")
            if target["kind"] == "local":
                branch_name = str(target["displayName"])
                result = await self._execute(
                    cwd,
                    ("switch", "--", branch_name),
                    timeout_seconds=self._command_timeout,
                )
                if result.exit_code != 0:
                    raise GitOperationError("branch switch failed")
                return result

            proposed_name = str(target["proposedLocalName"])
            branch_name = local_branch_name or proposed_name
            if not _REF.fullmatch(branch_name):
                raise ValueError("invalid local branch")
            if any(
                isinstance(item, dict) and item.get("displayName") == branch_name
                for item in local
            ):
                raise BranchLocalNameConflictError("local branch already exists")
            result = await self._execute(
                cwd,
                (
                    "switch",
                    "-c",
                    branch_name,
                    "--track=direct",
                    "--",
                    str(target["fullRef"]),
                ),
                timeout_seconds=self._command_timeout,
            )
            if result.exit_code != 0:
                raise GitOperationError("tracking branch creation failed")
            return result

    async def _load_branch_inventory(
        self, cwd: Path
    ) -> tuple[list[JsonValue], list[JsonValue]]:
        remotes_result = await self._execute(
            cwd, ("remote",), timeout_seconds=self._command_timeout
        )
        refs_result = await self._execute(
            cwd,
            (
                "for-each-ref",
                "--format=%(refname)\t%(HEAD)",
                "refs/heads",
                "refs/remotes",
            ),
            timeout_seconds=self._command_timeout,
        )
        if (
            remotes_result.exit_code != 0
            or refs_result.exit_code != 0
            or remotes_result.truncated
            or refs_result.truncated
        ):
            raise GitOperationError("branch inventory failed")
        remote_names = tuple(
            sorted(
                (name for name in remotes_result.output.splitlines() if _REF.fullmatch(name)),
                key=len,
                reverse=True,
            )
        )
        local: list[JsonValue] = []
        remote: list[JsonValue] = []
        for line in refs_result.output.splitlines():
            fields = line.split("\t")
            if len(fields) != 2:
                continue
            full_ref, head = fields
            if full_ref.startswith("refs/heads/"):
                kind = "local"
                name = full_ref.removeprefix("refs/heads/")
                remote_name: str | None = None
                remote_branch_name: str | None = None
                proposed_local_name: str | None = None
            elif full_ref.startswith("refs/remotes/"):
                kind = "remote"
                remote_name = next(
                    (
                        candidate
                        for candidate in remote_names
                        if full_ref.startswith(f"refs/remotes/{candidate}/")
                    ),
                    None,
                )
                if remote_name is None:
                    continue
                remote_branch_name = full_ref.removeprefix(
                    f"refs/remotes/{remote_name}/"
                )
                if not remote_branch_name or remote_branch_name == "HEAD":
                    continue
                proposed_local_name = remote_branch_name
                name = f"{remote_name}/{remote_branch_name}"
            else:
                continue
            if not name:
                continue
            current = head == "*"
            item: JsonObject = {
                "id": full_ref,
                "kind": kind,
                "displayName": name,
                "fullRef": full_ref,
                "remoteName": remote_name,
                "remoteBranchName": remote_branch_name,
                "proposedLocalName": proposed_local_name,
                "current": current,
                "disabledReason": "current_branch" if current else None,
            }
            (remote if kind == "remote" else local).append(item)
        return local, remote

    async def github_capability(self, repository_id: str | None = None) -> dict[str, object]:
        executable = shutil.which("gh")
        if executable is None:
            return {"available": False, "authenticated": False, "reason": "gh_not_installed"}
        account: str | None = None
        try:
            probe = await self._execute_external(
                (executable, "api", "user", "--jq", ".login"),
                timeout_seconds=self._command_timeout,
            )
            if probe.exit_code == 0:
                candidate = probe.output.strip()
                if re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", candidate):
                    account = candidate
        except GitOperationError:
            pass
        remote_url: str | None = None
        web_url: str | None = None
        published = False
        if repository_id is not None:
            result = await self.run(repository_id, "remotes")
            for line in result.output.splitlines():
                fields = line.split()
                if len(fields) >= 3 and fields[0] == "origin" and fields[2] == "(fetch)":
                    remote_url = self.redact_url(fields[1])
                    web_url = self.github_web_url(remote_url)
                    published = web_url is not None
                    break
        return {
            "available": True,
            "authenticated": account is not None,
            "account": account,
            "reason": None if account else "gh_not_authenticated",
            "published": published,
            "remoteUrl": remote_url,
            "webUrl": web_url,
        }

    async def github_origin_slug(self, repository_id: str) -> str:
        from codex_workbench.deploy_keys import normalize_github_repository

        _summary, cwd = await asyncio.to_thread(
            self._repositories.resolve_repository_id, repository_id
        )
        result = await self._execute(
            cwd,
            ("remote", "get-url", "origin"),
            timeout_seconds=self._command_timeout,
        )
        if result.exit_code != 0:
            raise ValueError("origin remote is unavailable")
        return normalize_github_repository(result.output.strip()).slug

    async def deploy_key_remote_preview(self, repository_id: str) -> dict[str, object]:
        if self._deploy_keys is None:
            raise GitOperationError("deploy key service is unavailable")
        _summary, cwd = await asyncio.to_thread(
            self._repositories.resolve_repository_id, repository_id
        )
        current = await self._execute(
            cwd,
            ("remote", "get-url", "origin"),
            timeout_seconds=self._command_timeout,
        )
        if current.exit_code != 0:
            raise ValueError("origin remote is unavailable")
        new_url = await self._deploy_keys.verified_repository_url(repository_id)
        return {
            "currentUrl": self.redact_url(current.output.strip()),
            "newUrl": new_url,
        }

    async def switch_origin_to_deploy_key(
        self, repository_id: str, *, confirmation: str
    ) -> dict[str, object]:
        preview = await self.deploy_key_remote_preview(repository_id)
        new_url = preview["newUrl"]
        if not isinstance(new_url, str) or confirmation != new_url:
            raise ValueError("origin change confirmation does not match")
        _summary, cwd = await asyncio.to_thread(
            self._repositories.resolve_repository_id, repository_id
        )
        result = await self._execute(
            cwd,
            ("remote", "set-url", "origin", new_url),
            display=("remote", "set-url", "origin", "github.com:repository"),
            timeout_seconds=self._command_timeout,
        )
        if result.exit_code != 0:
            raise GitOperationError("origin remote could not be changed")
        return {"currentUrl": preview["currentUrl"], "newUrl": new_url, "changed": True}

    async def publish_github(
        self,
        repository_id: str,
        *,
        owner: str,
        name: str,
        visibility: str,
        remote: str,
        initial_push: bool,
        public_confirmation: str | None,
    ) -> GitResult:
        if visibility not in {"private", "public"}:
            raise ValueError("invalid visibility")
        self.validate_name(name)
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", owner):
            raise ValueError("invalid owner")
        if remote != "origin":
            raise ValueError("unsupported remote")
        if visibility == "public" and public_confirmation != name:
            raise ValueError("public confirmation does not match")
        capability = await self.github_capability(repository_id)
        if capability.get("authenticated") is not True or capability.get("account") != owner:
            raise GitOperationError("GitHub is not authenticated")
        executable = shutil.which("gh")
        if executable is None:
            raise GitOperationError("GitHub CLI is unavailable")
        _summary, cwd = await asyncio.to_thread(
            self._repositories.resolve_repository_id, repository_id
        )
        args = [
            executable,
            "repo",
            "create",
            f"{owner}/{name}",
            f"--{visibility}",
            "--source",
            ".",
            "--remote",
            remote,
        ]
        if initial_push:
            args.append("--push")
        lock = self._locks.setdefault(repository_id, asyncio.Lock())
        async with lock:
            return await self._execute_external(
                tuple(args), cwd=cwd, timeout_seconds=self._network_timeout
            )

    @staticmethod
    def validate_name(name: str) -> None:
        if not _REPOSITORY_NAME.fullmatch(name) or name in {".", ".."} or name.endswith(".git"):
            raise ValueError("invalid repository name")

    @staticmethod
    def validate_clone_url(value: str) -> str:
        if any(character in value for character in ("\x00", "\r", "\n")) or value.startswith("-"):
            raise ValueError("invalid clone URL")
        parsed = urlsplit(value)
        if parsed.scheme == "https" and (
            parsed.username is not None or parsed.password is not None
        ):
            raise ValueError("credentials must not be embedded in clone URLs")
        if parsed.scheme in {"https", "ssh"} and parsed.hostname and parsed.path not in {"", "/"}:
            return value
        if parsed.scheme:
            raise ValueError("only HTTPS and SSH Git URLs are supported")
        if re.fullmatch(r"(?:[A-Za-z0-9._-]+@)?[A-Za-z0-9.-]+:[A-Za-z0-9._~/-]+(?:\.git)?", value):
            return value
        raise ValueError("only HTTPS and SSH Git URLs are supported")

    @staticmethod
    def redact_url(value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme and parsed.hostname:
            host = parsed.hostname
            if parsed.port is not None:
                host += f":{parsed.port}"
            return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
        return re.sub(r"^[^@\s]+@", "", value)

    @staticmethod
    def github_web_url(value: str) -> str | None:
        parsed = urlsplit(value)
        if parsed.hostname == "github.com":
            path = parsed.path.removesuffix(".git").strip("/")
        else:
            match = re.fullmatch(r"(?:[^@]+@)?github\.com:([^\s]+?)(?:\.git)?", value)
            path = match.group(1) if match else ""
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", path):
            return None
        return f"https://github.com/{path}"

    @classmethod
    def _github_slug(cls, value: str) -> str | None:
        web = cls.github_web_url(value)
        return None if web is None else web.removeprefix("https://github.com/")

    @staticmethod
    def repository_id_for_name(name: str) -> str:
        return sha256(name.encode("utf-8")).hexdigest()[:16]

    def _arguments(
        self, action: str, *, files: tuple[str, ...], message: str | None, branch: str | None
    ) -> tuple[tuple[str, ...], bool]:
        if action == "status":
            return (
                (
                    "status",
                    "--porcelain=v2",
                    "-z",
                    "--branch",
                    "--untracked-files=all",
                    "--no-renames",
                ),
                False,
            )
        if action == "diff":
            return (("diff", "--no-ext-diff", "--"), False)
        if action == "fetch":
            return (("fetch", "--prune"), True)
        if action == "pull":
            return (("pull", "--ff-only"), True)
        if action == "push":
            return (("push",), True)
        if action == "branches":
            return (
                (
                    "for-each-ref",
                    "--format=%(refname:short)\t%(refname)\t%(HEAD)",
                    "refs/heads",
                    "refs/remotes",
                ),
                False,
            )
        if action == "remotes":
            return (("remote", "-v"), False)
        if action == "log":
            return (
                (
                    "log",
                    "-n",
                    "20",
                    "--date=iso-strict",
                    "--pretty=format:%H%x09%h%x09%ad%x09%an%x09%s",
                ),
                False,
            )
        if action == "add":
            if not files:
                raise ValueError("files are required")
            return (("add", "--", *(self._validate_file(item) for item in files)), True)
        if action == "commit":
            if message is None or not message.strip() or len(message) > 10_000 or "\x00" in message:
                raise ValueError("invalid commit message")
            return (("commit", "-m", message.strip()), True)
        if action == "branch-create":
            if branch is None or not _REF.fullmatch(branch):
                raise ValueError("invalid branch")
            return (("switch", "-c", branch), True)
        if action == "branch-switch":
            raise ValueError("branch switch requires a server-validated target")
        raise ValueError("unsupported Git action")

    @staticmethod
    def _validate_file(value: str) -> str:
        path = PurePosixPath(value)
        if (
            not value
            or path.is_absolute()
            or ".." in path.parts
            or "\x00" in value
        ):
            raise ValueError("invalid repository-relative file")
        return value

    async def _execute(
        self,
        cwd: Path,
        args: tuple[str, ...],
        *,
        display: tuple[str, ...] | None = None,
        environment: dict[str, str] | None = None,
        timeout_seconds: float,
    ) -> GitResult:
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=self._git_environment(environment),
            start_new_session=os.name == "posix",
        )
        output = await self._communicate(process, timeout_seconds=timeout_seconds)
        truncated = len(output) > self._output_limit
        safe_output = output[: self._output_limit].decode("utf-8", errors="replace")
        shown = display or args
        return GitResult(
            "git " + " ".join(shown),
            process.returncode or 0,
            safe_output,
            round((time.monotonic() - started) * 1000),
            truncated,
        )

    def _require_clone_reservation(
        self, reservation: CloneTargetReservation, *, require_empty: bool = False
    ) -> None:
        root = self._root.resolve(strict=True)
        with self._reservation_lock:
            registered = self._clone_reservations.get(reservation.token)
        if registered != reservation:
            raise GitOperationError("clone target reservation is unavailable")
        try:
            metadata = reservation.target.lstat()
            resolved = reservation.target.resolve(strict=True)
        except FileNotFoundError:
            raise GitOperationError("clone target reservation is unavailable") from None
        if (
            reservation.target.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or resolved.parent != root
            or resolved == root
            or reservation.target.parent != root
            or metadata.st_dev != reservation.device
            or metadata.st_ino != reservation.inode
        ):
            raise GitOperationError("clone target reservation is unsafe")
        if require_empty and any(reservation.target.iterdir()):
            raise GitOperationError("clone target reservation is not empty")

    def _git_environment(self, selected: dict[str, str] | None) -> dict[str, str]:
        if selected is None:
            return self._native_env(optional_locks=True)
        environment = dict(selected)
        environment["GIT_OPTIONAL_LOCKS"] = "0"
        return environment

    async def _execute_external(
        self, args: tuple[str, ...], *, cwd: Path | None = None, timeout_seconds: float
    ) -> GitResult:
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=self._native_env(optional_locks=False),
            start_new_session=os.name == "posix",
        )
        output = await self._communicate(process, timeout_seconds=timeout_seconds)
        truncated = len(output) > self._output_limit
        safe_output = output[: self._output_limit].decode("utf-8", errors="replace")
        display = "gh " + " ".join(args[2:]) if Path(args[0]).name == "gh" else Path(args[0]).name
        return GitResult(
            display,
            process.returncode or 0,
            safe_output,
            round((time.monotonic() - started) * 1000),
            truncated,
        )

    async def _communicate(
        self, process: asyncio.subprocess.Process, *, timeout_seconds: float
    ) -> bytes:
        communication = asyncio.create_task(process.communicate())
        try:
            output, _stderr = await asyncio.wait_for(
                asyncio.shield(communication), timeout=timeout_seconds
            )
        except TimeoutError:
            await self._terminate_process_tree(process, communication)
            raise GitOperationTimeoutError("Git operation timed out") from None
        except BaseException:
            await self._terminate_process_tree(process, communication)
            raise
        return output or b""

    async def _terminate_process_tree(
        self,
        process: asyncio.subprocess.Process,
        communication: asyncio.Task[tuple[bytes | None, bytes | None]],
    ) -> None:
        if process.returncode is None:
            self._signal_process(process, hard=False)
        try:
            await asyncio.wait_for(asyncio.shield(communication), timeout=2.0)
            return
        except TimeoutError:
            if process.returncode is None:
                self._signal_process(process, hard=True)
        except BaseException:
            return
        try:
            await asyncio.wait_for(asyncio.shield(communication), timeout=2.0)
        except TimeoutError:
            communication.cancel()
            await asyncio.gather(communication, return_exceptions=True)
            if process.returncode is None:
                process.kill()
                await process.wait()
        except BaseException:
            return

    @staticmethod
    def _signal_process(process: asyncio.subprocess.Process, *, hard: bool) -> None:
        if os.name == "posix":
            selected_signal = 9 if hard else signal.SIGTERM
            with suppress(ProcessLookupError):
                os.kill(-process.pid, selected_signal)
            return
        if hard:
            process.kill()
        else:
            process.terminate()

    @staticmethod
    def _native_env(*, optional_locks: bool) -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_TERMINAL_PROMPT": "0",
        }
        if optional_locks:
            env["GIT_OPTIONAL_LOCKS"] = "0"
        for key in ("HOME", "XDG_CONFIG_HOME", "GH_CONFIG_DIR", "SSH_AUTH_SOCK"):
            value = os.environ.get(key)
            if value:
                env[key] = value
        return env
