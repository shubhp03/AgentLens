"""SSE: replay + live fan-out, against a REAL server on a real socket (ASGITransport buffers, so it can't show streaming)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

import httpx
import uvicorn
from agentlens_gateway.app import create_app
from agentlens_gateway.routes import runs as runs_routes
from agentlens_gateway.settings import Settings
from fastapi import FastAPI
from gwutil import HEADERS, FakeAgent, wait_for

API = "/api/v1"


@dataclass
class Live:
    base: str
    agent: FakeAgent
    app: FastAPI


@asynccontextmanager
async def live_server(settings: Settings, agent: FakeAgent) -> AsyncIterator[Live]:
    app = create_app(settings, agent_client=agent)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    )
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.02)
        assert server.started, "server did not start"
        port = server.servers[0].sockets[0].getsockname()[1]
        yield Live(f"http://127.0.0.1:{port}", agent, app)
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 15)


async def read_events(resp: httpx.Response) -> AsyncIterator[dict | str]:
    """Yield parsed `data:` payloads, and the literal ': keepalive' comments."""
    buf = ""
    async for chunk in resp.aiter_text():
        buf += chunk
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            if block.startswith(":"):
                yield block
            elif block.startswith("data:"):
                yield json.loads(block[5:].strip())


async def test_replay_of_a_finished_run_then_the_stream_closes(make_settings) -> None:
    async with (
        live_server(make_settings(), FakeAgent()) as srv,
        httpx.AsyncClient(base_url=srv.base, headers=HEADERS) as c,
    ):
        rid = (await c.post(f"{API}/runs", json={"question": "q", "config": CONFIG})).json()["id"]
        await wait_for(c, rid, "completed")
        async with c.stream("GET", f"{API}/runs/{rid}/events") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            assert resp.headers["cache-control"] == "no-cache"
            events = [e async for e in read_events(resp)]
    assert [e["kind"] for e in events] == ["span.finished"] * 4 + ["run.finished"]
    assert [e["seq"] for e in events[:4]] == [1, 2, 3, 4]
    assert events[-1]["status"] == "completed" and events[-1]["termination_reason"] == "answered"
    assert events[-1]["totals"]["cost_usd"] == 0.02 and events[-1]["final_answer"]["value"] == 42.0
    assert events[0]["usage"]["cache_read_tokens"] == 5  # restored from storage


async def test_live_stream_follows_a_run_in_flight_without_gaps_or_duplicates(
    make_settings,
) -> None:
    agent = FakeAgent()
    agent.mode = "block"
    async with (
        live_server(make_settings(), agent) as srv,
        httpx.AsyncClient(base_url=srv.base, headers=HEADERS) as c,
    ):
        rid = (await c.post(f"{API}/runs", json={"question": "q", "config": CONFIG})).json()["id"]
        await asyncio.wait_for(agent.first_span_sent.wait(), 5)
        got: list[dict] = []
        async with c.stream("GET", f"{API}/runs/{rid}/events", timeout=10) as resp:
            events = read_events(resp)
            got.append(
                await asyncio.wait_for(anext(events), 5)
            )  # the span stored before we connected (replay)
            assert got[0]["kind"] == "span.finished" and got[0]["seq"] == 1
            agent.release.set()  # the rest arrives live
            async for e in events:
                got.append(e)
    assert [(e["kind"], e.get("seq")) for e in got] == [
        ("span.finished", 1), ("span.finished", 2), ("span.finished", 3), ("span.finished", 4), ("run.finished", None)
    ]  # fmt: skip


async def test_two_subscribers_each_get_every_event(make_settings) -> None:
    agent = FakeAgent()
    agent.mode = "block"
    async with (
        live_server(make_settings(), agent) as srv,
        httpx.AsyncClient(base_url=srv.base, headers=HEADERS) as c,
    ):
        rid = (await c.post(f"{API}/runs", json={"question": "q", "config": CONFIG})).json()["id"]
        await asyncio.wait_for(agent.first_span_sent.wait(), 5)

        async def consume() -> list[str]:
            async with c.stream("GET", f"{API}/runs/{rid}/events", timeout=10) as resp:
                return [e["kind"] async for e in read_events(resp)]

        t1, t2 = asyncio.create_task(consume()), asyncio.create_task(consume())
        await asyncio.sleep(0.3)
        agent.release.set()
        r1, r2 = await asyncio.gather(t1, t2)
    assert r1 == r2 == ["span.finished"] * 4 + ["run.finished"]


async def test_keepalive_comments_flow_while_idle(make_settings, monkeypatch) -> None:
    monkeypatch.setattr(runs_routes, "KEEPALIVE_S", 0.1)
    agent = FakeAgent()
    agent.mode = "block"
    async with (
        live_server(make_settings(), agent) as srv,
        httpx.AsyncClient(base_url=srv.base, headers=HEADERS) as c,
    ):
        rid = (await c.post(f"{API}/runs", json={"question": "q", "config": CONFIG})).json()["id"]
        await asyncio.wait_for(agent.first_span_sent.wait(), 5)
        async with c.stream("GET", f"{API}/runs/{rid}/events", timeout=10) as resp:
            seen = []
            async for e in read_events(resp):
                seen.append(e)
                if "keepalive" in str(e):
                    break
        agent.release.set()
    assert any(isinstance(e, str) and "keepalive" in e for e in seen)


async def test_stream_ends_with_run_finished_when_the_run_is_cancelled(make_settings) -> None:
    agent = FakeAgent()
    agent.mode = "block_forever"
    async with (
        live_server(make_settings(), agent) as srv,
        httpx.AsyncClient(base_url=srv.base, headers=HEADERS) as c,
    ):
        rid = (await c.post(f"{API}/runs", json={"question": "q", "config": CONFIG})).json()["id"]
        await asyncio.wait_for(agent.first_span_sent.wait(), 5)

        async def consume() -> list[dict]:
            async with c.stream("GET", f"{API}/runs/{rid}/events", timeout=10) as resp:
                return [e async for e in read_events(resp) if isinstance(e, dict)]

        t = asyncio.create_task(consume())
        await asyncio.sleep(0.3)
        assert (await c.delete(f"{API}/runs/{rid}")).status_code == 202
        events = await asyncio.wait_for(t, 5)
    assert events[-1]["kind"] == "run.finished" and events[-1]["status"] == "cancelled"
    assert events[-1]["termination_reason"] == "cancelled"


async def test_stream_auth_and_unknown_run(gw) -> None:
    assert (await gw.client.get(f"{API}/runs/{uuid4()}/events")).status_code == 404
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gw.app), base_url="http://test"
    ) as anon:
        assert (await anon.get(f"{API}/runs/{uuid4()}/events")).status_code == 401


CONFIG = {
    "name": "t", "provider": "fake", "model": "fake-model", "system_prompt_id": "system_v1",
    "system_prompt_sha": "abc123", "tool_allowlist": ["calculate"],
}  # fmt: skip


async def test_a_span_seen_in_the_replay_is_not_delivered_again_when_it_also_arrives_live(
    make_settings,
) -> None:
    """The subscribe-then-replay ordering means a span can legitimately be in both; the client must see it once."""
    agent = FakeAgent()
    agent.mode = "block"
    async with (
        live_server(make_settings(), agent) as srv,
        httpx.AsyncClient(base_url=srv.base, headers=HEADERS) as c,
    ):
        rid = (await c.post(f"{API}/runs", json={"question": "q", "config": CONFIG})).json()["id"]
        await asyncio.wait_for(agent.first_span_sent.wait(), 5)
        stored = (await c.get(f"{API}/runs/{rid}", params={"include": "spans"})).json()["spans"][0]
        got: list[dict] = []
        async with c.stream("GET", f"{API}/runs/{rid}/events", timeout=10) as resp:
            events = read_events(resp)
            got.append(await asyncio.wait_for(anext(events), 5))
            from uuid import UUID

            from agentlens_gateway.store import span_finished_event

            dup = span_finished_event({**stored, "id": UUID(stored["id"]), "run_id": UUID(rid),
                                       "parent_span_id": None, "started_at": datetime.fromisoformat(stored["started_at"]),
                                       "ended_at": datetime.fromisoformat(stored["ended_at"])})  # fmt: skip
            srv.app.state.bus.publish(UUID(rid), dup)  # the same span, delivered live a second time
            agent.release.set()
            async for e in events:
                got.append(e)
    assert [e.get("seq") for e in got if e["kind"] == "span.finished"] == [1, 2, 3, 4]
