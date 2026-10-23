"""Run lifecycle: create -> execute -> persist trace -> terminal state; failure paths; limits; recovery."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import httpx
import pytest
from agentlens_agent.events import SpanFinished
from agentlens_gateway.agent_client import HttpAgentClient
from agentlens_gateway.store import Store
from gwutil import INLINE_CONFIG, FakeAgent, post_run, trace_events, wait_for

API = "/api/v1"


async def test_happy_path_persists_the_whole_trace(gw, sql) -> None:
    r = await post_run(gw.client, "What is 6*7?")
    assert r.status_code == 202
    created = r.json()
    assert (
        created["status"] == "queued"
        and created["question"] == "What is 6*7?"
        and created["source"] == "interactive"
    )

    done = await wait_for(gw.client, created["id"], "completed")
    assert done["termination_reason"] == "answered" and done["error"] is None
    assert done["final_answer"]["value"] == 42.0 and done["final_answer"]["text"] == "42"
    assert done["totals"] == {
        "input_tokens": 210, "output_tokens": 40, "cache_read_tokens": 10, "cost_usd": 0.02,
        "latency_ms": 900, "step_count": 2, "tool_call_count": 1,
    }  # fmt: skip
    assert done["started_at"] and done["finished_at"]

    full = (await gw.client.get(f"{API}/runs/{created['id']}", params={"include": "spans"})).json()
    spans = full["spans"]
    assert [s["seq"] for s in spans] == [1, 2, 3, 4]
    assert [s["type"] for s in spans] == ["llm_call", "tool_call", "llm_call", "agent"]
    root = spans[-1]
    assert root["parent_span_id"] is None and all(
        s["parent_span_id"] == root["id"] for s in spans[:-1]
    )
    assert (
        spans[0]["input_tokens"] == 100
        and spans[0]["cost_usd"] == 0.01
        and spans[0]["latency_ms"] == 80
    )
    assert (
        spans[0]["attributes"]["cache_read_tokens"] == 5
    )  # not lost, even though spans have no such column
    assert "spans" not in (await gw.client.get(f"{API}/runs/{created['id']}")).json()


async def test_the_agent_receives_the_snapshotted_config(gw) -> None:
    r = await post_run(gw.client)
    await wait_for(gw.client, r.json()["id"], "completed")
    sent = gw.agent.configs[0]
    assert (
        sent["model"] == "fake-model"
        and sent["tool_allowlist"] == ["calculate"]
        and sent["max_steps"] == 12
    )
    assert set(sent) == {"name", "provider", "model", "system_prompt_id", "temperature", "max_steps", "max_cost_usd",
                         "max_output_tokens", "tool_allowlist", "params"}  # fmt: skip


async def test_run_by_config_id_and_request_validation(gw) -> None:
    cfg = (await gw.client.post(f"{API}/configs", json=INLINE_CONFIG)).json()
    ok = await gw.client.post(f"{API}/runs", json={"question": "q", "config_id": cfg["id"]})
    assert ok.status_code == 202 and ok.json()["config_id"] == cfg["id"]
    assert (
        await gw.client.post(f"{API}/runs", json={"question": "q", "config_id": str(uuid4())})
    ).status_code == 404
    assert (
        await gw.client.post(f"{API}/runs", json={"question": "q"})
    ).status_code == 400  # no config
    both = {"question": "q", "config_id": cfg["id"], "config": INLINE_CONFIG}
    assert (await gw.client.post(f"{API}/runs", json=both)).status_code == 400
    assert (await post_run(gw.client, "x" * 2001)).status_code == 400
    assert (await post_run(gw.client, source="eval")).status_code == 400  # eval needs eval_run_id
    assert (await post_run(gw.client, eval_run_id=str(uuid4()))).status_code == 404


# ------------------------------------------------------------------ listing / pagination


async def test_list_runs_filters_and_keyset_pagination(gw) -> None:
    ids = []
    for i in range(5):
        r = await post_run(gw.client, f"question {i}")
        ids.append(r.json()["id"])
        await wait_for(gw.client, ids[-1], "completed")

    seen, cursor = [], None
    while True:
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        page = (await gw.client.get(f"{API}/runs", params=params)).json()
        assert len(page["items"]) <= 2
        seen += [r["id"] for r in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == ids[::-1]  # newest first, every run exactly once

    done = (await gw.client.get(f"{API}/runs", params={"status": "completed"})).json()["items"]
    assert len(done) == 5
    assert (await gw.client.get(f"{API}/runs", params={"status": "failed"})).json()["items"] == []
    assert (await gw.client.get(f"{API}/runs", params={"source": "eval"})).json()["items"] == []
    assert (await gw.client.get(f"{API}/runs", params={"limit": 0})).status_code == 400
    assert (await gw.client.get(f"{API}/runs", params={"cursor": "garbage"})).status_code == 400
    by_cfg = (
        await gw.client.get(f"{API}/runs", params={"config_id": done[0]["config_id"]})
    ).json()["items"]
    assert len(by_cfg) == 5


# ------------------------------------------------------------------ failure paths


async def test_agent_unreachable_fails_the_run_with_a_clear_error(gateway_factory) -> None:
    agent = FakeAgent()
    agent.mode = "unreachable"
    async with gateway_factory(agent) as gw:
        run = await wait_for(gw.client, (await post_run(gw.client)).json()["id"], "failed")
    assert run["termination_reason"] == "provider_error" and "unavailable" in run["error"]
    assert run["finished_at"] is not None


@pytest.mark.parametrize(
    ("mode", "fragment"),
    [
        ("no_finish", "before run.finished"),
        ("error_midway", "unavailable"),
        ("wrong_run", "different run"),
    ],
)
async def test_broken_agent_streams_end_in_failed_runs(gateway_factory, mode, fragment) -> None:
    agent = FakeAgent()
    agent.mode = mode
    async with gateway_factory(agent) as gw:
        run = await wait_for(gw.client, (await post_run(gw.client)).json()["id"], "failed")
    assert run["termination_reason"] == "provider_error" and fragment in run["error"]


async def test_partial_spend_before_a_failure_still_counts(gateway_factory) -> None:
    """A run that dies after spending money must still book that cost (the daily cap depends on it)."""
    agent = FakeAgent()
    agent.mode = "error_midway"
    agent.cost = 0.10
    async with gateway_factory(agent) as gw:
        run = await wait_for(gw.client, (await post_run(gw.client)).json()["id"], "failed")
    assert run["totals"]["cost_usd"] == pytest.approx(0.10)  # the two llm_call spans: 0.05 + 0.05
    assert run["totals"]["step_count"] == 2 and run["totals"]["tool_call_count"] == 1
    # tokens come from the llm_call spans only; the root span repeats the totals and must not be double counted
    assert (
        run["totals"]["input_tokens"],
        run["totals"]["output_tokens"],
        run["totals"]["cache_read_tokens"],
    ) == (210, 40, 10)


async def test_http_agent_client_maps_http_errors_and_bad_lines() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/agent/run" and request.headers.get("x-mode") == "422":
            return httpx.Response(422, json={"detail": "no price configured"})
        return httpx.Response(200, content=b"this is not json\n")

    from agentlens_gateway.agent_client import AgentError

    for mode, fragment in [("422", "HTTP 422"), ("bad", "invalid event")]:
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"x-mode": mode})
        client = HttpAgentClient("http://agent", http)
        with pytest.raises(AgentError, match=fragment):
            async for _ in client.stream(run_id=uuid4(), question="q", config={}):
                pass
        await client.aclose()


async def test_http_agent_client_parses_a_real_ndjson_stream() -> None:
    run_id = uuid4()
    started, spans, finished = trace_events(run_id)
    body = "".join(e.model_dump_json() + "\n" for e in [started, *spans, finished]).encode()
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))
    )
    got = [
        e
        async for e in HttpAgentClient("http://agent", http).stream(
            run_id=run_id, question="q", config={}
        )
    ]
    assert [e.kind for e in got] == ["run.started", *["span.finished"] * 4, "run.finished"]


async def test_timeout_ends_the_run_and_keeps_partial_cost(gateway_factory) -> None:
    agent = FakeAgent()
    agent.mode = "block_forever"
    async with gateway_factory(agent, run_timeout_s=0.3) as gw:
        run = await wait_for(gw.client, (await post_run(gw.client)).json()["id"], "failed")
    assert run["termination_reason"] == "timeout" and "0.3s" in run["error"]
    assert run["totals"]["cost_usd"] == pytest.approx(0.01)  # the one llm span that did complete


# ------------------------------------------------------------------ cancel / concurrency


async def test_cancel_a_running_run(gateway_factory) -> None:
    agent = FakeAgent()
    agent.mode = "block"
    async with gateway_factory(agent) as gw:
        rid = (await post_run(gw.client)).json()["id"]
        await asyncio.wait_for(agent.first_span_sent.wait(), 5)
        assert (await gw.client.get(f"{API}/runs/{rid}")).json()["status"] == "running"
        resp = await gw.client.delete(f"{API}/runs/{rid}")
        assert resp.status_code == 202
        run = await wait_for(gw.client, rid, "cancelled")
        assert run["termination_reason"] == "cancelled" and run["finished_at"]
        assert run["totals"]["cost_usd"] == pytest.approx(0.01)
        again = await gw.client.delete(f"{API}/runs/{rid}")
        assert again.status_code == 409
        spans = (await gw.client.get(f"{API}/runs/{rid}", params={"include": "spans"})).json()[
            "spans"
        ]
        assert [x["seq"] for x in spans] == [1]  # only what happened before the cancel is stored
    assert not agent.release.is_set()  # it was stopped, not allowed to run to completion


async def test_cancel_unknown_run_is_404(gw) -> None:
    assert (await gw.client.delete(f"{API}/runs/{uuid4()}")).status_code == 404


async def test_concurrency_limit_queues_the_rest_and_cancel_works_while_queued(
    gateway_factory,
) -> None:
    agent = FakeAgent()
    agent.mode = "block"
    async with gateway_factory(agent, max_concurrent_runs=1) as gw:
        a = (await post_run(gw.client, "A")).json()["id"]
        await asyncio.wait_for(agent.first_span_sent.wait(), 5)
        b = (await post_run(gw.client, "B")).json()["id"]
        c = (await post_run(gw.client, "C")).json()["id"]
        await asyncio.sleep(0.2)
        assert (await gw.client.get(f"{API}/runs/{b}")).json()[
            "status"
        ] == "queued"  # waiting for a slot
        assert len(agent.runs) == 1

        assert (
            await gw.client.delete(f"{API}/runs/{b}")
        ).status_code == 202  # cancel while still queued
        cancelled = await wait_for(gw.client, b, "cancelled")
        assert cancelled["started_at"] is None  # it never ran

        agent.release.set()
        assert (await wait_for(gw.client, a, "completed"))["termination_reason"] == "answered"
        assert (await wait_for(gw.client, c, "completed"))["termination_reason"] == "answered"
        assert b not in [
            str(r) for r in agent.runs
        ]  # the cancelled run was never sent to the agent


# ------------------------------------------------------------------ limits


async def test_rate_limit_on_run_creation_returns_429_with_retry_after(gateway_factory) -> None:
    async with gateway_factory(default_rate_limit_per_min=2) as gw:
        assert (await post_run(gw.client)).status_code == 202
        assert (await post_run(gw.client)).status_code == 202
        r = await post_run(gw.client)
        assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
        assert r.headers["content-type"].startswith("application/problem+json")
        assert (await gw.client.get(f"{API}/runs")).status_code == 200  # reads are not rate limited


async def test_daily_cost_cap_blocks_new_runs_until_utc_midnight(gateway_factory) -> None:
    agent = FakeAgent()
    agent.cost = 0.03
    async with gateway_factory(agent, daily_cost_cap_usd=0.05) as gw:
        for _ in range(2):  # 0.03 + 0.03 = 0.06 >= 0.05
            rid = (await post_run(gw.client)).json()["id"]
            await wait_for(gw.client, rid, "completed")
        r = await post_run(gw.client)
        assert r.status_code == 429 and "cost cap" in r.json()["detail"]
        assert 1 <= int(r.headers["retry-after"]) <= 86_400
        ov = (await gw.client.get(f"{API}/stats/overview")).json()
        assert (
            ov["runs_today"] == 2
            and ov["cost_today_usd"] == pytest.approx(0.06)
            and ov["daily_cap_usd"] == 0.05
        )


async def test_in_flight_budgets_are_reserved_against_the_cap(gateway_factory) -> None:
    """N concurrent runs must not each pass the cap check just because none has finished (and booked cost) yet."""
    agent = FakeAgent()
    agent.mode = "block"
    cfg = {**INLINE_CONFIG, "max_cost_usd": 0.04}
    async with gateway_factory(agent, daily_cost_cap_usd=0.05) as gw:
        assert (
            await gw.client.post(f"{API}/runs", json={"question": "q", "config": cfg})
        ).status_code == 202  # reserved 0.04
        assert (
            await gw.client.post(f"{API}/runs", json={"question": "q", "config": cfg})
        ).status_code == 202  # 0.04 < 0.05
        third = await gw.client.post(
            f"{API}/runs", json={"question": "q", "config": cfg}
        )  # reserved 0.08 >= 0.05
        assert third.status_code == 429
        agent.release.set()


# ------------------------------------------------------------------ restart / recovery


async def test_startup_recovery_fails_runs_left_in_flight(gateway_factory, sql) -> None:
    async with gateway_factory() as gw:
        cfg = (await gw.client.post(f"{API}/configs", json=INLINE_CONFIG)).json()["id"]
    ghost_q, ghost_r = str(uuid4()), str(uuid4())
    for rid, status in ((ghost_q, "queued"), (ghost_r, "running")):
        sql("INSERT INTO runs (id, config_id, question, status, started_at) VALUES (%s,%s,'q',%s, "
            "CASE WHEN %s = 'running' THEN now() END)", (rid, cfg, status, status))  # fmt: skip
    async with gateway_factory() as gw:  # a fresh process starts
        for rid in (ghost_q, ghost_r):
            run = (await gw.client.get(f"{API}/runs/{rid}")).json()
            assert run["status"] == "failed" and run["termination_reason"] == "gateway_restart"
            assert run["finished_at"] is not None


async def test_graceful_shutdown_ends_in_flight_runs_as_gateway_restart(
    gateway_factory, sql
) -> None:
    agent = FakeAgent()
    agent.mode = "block"
    async with gateway_factory(agent) as gw:
        rid = (await post_run(gw.client)).json()["id"]
        await asyncio.wait_for(agent.first_span_sent.wait(), 5)
    row = sql("SELECT status, termination_reason, cost_usd FROM runs WHERE id = %s", (rid,))[0]
    assert row["status"] == "failed" and row["termination_reason"] == "gateway_restart"
    assert float(row["cost_usd"]) == pytest.approx(0.01)
    assert agent.closed


# ------------------------------------------------------------------ persistence details


async def test_span_upsert_is_idempotent_and_survives_duplicate_seq(gw, sql) -> None:
    run = (await post_run(gw.client)).json()
    await wait_for(gw.client, run["id"], "completed")
    store: Store = gw.app.state.store
    _, spans, _ = trace_events(UUID(run["id"]))
    before = sql("SELECT count(*) AS n FROM spans WHERE run_id = %s", (run["id"],))[0]["n"]
    existing = (await store.list_spans(UUID(run["id"])))[0]
    replay = SpanFinished.model_validate(
        {
            **spans[0].model_dump(),
            "span_id": existing["id"],
            "seq": existing["seq"],
            "name": "changed",
        }
    )
    await store.upsert_span(replay)  # same id: updates in place
    clash = spans[1].model_copy(
        update={"seq": existing["seq"]}
    )  # different id, same (run, seq): agent bug
    await store.upsert_span(clash)  # must be tolerated, not raise
    after = sql(
        "SELECT count(*) AS n, bool_or(name = 'changed') AS changed FROM spans WHERE run_id = %s",
        (run["id"],),
    )[0]
    assert after["n"] == before and after["changed"]


async def test_first_terminal_write_wins(gw) -> None:
    """If the run was cancelled, a late run.finished from the agent must not resurrect it (and vice versa)."""
    from uuid import UUID

    store: Store = gw.app.state.store
    run = (await post_run(gw.client)).json()
    done = await wait_for(gw.client, run["id"], "completed")
    _, _, finished = trace_events(UUID(run["id"]), cost=9.99)
    assert await store.finalize_run(finished) is False  # already terminal: ignored
    assert (
        await store.fail_run(UUID(run["id"]), status="cancelled", reason="cancelled", error=None)
        is None
    )
    again = (await gw.client.get(f"{API}/runs/{run['id']}")).json()
    assert (
        again["status"] == "completed"
        and again["totals"]["cost_usd"] == done["totals"]["cost_usd"] == 0.02
    )
