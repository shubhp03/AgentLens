"""HTTP surface: configuration problems must be clear 4xx/5xx messages, never a bare 500, and never spend money."""

from __future__ import annotations

import pytest
from agentlens_agent.main import app
from fastapi.testclient import TestClient

BODY = {
    "run_id": "11111111-1111-4111-8111-111111111111",
    "question": "hi",
    "config": {"name": "t", "provider": "anthropic", "model": "claude-sonnet-5-5"},
}


@pytest.fixture
def client() -> TestClient:
    app.state.registry = object()  # never reached: building the runner fails first
    return TestClient(app)  # no `with`: skips the lifespan (which would connect to MCP servers)


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_missing_pricing_file_is_503_with_a_clear_message(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("PRICING_FILE", str(tmp_path / "nope.yaml"))
    r = client.post("/v1/agent/run", json=BODY)
    assert r.status_code == 503 and "nope.yaml" in r.json()["detail"]


def test_model_without_a_price_is_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    f = tmp_path / "p.yaml"
    f.write_text(
        'as_of: "2026-01-01"\nmodels:\n  "anthropic:other":\n    input_usd_per_mtok: 1\n    output_usd_per_mtok: 2\n'
    )
    monkeypatch.setenv("PRICING_FILE", str(f))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    r = client.post("/v1/agent/run", json=BODY)
    assert r.status_code == 422 and "claude-sonnet-5-5" in r.json()["detail"]


def test_unsupported_provider_is_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    f = tmp_path / "p.yaml"
    f.write_text("as_of: x\nmodels: {}\n")
    monkeypatch.setenv("PRICING_FILE", str(f))
    r = client.post(
        "/v1/agent/run", json={**BODY, "config": {**BODY["config"], "provider": "nope"}}
    )
    assert r.status_code == 422 and "unsupported provider" in r.json()["detail"]


def test_run_streams_ndjson_end_to_end(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """The real FastAPI route -> real AgentRunner -> NDJSON lines, with a scripted LLM and no tools."""
    import json

    from agentlens_agent import wiring
    from agentlens_agent.llm.base import LLMResponse, ToolCall
    from agentlens_agent.llm.fake import ScriptedLLM
    from agentlens_agent.registry import StaticToolRegistry

    f = tmp_path / "p.yaml"
    f.write_text(
        'as_of: "2026-01-01"\nmodels:\n  "fake:scripted":\n    input_usd_per_mtok: 1\n    output_usd_per_mtok: 2\n'
    )
    monkeypatch.setenv("PRICING_FILE", str(f))
    submit = ToolCall("c1", "submit_answer", {"abstained": True, "text": "cannot know"})
    monkeypatch.setattr(
        wiring, "build_llm", lambda cfg: ScriptedLLM([LLMResponse(text=None, tool_calls=(submit,))])
    )
    app.state.registry = StaticToolRegistry({})

    with client.stream("POST", "/v1/agent/run", json=BODY) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
        events = [json.loads(line) for line in r.iter_lines() if line]
    assert [e["kind"] for e in events] == [
        "run.started",
        "span.finished",
        "span.finished",
        "run.finished",
    ]
    assert (
        events[-1]["termination_reason"] == "answered"
        and events[-1]["final_answer"]["abstained"] is True
    )
    assert events[2]["type"] == "agent"  # root span last
