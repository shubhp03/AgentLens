"""Token-bucket rate limiter, one bucket per API key. In-process (single gateway instance, by design)."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    allowed: bool
    retry_after_s: int = 0


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_refill_time)

    def try_acquire(self, key: str, per_minute: int) -> Decision:
        """Capacity = per_minute (a full burst is allowed), refilled continuously at per_minute/60 per second."""
        now = self._clock()
        rate = per_minute / 60.0
        tokens, last = self._buckets.get(key, (float(per_minute), now))
        tokens = min(
            float(per_minute), tokens + (now - last) * rate
        )  # idle time never banks more than capacity
        if tokens >= 1.0:
            self._buckets[key] = (tokens - 1.0, now)
            return Decision(True)
        self._buckets[key] = (tokens, now)
        return Decision(False, max(1, math.ceil((1.0 - tokens) / rate)))
