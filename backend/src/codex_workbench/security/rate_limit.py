"""Bounded in-memory login throttling for the single backend process."""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int
    address_attempts: int


class LoginRateLimiter:
    """Apply per-address and process-global sliding-window limits."""

    def __init__(
        self,
        *,
        address_limit: int,
        global_limit: int,
        window_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._address_limit = address_limit
        self._global_limit = global_limit
        self._window = window_seconds
        self._clock = clock
        self._global_attempts: deque[float] = deque()
        self._address_attempts: dict[str, deque[float]] = {}
        self._lock = Lock()

    def consume(self, address_key: str) -> RateLimitDecision:
        with self._lock:
            now = self._clock()
            cutoff = now - self._window
            self._discard_before(self._global_attempts, cutoff)
            attempts = self._address_attempts.get(address_key)
            if attempts is None:
                self._bound_address_keys(cutoff)
                attempts = deque()
                self._address_attempts[address_key] = attempts
            self._discard_before(attempts, cutoff)

            address_retry = self._retry_after(attempts, self._address_limit, now)
            global_retry = self._retry_after(self._global_attempts, self._global_limit, now)
            retry_after = max(address_retry, global_retry)
            if retry_after > 0:
                return RateLimitDecision(False, retry_after, len(attempts))

            attempts.append(now)
            self._global_attempts.append(now)
            return RateLimitDecision(True, 0, len(attempts))

    @staticmethod
    def _discard_before(values: deque[float], cutoff: float) -> None:
        while values and values[0] <= cutoff:
            values.popleft()

    def _retry_after(self, values: deque[float], limit: int, now: float) -> int:
        if len(values) < limit:
            return 0
        return max(1, math.ceil(values[0] + self._window - now))

    def _bound_address_keys(self, cutoff: float) -> None:
        if len(self._address_attempts) < 2048:
            return
        for key, values in tuple(self._address_attempts.items()):
            self._discard_before(values, cutoff)
            if not values:
                del self._address_attempts[key]
        if len(self._address_attempts) < 2048:
            return
        oldest_key = min(
            self._address_attempts,
            key=lambda key: self._address_attempts[key][-1],
        )
        del self._address_attempts[oldest_key]
