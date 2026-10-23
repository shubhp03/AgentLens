"""agentlens-eval CLI.

Commands:
  validate-dataset  check a golden JSONL file
  run               dataset x config x repeats -> <out>/{results.jsonl,meta.json,traces/,report.md}
  report            regenerate report.md from a finished (or partial) run directory
  compare           paired McNemar comparison of two results.jsonl files
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agentlens_agent.events import Usage

from agentlens_evals.case import load_dataset
from agentlens_evals.compare import GateConfig, compare_case_outcomes, gate
from agentlens_evals.report import case_outcomes, render_report, summarize
from agentlens_evals.runner import RunRecord, meta_path, run_eval, write_meta


def _case_outcomes(results_jsonl: Path) -> dict[str, bool]:
    """results.jsonl lines: {"case_key": str, "repeat": int, "passed": bool}. A case passes if a
    strict majority of its repeats pass."""
    per_case: dict[str, list[bool]] = defaultdict(list)
    for line in results_jsonl.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            per_case[r["case_key"]].append(bool(r["passed"]))
    return {k: sum(v) * 2 > len(v) for k, v in per_case.items()}


def cmd_validate(args: argparse.Namespace) -> int:
    cases = load_dataset(args.path)
    by_cat: dict[str, int] = defaultdict(int)
    for c in cases:
        by_cat[c.category] += 1
    verified = sum(c.status == "verified" for c in cases)
    print(f"OK: {len(cases)} cases ({verified} verified, {len(cases) - verified} draft)")
    for cat, n in sorted(by_cat.items()):
        print(f"  {cat:<14}{n}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    cmp = compare_case_outcomes(
        _case_outcomes(Path(args.base)), _case_outcomes(Path(args.candidate))
    )
    print(
        f"n={cmp.n}  base={cmp.base_pass_rate:.1%} {tuple(round(x, 3) for x in cmp.base_ci)}  "
        f"cand={cmp.cand_pass_rate:.1%} {tuple(round(x, 3) for x in cmp.cand_ci)}"
    )
    print(
        f"delta={cmp.delta:+.1%}  base-only={cmp.base_only}  cand-only={cmp.cand_only}  "
        f"McNemar p={cmp.p_value:.3f}  regression={cmp.is_regression()}"
    )
    return 1 if cmp.is_regression() else 0


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _read_records(results: Path) -> list[RunRecord]:
    return [
        RunRecord.model_validate_json(line)
        for line in results.read_text().splitlines()
        if line.strip()
    ]


def _write_report(out: Path, cases: list, label_fallback: str = "run") -> Path:
    meta_file = meta_path(out / "results.jsonl")
    meta = json.loads(meta_file.read_text()) if meta_file.exists() else {"label": label_fallback}
    text = render_report(_read_records(out / "results.jsonl"), cases, meta)
    report = out / "report.md"
    report.write_text(text)
    return report


def _build_factory(config, pricing, registry):
    """One AgentRunner per question, sharing the LLM client and tool registry."""
    from agentlens_agent.config import load_system_prompt
    from agentlens_agent.loop import AgentRunner
    from agentlens_agent.wiring import build_llm

    prompt, _ = load_system_prompt(config.system_prompt_id)
    llm = build_llm(config)

    def factory(question: str):
        return AgentRunner(
            llm=llm,
            tools=registry,
            config=config,
            system_prompt=prompt,
            pricing=pricing,
            run_id=uuid4(),
        ).run(question)

    return factory


async def _run(args: argparse.Namespace) -> int:
    from agentlens_agent.config import load_agent_config, load_system_prompt
    from agentlens_agent.cost import PricingTable
    from agentlens_agent.mcp_registry import McpToolRegistry
    from agentlens_agent.registry import StaticToolRegistry
    from agentlens_agent.wiring import mcp_servers_from_env

    dataset = Path(args.dataset)
    cases = load_dataset(dataset, verified_only=args.verified_only)
    if args.split:
        cases = [c for c in cases if c.split == args.split]
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        print("no cases to run (check --dataset / --verified-only / --split)", file=sys.stderr)
        return 2

    config = load_agent_config(args.config)
    pricing = PricingTable.from_yaml(args.pricing)
    out = Path(args.out or f"eval-runs/{args.label}")
    results = out / "results.jsonl"

    servers = mcp_servers_from_env() or {
        "edgar": "stdio:edgar-mcp",
        "calc": "stdio:calc-mcp",
    }
    registry = McpToolRegistry(servers) if config.tool_allowlist else StaticToolRegistry({})
    if config.tool_allowlist:
        await registry.connect()
    try:
        # Fail before spending anything if this model has no price.
        pricing.cost_usd(config.provider, config.model, Usage())
        _, prompt_sha = load_system_prompt(config.system_prompt_id)
        write_meta(
            results,
            {
                "label": args.label,
                "date": datetime.now(UTC).isoformat(timespec="seconds"),
                "dataset": str(dataset),
                "dataset_sha256": _sha256(dataset),
                "config": config.name,
                "config_sha256": hashlib.sha256(config.model_dump_json().encode()).hexdigest(),
                "prompt_sha256": prompt_sha,
                "model": f"{config.provider}:{config.model}",
                "pricing_as_of": pricing.as_of or "unknown",
                "git_sha": _git_sha(),
                "repeats": args.repeats,
            },
        )

        def progress(r: RunRecord) -> None:
            mark = "PASS" if r.passed else f"FAIL[{r.failure_category}]"
            print(f"  {r.case_key}#{r.repeat} {mark} ${r.cost_usd:.4f}", flush=True)

        outcome = await run_eval(
            cases,
            _build_factory(config, pricing, registry),
            repeats=args.repeats,
            budget_usd=args.budget_usd,
            per_run_cap_usd=config.max_cost_usd,
            results_path=results,
            trace_dir=out / "traces",
            concurrency=args.concurrency,
            on_record=progress,
        )
    finally:
        if config.tool_allowlist:
            await registry.aclose()

    report = _write_report(out, cases)
    print(f"\nreport: {report}   spent: ${outcome.spent_usd:.4f} of ${args.budget_usd:.2f}")
    if outcome.skipped_for_budget:
        print(
            f"WARNING: budget reached; {outcome.skipped_for_budget} runs were NOT executed. "
            "Re-run the same command with a larger --budget-usd to resume.",
            file=sys.stderr,
        )
        return 3

    if not args.gate:
        return 0
    base_cmp = None
    if args.baseline:
        base_cmp = compare_case_outcomes(
            _case_outcomes(Path(args.baseline)), case_outcomes(outcome.records)
        )
    s = summarize(outcome.records, {c.case_key: c for c in cases})
    failures = gate(
        base_cmp,
        s["pass_rate"],
        s["cost_per_success"],
        GateConfig(min_pass_rate=args.min_pass_rate, max_cost_per_task_usd=args.max_cost_per_task),
    )
    for f in failures:
        print(f"GATE FAIL: {f}", file=sys.stderr)
    return 1 if failures else 0


def cmd_run(args: argparse.Namespace) -> int:
    return asyncio.run(_run(args))


def cmd_report(args: argparse.Namespace) -> int:
    cases = load_dataset(args.dataset)
    print(_write_report(Path(args.run_dir), cases))
    return 0


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="agentlens-eval")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate-dataset")
    v.add_argument("path")
    v.set_defaults(fn=cmd_validate)

    c = sub.add_parser("compare", help="paired comparison of two results.jsonl files")
    c.add_argument("base")
    c.add_argument("candidate")
    c.set_defaults(fn=cmd_compare)

    r = sub.add_parser("run", help="run an eval (resumable: re-run the same command to continue)")
    r.add_argument("--dataset", required=True)
    r.add_argument("--config", required=True)
    r.add_argument("--repeats", type=int, default=3)
    r.add_argument("--budget-usd", type=float, required=True, help="hard spend cap; required")
    r.add_argument("--label", default="manual")
    r.add_argument("--out", help="output dir (default eval-runs/<label>)")
    r.add_argument("--pricing", default="config/pricing.yaml")
    r.add_argument("--concurrency", type=int, default=4)
    r.add_argument("--split", choices=["dev", "test"], help="only run one split")
    r.add_argument("--limit", type=int, help="first N cases (pilot runs)")
    r.add_argument(
        "--verified-only",
        action="store_true",
        help="only status=verified cases; use for any run whose numbers you will publish",
    )
    r.add_argument("--gate", action="store_true", help="apply the regression gate (exit 1 on fail)")
    r.add_argument("--baseline", help="results.jsonl of the promoted baseline (paired McNemar)")
    r.add_argument("--min-pass-rate", type=float)
    r.add_argument("--max-cost-per-task", type=float)
    r.set_defaults(fn=cmd_run)

    rp = sub.add_parser("report", help="regenerate report.md for a run directory")
    rp.add_argument("run_dir")
    rp.add_argument("--dataset", required=True)
    rp.set_defaults(fn=cmd_report)

    args = p.parse_args(argv)
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
