"""EV-03: the gold-candidate generator (scripts/gold_candidates.py). Offline: synthetic companyfacts, no SEC calls."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from agentlens_evals.case import EvalCase

pytest.importorskip(
    "agentlens_tools"
)  # the script reads SEC via the tools package (present in the uv workspace)
_PATH = Path(__file__).resolve().parents[2] / "scripts" / "gold_candidates.py"
_spec = importlib.util.spec_from_file_location("gold_candidates", _PATH)
gc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gc)


def row(
    end: str,
    val: float,
    *,
    start: str | None,
    accn: str = "0000000001-24-000001",
    form: str = "10-K",
    filed: str = "2024-02-01",
):
    r = {"end": end, "val": val, "accn": accn, "form": form, "filed": filed}
    if start:
        r["start"] = start
    return r


def facts(**concepts: list[dict]) -> dict:
    return {"facts": {"us-gaap": {k: {"units": {"USD": v}} for k, v in concepts.items()}}}


SPEC = {
    "companies": {"AAA": {"name": "Alpha"}, "BBB": {"name": "Beta"}},
    "metrics": {
        "revenue": {
            "label": "total revenues",
            "flow": True,
            "concepts": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"],
        },
        "op_income": {
            "label": "operating income",
            "flow": True,
            "concepts": ["OperatingIncomeLoss"],
        },
        "total_assets": {"label": "total assets", "flow": False, "concepts": ["Assets"]},
    },
}

AAA = facts(
    Revenues=[
        row("2022-12-31", 100e6, start="2022-01-01"),
        row("2023-12-31", 120e6, start="2023-01-01"),
    ],
    OperatingIncomeLoss=[row("2023-12-31", 30e6, start="2023-01-01")],
    Assets=[row("2023-12-31", 500e6, start=None)],
)
BBB = facts(
    RevenueFromContractWithCustomerExcludingAssessedTax=[
        row("2022-06-30", 200e6, start="2021-07-01"),
        row("2023-06-30", 210e6, start="2022-07-01"),
    ],
    OperatingIncomeLoss=[row("2023-06-30", 21e6, start="2022-07-01")],
)


class FakeClient:
    async def tickers(self):
        return {"0": {"ticker": "AAA", "cik_str": 1}, "1": {"ticker": "BBB", "cik_str": 2}}

    async def company_facts(self, cik):
        return {1: AAA, 2: BBB}[cik]


def gen() -> gc.Gen:
    return gc.Gen(SPEC, FakeClient())


async def test_lookup_candidate_is_a_draft_with_provenance_and_valid_schema() -> None:
    c = {
        "key": "lookup-aaa-revenue-fy2023",
        "ticker": "AAA",
        "metric": "revenue",
        "end": "2023-12-31",
        "fy": "FY2023",
        "difficulty": "easy",
    }
    case, _, _ = await gen().lookup(c)
    EvalCase.model_validate(case)
    assert case["status"] == "draft" and case["provenance"]["verified_by"] is None
    assert case["expected"] == {
        "kind": "numeric",
        "value": 120.0,
        "unit": "USD_millions",
        "period": "FY2023",
        "rel_tolerance": gc.LOOKUP_TOL,
    }
    assert (
        "December 31, 2023" in case["question"]
        and case["provenance"]["xbrl_concept"] == "us-gaap:Revenues"
    )


async def test_instant_metric_ignores_duration_facts_and_uses_as_of_wording() -> None:
    c = {
        "key": "lookup-aaa-assets-fy2023",
        "ticker": "AAA",
        "metric": "total_assets",
        "end": "2023-12-31",
        "fy": "FY2023",
        "difficulty": "easy",
    }
    case, _, _ = await gen().lookup(c)
    assert case["expected"]["value"] == 500.0 and "as of December 31, 2023" in case["question"]


async def test_concept_priority_falls_through_to_the_one_that_has_data() -> None:
    c = {
        "key": "lookup-bbb-revenue-fy2023",
        "ticker": "BBB",
        "metric": "revenue",
        "end": "2023-06-30",
        "fy": "FY2023",
        "difficulty": "easy",
    }
    case, _, _ = await gen().lookup(c)
    assert case["provenance"]["xbrl_concept"].endswith(
        "RevenueFromContractWithCustomerExcludingAssessedTax"
    )


async def test_growth_and_margin_are_computed_from_the_facts() -> None:
    g = {
        "key": "calc-aaa-growth-fy23",
        "kind": "growth",
        "ticker": "AAA",
        "metric": "revenue",
        "prev_end": "2022-12-31",
        "end": "2023-12-31",
        "fy": "FY2023",
        "difficulty": "easy",
    }
    case, _, _ = await gen().growth(g)
    assert case["expected"]["value"] == 20.0 and case["expected"]["unit"] == "percent"
    m = {
        "key": "calc-aaa-opm-fy23",
        "ticker": "AAA",
        "num": "op_income",
        "den": "revenue",
        "margin_name": "operating margin",
        "end": "2023-12-31",
        "fy": "FY2023",
        "difficulty": "easy",
    }
    case, _, _ = await gen().margin(m)
    assert case["expected"]["value"] == 25.0


async def test_multi_hop_gold_is_the_difference_so_both_sides_are_needed() -> None:
    c = {
        "key": "multihop-opm-aaa-bbb",
        "num": "op_income",
        "margin_name": "operating margin",
        "fy": "FY2023",
        "difficulty": "hard",
        "a": {"ticker": "AAA", "den": "revenue", "end": "2023-12-31"},
        "b": {"ticker": "BBB", "den": "revenue", "end": "2023-06-30"},
    }
    case, facts_used, detail = await gen().higher_margin(c)
    assert case["expected"]["value"] == 15.0  # 25% vs 10%
    assert "period" not in case["expected"] and len(facts_used) == 4 and "Alpha higher" in detail


async def test_comparisons_that_are_too_close_are_rejected() -> None:
    c = {
        "key": "multihop-close",
        "num": "op_income",
        "margin_name": "operating margin",
        "fy": "FY2023",
        "difficulty": "hard",
        "a": {"ticker": "AAA", "den": "revenue", "end": "2023-12-31"},
        "b": {"ticker": "AAA", "den": "revenue", "end": "2023-12-31"},
    }
    with pytest.raises(LookupError, match="gap"):
        await gen().higher_margin(c)


def test_restatement_is_detected_and_the_latest_value_wins() -> None:
    rows = [
        row("2022-12-31", 100.0, start="2022-01-01", accn="a-1", filed="2023-02-01"),
        row("2022-12-31", 90.0, start="2022-01-01", accn="a-2", filed="2024-02-01"),
    ]
    f = gc.pick(facts(Revenues=rows), "AAA", 1, "revenue", SPEC, "2022-12-31")
    assert f.restated and f.value == 90.0 and f.accession == "a-2" and f.original["val"] == 100.0


def test_quarterly_and_non_10k_facts_are_ignored() -> None:
    rows = [
        row("2023-12-31", 30.0, start="2023-10-01"),
        row("2023-12-31", 99.0, start="2023-01-01", form="10-Q"),
    ]
    with pytest.raises(LookupError):
        gc.pick(facts(Revenues=rows), "AAA", 1, "revenue", SPEC, "2023-12-31")


def test_splits_hold_out_every_fourth_case_per_category() -> None:
    cases = [{"category": "lookup"} for _ in range(8)] + [{"category": "calc"} for _ in range(4)]
    gc.assign_splits(cases)
    assert [c["split"] for c in cases[:8]] == ["dev", "dev", "dev", "test"] * 2
    assert [c["split"] for c in cases[8:]] == ["dev", "dev", "dev", "test"]


def test_authored_unanswerables_are_abstain_drafts() -> None:
    case = gc.authored_case(
        {"key": "unans-future-thing", "q": "What will X earn in 2099?", "difficulty": "easy"}
    )
    EvalCase.model_validate(case)
    assert case["expected"] == {"kind": "abstain"} and case["status"] == "draft"
