"""Generate the dashboard's demo-mode data (dashboard/public/demo/*.json).

EVERYTHING GENERATED HERE IS SYNTHETIC. The companies are fictional, the model names are fake, the
prices are made up, the outcomes are scripted from a seeded RNG, and latencies are synthesized. The only
real parts are the code paths: the real AgentRunner produces the traces, the real scorer/classifier produces
the pass/fail and failure categories, and the real Wilson/McNemar code produces the statistics. The point is
to let anyone explore the UI with zero spend. It must never be presented as benchmark results.

Run:  uv run python scripts/gen_demo_data.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import shutil
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from agentlens_agent.answer import SUBMIT_ANSWER_NAME
from agentlens_agent.config import AgentConfig
from agentlens_agent.cost import ModelPrice, PricingTable
from agentlens_agent.events import RunFinished, RunStarted, SpanFinished, Usage
from agentlens_agent.llm.base import LLMError, LLMResponse, ToolCall, ToolSpec
from agentlens_agent.llm.fake import ScriptedLLM
from agentlens_agent.loop import AgentRunner
from agentlens_agent.registry import StaticToolRegistry
from agentlens_evals.case import EvalCase, Expected, Provenance
from agentlens_evals.compare import compare_case_outcomes
from agentlens_evals.report import case_outcomes, percentile, summarize
from agentlens_evals.runner import RunRecord, run_eval
from agentlens_evals.stats import wilson_interval

OUT = Path(__file__).resolve().parents[1] / "dashboard" / "public" / "demo"
NS = UUID("6f1c1b7e-0000-4000-8000-00000000a9e1")
NOW = datetime(2026, 10, 15, 16, 0, tzinfo=UTC)  # "today" in the demo
SEED = 20261018


def uid(*parts: object) -> str:
    return str(uuid5(NS, "/".join(map(str, parts))))


# ----------------------------------------------------------------------------- the fictional world
COMPANIES: dict[str, dict[str, Any]] = {
    "Acme Corp": {
        "cik": "0009990001",
        "revenue": {2021: 4210.0, 2022: 4820.0, 2023: 5310.0},
        "op_income": {2021: 610.0, 2022: 690.0, 2023: 801.0},
        "net_income": {2021: 455.0, 2022: 512.0, 2023: 603.0},
        "segments": ["Industrial", "Consumer", "Services"],
        "largest": "Industrial",
    },
    "Globex Industries": {
        "cik": "0009990002",
        "revenue": {2021: 9120.0, 2022: 9480.0, 2023: 9205.0},
        "op_income": {2021: 1180.0, 2022: 1260.0, 2023: 1010.0},
        "net_income": {2021: 840.0, 2022: 905.0, 2023: 722.0},
        "segments": ["Aerospace", "Energy"],
        "largest": "Aerospace",
    },
}
METRIC_NAME = {
    "revenue": "Revenues",
    "op_income": "OperatingIncomeLoss",
    "net_income": "NetIncomeLoss",
}
METRIC_WORDS = {
    "revenue": "total revenue",
    "op_income": "operating income",
    "net_income": "net income",
}


def accession(company: str, year: int) -> str:
    n = 9990000000 + int(COMPANIES[company]["cik"][-1]) * 100000 + year
    return f"{n:010d}-{year % 100:02d}-000001"


def fact_rows(company: str, metric: str) -> str:
    c = COMPANIES[company]
    rows = [
        {
            "start": f"{y}-01-01",
            "end": f"{y}-12-31",
            "value": v * 1e6,
            "unit": "USD",
            "accession": accession(company, y),
            "form": "10-K",
            "filed": f"{y + 1}-02-15",
            "reported_in_fy": y,
            "reported_in_fp": "FY",
        }
        for y, v in sorted(c[metric].items())
    ]
    return json.dumps(rows)


def pct_change(a: float, b: float) -> float:
    return (b - a) / a * 100


# ----------------------------------------------------------------------------- the demo dataset
def numeric(value: float, unit: str, period: str | None, tol: float = 0.005) -> Expected:
    return Expected(
        kind="numeric", value=round(value, 4), unit=unit, period=period, rel_tolerance=tol
    )


def mk(key, q, cat, diff, exp, tools, meta) -> tuple[EvalCase, dict[str, Any]]:
    case = EvalCase(
        case_key=key,
        question=q,
        category=cat,
        difficulty=diff,
        expected=exp,
        required_tools=tools,
        provenance=Provenance(
            source="synthetic fixture (fictional company)", verified_by="demo-generator"
        ),
        status="verified",
        split="test" if hash(key) % 4 == 0 else "dev",
    )
    return case, meta


def build_cases() -> list[tuple[EvalCase, dict[str, Any]]]:
    out: list[tuple[EvalCase, dict[str, Any]]] = []
    base_tools = ["resolve_company", "get_financial_facts"]
    for co, metric, year, diff in [
        ("Acme Corp", "revenue", 2023, "easy"), ("Acme Corp", "revenue", 2022, "easy"),
        ("Acme Corp", "op_income", 2023, "easy"), ("Acme Corp", "net_income", 2022, "medium"),
        ("Globex Industries", "revenue", 2023, "easy"), ("Globex Industries", "op_income", 2022, "medium"),
        ("Globex Industries", "net_income", 2023, "medium"), ("Globex Industries", "revenue", 2021, "easy"),
    ]:  # fmt: skip
        key = f"lookup-{co.split()[0].lower()}-{metric.replace('_', '')}-fy{year}"
        q = f"What was {co}'s {METRIC_WORDS[metric]} for fiscal year {year}?"
        gold = COMPANIES[co][metric][year]
        out.append(mk(key, q, "lookup", diff, numeric(gold, "USD_millions", f"FY{year}"), base_tools,
                      dict(co=co, metric=metric, year=year, gold=gold, unit="USD_millions", kind="lookup")))  # fmt: skip

    calc_tools = [*base_tools, "calculate"]
    for co in COMPANIES:
        r = COMPANIES[co]["revenue"]
        g = pct_change(r[2022], r[2023])
        out.append(mk(f"calc-{co.split()[0].lower()}-revgrowth-fy23", f"By what percent did {co}'s revenue change from fiscal 2022 to fiscal 2023?",
                      "calc", "medium", numeric(g, "percent", "FY2023", 0.01), calc_tools,
                      dict(co=co, metric="revenue", gold=g, unit="percent", kind="growth", expr=f"pct_change({r[2022]}, {r[2023]})")))  # fmt: skip
        m = COMPANIES[co]["op_income"][2023] / r[2023] * 100
        out.append(mk(f"calc-{co.split()[0].lower()}-opmargin-fy23", f"What was {co}'s operating margin (operating income / revenue) in fiscal 2023?",
                      "calc", "medium", numeric(m, "percent", "FY2023", 0.01), [*calc_tools],
                      dict(co=co, metric="revenue+op_income", gold=m, unit="percent", kind="margin", expr=f"{COMPANIES[co]['op_income'][2023]} / {r[2023]} * 100")))  # fmt: skip
    nm = COMPANIES["Acme Corp"]["net_income"][2022] / COMPANIES["Acme Corp"]["revenue"][2022] * 100
    out.append(mk("calc-acme-netmargin-fy22", "What was Acme Corp's net margin in fiscal 2022?", "calc", "medium",
                  numeric(nm, "percent", "FY2022", 0.01), calc_tools,
                  dict(co="Acme Corp", metric="revenue+net_income", gold=nm, unit="percent", kind="margin", expr="512.0 / 4820.0 * 100")))  # fmt: skip
    a = COMPANIES["Acme Corp"]["revenue"]
    cagr = ((a[2023] / a[2021]) ** 0.5 - 1) * 100
    out.append(mk("calc-acme-revcagr-fy21-23", "What was Acme Corp's compound annual revenue growth rate from fiscal 2021 to fiscal 2023?",
                  "calc", "hard", numeric(cagr, "percent", None, 0.01), calc_tools,
                  dict(co="Acme Corp", metric="revenue", gold=cagr, unit="percent", kind="growth", expr=f"cagr({a[2021]}, {a[2023]}, 2)")))  # fmt: skip

    text_tools = ["resolve_company", "list_filings", "get_filing_section"]
    for co in COMPANIES:
        segs = COMPANIES[co]["segments"]
        out.append(mk(f"text-{co.split()[0].lower()}-segments", f"Which reportable segments does {co} disclose in its latest 10-K?", "text_extract", "medium",
                      Expected(kind="text", must_contain=segs), text_tools, dict(co=co, kind="text", answer=", ".join(segs))))  # fmt: skip
    out.append(mk("text-globex-largest-segment", "Which is Globex Industries' largest segment by revenue?", "text_extract", "hard",
                  Expected(kind="text", must_contain=["Aerospace"]), text_tools, dict(co="Globex Industries", kind="text", answer="Aerospace")))  # fmt: skip

    ac, gl = COMPANIES["Acme Corp"], COMPANIES["Globex Industries"]
    out.append(mk("multi-higher-opmargin-fy23", "Between Acme Corp and Globex Industries, which had the higher operating margin in fiscal 2023?", "multi_hop", "hard",
                  Expected(kind="text", must_contain=["Acme"]), [*base_tools, "calculate"], dict(co="Acme Corp", kind="text", answer="Acme Corp (15.1% vs 11.0%)", metric="revenue+op_income")))  # fmt: skip
    out.append(mk("multi-revenue-decline-fy23", "Which of Acme Corp and Globex Industries reported lower revenue in fiscal 2023 than in 2022?", "multi_hop", "medium",
                  Expected(kind="text", must_contain=["Globex"]), base_tools, dict(co="Globex Industries", kind="text", answer="Globex Industries", metric="revenue")))  # fmt: skip
    comb = ac["revenue"][2023] + gl["revenue"][2023]
    out.append(mk("multi-combined-revenue-fy23", "What was the combined fiscal 2023 revenue of Acme Corp and Globex Industries?", "multi_hop", "medium",
                  numeric(comb, "USD_millions", "FY2023"), [*base_tools, "calculate"],
                  dict(co="Acme Corp", metric="revenue", gold=comb, unit="USD_millions", kind="sum", expr=f"{ac['revenue'][2023]} + {gl['revenue'][2023]}")))  # fmt: skip

    for key, q, diff in [
        (
            "unans-acme-fy2030-eps",
            "What will Acme Corp report as earnings per share for fiscal 2030?",
            "easy",
        ),
        ("unans-initech-revenue", "What was Initech Holdings' revenue last year?", "easy"),
        (
            "unans-globex-q4-2027",
            "What was Globex Industries' revenue in the fourth quarter of fiscal 2027?",
            "medium",
        ),
        (
            "unans-acme-headcount-plan",
            "How many people does Acme Corp plan to hire next year?",
            "medium",
        ),
    ]:
        out.append(
            mk(
                key,
                q,
                "unanswerable",
                diff,
                Expected(kind="abstain"),
                [],
                dict(co="Acme Corp", kind="unanswerable"),
            )
        )
    return out


# ----------------------------------------------------------------------------- scripted agent behaviours
LARGE = ("demo", "demo-model-large", 3.0, 15.0)
SMALL = ("demo", "demo-model-small", 0.25, 1.25)

TOOL_SPECS = {n: ToolSpec(n, n, {"type": "object"}) for n in
              ["resolve_company", "list_filings", "get_financial_facts", "get_filing_section", "search_xbrl_concepts", "calculate"]}  # fmt: skip


class Scenario:
    """Builds the scripted LLM turns + tool outputs for one run and one chosen outcome."""

    def __init__(
        self,
        case: EvalCase,
        meta: dict[str, Any],
        outcome: str,
        rng: random.Random,
        tools_enabled: bool,
    ):
        self.case, self.meta, self.outcome, self.rng, self.tools_enabled = (
            case,
            meta,
            outcome,
            rng,
            tools_enabled,
        )
        self.outputs: dict[str, Any] = {}  # tool name -> str | callable
        self.n = 0

    def tc(self, name: str, **args: Any) -> ToolCall:
        self.n += 1
        return ToolCall(f"call_{self.n}", name, args)

    def turn(self, *calls: ToolCall, text: str | None = None) -> LLMResponse:
        i = len(getattr(self, "_turns", []))
        self._turns = [*getattr(self, "_turns", []), 1]
        usage = Usage(
            input_tokens=1400 + 950 * i + self.rng.randint(0, 300),
            output_tokens=self.rng.randint(60, 240),
        )
        return LLMResponse(text, tuple(calls), usage)

    def submit(self, **kw: Any) -> ToolCall:
        kw.setdefault("text", "See value.")
        return ToolCall(f"call_{self.n + 1}_sub", SUBMIT_ANSWER_NAME, kw)

    def cite(self, year: int | None = None) -> list[dict[str, Any]]:
        co, y = self.meta["co"], year or self.meta.get("year", 2023)
        return [
            {
                "accession": accession(co, y),
                "concept": f"us-gaap:{METRIC_NAME.get(self.meta.get('metric', 'revenue').split('+')[0], 'Revenues')}",
            }
        ]

    def good_numeric(self, **over: Any) -> ToolCall:
        m = self.meta
        kw: dict[str, Any] = dict(value=round(m["gold"], 4), unit=m["unit"], period=self.case.expected.period,
                                  text=f"{m['co']}: {m['gold']:,.2f} ({m['unit']}).", citations=self.cite())  # fmt: skip
        kw.update(over)
        return self.submit(**kw)

    def build(self) -> list[LLMResponse | LLMError]:
        m, o = self.meta, self.outcome
        kind = m["kind"]
        if kind == "unanswerable":
            return self._unanswerable()
        if not self.tools_enabled:
            return self._no_tools()
        co = m["co"]
        resolve = self.tc("resolve_company", query=co)
        self.outputs["resolve_company"] = json.dumps([{"cik": COMPANIES[co]["cik"], "name": co}])
        if o == "format_error":
            return [
                self.turn(resolve),
                self.turn(text=f"The answer is {m.get('gold', m.get('answer'))}."),
                self.turn(text="As above."),
            ]
        if o == "step_limit":
            out: list[LLMResponse | LLMError] = [self.turn(resolve)]
            for i in range(11):
                out.append(
                    self.turn(
                        self.tc("list_filings", cik=COMPANIES[co]["cik"], form="10-K", page=i)
                    )
                )
            self.outputs["list_filings"] = json.dumps(
                [{"accession": accession(co, 2023), "form": "10-K"}]
            )
            return out
        if kind == "text":
            return self._text(resolve)
        return self._numeric(resolve)

    # -- numeric (lookup / growth / margin / sum)
    def _numeric(self, resolve: ToolCall) -> list[LLMResponse | LLMError]:
        m, o = self.meta, self.outcome
        co = m["co"]
        cik = COMPANIES[co]["cik"]
        metrics = m["metric"].split("+")
        facts_calls = [
            self.tc("get_financial_facts", cik=cik, concept=METRIC_NAME[x], period_type="annual")
            for x in metrics
        ]
        if m["kind"] == "sum":
            other = self.tc(
                "get_financial_facts",
                cik=COMPANIES["Globex Industries"]["cik"],
                concept="Revenues",
                period_type="annual",
            )
            facts_calls.append(other)
        # The fixture returns rows for the requested metric of the requested company (one handler per call id).
        by_id: dict[str, str] = {}
        for fc in facts_calls:
            c_name = next(k for k, v in COMPANIES.items() if v["cik"] == fc.arguments["cik"])
            metric = next(k for k, v in METRIC_NAME.items() if v == fc.arguments["concept"])
            by_id[fc.id] = fact_rows(c_name, metric)
        if (
            o == "retrieval_miss"
        ):  # agent picked the wrong concept: returns net income rows for a revenue question
            wrong = "net_income" if "net_income" not in metrics else "op_income"
            for fc in facts_calls:
                by_id[fc.id] = fact_rows(co, wrong)
        self.outputs["get_financial_facts"] = ("by_call_id", by_id)
        turns = [self.turn(resolve), self.turn(*facts_calls)]
        gold = m["gold"]

        if o == "tool_error_unrecovered":
            self.outputs["get_financial_facts"] = (
                "raise",
                "HTTP 503 from SEC EDGAR (fair-access throttle)",
            )
            turns.append(
                self.turn(
                    self.submit(
                        value=round(gold * 0.9, 2),
                        unit=m["unit"],
                        period=self.case.expected.period,
                        text="Estimated.",
                        citations=[],
                    )
                )
            )
            return turns

        if m["kind"] in {"growth", "margin", "sum"}:
            calc = self.tc("calculate", expression=m["expr"])
            wrong_val = round(gold * 1.07 + 0.31, 4)
            if o == "calc_error":
                calc = self.tc("calculate", expression=f"{m['expr']} * 1.07 + 0.31")
                self.outputs["calculate"] = str(wrong_val)
            else:
                self.outputs["calculate"] = str(round(gold, 4))
            turns.append(self.turn(calc))
            if o == "calc_error":
                turns.append(self.turn(self.good_numeric(value=wrong_val)))
                return turns

        if o == "unit_error":
            bad_unit = "USD_billions" if m["unit"] == "USD_millions" else "USD_millions"
            turns.append(self.turn(self.good_numeric(unit=bad_unit)))
        elif o == "wrong_period" and m["kind"] == "lookup":
            y = m["year"] - 1 if m["year"] > 2021 else m["year"] + 1
            turns.append(
                self.turn(
                    self.good_numeric(
                        value=COMPANIES[co][metrics[0]][y], period=f"FY{y}", citations=self.cite(y)
                    )
                )
            )
        elif o == "hallucinated_number":
            turns.append(
                self.turn(self.good_numeric(value=round(gold * 1.13 + 7, 2), citations=[]))
            )
        elif o == "false_abstain":
            turns.append(self.turn(self.submit(abstained=True, text="I could not find this.")))
        elif o == "retrieval_miss":
            wrong = "net_income" if "net_income" not in metrics else "op_income"
            y = m.get("year", 2023)
            turns.append(
                self.turn(self.good_numeric(value=COMPANIES[co][wrong][y], citations=self.cite(y)))
            )
        else:  # pass (and any outcome that does not apply to this kind collapses to pass)
            turns.append(self.turn(self.good_numeric()))
        return turns

    # -- text / multi-hop text
    def _text(self, resolve: ToolCall) -> list[LLMResponse | LLMError]:
        m, o = self.meta, self.outcome
        co = m["co"]
        cik = COMPANIES[co]["cik"]
        lf = self.tc("list_filings", cik=cik, form="10-K")
        self.outputs["list_filings"] = json.dumps(
            [{"accession": accession(co, 2023), "form": "10-K", "primary_document": "annual.htm"}]
        )
        gs = self.tc(
            "get_filing_section",
            cik=cik,
            accession=accession(co, 2023),
            item="1",
            primary_document="annual.htm",
        )
        segs = COMPANIES[co]["segments"]
        shown = segs if o != "retrieval_miss" else segs[:1]
        self.outputs["get_filing_section"] = json.dumps({"item": "1", "untrusted_text": f"{co} operates through the following reportable segments: {', '.join(shown)}. " f"The {COMPANIES[co]['largest']} segment is the largest by revenue."})  # fmt: skip
        ans = m["answer"] if o != "retrieval_miss" else ", ".join(shown)
        turns = [self.turn(resolve), self.turn(lf), self.turn(gs)]
        if o == "false_abstain":
            turns.append(self.turn(self.submit(abstained=True, text="Not found.")))
        else:
            turns.append(
                self.turn(
                    self.submit(
                        text=ans, citations=[{"accession": accession(co, 2023), "section": "1"}]
                    )
                )
            )
        return turns

    def _multi_text(
        self,
    ) -> list[LLMResponse | LLMError]:  # not used directly; multi-hop text goes through _numeric
        raise NotImplementedError

    def _unanswerable(self) -> list[LLMResponse | LLMError]:
        o = self.outcome
        if self.tools_enabled and "initech" in self.case.case_key:
            r = self.tc("resolve_company", query="Initech Holdings")
            self.outputs["resolve_company"] = "[]"
            first = [self.turn(r)]
        else:
            first = []
        if o == "missed_abstain":
            return [
                *first,
                self.turn(
                    self.submit(
                        value=round(self.rng.uniform(1, 9), 2),
                        unit="USD",
                        text="Estimated from trend.",
                        citations=[],
                    )
                ),
            ]
        if o == "format_error":
            return [*first, self.turn(text="Probably around 4."), self.turn(text="Around 4.")]
        return [
            *first,
            self.turn(
                self.submit(abstained=True, text="This cannot be answered from public SEC filings.")
            ),
        ]

    def _no_tools(self) -> list[LLMResponse | LLMError]:
        m, o = self.meta, self.outcome
        if o == "false_abstain":
            return [self.turn(self.submit(abstained=True, text="I do not have this information."))]
        if o == "format_error":
            return [
                self.turn(text="I believe it was in the millions."),
                self.turn(text="Roughly that."),
            ]
        if m["kind"] == "text":
            ans = m["answer"] if o == "pass" else m["answer"].split(",")[0]
            return [self.turn(self.submit(text=ans, citations=[]))]
        gold = m["gold"]
        val = gold if o == "pass" else round(gold * 1.19 + 11, 2)
        return [self.turn(self.good_numeric(value=val, citations=[]))]


# ----------------------------------------------------------------------------- outcome policy per arm
ARMS: dict[str, dict[str, Any]] = {
    "baseline-large": dict(
        model=LARGE,
        tools=True,
        p_pass=dict(lookup=0.875, calc=0.83, text_extract=0.67, multi_hop=0.67, unanswerable=0.75),
        fail=dict(
            lookup=["wrong_period", "unit_error", "retrieval_miss"],
            calc=["calc_error", "unit_error", "step_limit"],
            text_extract=["retrieval_miss", "false_abstain"],
            multi_hop=["calc_error", "retrieval_miss", "step_limit"],
            unanswerable=["missed_abstain"],
        ),
    ),  # fmt: skip
    "small-model": dict(
        model=SMALL,
        tools=True,
        p_pass=dict(lookup=0.75, calc=0.50, text_extract=0.34, multi_hop=0.34, unanswerable=0.50),
        fail=dict(
            lookup=["wrong_period", "unit_error", "hallucinated_number", "tool_error_unrecovered"],
            calc=["calc_error", "unit_error", "hallucinated_number", "step_limit"],
            text_extract=["retrieval_miss", "false_abstain", "format_error"],
            multi_hop=["calc_error", "retrieval_miss", "format_error", "step_limit"],
            unanswerable=["missed_abstain", "format_error"],
        ),
    ),  # fmt: skip
    "no-tools": dict(
        model=LARGE,
        tools=False,
        p_pass=dict(lookup=0.12, calc=0.04, text_extract=0.34, multi_hop=0.12, unanswerable=0.78),
        fail=dict(
            lookup=["hallucinated_number", "false_abstain", "hallucinated_number"],
            calc=["hallucinated_number", "false_abstain"],
            text_extract=["retrieval_miss", "false_abstain"],
            multi_hop=["hallucinated_number", "false_abstain"],
            unanswerable=["missed_abstain", "format_error"],
        ),
    ),  # fmt: skip
}


def assign_primaries(
    cases, arm, hardness: dict[str, float], rng: random.Random
) -> dict[str, tuple[bool, str]]:
    """Stratified by category: the arm passes the round(p*n) EASIEST cases (shared hardness => paired,
    realistic correlation between arms). Failing cases get a failure mode from the arm's pool."""
    out: dict[str, tuple[bool, str]] = {}
    for cat in {c.category for c, _ in cases}:
        keys = sorted(
            (c.case_key for c, _ in cases if c.category == cat), key=lambda k: hardness[k]
        )
        n_pass = round(arm["p_pass"][cat] * len(keys))
        for i, k in enumerate(keys):
            out[k] = (i < n_pass, rng.choice(arm["fail"][cat]))
    return out


