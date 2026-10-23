"""Deterministic scoring for numeric / text / abstain expectations. No LLM judge involved."""

from __future__ import annotations

import re
from dataclasses import dataclass

from agentlens_agent.answer import Answer

from agentlens_evals.case import Expected

# unit -> (dimension, multiplier to canonical base). percent and ratio share a dimension.
_UNITS: dict[str, tuple[str, float]] = {
    "USD": ("money", 1.0),
    "USD_thousands": ("money", 1e3),
    "USD_millions": ("money", 1e6),
    "USD_billions": ("money", 1e9),
    "percent": ("fraction", 0.01),
    "ratio": ("fraction", 1.0),
    "shares": ("count", 1.0),
    "count": ("count", 1.0),
}


def to_base(value: float, unit: str) -> tuple[str, float]:
    try:
        dim, mult = _UNITS[unit]
    except KeyError:
        raise ValueError(f"unknown unit {unit!r}") from None
    return dim, value * mult


def _norm_period(p: str | None) -> str | None:
    return re.sub(r"[\s_\-]", "", p).upper() if p else None


@dataclass(frozen=True)
class NumericScore:
    correct: bool
    unit_ok: bool
    value_ok: bool
    period_ok: bool | None  # None = not checked
    rel_error: float | None


def score_numeric(answer: Answer, expected: Expected) -> NumericScore:
    if expected.kind != "numeric" or expected.value is None or expected.unit is None:
        raise ValueError("expected must be a complete numeric expectation")
    if answer.abstained or answer.value is None or answer.unit is None:
        return NumericScore(False, False, False, None, None)
    try:
        a_dim, a_val = to_base(answer.value, answer.unit)
    except ValueError:
        return NumericScore(False, False, False, None, None)
    e_dim, e_val = to_base(expected.value, expected.unit)
    unit_ok = a_dim == e_dim
    rel_error = abs(a_val - e_val) / abs(e_val) if e_val != 0 else abs(a_val)
    value_ok = unit_ok and rel_error <= expected.rel_tolerance
    period_ok = None
    if expected.period:
        period_ok = _norm_period(answer.period) == _norm_period(expected.period)
    return NumericScore(
        correct=value_ok and period_ok is not False,
        unit_ok=unit_ok,
        value_ok=value_ok,
        period_ok=period_ok,
        rel_error=rel_error if unit_ok else None,
    )


def score_text(answer: Answer, must_contain: list[str]) -> bool:
    if answer.abstained:
        return False
    hay = re.sub(r"\s+", " ", answer.text).casefold()
    return all(re.sub(r"\s+", " ", s).casefold() in hay for s in must_contain)


def score_abstain(answer: Answer) -> bool:
    """Correct behaviour for unanswerable questions is to abstain (not to guess)."""
    return answer.abstained
