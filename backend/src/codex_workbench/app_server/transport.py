"""Transport contract; no child process is started in the foundation phase."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping
from enum import StrEnum

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = Mapping[str, JsonValue]


class TransportState(StrEnum):
    DISABLED = "disabled"
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    FAILED = "failed"


class AppServerUnavailableError(RuntimeError):
    """Raised when app-server use is disabled or unavailable."""


class AppServerTransport(ABC):
    """Bidirectional JSON-RPC message transport owned by the backend process."""

    @property
    @abstractmethod
    def state(self) -> TransportState:
        """Return current lifecycle state."""

    @abstractmethod
    async def start(self) -> None:
        """Start and initialize the transport."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the transport and settle pending requests."""

    @abstractmethod
    async def send(self, message: JsonObject) -> None:
        """Send one JSON-RPC envelope."""

    @abstractmethod
    def messages(self) -> AsyncIterator[JsonObject]:
        """Iterate over decoded inbound JSON-RPC envelopes."""


class DisabledAppServerTransport(AppServerTransport):
    """Fail-closed transport used until managed execution is implemented."""

    @property
    def state(self) -> TransportState:
        return TransportState.DISABLED

    async def start(self) -> None:
        raise AppServerUnavailableError("app-server startup is disabled in this foundation")

    async def stop(self) -> None:
        return None

    async def send(self, message: JsonObject) -> None:
        del message
        raise AppServerUnavailableError("app-server startup is disabled in this foundation")

    async def messages(self) -> AsyncIterator[JsonObject]:
        if False:
            yield {}
        raise AppServerUnavailableError("app-server startup is disabled in this foundation")