def pick_outcome(
    arm: dict[str, Any], cat: str, rng: random.Random, primary_pass: bool, primary_fail: str
) -> str:
    # Repeats mostly agree with the case's "primary" behaviour; the rest are flaky.
    if rng.random() < 0.82:
        return "pass" if primary_pass else primary_fail
    return "pass" if rng.random() < arm["p_pass"][cat] else rng.choice(arm["fail"][cat])


# ----------------------------------------------------------------------------- synthetic timeline
def retime(events: list[Any], start: datetime, rng: random.Random, fast: bool) -> list[Any]:
    """Replace the (instant) wall-clock stamps from the scripted run with plausible durations."""
    t = start
    out: list[Any] = []
    pending_tools: list[int] = []
    spans = [e for e in events if isinstance(e, SpanFinished) and e.type != "agent"]
    new_span: dict[Any, tuple[datetime, datetime]] = {}
    tool_batch_end = t
    batch_start = t
    last_type = None
    for s in spans:
        if s.type == "llm_call":
            dur = rng.uniform(0.5, 1.3) if fast else rng.uniform(1.0, 2.9)
            if last_type == "tool_call":
                t = tool_batch_end
            new_span[s.span_id] = (t, t + timedelta(seconds=dur))
            t = new_span[s.span_id][1]
        else:
            if last_type != "tool_call":
                batch_start = t
                tool_batch_end = t
            dur = (
                rng.uniform(0.25, 1.4)
                if s.name == "get_financial_facts"
                else rng.uniform(0.05, 0.6)
            )
            if s.name == "get_filing_section":
                dur = rng.uniform(0.9, 2.2)
            end = batch_start + timedelta(seconds=dur)
            new_span[s.span_id] = (batch_start, end)
            tool_batch_end = max(tool_batch_end, end)
        last_type = s.type
    if last_type == "tool_call":
        t = tool_batch_end
    end_t = t
    for e in events:
        if isinstance(e, RunStarted):
            out.append(e.model_copy(update={"started_at": start}))
        elif isinstance(e, SpanFinished):
            a, b = (start, end_t) if e.type == "agent" else new_span[e.span_id]
            out.append(e.model_copy(update={"started_at": a, "ended_at": b}))
        elif isinstance(e, RunFinished):
            totals = e.totals.model_copy(
                update={"latency_ms": int((end_t - start).total_seconds() * 1000)}
            )
            out.append(e.model_copy(update={"ended_at": end_t, "totals": totals}))
    del pending_tools
    return out


