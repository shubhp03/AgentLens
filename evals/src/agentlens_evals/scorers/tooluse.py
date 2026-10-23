from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any


def required_tool_recall(required: Iterable[str], called: Sequence[str]) -> float | None:
    req = set(required)
    if not req:
        return None
    return len(req & set(called)) / len(req)


def redundant_call_rate(calls: Sequence[tuple[str, dict[str, Any]]]) -> float:
    """Fraction of calls that exactly repeat an earlier (name, arguments) call."""
    if not calls:
        return 0.0
    seen: set[str] = set()
    dup = 0
    for name, args in calls:
        key = name + json.dumps(args, sort_keys=True, default=str)
        dup += key in seen
        seen.add(key)
    return dup / len(calls)


def error_call_rate(errors: Sequence[bool]) -> float:
    """Fraction of tool calls that returned an error (bad args, unknown concept, ...)."""
    return sum(errors) / len(errors) if errors else 0.0
