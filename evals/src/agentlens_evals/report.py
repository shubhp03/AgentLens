"""Markdown report generator (EV-08). Reports are the ONLY source for numbers used in the README,
a LinkedIn post or a resume bullet, so every figure carries its denominator and interval."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Any

from agentlens_evals.case import EvalCase
from agentlens_evals.runner import RunRecord
from agentlens_evals.stats import wilson_interval


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..100). Empty -> nan."""
    if not values:
        return math.nan
    xs = sorted(values)
    k = max(0, min(len(xs) - 1, math.ceil(q / 100 * len(xs)) - 1))
    return xs[k]


def case_outcomes(records: Sequence[RunRecord]) -> dict[str, bool]:
    """Strict-majority-of-repeats pass per case."""
    per: dict[str, list[bool]] = defaultdict(list)
    for r in records:
        per[r.case_key].append(r.passed)
    return {k: sum(v) * 2 > len(v) for k, v in per.items()}


def summarize(records: Sequence[RunRecord], cases: dict[str, EvalCase]) -> dict[str, Any]:
    outcomes = case_outcomes(records)
    n_cases, n_pass = len(outcomes), sum(outcomes.values())
    lo, hi = wilson_interval(n_pass, n_cases)
    passes = [r for r in records if r.passed]
    total_cost = sum(r.cost_usd for r in records)

    per_case: dict[str, list[bool]] = defaultdict(list)
    for r in records:
        per_case[r.case_key].append(r.passed)
    consistent = sum(len(set(v)) == 1 for v in per_case.values())

    answerable = [
        r for r in records if (c := cases.get(r.case_key)) and c.expected.kind != "abstain"
    ]
    unanswerable = [
        r for r in records if (c := cases.get(r.case_key)) and c.expected.kind == "abstain"
    ]

    by_cat: dict[str, list[bool]] = defaultdict(list)
    by_split: dict[str, list[bool]] = defaultdict(list)
    for key, ok in outcomes.items():
        if key in cases:
            by_cat[cases[key].category].append(ok)
            by_split[cases[key].split].append(ok)

    n_calls = sum(r.tool_calls for r in records)
    return {
        "n_cases": n_cases,
        "n_runs": len(records),
        "pass_rate": n_pass / n_cases if n_cases else math.nan,
        "ci": (lo, hi),
        "consistency": consistent / len(per_case) if per_case else math.nan,
        "total_cost": total_cost,
        "cost_per_success": total_cost / len(passes) if passes else math.nan,
        "cost_per_run": total_cost / len(records) if records else math.nan,
        "latency_p50": percentile([r.latency_ms for r in records], 50),
        "latency_p95": percentile([r.latency_ms for r in records], 95),
        "mean_steps": sum(r.steps for r in records) / len(records) if records else math.nan,
        "mean_tool_calls": n_calls / len(records) if records else math.nan,
        "tool_error_rate": (
            sum(r.error_rate * r.tool_calls for r in records) / n_calls if n_calls else 0.0
        ),
        "redundant_rate": (
            sum(r.redundant_rate * r.tool_calls for r in records) / n_calls if n_calls else 0.0
        ),
        "failures": Counter(r.failure_category for r in records if not r.passed),
        "n_failed_runs": sum(not r.passed for r in records),
        "false_abstain": (
            sum(r.failure_category == "false_abstain" for r in answerable),
            len(answerable),
        ),
        "missed_abstain": (
            sum(r.failure_category == "missed_abstain" for r in unanswerable),
            len(unanswerable),
        ),
        "by_category": {k: (sum(v), len(v)) for k, v in sorted(by_cat.items())},
        "by_split": {k: (sum(v), len(v)) for k, v in sorted(by_split.items())},
    }


def _pct(x: float) -> str:
    return "n/a" if math.isnan(x) else f"{x:.1%}"


def _money(x: float) -> str:
    return "n/a" if math.isnan(x) else f"${x:.4f}"


def _ms(x: float) -> str:
    return "n/a" if math.isnan(x) else f"{x / 1000:.1f}s"


