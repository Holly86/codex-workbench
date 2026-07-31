"""Bounded JSONL transport for one directly managed Codex app-server child."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import suppress
from enum import StrEnum
from typing import Final, cast

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]

logger = logging.getLogger("codex_workbench.app_server.transport")

_EOF: Final = object()


class TransportState(StrEnum):
    DISABLED = "disabled"
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    DRAINING = "draining"
    FAILED = "failed"


class AppServerUnavailableError(RuntimeError):
    """Raised when app-server use is disabled or unavailable."""


class AppServerProtocolError(RuntimeError):
    """Raised when stdout does not contain a valid bounded JSONL envelope."""


class AppServerBackpressureError(RuntimeError):
    """Raised instead of allowing an unbounded transport queue."""


class AppServerEofError(RuntimeError):
    """Raised when the child closes its protocol stream unexpectedly."""


class AppServerTransport(ABC):
    """Bidirectional protocol transport; it does not interpret Codex methods."""

    @property
    @abstractmethod
    def state(self) -> TransportState:
        """Return the transport lifecycle state."""

    @property
    @abstractmethod
    def returncode(self) -> int | None:
        """Return the child return code once known."""

    @abstractmethod
    async def start(self) -> None:
        """Start the child and protocol reader/writer tasks."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the child and settle stream tasks."""

    @abstractmethod
    async def send(self, message: JsonObject) -> None:
        """Queue one JSONL envelope for the child."""

    @abstractmethod
    def messages(self) -> AsyncIterator[JsonObject]:
        """Iterate over decoded inbound envelopes."""