# ----------------------------------------------------------------------------- scenario -> events
class World:
    def __init__(self) -> None:
        self.captured: dict[str, list[Any]] = {}
        self.clock = NOW - timedelta(days=9)

    def make_tools(self, sc: Scenario) -> StaticToolRegistry:
        def handler(name: str):
            async def h(args: dict[str, Any]) -> str:
                spec = sc.outputs.get(name, "ok")
                if isinstance(spec, tuple) and spec[0] == "raise":
                    raise RuntimeError(spec[1])
                if isinstance(spec, tuple) and spec[0] == "by_call_id":
                    # find the output for this call by matching cik+concept
                    c_name = next(k for k, v in COMPANIES.items() if v["cik"] == args["cik"])
                    metric = next(
                        (k for k, v in METRIC_NAME.items() if v == args["concept"]), "revenue"
                    )
                    # honour a deliberately-wrong fixture (retrieval_miss): all outputs identical
                    outs = set(spec[1].values())
                    return next(iter(outs)) if len(outs) == 1 else fact_rows(c_name, metric)
                return spec if isinstance(spec, str) else "ok"

            return h

        return StaticToolRegistry({n: (TOOL_SPECS[n], handler(n)) for n in TOOL_SPECS})

    async def run_scenario(
        self,
        sc: Scenario,
        arm: dict[str, Any],
        config: AgentConfig,
        start: datetime,
        rng: random.Random,
    ) -> list[Any]:
        script = sc.build()
        provider, model, pin, pout = arm["model"]
        pricing = PricingTable(
            {f"{provider}:{model}": ModelPrice(input_usd_per_mtok=pin, output_usd_per_mtok=pout)},
            as_of="synthetic",
        )
        llm = ScriptedLLM(script, provider=provider, model=model)
        runner = AgentRunner(
            llm=llm,
            tools=self.make_tools(sc),
            config=config,
            system_prompt="(demo)",
            pricing=pricing,
            run_id=UUID(int=rng.getrandbits(128)),
        )
        events = [e async for e in runner.run(sc.case.question)]
        return retime(events, start, rng, fast=(model == SMALL[1]))