def _frac(pair: tuple[int, int]) -> str:
    k, n = pair
    return f"{k}/{n} ({k / n:.1%})" if n else "n/a (0 cases)"


def render_report(
    records: Sequence[RunRecord], cases: Sequence[EvalCase], meta: dict[str, Any]
) -> str:
    by_key = {c.case_key: c for c in cases}
    s = summarize(records, by_key)
    lo, hi = s["ci"]
    drafts = sum(1 for c in cases if c.status != "verified")
    lines: list[str] = [f"# Eval report: {meta.get('label', 'unlabelled')}", ""]
    if drafts:
        lines += [
            f"> **WARNING: {drafts} of {len(cases)} cases are `draft` (unverified gold).** "
            "Do not publish these numbers.",
            "",
        ]
    lines += ["## Run metadata", "", "| Field | Value |", "|---|---|"]
    for k in (
        "date", "dataset", "dataset_sha256", "config", "config_sha256", "prompt_sha256",
        "model", "pricing_as_of", "git_sha", "repeats",
    ):  # fmt: skip
        lines.append(f"| {k} | {meta.get(k, 'unknown')} |")
    lines += [
        "",
        "## Headline",
        "",
        f"- **Pass rate: {_pct(s['pass_rate'])}** over {s['n_cases']} cases "
        f"(Wilson 95% CI {_pct(lo)} to {_pct(hi)}); {s['n_runs']} runs total",
        f"- Consistency (all repeats agree): {_pct(s['consistency'])}",
        f"- Cost per successful task: {_money(s['cost_per_success'])} "
        f"(cost per run {_money(s['cost_per_run'])}, total {_money(s['total_cost'])})",
        f"- Latency p50 / p95: {_ms(s['latency_p50'])} / {_ms(s['latency_p95'])}",
        f"- Mean steps {s['mean_steps']:.1f}, mean tool calls {s['mean_tool_calls']:.1f}, "
        f"tool-error rate {_pct(s['tool_error_rate'])}, redundant-call rate {_pct(s['redundant_rate'])}",
        f"- False-abstain (answerable runs): {_frac(s['false_abstain'])}; "
        f"missed-abstain (unanswerable runs): {_frac(s['missed_abstain'])}",
        "",
        "## Pass rate by case category",
        "",
        "| Category | Passed | Rate |",
        "|---|---|---|",
    ]
    for cat, (k, n) in s["by_category"].items():
        lines.append(f"| {cat} | {k}/{n} | {_pct(k / n)} |")
    lines += ["", "## Pass rate by split", "", "| Split | Passed | Rate |", "|---|---|---|"]
    for sp, (k, n) in s["by_split"].items():
        lines.append(f"| {sp} | {k}/{n} | {_pct(k / n)} |")
    if s["n_cases"] < 100:
        lines += [
            "",
            f"_n={s['n_cases']}: differences below ~10 percentage points are usually noise at this "
            "sample size. The held-out (`test`) row is the honest number._",
        ]
    lines += [
        "",
        "## Failure breakdown (failed runs)",
        "",
        "| Category | Runs | Share |",
        "|---|---|---|",
    ]
    total_failed = s["n_failed_runs"]
    for cat, n in s["failures"].most_common():
        lines.append(f"| {cat} | {n} | {_pct(n / total_failed)} |")
    if not s["failures"]:
        lines.append("| (none) | 0 | n/a |")
    if s["failures"].get("other", 0) * 4 > max(total_failed, 1):
        lines += [
            "",
            "> `other` is over 25% of failures: the taxonomy needs work. Read those traces by hand.",
        ]
    worst = sorted({r.case_key for r in records if not r.passed})
    if worst:
        lines += ["", "## Failed cases", ""]
        by_case: dict[str, list[RunRecord]] = defaultdict(list)
        for r in records:
            by_case[r.case_key].append(r)
        for key in worst:
            rs = by_case[key]
            cats = Counter(r.failure_category or "pass" for r in rs)
            lines.append(f"- `{key}`: " + ", ".join(f"{c} x{n}" for c, n in cats.most_common()))
    lines.append("")
    return "\n".join(lines)