class SubprocessJsonlTransport(AppServerTransport):
    """Direct subprocess transport with strict UTF-8 JSONL framing and bounds."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        max_line_bytes: int = 4 * 1024 * 1024,
        inbound_queue_size: int = 512,
        outbound_queue_size: int = 128,
        shutdown_timeout_seconds: float = 5.0,
    ) -> None:
        if not command or any(not part or "\x00" in part for part in command):
            raise ValueError("app-server command must be a non-empty validated argument vector")
        if max_line_bytes < 1024:
            raise ValueError("max_line_bytes is too small")
        self._command = tuple(command)
        self._environment = None if environment is None else dict(environment)
        self._max_line_bytes = max_line_bytes
        self._shutdown_timeout = shutdown_timeout_seconds
        self._inbound: asyncio.Queue[JsonObject | BaseException | object] = asyncio.Queue(
            maxsize=inbound_queue_size
        )
        self._outbound: asyncio.Queue[bytes | object] = asyncio.Queue(maxsize=outbound_queue_size)
        self._process: asyncio.subprocess.Process | None = None
        self._tasks: tuple[asyncio.Task[None], ...] = ()
        self._state = TransportState.STOPPED
        self._stopping = False

    @property
    def state(self) -> TransportState:
        return self._state

    @property
    def returncode(self) -> int | None:
        return None if self._process is None else self._process.returncode

    async def start(self) -> None:
        if self._state is not TransportState.STOPPED:
            raise AppServerUnavailableError("transport can only start from stopped")
        self._state = TransportState.STARTING
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._environment,
                limit=self._max_line_bytes + 1,
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            self._state = TransportState.FAILED
            raise AppServerUnavailableError("app-server child could not be started") from exc
        self._stopping = False
        self._state = TransportState.RUNNING
        self._tasks = (
            asyncio.create_task(self._read_stdout(), name="app-server-stdout"),
            asyncio.create_task(self._read_stderr(), name="app-server-stderr"),
            asyncio.create_task(self._write_stdin(), name="app-server-stdin"),
        )

    async def stop(self) -> None:
        process = self._process
        if process is None:
            self._state = TransportState.STOPPED
            return
        self._stopping = True
        self._state = TransportState.DRAINING
        with suppress(asyncio.QueueFull):
            self._outbound.put_nowait(_EOF)
        if process.stdin is not None:
            process.stdin.close()
            with suppress(BrokenPipeError, ConnectionResetError):
                await process.stdin.wait_closed()
        try:
            await asyncio.wait_for(process.wait(), timeout=self._shutdown_timeout)
        except TimeoutError:
            self._signal_process_group(signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), timeout=self._shutdown_timeout)
            except TimeoutError:
                self._signal_process_group(signal.SIGKILL)
                await process.wait()
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = ()
        self._process = None
        self._state = TransportState.STOPPED
        with suppress(asyncio.QueueFull):
            self._inbound.put_nowait(_EOF)

    async def send(self, message: JsonObject) -> None:
        if self._state is not TransportState.RUNNING:
            raise AppServerUnavailableError("app-server transport is not running")
        try:
            encoded = json.dumps(
                message,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise AppServerProtocolError("outbound JSON-RPC envelope is not serializable") from exc
        if len(encoded) > self._max_line_bytes:
            raise AppServerProtocolError("outbound JSON-RPC envelope exceeds the line limit")
        try:
            self._outbound.put_nowait(encoded + b"\n")
        except asyncio.QueueFull as exc:
            raise AppServerBackpressureError("app-server outbound queue is full") from exc

    async def messages(self) -> AsyncIterator[JsonObject]:
        while True:
            value = await self._inbound.get()
            if value is _EOF:
                return
            if isinstance(value, BaseException):
                raise value
            yield cast(JsonObject, value)

    async def _read_stdout(self) -> None:
        process = self._require_process()
        stdout = process.stdout
        if stdout is None:
            await self._publish_failure(AppServerEofError("app-server stdout is unavailable"))
            return
        try:
            while True:
                try:
                    line = await stdout.readline()
                except (ValueError, asyncio.LimitOverrunError) as exc:
                    raise AppServerProtocolError(
                        "app-server stdout line exceeds the limit"
                    ) from exc
                if not line:
                    if not self._stopping:
                        await self._publish_failure(
                            AppServerEofError("app-server stdout closed unexpectedly")
                        )
                    return
                if len(line) > self._max_line_bytes:
                    raise AppServerProtocolError("app-server stdout line exceeds the limit")
                try:
                    decoded = line.decode("utf-8", errors="strict")
                    value: object = json.loads(decoded)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise AppServerProtocolError("app-server emitted malformed JSONL") from exc
                if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
                    raise AppServerProtocolError("app-server envelope must be a JSON object")
                try:
                    self._inbound.put_nowait(cast(JsonObject, value))
                except asyncio.QueueFull as exc:
                    raise AppServerBackpressureError("app-server inbound queue is full") from exc
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            await self._publish_failure(exc)

    async def _read_stderr(self) -> None:
        process = self._require_process()
        stderr = process.stderr
        if stderr is None:
            return
        while True:
            chunk = await stderr.read(2048)
            if not chunk:
                return
            logger.warning(
                "app_server_stderr",
                extra={
                    "diagnostic": "[redacted]",
                    "bytes": len(chunk),
                },
            )

    async def _write_stdin(self) -> None:
        process = self._require_process()
        stdin = process.stdin
        if stdin is None:
            await self._publish_failure(AppServerEofError("app-server stdin is unavailable"))
            return
        try:
            while True:
                encoded = await self._outbound.get()
                if encoded is _EOF:
                    return
                stdin.write(cast(bytes, encoded))
                await stdin.drain()
        except asyncio.CancelledError:
            raise
        except (BrokenPipeError, ConnectionResetError) as exc:
            if not self._stopping:
                await self._publish_failure(
                    AppServerEofError("app-server stdin closed unexpectedly")
                )
            logger.debug("app_server_stdin_closed", extra={"error_type": type(exc).__name__})

    async def _publish_failure(self, exc: BaseException) -> None:
        if self._state is not TransportState.DRAINING:
            self._state = TransportState.FAILED
        try:
            self._inbound.put_nowait(exc)
        except asyncio.QueueFull:
            with suppress(asyncio.QueueEmpty):
                _ = self._inbound.get_nowait()
            self._inbound.put_nowait(exc)

    def _require_process(self) -> asyncio.subprocess.Process:
        if self._process is None:
            raise AppServerUnavailableError("app-server process is unavailable")
        return self._process

    def _signal_process_group(self, requested_signal: signal.Signals) -> None:
        process = self._process
        if process is None or process.returncode is not None:
            return
        try:
            os.killpg(process.pid, requested_signal)
        except ProcessLookupError:
            return


class DisabledAppServerTransport(AppServerTransport):
    """Fail-closed transport used when productive startup is explicitly disabled."""

    @property
    def state(self) -> TransportState:
        return TransportState.DISABLED

    @property
    def returncode(self) -> int | None:
        return None

    async def start(self) -> None:
        raise AppServerUnavailableError("app-server startup is disabled")

    async def stop(self) -> None:
        return None

    async def send(self, message: JsonObject) -> None:
        del message
        raise AppServerUnavailableError("app-server startup is disabled")

    async def messages(self) -> AsyncIterator[JsonObject]:
        if False:
            yield {}
        raise AppServerUnavailableError("app-server startup is disabled")
