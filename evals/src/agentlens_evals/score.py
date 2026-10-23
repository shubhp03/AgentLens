"""Pass/fail + diagnostics for one run. Deterministic: no LLM judge."""

from __future__ import annotations

from dataclasses import dataclass

from agentlens_agent.answer import Answer
from agentlens_agent.events import RunFinished

from agentlens_evals.case import EvalCase
from agentlens_evals.scorers.grounding import citation_validity, numeric_grounded
from agentlens_evals.scorers.numeric import NumericScore, score_abstain, score_numeric, score_text
from agentlens_evals.scorers.tooluse import (
    error_call_rate,
    redundant_call_rate,
    required_tool_recall,
)
from agentlens_evals.trace import TraceFacts


@dataclass(frozen=True)
class RunScore:
    passed: bool
    numeric: NumericScore | None
    tool_recall: float | None
    redundant_rate: float
    error_rate: float
    citation_validity: float | None
    numeric_grounded: bool | None


def answer_correct(case: EvalCase, answer: Answer) -> tuple[bool, NumericScore | None]:
    exp = case.expected
    if exp.kind == "abstain":
        return score_abstain(answer), None
    if exp.kind == "text":
        return score_text(answer, exp.must_contain), None
    ns = score_numeric(answer, exp)
    return ns.correct, ns


def score_run(case: EvalCase, final: RunFinished, facts: TraceFacts) -> RunScore:
    answer = final.final_answer
    format_ok = not (facts.no_submit or facts.invalid_answer)
    correct, ns = (False, None)
    if final.termination_reason == "answered" and answer is not None and format_ok:
        correct, ns = answer_correct(case, answer)

    grounded: bool | None = None
    cites: float | None = None
    if answer is not None:
        if answer.value is not None and answer.unit is not None and not answer.abstained:
            try:
                grounded = numeric_grounded(answer.value, answer.unit, facts.numbers)
            except ValueError:  # unknown unit
                grounded = False
        if not answer.abstained:
            cites = citation_validity([c.accession for c in answer.citations], facts.accessions)

    return RunScore(
        passed=correct,
        numeric=ns,
        tool_recall=required_tool_recall(case.required_tools, facts.called_tools),
        redundant_rate=redundant_call_rate(facts.calls),
        error_rate=error_call_rate(facts.call_errors),
        citation_validity=cites,
        numeric_grounded=grounded,
    )