def agent_config(name: str, arm: dict[str, Any], tools: bool) -> AgentConfig:
    provider, model, *_ = arm["model"]
    return AgentConfig(
        name=name, provider=provider, model=model, system_prompt_id="system_v1", max_steps=12, max_cost_usd=0.50,
        tool_allowlist=["resolve_company", "list_filings", "get_financial_facts", "get_filing_section", "calculate"] if tools else [],
    )  # fmt: skip


# ----------------------------------------------------------------------------- API shapes
def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z") if dt else None


def api_spans(events: list[Any]) -> list[dict[str, Any]]:
    out = []
    for e in events:
        if isinstance(e, SpanFinished):
            d = e.model_dump(mode="json")
            usage = d.get("usage") or {}
            out.append({
                "id": d["span_id"], "run_id": d["run_id"], "parent_span_id": d["parent_span_id"], "seq": d["seq"],
                "type": d["type"], "name": d["name"], "status": d["status"], "model": d["model"],
                "started_at": d["started_at"], "ended_at": d["ended_at"],
                "latency_ms": int((e.ended_at - e.started_at).total_seconds() * 1000),
                "input": d["input"], "output": d["output"], "error": d["error"],
                "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
                "cost_usd": d["cost_usd"], "attributes": d["attributes"],
            })  # fmt: skip
    return out


