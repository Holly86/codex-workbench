"""Native, bounded Git operations behind opaque repository identities."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

from codex_workbench.repositories import RepositoryDiscovery

_REF = re.compile(r"^(?![-./])(?!.*(?:\.\.|//|@\{|\\))[A-Za-z0-9._/-]{1,200}(?<![./])$")
_REPOSITORY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class GitOperationError(RuntimeError):
    pass


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


class GitService:
    def __init__(
        self,
        repositories: RepositoryDiscovery,
        root: Path,
        *,
        output_limit: int,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._repositories = repositories
        self._root = root
        self._output_limit = output_limit
        self._timeout = timeout_seconds
        self._locks: dict[str, asyncio.Lock] = {}

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
        lock = self._locks.setdefault(repository_id, asyncio.Lock())
        if mutating:
            async with lock:
                return await self._execute(cwd, args)
        return await self._execute(cwd, args)

    async def create(self, name: str, *, readme: bool) -> GitResult:
        self.validate_name(name)
        root = self._root.resolve(strict=True)
        target = root / name
        if target.exists() or target.is_symlink():
            raise GitOperationError("repository already exists")
        target.mkdir(mode=0o750)
        try:
            result = await self._execute(target, ("init", "--initial-branch=main"))
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
        return await self._execute(
            root,
            ("clone", "--", safe_url, name),
            display=("clone", "--", self.redact_url(safe_url), name),
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

    def github_capability(self) -> dict[str, object]:
        executable = shutil.which("gh")
        return {
            "available": executable is not None,
            "authenticated": False,
            "reason": "authentication_not_probed" if executable else "gh_not_installed",
        }

    def _arguments(
        self, action: str, *, files: tuple[str, ...], message: str | None, branch: str | None
    ) -> tuple[tuple[str, ...], bool]:
        if action == "status":
            return (("status", "--porcelain=v2", "--branch", "--untracked-files=all"), False)
        if action == "diff":
            return (("diff", "--no-ext-diff", "--"), False)
        if action == "fetch":
            return (("fetch", "--prune"), True)
        if action == "pull":
            return (("pull", "--ff-only"), True)
        if action == "push":
            return (("push",), True)
        if action == "branches":
            return (("branch", "--format=%(refname:short)\t%(HEAD)\t%(upstream:short)"), False)
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
        if action in {"branch-create", "branch-switch"}:
            if branch is None or not _REF.fullmatch(branch):
                raise ValueError("invalid branch")
            return (
                (("switch", "-c", branch) if action == "branch-create" else ("switch", branch)),
                True,
            )
        raise ValueError("unsupported Git action")

    @staticmethod
    def _validate_file(value: str) -> str:
        path = PurePosixPath(value)
        if (
            not value
            or value.startswith("-")
            or path.is_absolute()
            or ".." in path.parts
            or "\x00" in value
        ):
            raise ValueError("invalid repository-relative file")
        return value

    async def _execute(
        self, cwd: Path, args: tuple[str, ...], *, display: tuple[str, ...] | None = None
    ) -> GitResult:
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_OPTIONAL_LOCKS": "0",
            },
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=self._timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise GitOperationError("Git operation timed out") from None
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
