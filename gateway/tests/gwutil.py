"""Shared test helpers: a scriptable fake agent service, trace builders, polling."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
from agentlens_agent.answer import Answer
from agentlens_agent.events import RunFinished, RunStarted, RunTotals, SpanFinished, Usage, utcnow
from agentlens_gateway.agent_client import AgentError

KEY = "test-admin-key-0123456789abcdef"
HEADERS = {"X-API-Key": KEY}

INLINE_CONFIG: dict[str, Any] = {
    "name": "t",
    "provider": "fake",
    "model": "fake-model",
    "system_prompt_id": "system_v1",
    "system_prompt_sha": "abc123",
    "tool_allowlist": ["calculate"],
}


def trace_events(
    run_id: UUID, *, cost: float = 0.02, text: str = "42"
) -> tuple[RunStarted, list[SpanFinished], RunFinished]:
    """A realistic 2-step run: llm -> tool -> llm -> (root span LAST, like the real agent) -> run.finished."""
    t0 = utcnow()
    root = uuid4()

    def span(seq: int, type_: str, name: str, **kw: Any) -> SpanFinished:
        return SpanFinished(
            run_id=run_id, span_id=kw.pop("span_id", uuid4()), parent_span_id=kw.pop("parent", root), seq=seq,
            type=type_, name=name, started_at=t0 + timedelta(milliseconds=seq * 100),
            ended_at=t0 + timedelta(milliseconds=seq * 100 + 80), **kw,
        )  # fmt: skip

    spans = [
        span(1, "llm_call", "fake-model", model="fake-model", usage=Usage(input_tokens=100, output_tokens=20, cache_read_tokens=5),
             cost_usd=cost / 2, input={"new_messages": []}, output={"tool_calls": [{"name": "calculate"}]}, attributes={"attempt": 1}),
        span(2, "tool_call", "calculate", input={"expression": "6*7"}, output={"content": "42"}),
        span(3, "llm_call", "fake-model", model="fake-model", usage=Usage(input_tokens=110, output_tokens=20, cache_read_tokens=5),
             cost_usd=cost / 2, output={"tool_calls": [{"name": "submit_answer"}]}),
        span(4, "agent", "agent", span_id=root, parent=None, usage=Usage(input_tokens=210, output_tokens=40, cache_read_tokens=10),
             cost_usd=cost, input={"question": "q"}, output={"final_answer": {"text": text}}, attributes={"steps": 2}),
    ]  # fmt: skip
    finished = RunFinished(
        run_id=run_id, status="completed", termination_reason="answered",
        final_answer=Answer(text=text, value=42.0, unit="count"),
        totals=RunTotals(usage=Usage(input_tokens=210, output_tokens=40, cache_read_tokens=10), cost_usd=cost,
                         latency_ms=900, steps=2, tool_calls=1),
    )  # fmt: skip
    return RunStarted(run_id=run_id), spans, finished


class FakeAgent:
    """Stands in for the agent service. `mode` selects behaviour; `release` gates the 'block' mode so tests can
    observe a run that is genuinely in flight."""

    def __init__(self) -> None:
        self.mode = "ok"
        self.cost = 0.02
        self.release = asyncio.Event()
        self.first_span_sent = asyncio.Event()
        self.runs: list[UUID] = []
        self.configs: list[dict[str, Any]] = []
        self.closed = False

    async def stream(
        self, *, run_id: UUID, question: str, config: dict[str, Any]
    ) -> AsyncIterator[Any]:
        self.runs.append(run_id)
        self.configs.append(config)
        started, spans, finished = trace_events(run_id, cost=self.cost)
        if self.mode == "unreachable":
            raise AgentError("agent service unavailable: ConnectError")
        yield started
        if self.mode == "wrong_run":
            spans[0] = spans[0].model_copy(update={"run_id": uuid4()})
        if self.mode in ("block", "block_forever"):
            yield spans[0]
            self.first_span_sent.set()
            if self.mode == "block_forever":
                await asyncio.sleep(3600)
            await self.release.wait()
            for s in spans[1:]:
                yield s
            yield finished
            return
        for s in spans:
            yield s
        if self.mode == "error_midway":
            raise AgentError("agent service unavailable: ReadError")
        if self.mode != "no_finish":
            yield finished

    async def aclose(self) -> None:
        self.closed = True


async def wait_for(
    client: httpx.AsyncClient, run_id: str, status: str | set[str], within: float = 5.0
) -> dict[str, Any]:
    wanted = {status} if isinstance(status, str) else status
    deadline = time.monotonic() + within
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = (await client.get(f"/api/v1/runs/{run_id}")).json()
        if last["status"] in wanted:
            return last
        await asyncio.sleep(0.02)
    raise AssertionError(
        f"run {run_id} never reached {wanted}; last status {last.get('status')}: {last}"
    )


async def post_run(
    client: httpx.AsyncClient, question: str = "What is 6*7?", **extra: Any
) -> httpx.Response:
    body = {"question": question, "config": INLINE_CONFIG, **extra}
    return await client.post("/api/v1/runs", json=body)
