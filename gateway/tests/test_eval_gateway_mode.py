"""EV-10: `agentlens-eval run` in gateway mode, against a REAL gateway (real socket, real PostgreSQL).

The CLI runs in a worker thread with its own event loop (it calls asyncio.run), the gateway in the test's loop.
Acceptance: results match direct mode on the same smoke set; the dataset, config, eval run, runs and results are all
visible through the gateway API.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from agentlens_agent.config import load_system_prompt
from agentlens_evals import cli
from agentlens_evals.case import load_dataset
from agentlens_evals.runner import RunRecord, run_one
from gwutil import HEADERS, KEY, FakeAgent, trace_events
from test_sse import live_server

API = "/api/v1"

CASES = [
    # FakeAgent answers value=42 unit=count, so: pass, fail (wrong value), fail (should have abstained)
    {"case_key": "calc-answer-42", "question": "What is 6*7?", "category": "calc", "difficulty": "easy",
     "expected": {"kind": "numeric", "value": 42, "unit": "count"}},
    {"case_key": "calc-answer-1", "question": "What is 1*1?", "category": "calc", "difficulty": "easy",
     "expected": {"kind": "numeric", "value": 1, "unit": "count"}},
    {"case_key": "unans-no-answer", "question": "What will happen in 2099?", "category": "unanswerable",
     "difficulty": "easy", "expected": {"kind": "abstain"}},
]  # fmt: skip

CONFIG_YAML = """name: smoke
provider: fake
model: fake-model
system_prompt_id: system_v1
max_cost_usd: 0.5
tool_allowlist: [calculate]
"""


def write_dataset(tmp: Path, cases: list[dict] = CASES) -> Path:
    path = tmp / "smoke.jsonl"
    path.write_text("".join(json.dumps(c) + "\n" for c in cases))
    return path


async def run_cli(monkeypatch: pytest.MonkeyPatch, base: str, tmp: Path, *extra: str) -> int:
    monkeypatch.setenv("AGENTLENS_GATEWAY_URL", base)
    monkeypatch.setenv("ADMIN_API_KEY", KEY)
    (tmp / "config.yaml").write_text(CONFIG_YAML)
    argv = [
        "run", "--dataset", str(tmp / "smoke.jsonl"), "--config", str(tmp / "config.yaml"),
        "--repeats", "2", "--budget-usd", "5", "--label", "ev10", "--out", str(tmp / "out"), *extra,
    ]  # fmt: skip

    def call() -> int:
        try:
            cli.main(argv)
        except SystemExit as e:
            return int(e.code or 0)
        return 0

    return await asyncio.to_thread(call)


def read_results(tmp: Path) -> list[RunRecord]:
    lines = (tmp / "out" / "results.jsonl").read_text().splitlines()
    return [RunRecord.model_validate_json(x) for x in lines if x.strip()]


async def direct_mode_records(tmp: Path, repeats: int) -> list[RunRecord]:
    """The same smoke set through direct mode, with the agent's events coming from the same scripted trace."""

    async def factory(_question: str):
        started, spans, finished = trace_events(uuid4())
        for ev in (started, *spans, finished):
            yield ev

    cases = load_dataset(tmp / "smoke.jsonl")
    return [await run_one(c, r, factory) for c in cases for r in range(repeats)]


def comparable(r: RunRecord) -> dict:
    return r.model_dump(exclude={"run_id"})


async def test_gateway_mode_matches_direct_mode_and_publishes_everything(
    make_settings, tmp_path, monkeypatch
) -> None:
    write_dataset(tmp_path)
    async with (
        live_server(make_settings(), FakeAgent()) as srv,
        httpx.AsyncClient(base_url=srv.base, headers=HEADERS) as c,
    ):
        code = await run_cli(monkeypatch, srv.base, tmp_path)
        assert code == 0

        gw = sorted(read_results(tmp_path), key=lambda r: (r.case_key, r.repeat))
        direct = sorted(
            await direct_mode_records(tmp_path, 2), key=lambda r: (r.case_key, r.repeat)
        )
        assert [comparable(r) for r in gw] == [
            comparable(r) for r in direct
        ]  # THE acceptance criterion
        assert [(r.case_key, r.passed) for r in gw if r.repeat == 0] == [
            ("calc-answer-1", False), ("calc-answer-42", True), ("unans-no-answer", False)
        ]  # fmt: skip

        ers = (await c.get(f"{API}/eval-runs")).json()
        assert len(ers) == 1
        er = (await c.get(f"{API}/eval-runs/{ers[0]['id']}")).json()
        assert er["status"] == "completed" and er["label"] == "ev10" and er["repeats"] == 2
        results = (await c.get(f"{API}/eval-runs/{ers[0]['id']}/results")).json()
        assert len(results) == 6
        by_key = {(r["case_key"], r["repeat_idx"]): r for r in results}
        assert by_key[("calc-answer-42", 0)]["passed"] is True
        assert by_key[("calc-answer-1", 1)]["failure_category"] is not None
        assert by_key[("calc-answer-42", 0)]["scores"]["task_success"] is True

        runs = (await c.get(f"{API}/runs", params={"eval_run_id": ers[0]["id"]})).json()["items"]
        assert len(runs) == 6 and {r["source"] for r in runs} == {"eval"}
        stored = {r["run_id"] for r in results}
        assert stored == {r["id"] for r in runs}  # every result links to a stored, traced run
        spans = (await c.get(f"{API}/runs/{runs[0]['id']}", params={"include": "spans"})).json()
        assert [s["type"] for s in spans["spans"]][-1] == "agent"

        cfgs = (await c.get(f"{API}/configs")).json()
        assert len(cfgs) == 1 and cfgs[0]["system_prompt_sha"] == load_system_prompt("system_v1")[1]
        assert len((await c.get(f"{API}/datasets")).json()) == 1


