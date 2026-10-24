"""Gold-CANDIDATE generator (EV-03): spec (questions) + SEC XBRL (values) -> DRAFT cases + a verification worksheet.

    uv run python scripts/gold_candidates.py evals/datasets/golden_v1_spec.yaml evals/datasets/golden_v1_candidates.jsonl

Hard rules (AGENTS.md, docs/EVAL_DESIGN.md section 2):
  * Every emitted case is status=draft with provenance.verified_by unset. This script can never produce a verified case.
  * Values come from SEC's XBRL companyfacts API, not from a model. They are still only CANDIDATES: a human reads each one
    in the filing (the worksheet links the filing and names the concept and period) and flips it to verified.
  * Text cases get an EMPTY must_contain on purpose; the exact phrases are the human's to write.
Needs SEC_USER_AGENT in the environment (SEC fair-access policy).
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import yaml
from agentlens_evals.case import EvalCase
from agentlens_tools.edgar_client import SecClient
from agentlens_tools.edgar_parse import accession_nodash

# Tolerances: lookups are exact to the reported million; derived values allow for rounding in how the answer is stated.
LOOKUP_TOL, CALC_TOL, DIFF_TOL = 0.001, 0.01, 0.02
MIN_GAP_PP = 2.0  # comparisons closer than this are too sensitive to rounding in how an answer states the two sides
TOOLS = {
    "lookup": ["resolve_company", "get_financial_facts"],
    "calc": ["get_financial_facts", "calculate"],
    "multi_hop": ["get_financial_facts", "calculate"],
    "text_extract": ["list_filings", "get_filing_section"],
}


def long_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


class Fact:
    """One XBRL value for (company, concept, period), with how it moved between filings."""

    def __init__(
        self, ticker: str, cik: int, metric: str, concept: str, rows: list[dict], flow: bool
    ) -> None:
        rows = sorted(rows, key=lambda r: r["filed"])
        self.ticker, self.cik, self.metric, self.concept = ticker, cik, metric, concept
        self.latest, self.original = rows[-1], rows[0]
        self.value: float = self.latest["val"]
        self.restated = len({r["val"] for r in rows}) > 1
        self.history = [(r["accn"], r["form"], r["filed"], r["val"]) for r in rows]
        self.start, self.end = self.latest.get("start"), self.latest["end"]
        self.flow = flow

    @property
    def accession(self) -> str:
        return self.latest["accn"]

    def url(self) -> str:
        return f"https://www.sec.gov/Archives/edgar/data/{self.cik}/{accession_nodash(self.accession)}/"

    def cite(self) -> str:
        return f"us-gaap:{self.concept} {self.ticker} period ending {self.end} = {self.value:,.0f} ({self.accession})"


def pick(facts: dict, ticker: str, cik: int, metric: str, spec: dict, end: str) -> Fact:
    m = spec["metrics"][metric]
    us = facts["facts"]["us-gaap"]
    for concept in m["concepts"]:
        node = us.get(concept)
        if not node:
            continue
        rows = []
        for r in node["units"].get("USD", []):
            if r["end"] != end or not r["form"].startswith("10-K"):
                continue
            if m["flow"]:
                if (
                    "start" not in r
                    or not 350
                    <= (date.fromisoformat(end) - date.fromisoformat(r["start"])).days
                    <= 380
                ):
                    continue
            elif "start" in r:
                continue
            rows.append(r)
        if rows:
            return Fact(ticker, cik, metric, concept, rows, m["flow"])
    raise LookupError(
        f"{ticker}: no 10-K {metric} fact for period ending {end} (tried {m['concepts']})"
    )


class Gen:
    def __init__(self, spec: dict, client: SecClient) -> None:
        self.spec, self.client = spec, client
        self.ciks: dict[str, int] = {}
        self.facts: dict[str, dict] = {}
        self.now = datetime.now(UTC).date().isoformat()

    async def load(self, ticker: str) -> tuple[dict, int]:
        if ticker not in self.facts:
            tickers = await self.client.tickers()
            cik = next(v["cik_str"] for v in tickers.values() if v["ticker"] == ticker)
            self.ciks[ticker] = cik
            self.facts[ticker] = await self.client.company_facts(cik)
        return self.facts[ticker], self.ciks[ticker]

    async def fact(self, ticker: str, metric: str, end: str) -> Fact:
        facts, cik = await self.load(ticker)
        return pick(facts, ticker, cik, metric, self.spec, end)

    def name(self, ticker: str) -> str:
        return self.spec["companies"][ticker]["name"]

    def label(self, metric: str) -> str:
        return self.spec["metrics"][metric]["label"]

    def check_gap(self, c: dict, gap_pp: float) -> None:
        if gap_pp < MIN_GAP_PP:
            raise LookupError(
                f"{c['key']}: gap {gap_pp:.2f} pp < {MIN_GAP_PP} pp; pick a pair that differs more"
            )

    def case(
        self, c: dict, category: str, question: str, expected: dict, inputs: list[Fact]
    ) -> dict:
        latest = max(inputs, key=lambda f: f.latest["filed"])
        tags = list(c.get("tags", [])) + ["xbrl", "candidate"]
        if any(f.restated for f in inputs):
            tags.append("restated")
        return {
            "case_key": c["key"], "question": question, "category": category, "difficulty": c["difficulty"],
            "expected": expected, "required_tools": TOOLS["calc" if category in ("calc", "multi_hop") else category],
            "status": "draft", "tags": tags,
            "provenance": {
                "source": "SEC XBRL companyfacts API (data.sec.gov): " + "; ".join(f.cite() for f in inputs),
                "accession": latest.accession, "xbrl_concept": f"us-gaap:{latest.concept}",
                "retrieved_at": self.now, "verified_by": None,
            },
        }  # fmt: skip

    # ---- one builder per kind; each returns (case dict, worksheet detail) ----------------------------------
    async def lookup(self, c: dict) -> tuple[dict, list[Fact], str]:
        f = await self.fact(c["ticker"], c["metric"], c["end"])
        what = self.label(c["metric"])
        period = "as of" if not f.flow else "for the fiscal year ended"
        q = f"What was {self.name(c['ticker'])}'s {what} {period} {long_date(c['end'])}?"
        if f.restated:
            q += " Use the value as most recently reported in SEC filings."
        exp = {
            "kind": "numeric",
            "value": round(f.value / 1e6, 3),
            "unit": "USD_millions",
            "period": c["fy"],
            "rel_tolerance": LOOKUP_TOL,
        }
        return self.case(c, "lookup", q, exp, [f]), [f], f"{f.value:,.0f} USD"

    async def growth(self, c: dict) -> tuple[dict, list[Fact], str]:
        prev, cur = (
            await self.fact(c["ticker"], c["metric"], c["prev_end"]),
            await self.fact(c["ticker"], c["metric"], c["end"]),
        )
        g = (cur.value - prev.value) / prev.value * 100
        q = (
            f"By what percentage did {self.name(c['ticker'])}'s {self.label(c['metric'])} change from the fiscal year ended "
            f"{long_date(c['prev_end'])} to the fiscal year ended {long_date(c['end'])}?"
        )
        exp = {
            "kind": "numeric",
            "value": round(g, 4),
            "unit": "percent",
            "period": c["fy"],
            "rel_tolerance": CALC_TOL,
        }
        return (
            self.case(c, "calc", q, exp, [prev, cur]),
            [prev, cur],
            f"({cur.value:,.0f} - {prev.value:,.0f}) / {prev.value:,.0f} = {g:.4f}%",
        )

    async def _ratio(
        self, c: dict, ticker: str, num: str, den: str, end: str
    ) -> tuple[float, list[Fact]]:
        n, d = await self.fact(ticker, num, end), await self.fact(ticker, den, end)
        return n.value / d.value, [n, d]

    async def margin(self, c: dict) -> tuple[dict, list[Fact], str]:
        r, fs = await self._ratio(c, c["ticker"], c["num"], c["den"], c["end"])
        q = f"What was {self.name(c['ticker'])}'s {c['margin_name']} for the fiscal year ended {long_date(c['end'])}, as a percentage of {self.label(c['den'])}?"
        exp = {
            "kind": "numeric",
            "value": round(r * 100, 4),
            "unit": "percent",
            "period": c["fy"],
            "rel_tolerance": CALC_TOL,
        }
        return (
            self.case(c, "calc", q, exp, fs),
            fs,
            f"{fs[0].value:,.0f} / {fs[1].value:,.0f} = {r * 100:.4f}%",
        )

    async def ratio(self, c: dict) -> tuple[dict, list[Fact], str]:
        r, fs = await self._ratio(c, c["ticker"], c["num"], c["den"], c["end"])
        q = f"What was {self.name(c['ticker'])}'s {c['ratio_name']} for the fiscal year ended {long_date(c['end'])}?"
        exp = {
            "kind": "numeric",
            "value": round(r, 4),
            "unit": "ratio",
            "period": c["fy"],
            "rel_tolerance": CALC_TOL,
        }
        return (
            self.case(c, "calc", q, exp, fs),
            fs,
            f"{fs[0].value:,.0f} / {fs[1].value:,.0f} = {r:.4f}",
        )

    async def higher_margin(self, c: dict) -> tuple[dict, list[Fact], str]:
        out = []
        for side in (c["a"], c["b"]):
            r, fs = await self._ratio(c, side["ticker"], c["num"], side["den"], side["end"])
            out.append((side, r, fs))
        (sa, ra, fa), (sb, rb, fb) = out
        na, nb = self.name(sa["ticker"]), self.name(sb["ticker"])
        diff = abs(ra - rb) * 100
        self.check_gap(c, diff)
        q = (
            f"For fiscal {c['fy'][2:]}, which company had the higher {c['margin_name']}: {na} (fiscal year ended {long_date(sa['end'])}) "
            f"or {nb} (fiscal year ended {long_date(sb['end'])})? By how many percentage points did the higher one exceed the other?"
        )
        exp = {
            "kind": "numeric",
            "value": round(diff, 4),
            "unit": "percent",
            "rel_tolerance": DIFF_TOL,
        }
        winner = na if ra >= rb else nb
        detail = f"{na} {ra * 100:.4f}% vs {nb} {rb * 100:.4f}% -> {winner} higher by {diff:.4f} pp"
        return self.case(c, "multi_hop", q, exp, fa + fb), fa + fb, detail

    async def faster_growth(self, c: dict) -> tuple[dict, list[Fact], str]:
        res = []
        for side in (c["a"], c["b"]):
            m = side.get("metric", c["metric"])
            p, k = (
                await self.fact(side["ticker"], m, side["prev_end"]),
                await self.fact(side["ticker"], m, side["end"]),
            )
            res.append((side, (k.value - p.value) / p.value * 100, [p, k]))
        (sa, ga, fa), (sb, gb, fb) = res
        na, nb = self.name(sa["ticker"]), self.name(sb["ticker"])
        diff = abs(ga - gb)
        self.check_gap(c, diff)
        q = (
            f"Which company's total revenue grew faster in fiscal {c['fy'][2:]}: {na} (fiscal year ended {long_date(sa['end'])} vs "
            f"{long_date(sa['prev_end'])}) or {nb} (fiscal year ended {long_date(sb['end'])} vs {long_date(sb['prev_end'])})? "
            "By how many percentage points did the faster growth rate exceed the slower one?"
        )
        exp = {
            "kind": "numeric",
            "value": round(diff, 4),
            "unit": "percent",
            "rel_tolerance": DIFF_TOL,
        }
        winner = na if ga >= gb else nb
        return (
            self.case(c, "multi_hop", q, exp, fa + fb),
            fa + fb,
            f"{na} {ga:.4f}% vs {nb} {gb:.4f}% -> {winner} faster by {diff:.4f} pp",
        )

    async def cross_period_margin(self, c: dict) -> tuple[dict, list[Fact], str]:
        rp, fp = await self._ratio(c, c["ticker"], c["num"], c["den"], c["prev"]["end"])
        rc, fc = await self._ratio(c, c["ticker"], c["num"], c["den"], c["cur"]["end"])
        diff = abs(rp - rc) * 100
        self.check_gap(c, diff)
        q = (
            f"Which fiscal year had the higher {c['margin_name']} for {self.name(c['ticker'])}: the year ended {long_date(c['prev']['end'])} "
            f"or the year ended {long_date(c['cur']['end'])}? By how many percentage points did it exceed the other year?"
        )
        exp = {
            "kind": "numeric",
            "value": round(diff, 4),
            "unit": "percent",
            "rel_tolerance": DIFF_TOL,
        }
        win_fy = c["prev"]["fy"] if rp >= rc else c["cur"]["fy"]
        return (
            self.case(c, "multi_hop", q, exp, fp + fc),
            fp + fc,
            f"{c['prev']['fy']} {rp * 100:.4f}% vs {c['cur']['fy']} {rc * 100:.4f}% -> {win_fy} higher by {diff:.4f} pp",
        )

    async def text(self, c: dict) -> tuple[dict, str]:
        cik = self.ciks.get(c["ticker"]) or (await self.load(c["ticker"]))[1]
        subs = await self.client.submissions(cik)
        rec = subs["filings"]["recent"]
        for i, (form, rep) in enumerate(zip(rec["form"], rec["reportDate"], strict=True)):
            if form == "10-K" and rep == c["end"]:
                accn, doc = rec["accessionNumber"][i], rec["primaryDocument"][i]
                break
        else:
            raise LookupError(
                f"{c['ticker']}: no 10-K with reportDate {c['end']} in recent filings"
            )
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash(accn)}/{doc}"
        case = {
            "case_key": c["key"], "question": c["q"], "category": "text_extract", "difficulty": c["difficulty"],
            "expected": {"kind": "text", "must_contain": []}, "required_tools": TOOLS["text_extract"],
            "status": "draft", "tags": ["10-k-text", "candidate", "needs-gold"],
            "provenance": {"source": f"SEC EDGAR 10-K, Item {c['item']}: {url}", "accession": accn, "xbrl_concept": None,
                           "retrieved_at": self.now, "verified_by": None},
        }  # fmt: skip
        return case, url


def authored_case(a: dict) -> dict:
    return {
        "case_key": a["key"], "question": a["q"], "category": "unanswerable", "difficulty": a["difficulty"],
        "expected": {"kind": "abstain"}, "status": "draft", "tags": a.get("tags", []) + ["candidate"],
    }  # fmt: skip


def assign_splits(cases: list[dict]) -> None:
    """Hold out ~25% as `test`, stratified by category (every 4th case within a category, in file order)."""
    seen: dict[str, int] = {}
    for c in cases:
        n = seen.get(c["category"], 0)
        seen[c["category"]] = n + 1
        c["split"] = "test" if n % 4 == 3 else "dev"


async def build(spec: dict) -> tuple[list[dict], list, list, list[str]]:
    client = SecClient()
    gen = Gen(spec, client)
    cases: list[dict] = []
    rows: list[tuple[dict, list[Fact], str]] = []
    text_rows: list[tuple[dict, str]] = []
    errors: list[str] = []
    try:
        for c in spec["cases"]:
            try:
                case, facts, detail = await getattr(gen, c["kind"])(c)
            except (LookupError, KeyError, StopIteration) as e:
                errors.append(f"{c['key']}: {type(e).__name__}: {e}")
                continue
            cases.append(case)
            rows.append((case, facts, detail))
        for t in spec["text_cases"]:
            try:
                case, url = await gen.text(t)
            except (LookupError, KeyError, StopIteration) as e:
                errors.append(f"{t['key']}: {type(e).__name__}: {e}")
                continue
            cases.append(case)
            text_rows.append((case, url))
        cases.extend(authored_case(a) for a in spec["authored"])
    finally:
        await client.aclose()
    return cases, rows, text_rows, errors


def main(spec_path: str, out_path: str) -> int:
    spec = yaml.safe_load(Path(spec_path).read_text())
    cases, rows, text_rows, errors = asyncio.run(build(spec))

    assign_splits(cases)
    for c in cases:
        EvalCase.model_validate(c)  # the same schema the harness loads with
        assert c["status"] == "draft" and not (c.get("provenance") or {}).get("verified_by")
    keys = [c["case_key"] for c in cases]
    assert len(keys) == len(set(keys)), "duplicate case_key"

    out = Path(out_path)
    header = (
        "# DRAFT gold candidates generated by scripts/gold_candidates.py from SEC XBRL. NOT verified. Values are candidates only;\n"
        "# a human must read each one in the filing and set status=verified + provenance.verified_by (docs/EVAL_DESIGN.md section 2).\n"
    )
    out.write_text(header + "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases))
    split = {c["case_key"]: c["split"] for c in cases}
    write_worksheet(
        out.with_name(out.stem.replace("_candidates", "") + "_worksheet.md"),
        rows,
        text_rows,
        spec,
        split,
    )

    by_cat: dict[str, int] = {}
    for c in cases:
        by_cat[c["category"]] = by_cat.get(c["category"], 0) + 1
    print(f"wrote {len(cases)} draft cases -> {out}   {by_cat}")
    for e in errors:
        print("ERROR", e, file=sys.stderr)
    return 1 if errors else 0


def write_worksheet(
    path: Path, rows: list, text_rows: list, spec: dict, split: dict[str, str]
) -> None:
    L = [
        "# Golden set v1: verification worksheet",
        "",
        "Generated by `scripts/gold_candidates.py` from SEC XBRL. **Every value below is a CANDIDATE, not gold.**",
        "For each case: open the filing link, find the line item for the stated period, confirm the number (and that the question has",
        "exactly one defensible answer), then in `golden_v1_candidates.jsonl` set `status: verified` and fill `provenance.verified_by`.",
        "Protocol: docs/EVAL_DESIGN.md section 2 (re-verify >=24h later; keep the `test` split unseen while tuning).",
        "",
        "Flags to look at first: **RESTATED** (the value changed between filings; the candidate is the most recent), "
        "**concept** differing from the obvious one, and 52/53-week or non-calendar fiscal years.",
        "",
        "## Numeric cases (lookup, calc, multi_hop)",
        "",
        "| # | case_key | split | candidate gold | how it was computed | inputs (concept, period end, filing) | flags |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, (case, facts, detail) in enumerate(rows, 1):
        exp = case["expected"]
        gold = f"{exp['value']:,} {exp['unit']}" + (
            f" ({exp['period']})" if exp.get("period") else ""
        )
        inputs = "<br>".join(
            f"{f.ticker} `{f.concept}` {f.end} · [{f.accession}]({f.url()})" for f in facts
        )
        flags = []
        for f in facts:
            if f.restated:
                flags.append(
                    "RESTATED " + " → ".join(f"{v:,.0f} ({a})" for a, _, _, v in f.history)
                )
        L.append(
            f"| {i} | `{case['case_key']}` | {split[case['case_key']]} | {gold} | {detail} | {inputs} | {'<br>'.join(flags)} |"
        )
    L += ["", "Questions (read these as a skeptical expert: is there exactly one answer?):", ""]
    L += [f"- `{c['case_key']}`: {c['question']}" for c, _, _ in rows]
    L += [
        "",
        "## text_extract cases (gold is yours to write)",
        "",
        "`must_contain` is empty on purpose. Read the section, write the phrases a correct answer must include, then verify.",
        "",
        "| case_key | split | question | 10-K document |",
        "|---|---|---|---|",
    ]
    L += [
        f"| `{c['case_key']}` | {split[c['case_key']]} | {c['question']} | [{c['provenance']['accession']}]({u}) |"
        for c, u in text_rows
    ]
    L += [
        "",
        "## unanswerable cases (expected: abstain)",
        "",
        "Confirm each is genuinely unanswerable from SEC filings (future, private, non-public, undisclosed, ambiguous).",
        "",
    ]
    L += [f"- `{a['key']}` ({split.get(a['key'], '?')}): {a['q']}" for a in spec["authored"]]
    path.write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
