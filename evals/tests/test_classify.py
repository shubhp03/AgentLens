"""One test per failure category, driving real AgentRunner traces through scorer + classifier."""

from __future__ import annotations

import json

from agentlens_agent.llm.base import LLMError
from agentlens_evals.case import Expected
from agentlens_evals.classify import ALL_CATEGORIES
from helpers import (
    FACTS_FY22_FY23,
    call,
    case,
    execute,
    fact,
    good_answer,
    resp,
    submit,
)

GOLD_ROWS = {"get_financial_facts": FACTS_FY22_FY23}


async def test_pass_has_no_category():
    r = await execute(
        case(),
        [resp(call("get_financial_facts", cik="1")), resp(good_answer())],
        GOLD_ROWS,
    )
    assert r.passed and r.failure_category is None and r.numeric_grounded is True


async def test_provider_error():
    r = await execute(case(), [LLMError("down", retryable=False)])
    assert r.failure_category == "provider_error"


async def test_step_limit():
    script = [resp(call("calculate", expression=str(i))) for i in range(2)]
    r = await execute(case(), script, max_steps=2)
    assert r.failure_category == "step_limit"


async def test_budget_exceeded():
    r = await execute(case(), [resp(call("calculate", expression="1"))], price=1e6, max_cost=0.01)
    assert r.failure_category == "budget_exceeded"


async def test_format_error_when_never_submits():
    r = await execute(case(), [resp(text="383285"), resp(text="383285")])
    assert r.failure_category == "format_error"


async def test_missed_abstain():
    c = case(
        category="unanswerable",
        expected=Expected(kind="abstain"),
    )
    r = await execute(c, [resp(submit(value=5.0, unit="count"))])
    assert r.failure_category == "missed_abstain"


async def test_abstain_correct_passes():
    c = case(category="unanswerable", expected=Expected(kind="abstain"))
    r = await execute(c, [resp(submit(abstained=True))])
    assert r.passed


async def test_false_abstain():
    r = await execute(case(), [resp(submit(abstained=True))])
    assert r.failure_category == "false_abstain"


async def test_tool_error_unrecovered():
    def boom(_):
        raise RuntimeError("503 from SEC")

    r = await execute(
        case(),
        [resp(call("get_financial_facts", cik="1")), resp(submit(value=1.0, unit="count"))],
        {"get_financial_facts": boom},
    )
    assert r.failure_category == "tool_error_unrecovered"


async def test_unit_error_scale():
    r = await execute(
        case(),
        [resp(call("get_financial_facts")), resp(good_answer(value=383285.0, unit="USD_billions"))],
        GOLD_ROWS,
    )
    assert r.failure_category == "unit_error"


async def test_unit_error_dimension():
    r = await execute(
        case(),
        [resp(call("get_financial_facts")), resp(good_answer(value=383285.0, unit="shares"))],
        GOLD_ROWS,
    )
    assert r.failure_category == "unit_error"


async def test_wrong_period_picks_other_year_fact():
    r = await execute(
        case(),
        [resp(call("get_financial_facts")), resp(good_answer(value=394328.0, period="FY2022"))],
        GOLD_ROWS,
    )
    assert r.failure_category == "wrong_period"


async def test_wrong_period_label_only():
    r = await execute(
        case(),
        [resp(call("get_financial_facts")), resp(good_answer(period="FY2022"))],
        GOLD_ROWS,
    )
    assert r.failure_category == "wrong_period"


async def test_calc_error_inputs_present_result_wrong():
    c = case(
        category="calc",
        expected=Expected(kind="numeric", value=-2.8, unit="percent", rel_tolerance=0.02),
    )
    r = await execute(
        c,
        [
            resp(call("get_financial_facts")),
            resp(submit(value=2.8, unit="percent", period="FY2023")),
        ],
        GOLD_ROWS,
    )
    assert r.failure_category == "calc_error"


async def test_hallucinated_number():
    r = await execute(
        case(),
        [resp(call("resolve_company", query="apple")), resp(good_answer(value=999999.0))],
        {"resolve_company": json.dumps([{"cik": "0000320193", "name": "Apple Inc."}])},
    )
    assert r.failure_category == "hallucinated_number"


async def test_wrong_tool_when_required_tool_never_called():
    c = case(required_tools=["resolve_company", "get_financial_facts"])
    # answer is grounded in a (wrong) number a tool returned, so rule 11 does not fire
    r = await execute(
        c,
        [
            resp(call("calculate", expression="5")),
            resp(good_answer(value=5.0, unit="USD_millions")),
        ],
        {"calculate": "5"},
    )
    assert r.failure_category == "wrong_tool"


async def test_bad_args():
    calls = {"n": 0}

    def flaky(_):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise ValueError("concept not found")
        return json.dumps([fact("2021-09-25", 365_817e6)])

    r = await execute(
        case(),
        [
            resp(call("get_financial_facts", concept="Nope")),
            resp(call("get_financial_facts", concept="Nope2")),
            resp(call("get_financial_facts", concept="Revenues")),
            resp(good_answer(value=365817.0, period="FY2023")),
        ],
        {"get_financial_facts": flaky},
    )
    assert r.failure_category == "bad_args"


async def test_retrieval_miss_gold_never_in_tool_output():
    r = await execute(
        case(),
        [resp(call("get_financial_facts")), resp(good_answer(value=365817.0))],
        {"get_financial_facts": json.dumps([fact("2021-09-25", 365_817e6)])},
    )
    assert r.failure_category == "retrieval_miss"


async def test_other_is_the_fallthrough():
    c = case(
        category="text_extract",
        expected=Expected(kind="text", must_contain=["Americas"]),
    )
    r = await execute(
        c,
        [resp(call("get_financial_facts")), resp(submit(text="Europe only"))],
        {"get_financial_facts": "segments: Americas, Europe"},
    )
    assert r.failure_category == "other"


def test_categories_match_database_enum():
    """The classifier's categories must equal the CHECK constraint in the Postgres migration."""
    import re
    from pathlib import Path

    sql = (
        next(p for p in Path(__file__).resolve().parents if (p / "gateway").exists())
        / "gateway/migrations/V1__init.sql"
    )
    m = re.search(
        r"failure_category text CHECK \(failure_category IN\s*\((.*?)\)\)", sql.read_text(), re.S
    )
    assert m
    db = set(re.findall(r"'([a-z_]+)'", m.group(1)))
    assert db == set(ALL_CATEGORIES)
