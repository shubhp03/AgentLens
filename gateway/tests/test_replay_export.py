"""scripts/replay.py export: eval runs reach the static replay ONLY when their dataset is fully human-verified.

Real gateway on a real socket with real PostgreSQL (the fake agent is the only stand-in). The CLI and the exporter are
synchronous, so both run in worker threads.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from agentlens_evals import cli
from gwutil import HEADERS, KEY, FakeAgent
from test_eval_gateway_mode import CASES, CONFIG_YAML
from test_sse import live_server

API = "/api/v1"
_PATH = Path(__file__).resolve().parents[2] / "scripts" / "replay.py"
_spec = importlib.util.spec_from_file_location("replay_script", _PATH)
replay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(replay)

VERIFIED = [
    c | {"status": "verified", "provenance": {"source": "unit test", "verified_by": "tester"}}
    for c in CASES
]


def write_cases(tmp: Path, cases: list[dict]) -> None:
    (tmp / "smoke.jsonl").write_text("".join(json.dumps(c) + "\n" for c in cases))
    (tmp / "config.yaml").write_text(CONFIG_YAML)


async def eval_run(monkeypatch: pytest.MonkeyPatch, base: str, tmp: Path, out: str) -> None:
    monkeypatch.setenv("AGENTLENS_GATEWAY_URL", base)
    monkeypatch.setenv("ADMIN_API_KEY", KEY)
    argv = [
        "run", "--dataset", str(tmp / "smoke.jsonl"), "--config", str(tmp / "config.yaml"),
        "--repeats", "1", "--budget-usd", "5", "--label", out, "--out", str(tmp / out),
    ]  # fmt: skip

    def call() -> None:
        try:
            cli.main(argv)
        except SystemExit as e:
            assert not e.code, f"eval run failed with exit {e.code}"

    await asyncio.to_thread(call)


async def export(base: str, out: Path, **kw) -> dict:
    return await asyncio.to_thread(replay.export_replay, base, KEY, out, **kw)


def ids(path: Path) -> set[str]:
    return {p.stem for p in path.glob("*.json")}


async def test_verified_eval_runs_are_published_with_comparisons_and_failed_traces(
    make_settings, tmp_path, monkeypatch
) -> None:
    write_cases(tmp_path, VERIFIED)
    async with (
        live_server(make_settings(), FakeAgent()) as srv,
        httpx.AsyncClient(base_url=srv.base, headers=HEADERS) as c,
    ):
        await eval_run(monkeypatch, srv.base, tmp_path, "a")
        await eval_run(
            monkeypatch, srv.base, tmp_path, "b"
        )  # same dataset + config, a second eval run
        ers = (await c.get(f"{API}/eval-runs")).json()
        assert len(ers) == 2
        await c.patch(f"{API}/eval-runs/{ers[0]['id']}", json={"is_baseline": True})
        await c.post(
            f"{API}/runs",
            json={"question": "What is 6*7?", "config": {
                "name": "t", "provider": "fake", "model": "fake-model", "system_prompt_id": "system_v1",
                "system_prompt_sha": "x", "tool_allowlist": ["calculate"]}},
        )  # fmt: skip
        await asyncio.sleep(1)

        out = tmp_path / "replay"
        r = await export(srv.base, out)
        assert r["eval_runs"] == 2 and r["compares"] == 2 and r["skipped"] == []

        published = json.loads((out / "eval-runs.json").read_text())
        assert {e["id"] for e in published} == {e["id"] for e in ers}
        for e in ers:
            detail = json.loads((out / "eval-runs" / f"{e['id']}.json").read_text())
            assert detail["metrics"]["n_cases"] == 3 and detail["status"] == "completed"
            results = json.loads((out / "eval-results" / f"{e['id']}.json").read_text())
            assert len(results) == 3
        a, b = ers[0]["id"], ers[1]["id"]
        assert (out / "compare" / f"{a}__{b}.json").exists() and (
            out / "compare" / f"{b}__{a}.json"
        ).exists()

        # default: traces only for FAILED results; the interactive run is always there; runs.json stays interactive-only
        runs = json.loads((out / "runs.json").read_text())
        assert len(runs) == 1 and runs[0]["source"] == "interactive"
        failed = {
            res["run_id"]
            for e in ers
            for res in json.loads((out / "eval-results" / f"{e['id']}.json").read_text())
            if not res["passed"]
        }
        passed = {
            res["run_id"]
            for e in ers
            for res in json.loads((out / "eval-results" / f"{e['id']}.json").read_text())
            if res["passed"]
        }
        have = ids(out / "runs")
        assert failed <= have and not (passed & have) and runs[0]["id"] in have

        overview = json.loads((out / "overview.json").read_text())
        assert overview["latest_baseline_pass_rate"] == pytest.approx(1 / 3, abs=1e-3)

        r_all = await export(srv.base, tmp_path / "replay_all", traces="all")
        assert (passed | failed) <= ids(tmp_path / "replay_all" / "runs") and r_all["traces"] == r[
            "traces"
        ] + len(passed)
        assert KEY not in "".join(p.read_text() for p in out.rglob("*.json"))


async def test_eval_runs_on_unverified_datasets_are_never_published(
    make_settings, tmp_path, monkeypatch
) -> None:
    write_cases(tmp_path, CASES)  # complete gold, but status=draft
    async with live_server(make_settings(), FakeAgent()) as srv:
        await eval_run(monkeypatch, srv.base, tmp_path, "draft")
        out = tmp_path / "replay"
        r = await export(srv.base, out)
    assert r["eval_runs"] == 0 and r["compares"] == 0
    assert len(r["skipped"]) == 1 and "unverified" in r["skipped"][0]
    assert json.loads((out / "eval-runs.json").read_text()) == []
    assert not (out / "eval-runs").exists() and not (out / "eval-results").exists()
    assert "No eval runs are included" in (out / "README.txt").read_text()


async def test_a_dataset_with_one_draft_case_blocks_publication(
    make_settings, tmp_path, monkeypatch
) -> None:
    mixed = [*VERIFIED[:2], CASES[2]]  # two verified, one draft
    write_cases(tmp_path, mixed)
    async with live_server(make_settings(), FakeAgent()) as srv:
        await eval_run(monkeypatch, srv.base, tmp_path, "mixed")
        r = await export(srv.base, tmp_path / "replay")
    assert r["eval_runs"] == 0 and "unverified" in r["skipped"][0]
