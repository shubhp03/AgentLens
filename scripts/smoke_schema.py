"""Applies the SQL migrations (gateway/migrations) to a throwaway embedded Postgres and checks constraints + views.

Run: uv run --group db-test python scripts/smoke_schema.py
(The gateway test-suite also runs these migrations on real PostgreSQL; this is the fast standalone check.)
"""

import json
import sys
import tempfile
import uuid
from pathlib import Path

import pgserver
import psycopg2

MIGRATIONS = sorted(Path(__file__).resolve().parents[1].glob("gateway/migrations/V*__*.sql"))


def expect_error(cur, sql, args=(), *, why):
    cur.execute("SAVEPOINT s")
    try:
        cur.execute(sql, args)
    except psycopg2.Error:
        cur.execute("ROLLBACK TO SAVEPOINT s")
        return
    raise AssertionError(f"expected constraint violation: {why}")


def main() -> int:
    srv = pgserver.get_server(tempfile.mkdtemp())
    conn = psycopg2.connect(srv.get_uri())
    conn.autocommit = False
    cur = conn.cursor()
    for m in MIGRATIONS:
        cur.execute(m.read_text())
    ids = {
        k: str(uuid.uuid4()) for k in ("cfg", "ds", "case", "evr", "run1", "run2", "span1", "span2")
    }

    cur.execute(
        "INSERT INTO agent_configs (id,name,config_hash,provider,model,system_prompt_id,system_prompt_sha)"
        " VALUES (%s,'baseline','h1','anthropic','m','system_v1','sha')",
        (ids["cfg"],),
    )
    cur.execute(
        "INSERT INTO datasets (id,name,version,content_hash) VALUES (%s,'golden','v0','h')",
        (ids["ds"],),
    )
    cur.execute(
        "INSERT INTO eval_cases (id,dataset_id,case_key,question,category,difficulty,expected)"
        " VALUES (%s,%s,'c1','q','lookup','easy',%s)",
        (ids["case"], ids["ds"], json.dumps({"kind": "numeric"})),
    )
    cur.execute(
        "INSERT INTO eval_runs (id,dataset_id,config_id,is_baseline) VALUES (%s,%s,%s,true)",
        (ids["evr"], ids["ds"], ids["cfg"]),
    )
    expect_error(
        cur,
        "INSERT INTO eval_runs (dataset_id,config_id,is_baseline) VALUES (%s,%s,true)",
        (ids["ds"], ids["cfg"]),
        why="one baseline per dataset",
    )

    for rid, cost, lat in ((ids["run1"], 0.10, 1000), (ids["run2"], 0.30, 3000)):
        cur.execute(
            "INSERT INTO runs (id,config_id,eval_run_id,source,question,status,termination_reason,"
            "cost_usd,latency_ms,created_at,started_at,finished_at) "
            "VALUES (%s,%s,%s,'eval','q','completed','answered',%s,%s,now(),now(),now())",
            (rid, ids["cfg"], ids["evr"], cost, lat),
        )
    expect_error(
        cur,
        "INSERT INTO runs (id,config_id,question,status) VALUES (%s,%s,'q','completed')",
        (str(uuid.uuid4()), ids["cfg"]),
        why="completed run needs finished_at",
    )

    # spans: child may be persisted before its parent (no FK), latency is derived, (run_id, seq) unique
    cur.execute(
        "INSERT INTO spans (id,run_id,parent_span_id,seq,type,name,started_at,ended_at)"
        " VALUES (%s,%s,%s,1,'llm_call','m',now() - interval '2 seconds',now())",
        (ids["span1"], ids["run1"], ids["span2"]),
    )
    cur.execute("SELECT latency_ms FROM spans WHERE id=%s", (ids["span1"],))
    assert 1900 <= cur.fetchone()[0] <= 2100
    expect_error(
        cur,
        "INSERT INTO spans (id,run_id,seq,type,name,started_at) VALUES (%s,%s,1,'tool_call','t',now())",
        (str(uuid.uuid4()), ids["run1"]),
        why="unique (run_id, seq)",
    )

    # eval_results: pass <=> no failure category
    ins = (
        "INSERT INTO eval_results (eval_run_id,case_id,run_id,repeat_idx,passed,failure_category,latency_ms,cost_usd)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
    )
    cur.execute(ins, (ids["evr"], ids["case"], ids["run1"], 0, True, None, 1000, 0.10))
    cur.execute(ins, (ids["evr"], ids["case"], ids["run2"], 1, False, "wrong_period", 3000, 0.30))
    expect_error(
        cur,
        ins,
        (ids["evr"], ids["case"], None, 2, False, None, 1, 0),
        why="failure needs category",
    )
    expect_error(
        cur,
        ins,
        (ids["evr"], ids["case"], None, 3, True, "other", 1, 0),
        why="pass must not have category",
    )

    # Case-level metrics: case c1 has 1 pass of 2 repeats => NOT a majority => the case fails.
    cur.execute(
        "SELECT n_results, n_cases, n_cases_passed, pass_rate, run_pass_rate, total_cost_usd, "
        "cost_per_success_usd, p50_latency_ms FROM eval_run_metrics WHERE eval_run_id=%s",
        (ids["evr"],),
    )
    n, n_cases, n_cp, rate, run_rate, total, per_success, p50 = cur.fetchone()
    assert (n, n_cases, n_cp) == (2, 1, 0), (n, n_cases, n_cp)
    assert (float(rate), float(run_rate)) == (0.0, 0.5), "case-level vs run-level pass rate"
    assert (float(total), float(per_success), float(p50)) == (0.4, 0.4, 2000.0)

    # A second case with 2 passes of 3 repeats IS a majority.
    case2 = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO eval_cases (id,dataset_id,case_key,question,category,difficulty,expected)"
        " VALUES (%s,%s,'c2','q','lookup','easy',%s)",
        (case2, ids["ds"], json.dumps({"kind": "numeric"})),
    )
    for rep, ok in ((0, True), (1, True), (2, False)):
        cur.execute(ins, (ids["evr"], case2, None, rep, ok, None if ok else "other", 10, 0))
    cur.execute(
        "SELECT n_cases, n_cases_passed, pass_rate FROM eval_run_metrics WHERE eval_run_id=%s",
        (ids["evr"],),
    )
    n_cases, n_cp, rate = cur.fetchone()
    assert (n_cases, n_cp, float(rate)) == (2, 1, 0.5), "strict majority per case"

    cur.execute("SELECT sum(runs), sum(cost_usd) FROM daily_spend")
    runs, cost = cur.fetchone()
    assert runs == 2 and float(cost) == 0.4
    print(f"schema OK ({len(MIGRATIONS)} migration(s) applied, constraints and views verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
