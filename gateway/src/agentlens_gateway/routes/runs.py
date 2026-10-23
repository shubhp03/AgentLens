"""Runs: create, list, get (+trace), cancel, and the live SSE stream."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from agentlens_gateway import costcap
from agentlens_gateway.bus import CLOSED, LAGGED
from agentlens_gateway.problems import ProblemError, not_found
from agentlens_gateway.schemas import RunCreate
from agentlens_gateway.security import Principal, authenticate
from agentlens_gateway.store import (
    TERMINAL,
    run_finished_event,
    run_out,
    span_finished_event,
    span_out,
)

router = APIRouter(dependencies=[Depends(authenticate)])
KEEPALIVE_S = 15.0

AGENT_CONFIG_FIELDS = (
    "name", "provider", "model", "system_prompt_id", "temperature", "max_steps", "max_cost_usd",
    "max_output_tokens", "tool_allowlist", "params",
)  # fmt: skip


@router.post("/runs", status_code=202)
async def create_run(
    body: RunCreate, request: Request, principal: Annotated[Principal, Depends(authenticate)]
):
    st = request.app.state
    rl = st.limiter.try_acquire(str(principal.key_id), principal.rate_limit_per_min)
    if not rl.allowed:
        raise ProblemError(
            429,
            "Too Many Requests",
            "per-key rate limit exceeded",
            {"Retry-After": str(rl.retry_after_s)},
        )

    if body.config_id is not None:
        config = await st.store.get_config(body.config_id)
        if config is None:
            raise not_found("config", body.config_id)
    else:
        assert body.config is not None
        config, _ = await st.store.create_config(body.config.model_dump())

    source = body.source or ("eval" if body.eval_run_id else "interactive")
    if body.eval_run_id is not None:
        if source != "eval":
            raise ProblemError(400, "Bad Request", "eval_run_id requires source=eval")
        if not await st.store.eval_run_exists(body.eval_run_id):
            raise not_found("eval run", body.eval_run_id)
    elif source == "eval":
        raise ProblemError(400, "Bad Request", "source=eval requires eval_run_id")

    spent, reserved = await st.store.spend_today()
    cap = costcap.check(spent, reserved, st.settings.daily_cost_cap_usd, datetime.now(UTC))
    if not cap.allowed:
        raise ProblemError(
            429,
            "Too Many Requests",
            "daily cost cap reached",
            {"Retry-After": str(cap.retry_after_s)},
        )

    run = await st.store.insert_run(
        run_id=uuid4(), config_id=config["id"], api_key_id=principal.key_id,
        eval_run_id=body.eval_run_id, source=source, question=body.question,
    )  # fmt: skip
    st.executor.submit(run["id"], body.question, {k: config[k] for k in AGENT_CONFIG_FIELDS})
    return run_out(run)


@router.get("/runs")
async def list_runs(
    request: Request,
    status: Literal["queued", "running", "completed", "failed", "cancelled"] | None = None,
    source: Literal["interactive", "eval"] | None = None,
    config_id: UUID | None = None,
    eval_run_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
):
    rows, nxt = await request.app.state.store.list_runs(
        status=status,
        source=source,
        config_id=config_id,
        eval_run_id=eval_run_id,
        limit=limit,
        cursor=cursor,
    )
    return {"items": [run_out(r) for r in rows], "next_cursor": nxt}


@router.get("/runs/{run_id}")
async def get_run(run_id: UUID, request: Request, include: Literal["spans"] | None = None):
    store = request.app.state.store
    run = await store.get_run(run_id)
    if run is None:
        raise not_found("run", run_id)
    out = run_out(run)
    if include == "spans":
        out["spans"] = [span_out(s) for s in await store.list_spans(run_id)]
    return out


@router.delete("/runs/{run_id}", status_code=202)
async def cancel_run(run_id: UUID, request: Request):
    st = request.app.state
    run = await st.store.get_run(run_id)
    if run is None:
        raise not_found("run", run_id)
    if run["status"] in TERMINAL:
        raise ProblemError(409, "Conflict", f"run is already {run['status']}")
    if not await st.executor.cancel(run_id):
        # Active in the database but not in this process (should not happen after crash recovery): end it directly.
        await st.store.fail_run(
            run_id, status="cancelled", reason="cancelled", error="cancelled by request"
        )
    return run_out(await st.store.get_run(run_id))


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


@router.get("/runs/{run_id}/events")
async def stream_run_events(run_id: UUID, request: Request):
    """Replay what is already stored, then stream live; ends after run.finished.

    Ordering matters (no lost or duplicated spans): subscribe FIRST, then read the run status, then read the spans.
    Spans are persisted before they are published, so anything missing from the replay arrives on the subscription,
    and anything that appears in both is de-duplicated by span id."""
    st = request.app.state
    if await st.store.get_run(run_id) is None:
        raise not_found("run", run_id)

    async def gen() -> AsyncIterator[str]:
        sub = st.bus.subscribe(run_id)
        try:
            run = await st.store.get_run(run_id)
            spans = await st.store.list_spans(run_id)
            seen: set[str] = set()
            for s in spans:
                seen.add(str(s["id"]))
                yield _sse(span_finished_event(s))
            if run is None:
                return
            if run["status"] in TERMINAL:
                yield _sse(run_finished_event(run))
                return
            while True:
                ev = await sub.get(KEEPALIVE_S)
                if ev is None:
                    yield ": keepalive\n\n"
                    current = await st.store.get_run(
                        run_id
                    )  # safety net if the run ended without us hearing
                    if (
                        current
                        and current["status"] in TERMINAL
                        and not st.executor.is_active(run_id)
                    ):
                        yield _sse(run_finished_event(current))
                        return
                    continue
                if ev is CLOSED or ev is LAGGED:
                    return
                if ev["kind"] == "span.finished":
                    if ev["span_id"] in seen:
                        continue
                    seen.add(ev["span_id"])
                yield _sse(ev)
                if ev["kind"] == "run.finished":
                    return
        finally:
            st.bus.unsubscribe(run_id, sub)

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }  # no proxy buffering of the stream
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)