def api_run(
    events: list[Any], question: str, config_id: str, source: str, eval_run_id: str | None
) -> dict[str, Any]:
    started = next(e for e in events if isinstance(e, RunStarted))
    fin = next(e for e in events if isinstance(e, RunFinished))
    t = fin.totals
    return {
        "id": str(fin.run_id), "config_id": config_id, "eval_run_id": eval_run_id, "source": source, "question": question,
        "status": fin.status, "termination_reason": fin.termination_reason,
        "final_answer": fin.final_answer.model_dump(mode="json") if fin.final_answer else None, "error": fin.error,
        "totals": {"input_tokens": t.usage.input_tokens, "output_tokens": t.usage.output_tokens, "cache_read_tokens": 0,
                   "cost_usd": round(t.cost_usd, 6), "latency_ms": t.latency_ms, "step_count": t.steps, "tool_call_count": t.tool_calls},
        "created_at": iso(started.started_at - timedelta(milliseconds=120)), "started_at": iso(started.started_at), "finished_at": iso(fin.ended_at),
    }  # fmt: skip


def write(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False))


# ----------------------------------------------------------------------------- main
async def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    world = World()
    cases = build_cases()
    by_question = {c.question: (c, m) for c, m in cases}
    REPEATS = 3
    hardness = {c.case_key: random.Random(f"{SEED}-{c.case_key}").random() for c, _ in cases}

    # configs
    arms_cfg: dict[str, tuple[AgentConfig, str]] = {}
    configs_json = []
    for name, arm in ARMS.items():
        cfg = agent_config(name, arm, arm["tools"])
        cid = uid("config", name)
        arms_cfg[name] = (cfg, cid)
        payload = cfg.model_dump(mode="json")
        sha = hashlib.sha256(f"system_v1:{name}".encode()).hexdigest()
        configs_json.append({
            "id": cid, "name": name, "provider": cfg.provider, "model": cfg.model, "system_prompt_id": cfg.system_prompt_id,
            "system_prompt_sha": sha, "temperature": 0, "max_steps": cfg.max_steps, "max_cost_usd": cfg.max_cost_usd,
            "max_output_tokens": cfg.max_output_tokens, "tool_allowlist": cfg.tool_allowlist, "params": {},
            "config_hash": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
            "created_at": iso(NOW - timedelta(days=12)),
        })  # fmt: skip
    write(OUT / "configs.json", configs_json)

    eval_runs: list[dict[str, Any]] = []
    all_results: dict[str, list[dict[str, Any]]] = {}
    outcomes_by_arm: dict[str, dict[str, bool]] = {}
    api_runs: list[dict[str, Any]] = []
    run_details: dict[str, dict[str, Any]] = {}

    for ai, (arm_name, arm) in enumerate(ARMS.items()):
        cfg, cid = arms_cfg[arm_name]
        arng = random.Random(SEED + ai)
        primaries = assign_primaries(cases, arm, hardness, arng)
        eval_id = uid("evalrun", arm_name)
        started = NOW - timedelta(days=7 - ai * 2, hours=3)
        counters: Counter[str] = Counter()
        clock = {"t": started}

        def factory(
            question: str,
            arm=arm,
            arm_name=arm_name,
            cfg=cfg,
            arng=arng,
            primaries=primaries,
            counters=counters,
            clock=clock,
        ):
            case, meta = by_question[question]
            rep = counters[case.case_key]
            counters[case.case_key] += 1
            ok, bad = primaries[case.case_key]
            outcome = pick_outcome(arm, case.category, arng, ok, bad)
            if not ok and arm["tools"] is False and case.category == "lookup":
                outcome = "hallucinated_number" if arng.random() < 0.7 else outcome
            sc = Scenario(case, meta, outcome, arng, arm["tools"])
            start = clock["t"]
            clock["t"] += timedelta(seconds=40)

            async def gen() -> AsyncIterator[Any]:
                events = await world.run_scenario(sc, arm, cfg, start, arng)
                fin = next(e for e in events if isinstance(e, RunFinished))
                world.captured[str(fin.run_id)] = events
                for e in events:
                    yield e

            _ = rep
            return gen()

        results_path = Path("/tmp/agentlens-demo-results") / f"{arm_name}.jsonl"
        if results_path.exists():
            results_path.unlink()
        outcome = await run_eval([c for c, _ in cases], factory, repeats=REPEATS, budget_usd=1e9, per_run_cap_usd=None,
                                 results_path=results_path, concurrency=1)  # fmt: skip
        recs: list[RunRecord] = sorted(outcome.records, key=lambda r: (r.case_key, r.repeat))
        case_map = {c.case_key: c for c, _ in cases}
        summ = summarize(recs, case_map)
        outcomes_by_arm[eval_id] = case_outcomes(recs)
        n = summ["n_cases"]
        k = sum(outcomes_by_arm[eval_id].values())
        lo, hi = wilson_interval(k, n)
        cat_rates = {cat: ok / tot for cat, (ok, tot) in summ["by_category"].items()}
        passed = [r for r in recs if r.passed]
        total_cost = sum(r.cost_usd for r in recs)
        eval_runs.append({
            "id": eval_id, "dataset_id": uid("dataset", "demo-sec-qa"), "config_id": cid, "label": arm_name, "trigger": "manual",
            "git_sha": "d3m0c0de", "repeats": REPEATS, "status": "completed", "is_baseline": arm_name == "baseline-large", "gate_passed": None,
            "notes": "SYNTHETIC sample data", "started_at": iso(started), "finished_at": iso(clock["t"]),
            "metrics": {
                "n_results": len(recs), "n_cases": n, "n_cases_passed": k, "pass_rate": round(k / n, 4),
                "run_pass_rate": round(len(passed) / len(recs), 4), "pass_rate_ci": [round(lo, 4), round(hi, 4)],
                "total_cost_usd": round(total_cost, 6), "avg_cost_usd": round(total_cost / len(recs), 6),
                "cost_per_success_usd": round(total_cost / len(passed), 6) if passed else None,
                "p50_latency_ms": percentile([r.latency_ms for r in recs], 50), "p95_latency_ms": percentile([r.latency_ms for r in recs], 95),
            },
            "failure_breakdown": dict(summ["failures"]), "category_pass_rates": {c: round(v, 4) for c, v in cat_rates.items()},
        })  # fmt: skip
        results_json = []
        for r in recs:
            ev = world.captured[r.run_id]
            run = api_run(ev, case_map[r.case_key].question, cid, "eval", eval_id)
            api_runs.append(run)
            run_details[run["id"]] = {**run, "spans": api_spans(ev)}
            results_json.append({
                "id": uid("result", eval_id, r.case_key, r.repeat), "case_key": r.case_key, "repeat_idx": r.repeat, "run_id": r.run_id,
                "passed": r.passed, "failure_category": r.failure_category, "judge_rationale": None,
                "scores": {"required_tool_recall": r.tool_recall, "redundant_call_rate": r.redundant_rate, "error_call_rate": r.error_rate,
                           "citation_validity": r.citation_validity, "numeric_grounded": r.numeric_grounded},
                "latency_ms": r.latency_ms, "cost_usd": round(r.cost_usd, 6),
            })  # fmt: skip
        all_results[eval_id] = results_json

    # interactive runs ("today")
    interactive = [
        ("lookup-acme-revenue-fy2023", "pass"), ("calc-globex-revgrowth-fy23", "pass"), ("multi-combined-revenue-fy23", "pass"),
        ("unans-acme-fy2030-eps", "pass"), ("text-acme-segments", "pass"), ("lookup-globex-opincome-fy2022", "unit_error"),
        ("calc-acme-revcagr-fy21-23", "step_limit"), ("lookup-acme-netincome-fy2022", "tool_error_unrecovered"),
    ]  # fmt: skip
    irng = random.Random(SEED + 99)
    case_lookup = {c.case_key: (c, m) for c, m in cases}
    cfg, cid = arms_cfg["baseline-large"]
    for i, (key, outcome) in enumerate(interactive):
        case, meta = case_lookup[key]
        sc = Scenario(case, meta, outcome, irng, True)
        start = NOW - timedelta(minutes=95 - i * 11)
        ev = await world.run_scenario(sc, ARMS["baseline-large"], cfg, start, irng)
        run = api_run(ev, case.question, cid, "interactive", None)
        api_runs.append(run)
        run_details[run["id"]] = {**run, "spans": api_spans(ev)}

    api_runs.sort(key=lambda r: r["created_at"], reverse=True)
    write(OUT / "runs.json", api_runs)
    for rid, d in run_details.items():
        write(OUT / "runs" / f"{rid}.json", d)
    write(
        OUT / "eval-runs.json",
        [
            {k: v for k, v in e.items() if k not in {"failure_breakdown", "category_pass_rates"}}
            for e in eval_runs
        ],
    )
    for e in eval_runs:
        write(OUT / "eval-runs" / f"{e['id']}.json", e)
        write(OUT / "eval-results" / f"{e['id']}.json", all_results[e["id"]])

    # comparisons for every ordered pair, computed by the REAL comparison code
    for a in eval_runs:
        for b in eval_runs:
            if a["id"] == b["id"]:
                continue
            ba, bb = outcomes_by_arm[a["id"]], outcomes_by_arm[b["id"]]
            cmp = compare_case_outcomes(ba, bb)
            flipped = [
                {"case_key": k, "direction": "regressed" if ba[k] and not bb[k] else "improved"}
                for k in sorted(ba)
                if ba[k] != bb[k]
            ]
            write(OUT / "compare" / f"{a['id']}__{b['id']}.json", {
                "n": cmp.n, "base_pass_rate": cmp.base_pass_rate, "candidate_pass_rate": cmp.cand_pass_rate,
                "base_ci": list(cmp.base_ci), "candidate_ci": list(cmp.cand_ci), "delta": cmp.delta,
                "base_only": cmp.base_only, "candidate_only": cmp.cand_only, "p_value": cmp.p_value,
                "regression": cmp.is_regression(), "flipped_cases": flipped,
            })  # fmt: skip

    todays = [r for r in api_runs if r["created_at"][:10] == NOW.date().isoformat()]
    base = next(e for e in eval_runs if e["is_baseline"])
    write(OUT / "overview.json", {
        "runs_today": len(todays), "cost_today_usd": round(sum(r["totals"]["cost_usd"] for r in todays), 6),
        "daily_cap_usd": 5.0, "latest_baseline_pass_rate": base["metrics"]["pass_rate"],
    })  # fmt: skip
    write(
        OUT / "README.txt",
        "SYNTHETIC SAMPLE DATA about fictional companies. Generated by scripts/gen_demo_data.py. Not benchmark results.\n",
    )
    print(f"wrote {len(run_details)} runs, {len(eval_runs)} eval runs -> {OUT}")
    for e in eval_runs:
        m = e["metrics"]
        print(
            f"  {e['label']:<15} pass={m['pass_rate']:.1%} (runs {m['run_pass_rate']:.1%}) ci={m['pass_rate_ci']} cost/success=${m['cost_per_success_usd']:.4f} failures={e['failure_breakdown']}"
        )


if __name__ == "__main__":
    asyncio.run(main())
