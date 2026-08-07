"""One-process Codex app-server supervision, version policy, and bounded restart."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import signal
import stat
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from codex_workbench import __version__
from codex_workbench.app_server.client import (
    AppServerClient,
    DisconnectHandler,
    NotificationHandler,
    ServerRequestHandler,
)
from codex_workbench.app_server.transport import SubprocessJsonlTransport
from codex_workbench.config import Settings

logger = logging.getLogger("codex_workbench.app_server.supervisor")

type ReadyHandler = Callable[[int], Awaitable[None]]
type TerminalFailureHandler = Callable[[int, str], Awaitable[None]]
type TransportFactory = Callable[[int], SubprocessJsonlTransport]
type VersionChecker = Callable[[], Awaitable[str]]

APP_SERVER_ARGUMENTS = ("app-server", "--stdio")

_VERSION_PATTERN = re.compile(
    r"codex-cli ((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))"
)


class VersionCheckReason(StrEnum):
    BINARY_NOT_FOUND = "binary_not_found"
    EXECUTION_FAILED = "execution_failed"
    MALFORMED_VERSION_OUTPUT = "malformed_version_output"
    UNSUPPORTED_VERSION = "unsupported_version"
    TIMEOUT = "timeout"
    PERMISSION_DENIED = "permission_denied"
    UNEXPECTED_STDERR = "unexpected_stderr"


class CodexVersionCheckError(RuntimeError):
    """A content-free, operator-safe Codex compatibility failure."""

    def __init__(self, reason: VersionCheckReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


def parse_codex_version_output(
    stdout: bytes,
    stderr: bytes,
    *,
    expected_version: str,
) -> str:
    """Parse one exact ``codex-cli X.Y.Z`` line without treating warnings as version data."""

    if stderr.strip():
        raise CodexVersionCheckError(VersionCheckReason.UNEXPECTED_STDERR)
    try:
        text = stdout.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise CodexVersionCheckError(VersionCheckReason.MALFORMED_VERSION_OUTPUT) from exc
    match = _VERSION_PATTERN.fullmatch(text)
    if match is None:
        raise CodexVersionCheckError(VersionCheckReason.MALFORMED_VERSION_OUTPUT)
    version = match.group(1)
    if version != expected_version:
        raise CodexVersionCheckError(VersionCheckReason.UNSUPPORTED_VERSION)
    return version


async def check_codex_version(
    executable: str,
    *,
    expected_version: str,
    environment: Mapping[str, str],
    timeout_seconds: float,
) -> str:
    """Execute the version probe directly and return only an exactly supported version."""

    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            "--version",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
            start_new_session=True,
        )
    except PermissionError as exc:
        raise CodexVersionCheckError(VersionCheckReason.PERMISSION_DENIED) from exc
    except FileNotFoundError as exc:
        reason = (
            VersionCheckReason.EXECUTION_FAILED
            if Path(executable).exists()
            else VersionCheckReason.BINARY_NOT_FOUND
        )
        raise CodexVersionCheckError(reason) from exc
    except OSError as exc:
        raise CodexVersionCheckError(VersionCheckReason.EXECUTION_FAILED) from exc
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
    except asyncio.CancelledError:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
        raise
    except TimeoutError:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
        raise CodexVersionCheckError(VersionCheckReason.TIMEOUT) from None
    if process.returncode != 0:
        raise CodexVersionCheckError(VersionCheckReason.EXECUTION_FAILED)
    return parse_codex_version_output(
        stdout,
        stderr,
        expected_version=expected_version,
    )


class ProcessState(StrEnum):
    STOPPED = "stopped"
    CHECKING = "checking"
    STARTING = "starting"
    INITIALIZING = "initializing"
    READY = "ready"
    DRAINING = "draining"
    BACKOFF = "backoff"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ProcessStatus:
    state: ProcessState
    generation: int
    version: str | None
    restart_attempts: int
    error_code: str | None


class AppServerSupervisor:
    def __init__(
        self,
        settings: Settings,
        *,
        notification_handler: NotificationHandler,
        server_request_handler: ServerRequestHandler,
        disconnect_handler: DisconnectHandler,
        ready_handler: ReadyHandler,
        terminal_failure_handler: TerminalFailureHandler | None = None,
        transport_factory: TransportFactory | None = None,
        version_checker: VersionChecker | None = None,
    ) -> None:
        self._settings = settings
        self._notification_handler = notification_handler
        self._server_request_handler = server_request_handler
        self._external_disconnect_handler = disconnect_handler
        self._ready_handler = ready_handler
        self._terminal_failure_handler = terminal_failure_handler
        self._environment = self._minimal_environment(os.environ)
        self._configured_executable = settings.codex_executable
        self._command: tuple[str, str, str] | None = None
        self._transport_factory = transport_factory or self._make_transport
        self._version_checker = version_checker
        self._state = ProcessState.STOPPED
        self._generation = 0
        self._version: str | None = None
        self._error_code: str | None = None
        self._client: AppServerClient | None = None
        self._transport: SubprocessJsonlTransport | None = None
        self._restart_times: deque[float] = deque()
        self._restart_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._lifecycle_lock = asyncio.Lock()

    @property
    def client(self) -> AppServerClient:
        if self._state is not ProcessState.READY or self._client is None:
            raise RuntimeError("app-server is not ready")
        return self._client

    @property
    def status(self) -> ProcessStatus:
        return ProcessStatus(
            state=self._state,
            generation=self._generation,
            version=self._version,
            restart_attempts=len(self._restart_times),
            error_code=self._error_code,
        )

    def client_for_generation(self, generation: int) -> AppServerClient:
        if generation != self._generation or self._client is None:
            raise RuntimeError("app-server generation is no longer active")
        return self._client

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._state is not ProcessState.STOPPED:
                return
            self._stopping = False
            self._version = None
            self._error_code = None
            self._state = ProcessState.CHECKING
            try:
                executable = self._resolve_executable(self._configured_executable)
                self._command = (executable, *APP_SERVER_ARGUMENTS)
                version = (
                    await self._version_checker()
                    if self._version_checker is not None
                    else await self._check_installed_version()
                )
                if version != self._settings.expected_codex_version:
                    raise CodexVersionCheckError(VersionCheckReason.UNSUPPORTED_VERSION)
                self._version = version
            except CodexVersionCheckError as exc:
                self._error_code = exc.reason.value
                self._state = ProcessState.FAILED
                logger.error(
                    "app_server_version_check_failed",
                    extra={"reason": exc.reason.value},
                )
                await self._notify_terminal_failure()
                return
            except Exception:
                self._error_code = VersionCheckReason.EXECUTION_FAILED.value
                self._state = ProcessState.FAILED
                logger.error(
                    "app_server_version_check_failed",
                    extra={"reason": VersionCheckReason.EXECUTION_FAILED.value},
                )
                await self._notify_terminal_failure()
                return
            try:
                await self._start_generation()
            except Exception as exc:
                self._error_code = type(exc).__name__
                self._state = ProcessState.FAILED
                logger.error(
                    "app_server_start_failed",
                    extra={"error_type": type(exc).__name__},
                )
                if not self._stopping:
                    await self._schedule_restart()

    async def stop(self) -> None:
        self._stopping = True
        self._state = ProcessState.DRAINING
        restart = self._restart_task
        if restart is not None and not restart.done():
            restart.cancel()
            await asyncio.gather(restart, return_exceptions=True)
        self._restart_task = None
        client = self._client
        transport = self._transport
        self._client = None
        self._transport = None
        if client is not None:
            await client.close()
        if transport is not None:
            await transport.stop()
        self._state = ProcessState.STOPPED

    async def _start_generation(self) -> None:
        self._generation += 1
        generation = self._generation
        self._state = ProcessState.STARTING
        transport = self._transport_factory(generation)
        await transport.start()
        self._transport = transport
        self._state = ProcessState.INITIALIZING
        client = AppServerClient(
            transport,
            generation=generation,
            application_version=__version__,
            request_timeout_seconds=self._settings.app_server_request_timeout_seconds,
            notification_handler=self._notification_handler,
            server_request_handler=self._server_request_handler,
            disconnect_handler=self._on_disconnect,
        )
        self._client = client
        try:
            await client.initialize()
        except BaseException:
            await client.close()
            await transport.stop()
            self._client = None
            self._transport = None
            raise
        self._state = ProcessState.READY
        self._error_code = None
        logger.info(
            "app_server_ready",
            extra={"generation": generation, "codex_version": self._version},
        )
        await self._ready_handler(generation)

    async def _on_disconnect(self, generation: int, exc: BaseException) -> None:
        if generation != self._generation or self._stopping:
            return
        self._state = ProcessState.FAILED
        self._error_code = type(exc).__name__
        await self._external_disconnect_handler(generation, exc)
        await self._schedule_restart()

    async def _schedule_restart(self) -> None:
        if self._stopping or (self._restart_task is not None and not self._restart_task.done()):
            return
        now = time.monotonic()
        cutoff = now - self._settings.app_server_restart_window_seconds
        while self._restart_times and self._restart_times[0] < cutoff:
            self._restart_times.popleft()
        if len(self._restart_times) >= self._settings.app_server_restart_limit:
            self._state = ProcessState.FAILED
            self._error_code = "restart_limit_reached"
            logger.error("app_server_restart_limit_reached")
            await self._notify_terminal_failure()
            return
        self._restart_times.append(now)
        attempt = len(self._restart_times)
        delay = min(
            self._settings.app_server_backoff_initial_seconds * (2 ** (attempt - 1)),
            self._settings.app_server_backoff_max_seconds,
        )
        self._state = ProcessState.BACKOFF
        self._restart_task = asyncio.create_task(
            self._restart_after(delay), name="app-server-restart"
        )

    async def _restart_after(self, delay: float) -> None:
        await asyncio.sleep(delay)
        if self._stopping:
            return
        async with self._lifecycle_lock:
            old_client = self._client
            old_transport = self._transport
            self._client = None
            self._transport = None
            if old_client is not None:
                await old_client.close()
            if old_transport is not None:
                await old_transport.stop()
            try:
                await self._start_generation()
            except Exception as exc:
                self._state = ProcessState.FAILED
                self._error_code = type(exc).__name__
                logger.error(
                    "app_server_restart_failed",
                    extra={"error_type": type(exc).__name__},
                )
                self._restart_task = None
                await self._schedule_restart()

    async def _notify_terminal_failure(self) -> None:
        if self._terminal_failure_handler is not None:
            await self._terminal_failure_handler(
                self._generation,
                self._error_code or "app_server_failed",
            )

    def _make_transport(self, generation: int) -> SubprocessJsonlTransport:
        del generation
        command = self._command
        if command is None:
            raise RuntimeError("app-server command is unavailable before version verification")
        return SubprocessJsonlTransport(
            command,
            environment=self._environment,
            max_line_bytes=self._settings.app_server_max_line_bytes,
            shutdown_timeout_seconds=self._settings.app_server_shutdown_timeout_seconds,
        )

    async def _check_installed_version(self) -> str:
        command = self._command
        if command is None:
            raise CodexVersionCheckError(VersionCheckReason.EXECUTION_FAILED)
        return await check_codex_version(
            command[0],
            expected_version=self._settings.expected_codex_version,
            environment=self._environment,
            timeout_seconds=self._settings.app_server_version_check_timeout_seconds,
        )

    @staticmethod
    def _resolve_executable(configured: str) -> str:
        if "/" in configured:
            path = Path(configured)
            if not path.is_absolute():
                raise CodexVersionCheckError(VersionCheckReason.BINARY_NOT_FOUND)
        else:
            located = shutil.which(configured)
            if located is None:
                raise CodexVersionCheckError(VersionCheckReason.BINARY_NOT_FOUND)
            path = Path(located)
        try:
            candidate = Path(os.path.abspath(path))
            resolved = candidate.resolve(strict=True)
            metadata = resolved.stat()
        except PermissionError as exc:
            raise CodexVersionCheckError(VersionCheckReason.PERMISSION_DENIED) from exc
        except FileNotFoundError as exc:
            raise CodexVersionCheckError(VersionCheckReason.BINARY_NOT_FOUND) from exc
        except OSError as exc:
            raise CodexVersionCheckError(VersionCheckReason.EXECUTION_FAILED) from exc
        if not stat.S_ISREG(metadata.st_mode):
            raise CodexVersionCheckError(VersionCheckReason.EXECUTION_FAILED)
        if not os.access(candidate, os.X_OK):
            raise CodexVersionCheckError(VersionCheckReason.PERMISSION_DENIED)
        # Execute the validated launcher path, not its npm symlink target, so launcher semantics
        # remain identical to an operator's `/usr/local/bin/codex` invocation.
        return str(candidate)

    @staticmethod
    def _minimal_environment(source: Mapping[str, str]) -> dict[str, str]:
        allowed = (
            "HOME",
            "PATH",
            "LANG",
            "LC_ALL",
            "TMPDIR",
            "CODEX_HOME",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "no_proxy",
        )
        return {name: source[name] for name in allowed if name in source}
