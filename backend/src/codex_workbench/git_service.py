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
                timeout_seconds=self._timeout,
            )
        return await self._execute(
            root,
            ("clone", "--", safe_url, name),
            display=("clone", "--", self.redact_url(safe_url), name),
        )

    async def clone_preflight(self, url: str) -> dict[str, object]:
        safe_url = self.validate_clone_url(url)
        root = self._root.resolve(strict=True)
        parsed = urlsplit(safe_url)
        is_ssh = parsed.scheme == "ssh" or (not parsed.scheme and ":" in safe_url)
        probe = await self._execute(
            root,
            ("ls-remote", "--exit-code", safe_url, "HEAD"),
            display=("ls-remote", "--exit-code", self.redact_url(safe_url), "HEAD"),
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
                    timeout_seconds=10,
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
        branch: str | None = None
        upstream: str | None = None
        ahead = behind = staged = modified = untracked = conflicts = 0
        detached = False
        changes: list[dict[str, object]] = []
        for line in result.output.splitlines():
            if line.startswith("# branch.head "):
                value = line.removeprefix("# branch.head ")
                detached = value == "(detached)"
                branch = None if detached else value
            elif line.startswith("# branch.upstream "):
                upstream = line.removeprefix("# branch.upstream ")
            elif line.startswith("# branch.ab "):
                match = re.fullmatch(r"# branch\.ab \+(\d+) -(\d+)", line)
                if match:
                    ahead, behind = int(match.group(1)), int(match.group(2))
            elif line.startswith("? "):
                untracked += 1
                changes.append({"path": line[2:], "staged": False, "kind": "untracked"})
            elif line.startswith(("1 ", "2 ", "u ")):
                parts = line.split(" ")
                xy = parts[1] if len(parts) > 1 else ".."
                path = parts[-1] if parts else ""
                is_conflict = line.startswith("u ") or "U" in xy
                conflicts += int(is_conflict)
                staged += int(xy[0] not in {".", "?"})
                modified += int(xy[1] not in {".", "?"})
                changes.append(
                    {
                        "path": path,
                        "staged": xy[0] not in {".", "?"},
                        "kind": "conflict" if is_conflict else "modified",
                    }
                )
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

    async def github_capability(self, repository_id: str | None = None) -> dict[str, object]:
        executable = shutil.which("gh")
        if executable is None:
            return {"available": False, "authenticated": False, "reason": "gh_not_installed"}
        account: str | None = None
        try:
            probe = await self._execute_external(
                (executable, "api", "user", "--jq", ".login"), timeout_seconds=10
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
            return await self._execute_external(tuple(args), cwd=cwd, timeout_seconds=120)

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
            env=self._native_env(optional_locks=True),
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
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise GitOperationError("native operation timed out") from None
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