async def test_rerun_resumes_the_same_eval_run_and_starts_no_new_runs(
    make_settings, tmp_path, monkeypatch
) -> None:
    write_dataset(tmp_path)
    agent = FakeAgent()
    async with (
        live_server(make_settings(), agent) as srv,
        httpx.AsyncClient(base_url=srv.base, headers=HEADERS) as c,
    ):
        assert await run_cli(monkeypatch, srv.base, tmp_path) == 0
        started = len(agent.runs)
        assert await run_cli(monkeypatch, srv.base, tmp_path) == 0
        assert len(agent.runs) == started  # nothing re-executed, nothing re-billed
        assert len((await c.get(f"{API}/eval-runs")).json()) == 1
        assert len((await c.get(f"{API}/datasets")).json()) == 1


async def test_incomplete_gold_is_refused_before_any_model_call(
    make_settings, tmp_path, monkeypatch, capsys
) -> None:
    draft = {"case_key": "lookup-draft-empty", "question": "q?", "category": "lookup",
             "difficulty": "easy", "expected": {"kind": "numeric", "unit": "USD_millions"}}  # fmt: skip
    write_dataset(tmp_path, [draft])
    agent = FakeAgent()
    async with live_server(make_settings(), agent) as srv:
        assert await run_cli(monkeypatch, srv.base, tmp_path) == 2
    assert agent.runs == []
    assert "incomplete gold" in capsys.readouterr().err


async def test_a_run_the_gateway_fails_is_published_as_a_failed_result(
    make_settings, tmp_path, monkeypatch
) -> None:
    write_dataset(tmp_path, CASES[:1])
    agent = FakeAgent()
    agent.mode = "unreachable"  # the agent service is down: the gateway ends each run as failed/provider_error
    async with (
        live_server(make_settings(), agent) as srv,
        httpx.AsyncClient(base_url=srv.base, headers=HEADERS) as c,
    ):
        assert await run_cli(monkeypatch, srv.base, tmp_path) == 0
        recs = read_results(tmp_path)
        assert len(recs) == 2 and all(not r.passed for r in recs)
        assert all(r.failure_category == "provider_error" and r.run_id for r in recs)
        er = (await c.get(f"{API}/eval-runs")).json()[0]
        results = (await c.get(f"{API}/eval-runs/{er['id']}/results")).json()
        assert len(results) == 2 and {r["failure_category"] for r in results} == {"provider_error"}


async def test_missing_admin_key_is_a_clear_error(
    make_settings, tmp_path, monkeypatch, capsys
) -> None:
    write_dataset(tmp_path)
    async with live_server(make_settings(), FakeAgent()) as srv:
        monkeypatch.delenv("ADMIN_API_KEY", raising=False)
        monkeypatch.setenv("AGENTLENS_GATEWAY_URL", srv.base)
        (tmp_path / "config.yaml").write_text(CONFIG_YAML)
        argv = ["run", "--dataset", str(tmp_path / "smoke.jsonl"), "--config",
                str(tmp_path / "config.yaml"), "--budget-usd", "1", "--out", str(tmp_path / "o")]  # fmt: skip
        with pytest.raises(SystemExit) as e:
            await asyncio.to_thread(cli.main, argv)
    assert e.value.code == 2 and "ADMIN_API_KEY" in capsys.readouterr().err


async def test_budget_stop_exits_3_and_leaves_the_eval_run_open_for_a_resume(
    make_settings, tmp_path, monkeypatch
) -> None:
    write_dataset(tmp_path)
    agent = FakeAgent()
    async with (
        live_server(make_settings(), agent) as srv,
        httpx.AsyncClient(base_url=srv.base, headers=HEADERS) as c,
    ):
        # per-run reservation is the config's max_cost_usd (0.5): a 0.1 budget cannot start any run
        code = await run_cli(monkeypatch, srv.base, tmp_path, "--budget-usd", "0.1")
        assert code == 3 and agent.runs == []
        er = (await c.get(f"{API}/eval-runs")).json()[0]
        assert (await c.get(f"{API}/eval-runs/{er['id']}")).json()["status"] == "running"
        # resume with a real budget: same eval run, now completed
        assert await run_cli(monkeypatch, srv.base, tmp_path) == 0
        assert len((await c.get(f"{API}/eval-runs")).json()) == 1
        assert (await c.get(f"{API}/eval-runs/{er['id']}")).json()["status"] == "completed"
