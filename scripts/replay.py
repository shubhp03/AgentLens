"""Record real runs through a live gateway, then export them as the dashboard's static REPLAY dataset.

    # 1. with the stack up (docs/RUNNING.md section 4) and ADMIN_API_KEY set
    uv run python scripts/replay.py record  --gateway http://localhost:8080
    # 2. snapshot interactive runs + publishable eval runs into dashboard/public/replay/
    uv run python scripts/replay.py export  --gateway http://localhost:8080

What the replay is: real Claude calls over real SEC data, captured once and served as static JSON (no backend, no spend).
What it is NOT: benchmark results. Eval runs are published ONLY if they are completed and their dataset is fully human-verified
(every case status=verified); anything else is skipped with a reason, so the Evals pages stay empty until real results exist (EV-02).

`record` only asks questions taken from DEV-split golden candidates, so the held-out test split is never exercised.
It spends real money (a few cents per question, each run capped by the config's max_cost_usd).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from agentlens_agent.config import load_agent_config, load_system_prompt

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "evals" / "datasets" / "golden_v1_candidates.jsonl"
OUT = ROOT / "dashboard" / "public" / "replay"
API = "/api/v1"

# Chosen to show different behaviours, not to flatter the agent: a plain lookup, a concept-variant trap, a derived metric,
# text extraction from a 10-K, a two-company comparison, and an abstention. All are dev-split candidates.
RECORD_KEYS = [
    "calc-aapl-growth-fy23",
    "multihop-opmargin-googl-msft-fy23",
    "lookup-nvda-revenue-fy2024",
    "lookup-googl-revenue-fy2022",
    "calc-nvda-op-margin-fy24",
    "text-msft-segments-fy2024",
    "multihop-opmargin-nvda-msft-fy24",
    "unans-tsla-revenue-fy2030",
]


def client(gateway: str) -> httpx.Client:
    key = os.environ.get("ADMIN_API_KEY")
    if not key:
        sys.exit("ADMIN_API_KEY must be set in the environment")
    return httpx.Client(base_url=gateway.rstrip("/"), headers={"X-API-Key": key}, timeout=60)


def dev_questions() -> list[tuple[str, str]]:
    cases = {}
    for line in CANDIDATES.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            c = json.loads(line)
            cases[c["case_key"]] = c
    out = []
    for k in RECORD_KEYS:
        if cases[k]["split"] != "dev":
            sys.exit(f"{k} is not a dev-split case; refusing to record held-out questions")
        out.append((k, cases[k]["question"]))
    return out


def record(args: argparse.Namespace) -> int:
    cfg = load_agent_config(args.config)
    _, sha = load_system_prompt(cfg.system_prompt_id)
    body_cfg = cfg.model_dump(mode="json") | {"system_prompt_sha": sha}
    spent = 0.0
    with client(args.gateway) as c:
        for key, question in dev_questions():
            r = c.post(f"{API}/runs", json={"question": question, "config": body_cfg})
            r.raise_for_status()
            rid = r.json()["id"]
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                run = c.get(f"{API}/runs/{rid}").json()
                if run["status"] in ("completed", "failed", "cancelled"):
                    break
                time.sleep(1)
            cost = run["totals"]["cost_usd"]
            spent += cost
            print(f"{key:<36} {run['status']}/{run['termination_reason']}  ${cost:.4f}  {rid}")
    print(f"spent ${spent:.4f}")
    return 0


def write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")


def _paged_runs(c: httpx.Client, **params: str) -> list[dict]:
    items: list[dict] = []
    cursor = None
    while True:
        page = c.get(
            f"{API}/runs", params={**params, "limit": 200, **({"cursor": cursor} if cursor else {})}
        ).json()
        items += page["items"]
        cursor = page["next_cursor"]
        if not cursor:
            return items


def publishable_eval_runs(c: httpx.Client) -> tuple[list[dict], list[str]]:
    """Completed eval runs whose dataset is FULLY verified. Anything else is skipped with a reason: unverified (draft)
    gold must never reach a public page, whatever its numbers look like."""
    ok: list[dict] = []
    skipped: list[str] = []
    verified: dict[str, bool] = {}
    for er in c.get(f"{API}/eval-runs", params={"limit": 200}).json():
        label = f"{er.get('label') or er['id'][:8]} ({er['id'][:8]})"
        if er["status"] != "completed":
            skipped.append(f"{label}: status {er['status']}, not completed")
            continue
        ds = er["dataset_id"]
        if ds not in verified:
            cases = c.get(f"{API}/datasets/{ds}/cases").json()
            verified[ds] = bool(cases) and all(x["status"] == "verified" for x in cases)
        if not verified[ds]:
            skipped.append(f"{label}: dataset has unverified (draft) cases")
            continue
        ok.append(er)
    return ok, skipped


def export_replay(
    gateway: str, key: str, out: Path, *, since: str | None = None, traces: str = "failed"
) -> dict:
    """Snapshot a gateway into the dashboard's static dataset layout (see DemoAgentLensApi). `traces` controls which
    eval-linked runs get a full trace file: 'failed' (default, keeps the repo lean; the Evals page lists failures first)
    or 'all'."""
    with httpx.Client(base_url=gateway.rstrip("/"), headers={"X-API-Key": key}, timeout=60) as c:
        runs = [
            r
            for r in _paged_runs(c, source="interactive")
            if r["status"] in ("completed", "failed") and (not since or r["created_at"] >= since)
        ]
        runs.sort(key=lambda r: r["created_at"], reverse=True)
        details = {
            r["id"]: c.get(f"{API}/runs/{r['id']}", params={"include": "spans"}).json()
            for r in runs
        }
        eval_runs, skipped = publishable_eval_runs(c)
        eval_detail = {er["id"]: c.get(f"{API}/eval-runs/{er['id']}").json() for er in eval_runs}
        eval_results = {
            er["id"]: c.get(f"{API}/eval-runs/{er['id']}/results").json() for er in eval_runs
        }
        for results in eval_results.values():
            for r in results:
                want = traces == "all" or not r["passed"]
                if want and r["run_id"] and r["run_id"] not in details:
                    details[r["run_id"]] = c.get(
                        f"{API}/runs/{r['run_id']}", params={"include": "spans"}
                    ).json()
        compares: dict[str, dict] = {}
        for base in eval_runs:
            for cand in eval_runs:
                if base["id"] != cand["id"] and base["dataset_id"] == cand["dataset_id"]:
                    r = c.get(
                        f"{API}/eval-runs/compare",
                        params={"base": base["id"], "candidate": cand["id"]},
                    )
                    if (
                        r.is_success
                    ):  # case sets that differ are refused by the gateway; leave those pairs out
                        compares[f"{base['id']}__{cand['id']}"] = r.json()
        configs = c.get(f"{API}/configs").json()
        overview = c.get(f"{API}/stats/overview").json()

    used = {r["config_id"] for r in runs} | {er["config_id"] for er in eval_runs}
    baseline_ids = {er["id"] for er in eval_runs if er.get("is_baseline")}
    if out.exists():
        shutil.rmtree(out)
    write(out / "runs.json", runs)
    for rid, d in details.items():
        write(out / "runs" / f"{rid}.json", d)
    write(out / "configs.json", [x for x in configs if x["id"] in used])
    write(out / "eval-runs.json", eval_runs)
    for er_id in eval_detail:
        write(out / "eval-runs" / f"{er_id}.json", eval_detail[er_id])
        write(out / "eval-results" / f"{er_id}.json", eval_results[er_id])
    for name, cmp in compares.items():
        write(out / "compare" / f"{name}.json", cmp)
    cost = sum(r["totals"]["cost_usd"] for r in runs)
    baseline_rate = next(
        (eval_detail[i]["metrics"]["pass_rate"] for i in baseline_ids if i in eval_detail), None
    )
    write(out / "overview.json", {
        "runs_today": len(runs), "cost_today_usd": round(cost, 6),
        "daily_cap_usd": overview["daily_cap_usd"], "latest_baseline_pass_rate": baseline_rate,
    })  # fmt: skip
    evals_note = (
        f"{len(eval_runs)} eval run(s) on fully human-verified datasets are included."
        if eval_runs
        else "No eval runs are included."
    )
    (out / "README.txt").write_text(
        f"Runs recorded from a live stack (real Claude calls, real SEC data) and exported {datetime.now(UTC).isoformat(timespec='seconds')}"
        f" by scripts/replay.py. {evals_note} Individual runs are not benchmark results.\n"
    )
    leaks = [p for p in out.rglob("*.json") if any(x in p.read_text() for x in ("sk-ant", key))]
    if leaks:
        shutil.rmtree(out)
        raise SystemExit(f"secret-looking content in {leaks}; export removed")
    return {
        "runs": len(runs), "cost": cost, "eval_runs": len(eval_runs),
        "compares": len(compares), "traces": len(details), "skipped": skipped,
    }  # fmt: skip


def export(args: argparse.Namespace) -> int:
    key = os.environ.get("ADMIN_API_KEY")
    if not key:
        sys.exit("ADMIN_API_KEY must be set in the environment")
    r = export_replay(args.gateway, key, OUT, since=args.since, traces=args.traces)
    for why in r["skipped"]:
        print(f"SKIPPED eval run {why}", file=sys.stderr)
    print(
        f"exported {r['runs']} interactive runs (${r['cost']:.4f}), {r['eval_runs']} eval runs, "
        f"{r['compares']} comparisons, {r['traces']} trace files -> {OUT}"
    )
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("record", record), ("export", export)):
        s = sub.add_parser(name)
        s.add_argument("--gateway", default="http://localhost:8080")
        if name == "export":
            s.add_argument(
                "--since",
                help="only runs created at/after this ISO timestamp (skip older recordings)",
            )
            s.add_argument(
                "--traces",
                choices=["failed", "all"],
                default="failed",
                help="which eval-linked runs get a trace file (default: failed ones only)",
            )
        if name == "record":
            s.add_argument("--config", default=str(ROOT / "config" / "agents" / "baseline.yaml"))
        s.set_defaults(fn=fn)
    args = p.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
