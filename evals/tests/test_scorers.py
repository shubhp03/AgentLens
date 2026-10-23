import pytest
from agentlens_agent.answer import Answer
from agentlens_evals.case import Expected
from agentlens_evals.scorers.grounding import citation_validity, numeric_grounded
from agentlens_evals.scorers.numeric import score_abstain, score_numeric, score_text
from agentlens_evals.scorers.tooluse import (
    error_call_rate,
    redundant_call_rate,
    required_tool_recall,
)

EXP = Expected(
    kind="numeric", value=383285, unit="USD_millions", period="FY2023"
)  # synthetic fixture


def ans(**kw):
    kw.setdefault("text", "x")
    return Answer(**kw)


def test_numeric_unit_normalization_accepts_equivalent_scales():
    s = score_numeric(ans(value=383.285, unit="USD_billions", period="fy 2023"), EXP)
    assert s.correct and s.unit_ok and s.period_ok


def test_numeric_wrong_unit_scale_fails_value():
    s = score_numeric(ans(value=383285, unit="USD_billions", period="FY2023"), EXP)
    assert not s.correct and s.unit_ok and not s.value_ok


def test_numeric_wrong_dimension_and_period():
    assert not score_numeric(ans(value=383285, unit="percent", period="FY2023"), EXP).unit_ok
    s = score_numeric(ans(value=383285, unit="USD_millions", period="FY2022"), EXP)
    assert s.value_ok and s.period_ok is False and not s.correct


def test_percent_and_ratio_are_interchangeable():
    e = Expected(kind="numeric", value=12.5, unit="percent", rel_tolerance=0.001)
    assert score_numeric(ans(value=0.125, unit="ratio"), e).correct


def test_abstention_handling():
    a = ans(abstained=True)
    assert score_abstain(a) and not score_numeric(a, EXP).correct and not score_text(a, ["x"])
    assert not score_abstain(ans(value=1.0, unit="USD"))


def test_text_containment_is_whitespace_and_case_insensitive():
    assert score_text(
        ans(text="Intelligent  Cloud and Productivity"), ["intelligent cloud", "PRODUCTIVITY"]
    )
    assert not score_text(ans(text="only one"), ["one", "two"])


def test_citation_validity_and_numeric_grounding():
    assert citation_validity([], {"A"}) is None
    assert citation_validity(["A", "B"], {"A"}) == 0.5
    assert numeric_grounded(383.285, "USD_billions", [383285e6])
    assert not numeric_grounded(999.0, "USD_millions", [383285.0])


def test_tool_use_metrics():
    assert required_tool_recall(["a", "b"], ["a"]) == 0.5
    assert required_tool_recall([], ["a"]) is None
    calls = [("t", {"x": 1}), ("t", {"x": 1}), ("t", {"x": 2})]
    assert redundant_call_rate(calls) == pytest.approx(1 / 3)
    assert error_call_rate([True, False, False, False]) == 0.25
