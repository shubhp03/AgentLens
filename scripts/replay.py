"""Record real runs through a live gateway, then export them as the dashboard's static REPLAY dataset.

    # 1. with the stack up (docs/RUNNING.md section 4) and ADMIN_API_KEY set
    uv run python scripts/replay.py record  --gateway http://localhost:8080
    # 2. snapshot everything the gateway holds (source=interactive) into dashboard/public/replay/
    uv run python scripts/replay.py export  --gateway http://localhost:8080

What the replay is: real Claude calls over real SEC data, captured once and served as static JSON (no backend, no spend).
What it is NOT: benchmark results. Eval pages stay empty until real, human-verified eval runs exist (EV-02).

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


def export(args: argparse.Namespace) -> int:
    with client(args.gateway) as c:
        items, cursor = [], None
        while True:
            page = c.get(
                f"{API}/runs",
                params={
                    "source": "interactive",
                    "limit": 200,
                    **({"cursor": cursor} if cursor else {}),
                },
            ).json()
            items += page["items"]
            cursor = page["next_cursor"]
            if not cursor:
                break
        runs = [
            r
            for r in items
            if r["status"] in ("completed", "failed")
            and (not args.since or r["created_at"] >= args.since)
        ]
        runs.sort(key=lambda r: r["created_at"], reverse=True)
        details = {
            r["id"]: c.get(f"{API}/runs/{r['id']}", params={"include": "spans"}).json()
            for r in runs
        }
        configs = c.get(f"{API}/configs").json()
        used = {r["config_id"] for r in runs}
        overview = c.get(f"{API}/stats/overview").json()

    if OUT.exists():
        shutil.rmtree(OUT)
    write(OUT / "runs.json", runs)
    for rid, d in details.items():
        write(OUT / "runs" / f"{rid}.json", d)
    write(OUT / "configs.json", [x for x in configs if x["id"] in used])
    write(
        OUT / "eval-runs.json", []
    )  # no real, verified eval runs exist yet; never publish plumbing checks as results
    cost = sum(r["totals"]["cost_usd"] for r in runs)
    write(OUT / "overview.json", {
        "runs_today": len(runs), "cost_today_usd": round(cost, 6),
        "daily_cap_usd": overview["daily_cap_usd"], "latest_baseline_pass_rate": None,
    })  # fmt: skip
    (OUT / "README.txt").write_text(
        f"Runs recorded from a live stack (real Claude calls, real SEC data) and exported {datetime.now(UTC).isoformat(timespec='seconds')}"
        " by scripts/replay.py. Individual runs, not benchmark results. No eval runs are included.\n"
    )
    leaks = [
        p
        for p in OUT.rglob("*.json")
        if any(s in p.read_text() for s in ("sk-ant", os.environ["ADMIN_API_KEY"]))
    ]
    if leaks:
        sys.exit(f"secret-looking content in {leaks}")
    print(f"exported {len(runs)} runs, cost ${cost:.4f} -> {OUT}")
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
        if name == "record":
            s.add_argument("--config", default=str(ROOT / "config" / "agents" / "baseline.yaml"))
        s.set_defaults(fn=fn)
    args = p.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
