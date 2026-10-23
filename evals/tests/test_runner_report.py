from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from agentlens_agent.answer import Answer
from agentlens_agent.events import RunFinished, RunTotals, Usage
from agentlens_evals.report import case_outcomes, percentile, render_report, summarize
from agentlens_evals.runner import RunRecord, load_done, run_eval
from helpers import case


def rec(key="lookup-a", repeat=0, passed=True, cat=None, cost=0.1, lat=1000, **kw) -> RunRecord:
    return RunRecord(
        case_key=key,
        repeat=repeat,
        passed=passed,
        failure_category=None if passed else (cat or "other"),
        termination_reason="answered",
        cost_usd=cost,
        latency_ms=lat,
        steps=2,
        tool_calls=3,
        **kw,
    )


def fake_factory(cost=0.1, answer=None, delay=0.0, boom_on: str | None = None):
    def factory(question: str):
        async def gen():
            if boom_on and boom_on in question:
                raise RuntimeError("kaboom")
            await asyncio.sleep(delay)
            yield RunFinished(
                run_id=uuid4(),
                status="completed",
                termination_reason="answered",
                final_answer=answer or Answer(text="x", abstained=True),
                totals=RunTotals(usage=Usage(), cost_usd=cost, latency_ms=5, steps=1, tool_calls=0),
            )

        return gen()

    return factory


def abstain_case(key):
    from agentlens_evals.case import Expected

    return case(case_key=key, category="unanswerable", expected=Expected(kind="abstain"))


async def test_run_eval_writes_results_and_resumes(tmp_path):
    cases = [abstain_case("unans-aaa"), abstain_case("unans-bbb")]
    path = tmp_path / "results.jsonl"
    out = await run_eval(
        cases, fake_factory(), repeats=2, budget_usd=10, per_run_cap_usd=1, results_path=path
    )
    assert len(out.records) == 4 and all(r.passed for r in out.records)
    assert len(path.read_text().splitlines()) == 4

    calls = []
    out2 = await run_eval(
        cases,
        lambda q: calls.append(q) or fake_factory()(q),
        repeats=3,
        budget_usd=10,
        per_run_cap_usd=1,
        results_path=path,
    )
    assert len(calls) == 2  # only the third repeat of each case ran
    assert len(out2.records) == 6 and len(load_done(path)) == 6


async def test_budget_stops_new_runs(tmp_path):
    cases = [abstain_case(f"unans-{i:03d}") for i in range(10)]
    out = await run_eval(
        cases,
        fake_factory(cost=0.5),
        repeats=1,
        budget_usd=1.2,
        per_run_cap_usd=0.5,
        results_path=tmp_path / "r.jsonl",
        concurrency=1,
    )
    assert out.spent_usd <= 1.2 + 1e-9
    assert out.skipped_for_budget > 0 and len(out.records) + out.skipped_for_budget == 10


async def test_budget_reservation_bounds_overshoot_under_concurrency(tmp_path):
    cases = [abstain_case(f"unans-{i:03d}") for i in range(20)]
    out = await run_eval(
        cases,
        fake_factory(cost=0.5, delay=0.01),
        repeats=1,
        budget_usd=2.0,
        per_run_cap_usd=0.5,
        results_path=tmp_path / "r.jsonl",
        concurrency=8,
    )
    assert out.spent_usd <= 2.0 + 1e-9  # in-flight reservations prevent the classic overshoot


async def test_one_crashing_run_does_not_kill_the_eval(tmp_path):
    good, bad = abstain_case("unans-good"), abstain_case("unans-bad")
    bad = bad.model_copy(update={"question": "please BOOM"})
    out = await run_eval(
        [good, bad],
        fake_factory(boom_on="BOOM"),
        repeats=1,
        budget_usd=5,
        per_run_cap_usd=1,
        results_path=tmp_path / "r.jsonl",
    )
    by = {r.case_key: r for r in out.records}
    assert by["unans-good"].passed
    assert not by["unans-bad"].passed and "kaboom" in by["unans-bad"].error


async def test_traces_are_written(tmp_path):
    await run_eval(
        [abstain_case("unans-aaa")],
        fake_factory(),
        repeats=1,
        budget_usd=5,
        per_run_cap_usd=1,
        results_path=tmp_path / "r.jsonl",
        trace_dir=tmp_path / "traces",
    )
    lines = (tmp_path / "traces" / "unans-aaa__0.jsonl").read_text().splitlines()
    assert json.loads(lines[-1])["kind"] == "run.finished"


def test_percentile_nearest_rank():
    assert percentile([1, 2, 3, 4, 5], 50) == 3
    assert percentile([1, 2, 3, 4, 5], 95) == 5
    assert percentile([], 50) != percentile([], 50)  # nan


def test_case_outcome_is_strict_majority():
    recs = [rec("a", 0, True), rec("a", 1, True), rec("a", 2, False)]
    recs += [rec("b", 0, True), rec("b", 1, False)]  # tie -> not a pass
    assert case_outcomes(recs) == {"a": True, "b": False}


def test_summary_numbers():
    cases = {c.case_key: c for c in [abstain_case("unans-aaa"), abstain_case("unans-bbb")]}
    recs = [
        rec("unans-aaa", 0, True, cost=0.2, lat=1000),
        rec("unans-bbb", 0, False, cat="missed_abstain", cost=0.4, lat=3000),
    ]
    s = summarize(recs, cases)
    assert s["pass_rate"] == 0.5 and s["n_cases"] == 2
    assert s["cost_per_success"] == pytest.approx(0.6)  # total cost / passes: failures are not free
    assert s["missed_abstain"] == (1, 2)
    assert s["failures"]["missed_abstain"] == 1
    assert s["latency_p95"] == 3000


def test_report_renders_and_warns_on_drafts():
    cases = [abstain_case("unans-aaa")]
    recs = [rec("unans-aaa", 0, False, cat="missed_abstain")]
    text = render_report(recs, cases, {"label": "t", "model": "fake:m", "repeats": 1})
    assert "Pass rate: 0.0%" in text
    assert "missed_abstain" in text and "Wilson 95% CI" in text
    assert "unans-aaa" in text and "fake:m" in text


def test_report_flags_draft_cases():
    c = abstain_case("unans-aaa")  # status defaults to draft
    assert "WARNING" in render_report([rec("unans-aaa")], [c], {})
