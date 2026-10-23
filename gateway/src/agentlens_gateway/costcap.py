"""Daily spend guard. A day is a UTC day (matches the `daily_spend` view)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class Decision:
    allowed: bool
    retry_after_s: int = 0


def check(spent_usd: float, reserved_usd: float, cap_usd: float, now: datetime) -> Decision:
    """Allow a new run only while spend so far + the budgets reserved by in-flight runs stays under the cap.
    The reservation matters: finished runs are the only ones with booked cost, so without it N concurrent runs could
    each pass the check and blow through the cap together."""
    if spent_usd + reserved_usd < cap_usd:
        return Decision(True)
    now = now.astimezone(UTC)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return Decision(False, max(1, math.ceil((midnight - now).total_seconds())))
