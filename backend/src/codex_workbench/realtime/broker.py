"""Bounded per-session event fan-out with monotonic sequence numbers."""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime

from codex_workbench.app_server.transport import JsonObject


class SlowRealtimeClientError(RuntimeError):
    """Raised when a subscriber cannot keep up with the bounded event stream."""


@dataclass(frozen=True, slots=True)
class RealtimeEnvelope:
    sequence: int
    occurred_at: str
    type: str
    payload: JsonObject
    thread_id: str | None = None
    turn_id: str | None = None
    item_id: str | None = None

    def browser_dict(self) -> JsonObject:
        return {
            "sequence": self.sequence,
            "occurredAt": self.occurred_at,
            "type": self.type,
            "payload": self.payload,
            "threadId": self.thread_id,
            "turnId": self.turn_id,
            "itemId": self.item_id,
        }


class RealtimeSubscription:
    def __init__(self, broker: RealtimeBroker, session_id: str, queue_size: int) -> None:
        self._broker = broker
        self.session_id = session_id
        self.queue: asyncio.Queue[RealtimeEnvelope | SlowRealtimeClientError] = asyncio.Queue(
            maxsize=queue_size
        )
        self.closed = False

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        await self._broker.unsubscribe(self)


@dataclass(slots=True)
class _SessionStream:
    next_sequence: int
    history: deque[RealtimeEnvelope]
    subscribers: set[RealtimeSubscription]


class RealtimeBroker:
    def __init__(self, *, history_size: int = 512, subscriber_queue_size: int = 128) -> None:
        self._history_size = history_size
        self._subscriber_queue_size = subscriber_queue_size
        self._sessions: dict[str, _SessionStream] = {}
        self._lock = asyncio.Lock()

    async def publish(
        self,
        session_id: str,
        event_type: str,
        payload: JsonObject,
        *,
        thread_id: str | None = None,
        turn_id: str | None = None,
        item_id: str | None = None,
    ) -> RealtimeEnvelope:
        async with self._lock:
            stream = self._stream(session_id)
            envelope = RealtimeEnvelope(
                sequence=stream.next_sequence,
                occurred_at=datetime.now(UTC).isoformat(),
                type=event_type,
                payload=payload,
                thread_id=thread_id,
                turn_id=turn_id,
                item_id=item_id,
            )
            stream.next_sequence += 1
            stream.history.append(envelope)
            stale: list[RealtimeSubscription] = []
            for subscriber in stream.subscribers:
                try:
                    subscriber.queue.put_nowait(envelope)
                except asyncio.QueueFull:
                    stale.append(subscriber)
            for subscriber in stale:
                stream.subscribers.discard(subscriber)
                subscriber.closed = True
                with suppress(asyncio.QueueEmpty):
                    _ = subscriber.queue.get_nowait()
                subscriber.queue.put_nowait(
                    SlowRealtimeClientError("realtime client exceeded its bounded queue")
                )
            return envelope

    async def subscribe(
        self, session_id: str, last_sequence: int | None
    ) -> tuple[RealtimeSubscription, tuple[RealtimeEnvelope, ...], bool, int]:
        async with self._lock:
            stream = self._stream(session_id)
            subscription = RealtimeSubscription(self, session_id, self._subscriber_queue_size)
            stream.subscribers.add(subscription)
            history = tuple(stream.history)
            latest = stream.next_sequence - 1
            if last_sequence is None:
                return subscription, (), False, latest
            oldest = history[0].sequence if history else stream.next_sequence
            missed = last_sequence < oldest - 1
            replay = (
                ()
                if missed
                else tuple(event for event in history if event.sequence > last_sequence)
            )
            return subscription, replay, missed, latest

    async def unsubscribe(self, subscription: RealtimeSubscription) -> None:
        async with self._lock:
            stream = self._sessions.get(subscription.session_id)
            if stream is not None:
                stream.subscribers.discard(subscription)

    def latest_sequence(self, session_id: str) -> int:
        stream = self._sessions.get(session_id)
        return 0 if stream is None else stream.next_sequence - 1

    def _stream(self, session_id: str) -> _SessionStream:
        stream = self._sessions.get(session_id)
        if stream is None:
            stream = _SessionStream(
                next_sequence=1,
                history=deque(maxlen=self._history_size),
                subscribers=set(),
            )
            self._sessions[session_id] = stream
        return stream
