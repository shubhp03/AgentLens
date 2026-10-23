"""End-to-end CLI: `agentlens-eval run` against a scripted fake provider, no network, no spend."""

from __future__ import annotations

import json
from pathlib import Path

from agentlens_evals import cli

DATASET = [
    {
        "case_key": "unans-future-eps",
        "question": "What will Apple's FY2030 EPS be?",
        "category": "unanswerable",
        "difficulty": "easy",
        "expected": {"kind": "abstain"},
        "status": "verified",
        "provenance": {"source": "n/a (unanswerable by construction)", "verified_by": "tester"},
    },
]


def test_run_report_and_compare(tmp_path: Path, monkeypatch, capsys):
    ds = tmp_path / "ds.jsonl"
    ds.write_text("\n".join(json.dumps(c) for c in DATASET))
    pricing = tmp_path / "pricing.yaml"
    pricing.write_text(
        "as_of: 2026-10-01\nmodels:\n  fake:scripted:\n    input_usd_per_mtok: 1.0\n    output_usd_per_mtok: 1.0\n"
    )
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(
        "name: fake-agent\nprovider: fake\nmodel: scripted\nmax_steps: 3\nmax_cost_usd: 1.0\ntool_allowlist: []\n"
    )

    from agentlens_agent import wiring
    from agentlens_agent.answer import SUBMIT_ANSWER_NAME
    from agentlens_agent.events import Usage
    from agentlens_agent.llm.base import LLMResponse, ToolCall
    from agentlens_agent.llm.fake import ScriptedLLM

    class AlwaysAbstain(ScriptedLLM):
        async def complete(self, **kw):
            return LLMResponse(
                None,
                (ToolCall("a", SUBMIT_ANSWER_NAME, {"abstained": True, "text": "future"}),),
                Usage(input_tokens=10, output_tokens=5),
            )

    monkeypatch.setattr(wiring, "build_llm", lambda config: AlwaysAbstain(script=[]))

    out = tmp_path / "run"
    args = [
        "run", "--dataset", str(ds), "--config", str(cfg), "--pricing", str(pricing),
        "--repeats", "2", "--budget-usd", "5", "--out", str(out), "--label", "e2e", "--verified-only",
    ]  # fmt: skip
    code = None
    try:
        cli.main(args)
    except SystemExit as e:
        code = e.code
    assert code == 0, capsys.readouterr()

    results = [json.loads(line) for line in (out / "results.jsonl").read_text().splitlines()]
    assert len(results) == 2 and all(r["passed"] for r in results)
    meta = json.loads((out / "meta.json").read_text())
    assert meta["model"] == "fake:scripted" and meta["pricing_as_of"] == "2026-10-01"
    assert len(meta["dataset_sha256"]) == 64 and meta["repeats"] == 2
    report = (out / "report.md").read_text()
    assert "Pass rate: 100.0%" in report and "WARNING" not in report

    # compare a run with itself: no regression, exit code 0
    try:
        cli.main(["compare", str(out / "results.jsonl"), str(out / "results.jsonl")])
    except SystemExit as e:
        assert e.code == 0

    # regenerate the report from disk
    cli.sys.argv  # noqa: B018 - keep import used
    try:
        cli.main(["report", str(out), "--dataset", str(ds)])
    except SystemExit as e:
        assert e.code == 0


def test_gate_fails_below_floor(tmp_path: Path, monkeypatch):
    ds = tmp_path / "ds.jsonl"
    case = {
        **DATASET[0],
        "expected": {"kind": "text", "must_contain": ["zzz"]},
        "category": "text_extract",
    }
    ds.write_text(json.dumps(case))
    pricing = tmp_path / "p.yaml"
    pricing.write_text(
        "models:\n  fake:scripted:\n    input_usd_per_mtok: 1.0\n    output_usd_per_mtok: 1.0\n"
    )
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "name: a\nprovider: fake\nmodel: scripted\nmax_cost_usd: 1.0\ntool_allowlist: []\n"
    )

    from agentlens_agent import wiring
    from agentlens_agent.answer import SUBMIT_ANSWER_NAME
    from agentlens_agent.events import Usage
    from agentlens_agent.llm.base import LLMResponse, ToolCall
    from agentlens_agent.llm.fake import ScriptedLLM

    class Wrong(ScriptedLLM):
        async def complete(self, **kw):
            return LLMResponse(
                None,
                (ToolCall("a", SUBMIT_ANSWER_NAME, {"text": "nope"}),),
                Usage(input_tokens=1, output_tokens=1),
            )

    monkeypatch.setattr(wiring, "build_llm", lambda config: Wrong(script=[]))
    try:
        cli.main(["run", "--dataset", str(ds), "--config", str(cfg), "--pricing", str(pricing),
                  "--repeats", "1", "--budget-usd", "1", "--out", str(tmp_path / "o"),
                  "--gate", "--min-pass-rate", "0.5"])  # fmt: skip
    except SystemExit as e:
        assert e.code == 1
