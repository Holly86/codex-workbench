"""Generation-scoped JSON-RPC request correlation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import cast

from codex_workbench.app_server.transport import JsonObject, JsonScalar, JsonValue


class AppServerRequestError(RuntimeError):
    """Typed, sanitized failure returned by app-server."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message[:500])
        self.code = code


class AppServerRequestTimeoutError(TimeoutError):
    """Raised when an app-server request exceeds its deadline."""


class AppServerDisconnectedError(ConnectionError):
    """Raised for all pending calls when their process generation ends."""


@dataclass(frozen=True, slots=True)
class PendingRequest:
    generation: int
    request_id: int
    method: str
    owner: str
    future: asyncio.Future[JsonValue]


class RequestCorrelator:
    """Allocate ids and settle each response at most once."""

    def __init__(self, generation: int) -> None:
        self.generation = generation
        self._next_id = 1
        self._pending: dict[int, PendingRequest] = {}

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def create(self, method: str, owner: str) -> PendingRequest:
        request_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[JsonValue] = asyncio.get_running_loop().create_future()
        pending = PendingRequest(
            generation=self.generation,
            request_id=request_id,
            method=method,
            owner=owner,
            future=future,
        )
        self._pending[request_id] = pending
        return pending

    def resolve(self, envelope: JsonObject) -> bool:
        raw_id = envelope.get("id")
        if not isinstance(raw_id, int) or isinstance(raw_id, bool):
            return False
        pending = self._pending.pop(raw_id, None)
        if pending is None or pending.future.done():
            return False
        if "error" in envelope:
            error = envelope["error"]
            code = -32000
            message = "App Server request failed"
            if isinstance(error, dict):
                raw_code = error.get("code")
                raw_message = error.get("message")
                if isinstance(raw_code, int) and not isinstance(raw_code, bool):
                    code = raw_code
                if isinstance(raw_message, str):
                    message = raw_message
            pending.future.set_exception(AppServerRequestError(code, message))
            return True
        if "result" not in envelope:
            pending.future.set_exception(AppServerRequestError(-32603, "Malformed response"))
            return True
        pending.future.set_result(envelope["result"])
        return True

    def abandon(self, request_id: int) -> None:
        pending = self._pending.pop(request_id, None)
        if pending is not None and not pending.future.done():
            pending.future.cancel()

    def fail_all(self, reason: str) -> None:
        pending = tuple(self._pending.values())
        self._pending.clear()
        for item in pending:
            if not item.future.done():
                item.future.set_exception(
                    AppServerDisconnectedError(
                        f"app-server generation {self.generation} disconnected: {reason}"
                    )
                )


def is_request_id(value: JsonValue | None) -> bool:
    """Match the generated RequestId union without accepting JSON booleans."""

    return isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))


def native_request_id(value: JsonValue | None) -> JsonScalar:
    if not is_request_id(value):
        raise ValueError("server request id is invalid")
    return cast(JsonScalar, value)
