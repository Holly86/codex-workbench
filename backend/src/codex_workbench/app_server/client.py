"""Initialized JSON-RPC client over the private app-server transport."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from codex_workbench.app_server.correlation import (
    AppServerRequestTimeoutError,
    RequestCorrelator,
)
from codex_workbench.app_server.protocol import EnvelopeKind, ProtocolAdapter
from codex_workbench.app_server.transport import (
    AppServerProtocolError,
    AppServerTransport,
    JsonObject,
    JsonScalar,
    JsonValue,
)

logger = logging.getLogger("codex_workbench.app_server.client")

type NotificationHandler = Callable[[int, str, JsonObject], Awaitable[None]]
type ServerRequestHandler = Callable[[int, JsonScalar, str, JsonObject], Awaitable[None]]
type DisconnectHandler = Callable[[int, BaseException], Awaitable[None]]


class AppServerClient:
    """Own initialization, ids, correlation, and envelope routing for one generation."""

    def __init__(
        self,
        transport: AppServerTransport,
        *,
        generation: int,
        application_version: str,
        request_timeout_seconds: float,
        notification_handler: NotificationHandler,
        server_request_handler: ServerRequestHandler,
        disconnect_handler: DisconnectHandler,
    ) -> None:
        self.transport = transport
        self.generation = generation
        self._application_version = application_version
        self._request_timeout = request_timeout_seconds
        self._notification_handler = notification_handler
        self._server_request_handler = server_request_handler
        self._disconnect_handler = disconnect_handler
        self._adapter = ProtocolAdapter()
        self._correlator = RequestCorrelator(generation)
        self._send_lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        self._intentional_stop = False
        self.initialized = False

    async def initialize(self) -> None:
        if self._reader_task is not None:
            raise AppServerProtocolError("app-server client was initialized more than once")
        self._reader_task = asyncio.create_task(self._read_messages(), name="app-server-client")
        result = await self.call(
            "initialize",
            self._adapter.initialize_params(self._application_version),
            owner="supervisor",
        )
        self._adapter.validate_initialize_result(result)
        await self.transport.send({"method": "initialized"})
        self.initialized = True

    async def call(
        self,
        method: str,
        params: JsonObject | None,
        *,
        owner: str,
        timeout_seconds: float | None = None,
    ) -> JsonValue:
        if not method or len(method) > 200:
            raise ValueError("protocol method is invalid")
        async with self._send_lock:
            pending = self._correlator.create(method, owner)
            try:
                request: JsonObject = {"method": method, "id": pending.request_id}
                if params is not None:
                    request["params"] = params
                await self.transport.send(request)
            except BaseException:
                self._correlator.abandon(pending.request_id)
                raise
        deadline = self._request_timeout if timeout_seconds is None else timeout_seconds
        try:
            return await asyncio.wait_for(pending.future, timeout=deadline)
        except asyncio.CancelledError:
            self._correlator.abandon(pending.request_id)
            raise
        except TimeoutError as exc:
            self._correlator.abandon(pending.request_id)
            raise AppServerRequestTimeoutError(f"app-server request timed out: {method}") from exc

    async def respond(self, request_id: JsonScalar, result: JsonObject) -> None:
        await self.transport.send({"id": request_id, "result": result})

    async def respond_error(
        self,
        request_id: JsonScalar,
        *,
        code: int = -32601,
        message: str = "Request is not supported by Codex Workbench",
    ) -> None:
        await self.transport.send(
            {"id": request_id, "error": {"code": code, "message": message[:200]}}
        )

    async def close(self) -> None:
        self._intentional_stop = True
        self._correlator.fail_all("client shutdown")
        reader = self._reader_task
        if reader is not None and not reader.done():
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        self._reader_task = None

    async def _read_messages(self) -> None:
        try:
            async for envelope in self.transport.messages():
                try:
                    classified = self._adapter.classify(envelope)
                except ValueError as exc:
                    raise AppServerProtocolError("app-server emitted a malformed envelope") from exc
                if classified.kind is EnvelopeKind.RESPONSE:
                    if not self._correlator.resolve(envelope):
                        logger.warning(
                            "app_server_unknown_response_id",
                            extra={"generation": self.generation},
                        )
                elif classified.kind is EnvelopeKind.NOTIFICATION:
                    if classified.method is None:
                        continue
                    await self._notification_handler(
                        self.generation,
                        classified.method,
                        classified.params,
                    )
                else:
                    raw_id = envelope.get("id")
                    if not isinstance(raw_id, str | int) or isinstance(raw_id, bool):
                        logger.warning(
                            "app_server_invalid_server_request_id",
                            extra={"generation": self.generation},
                        )
                        continue
                    if classified.method is None:
                        continue
                    await self._server_request_handler(
                        self.generation,
                        raw_id,
                        classified.method,
                        classified.params,
                    )
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._correlator.fail_all(type(exc).__name__)
            if not self._intentional_stop:
                await self._disconnect_handler(self.generation, exc)
