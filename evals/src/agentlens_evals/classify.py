"""Failure classifier (EV-06): every failed run gets exactly one category.

Rules are evaluated IN ORDER and the first match wins; the order is documented in
docs/EVAL_DESIGN.md section 5. Rules 1-6 are facts about the run's termination and answer shape.
Rules 7-14 are heuristics over the trace - treat the breakdown as evidence, and validate the
classifier against hand-labelled failures before quoting agreement numbers.
"""

from __future__ import annotations

import math
from typing import Literal, get_args

from agentlens_agent.answer import Answer
from agentlens_agent.events import RunFinished

from agentlens_evals.case import EvalCase
from agentlens_evals.score import RunScore
from agentlens_evals.scorers.grounding import numeric_grounded
from agentlens_evals.scorers.numeric import to_base
from agentlens_evals.trace import TraceFacts

FailureCategory = Literal[
    "provider_error",
    "step_limit",
    "budget_exceeded",
    "format_error",
    "missed_abstain",
    "false_abstain",
    "tool_error_unrecovered",
    "unit_error",
    "wrong_period",
    "calc_error",
    "hallucinated_number",
    "wrong_tool",
    "bad_args",
    "retrieval_miss",
    "other",
]
ALL_CATEGORIES: tuple[str, ...] = get_args(FailureCategory)
_SCALES = (1e3, 1e6, 1e9)
_REL = 0.005


def _close(a: float, b: float, rel: float = _REL) -> bool:
    return math.isclose(a, b, rel_tol=rel, abs_tol=1e-9)


def _is_unit_error(case: EvalCase, ans: Answer) -> bool:
    exp = case.expected
    if exp.kind != "numeric" or ans.value is None or ans.unit is None or exp.value is None:
        return False
    a_dim, a_base = to_base(ans.value, ans.unit)
    e_dim, e_base = to_base(exp.value, exp.unit or "")
    if a_dim != e_dim:
        return True
    if e_base == 0 or _close(a_base, e_base, exp.rel_tolerance):
        return False
    return any(
        _close(a_base * s, e_base, exp.rel_tolerance)
        or _close(a_base / s, e_base, exp.rel_tolerance)
        for s in _SCALES
    )


def _row_key(row: dict) -> tuple:
    return (row.get("start"), row.get("end"))


def _row_base(row: dict) -> float | None:
    try:
        return to_base(float(row["value"]), str(row.get("unit", "USD")))[1]
    except (ValueError, KeyError, TypeError):
        return None


def _is_wrong_period(case: EvalCase, ans: Answer, score: RunScore, facts: TraceFacts) -> bool:
    exp = case.expected
    if exp.kind != "numeric" or ans.value is None or ans.unit is None or exp.value is None:
        return False
    # Right number, wrong period label.
    if score.numeric is not None and score.numeric.value_ok and score.numeric.period_ok is False:
        return True
    # Answer equals a retrieved fact for a DIFFERENT period than the one that holds the gold value.
    _, a_base = to_base(ans.value, ans.unit)
    _, e_base = to_base(exp.value, exp.unit or "")
    gold_periods = {
        _row_key(r)
        for r in facts.fact_rows
        if (b := _row_base(r)) is not None and _close(b, e_base, exp.rel_tolerance)
    }
    ans_periods = {
        _row_key(r)
        for r in facts.fact_rows
        if (b := _row_base(r)) is not None and _close(b, a_base, exp.rel_tolerance)
    }
    return bool(gold_periods) and bool(ans_periods) and not (ans_periods & gold_periods)


def _distinct_fact_count(facts: TraceFacts) -> int:
    return len(
        {
            (r.get("start"), r.get("end"), r.get("value"), r.get("accession"))
            for r in facts.fact_rows
        }
    )


def _gold_in_outputs(case: EvalCase, facts: TraceFacts) -> bool:
    exp = case.expected
    if exp.kind == "numeric" and exp.value is not None and exp.unit is not None:
        return numeric_grounded(exp.value, exp.unit, facts.numbers, exp.rel_tolerance)
    if exp.kind == "text":
        hay = " ".join(facts.outputs).casefold()
        return all(s.casefold() in hay for s in exp.must_contain)
    return True


def classify_failure(
    case: EvalCase, final: RunFinished, facts: TraceFacts, score: RunScore
) -> FailureCategory | None:
    """Return None for a passed run, otherwise the single best category."""
    if score.passed:
        return None
    reason, ans = final.termination_reason, final.final_answer
    exp = case.expected

    if reason in ("provider_error", "timeout"):  # 1
        return "provider_error"
    if reason == "step_limit":  # 2
        return "step_limit"
    if reason == "budget_exceeded":  # 3
        return "budget_exceeded"
    if facts.no_submit or facts.invalid_answer or ans is None:  # 4
        return "format_error"
    if exp.kind == "abstain" and not ans.abstained:  # 5
        return "missed_abstain"
    if exp.kind != "abstain" and ans.abstained:  # 6
        return "false_abstain"
    if facts.last_tool_errored:  # 7
        return "tool_error_unrecovered"
    if _is_unit_error(case, ans):  # 8
        return "unit_error"
    if _is_wrong_period(case, ans, score, facts):  # 9
        return "wrong_period"
    numeric_answer = exp.kind == "numeric" and ans.value is not None
    if (  # 10: the inputs were retrieved, the arithmetic/combination is wrong
        numeric_answer
        and case.category in ("calc", "multi_hop")
        and _distinct_fact_count(facts) >= 2
    ):
        return "calc_error"
    if numeric_answer and score.numeric_grounded is False:  # 11
        return "hallucinated_number"
    if score.tool_recall is not None and score.tool_recall < 1:  # 12
        return "wrong_tool"
    if sum(facts.call_errors) >= 2:  # 13
        return "bad_args"
    if facts.calls and not _gold_in_outputs(case, facts):  # 14
        return "retrieval_miss"
    return "other"  # 15
