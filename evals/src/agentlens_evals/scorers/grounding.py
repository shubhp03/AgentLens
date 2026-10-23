"""Groundedness checks computed from the trace (deterministic, auditable)."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from agentlens_evals.scorers.numeric import to_base


def citation_validity(cited: Sequence[str], retrieved: set[str]) -> float | None:
    """Fraction of cited accession numbers that the agent actually retrieved in THIS run.

    None when nothing was cited (callers treat uncited numeric answers as ungrounded).
    """
    if not cited:
        return None
    return sum(1 for a in cited if a in retrieved) / len(cited)


def numeric_grounded(
    value: float, unit: str, source_values: Iterable[float], rel_tol: float = 0.005
) -> bool:
    """Is the answer traceable to a number that appeared in a tool output (XBRL fact or calculator
    result)? An answer matching no tool-produced number came from model memory/mental math.

    We accept matches against both the raw answer value and its unit-converted base value.
    """
    _, base = to_base(value, unit)
    candidates = {value, base}
    for s in source_values:
        for c in candidates:
            if math.isclose(c, s, rel_tol=rel_tol, abs_tol=1e-9):
                return True
    return False
