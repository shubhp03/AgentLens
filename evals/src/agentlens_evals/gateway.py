"""Gateway mode (EV-10): the eval runner drives the agent THROUGH the gateway instead of in-process.

Flow (docs/ARCHITECTURE.md 4.2): register dataset + config -> create eval run -> per case POST /runs
{source: eval, eval_run_id}, follow the run's SSE stream, score locally with the SAME deterministic scorers as
direct mode -> POST results in batches -> complete the eval run.

The gateway executes the agent and stores every run and span, so traces are linked to the eval run for free; this module
only has to turn a gateway run back into the event list `run_one` already knows how to score. `run_one`, the scorers,
the classifier and the report are shared with direct mode, so the two modes cannot drift.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from agentlens_agent.config import AgentConfig
from agentlens_agent.events import RunFinished, from_wire
from pydantic import ValidationError

from agentlens_evals.case import EvalCase
from agentlens_evals.runner import RunRecord

API = "/api/v1"
BATCH = 200  # POST /eval-runs/{id}/results accepts 1..200 per request
MAX_RETRY_AFTER_S = (
    65  # ride out a per-minute rate limit; a daily cost cap (hours) is not waited out
)
MAX_RETRIES = 5


class GatewayError(RuntimeError):
    pass


def incomplete_gold(cases: list[EvalCase]) -> list[str]:
    """case_keys whose expectation cannot be scored yet (empty gold). Checked BEFORE any model call, because the
    scorer raises on them and the model spend would already be gone."""
    bad: list[str] = []
    for c in cases:
        e = c.expected
        if (e.kind == "numeric" and (e.value is None or e.unit is None)) or (
            e.kind == "text" and not e.must_contain
        ):
            bad.append(c.case_key)
    return bad


def _error(resp: httpx.Response) -> GatewayError:
    try:
        body = resp.json()
        detail = body.get("detail") or body.get("title") or resp.text
    except ValueError:
        detail = resp.text
    return GatewayError(
        f"{resp.request.method} {resp.request.url.path} -> {resp.status_code}: {detail}"[:500]
    )


class GatewayClient:
    def __init__(self, base_url: str, api_key: str, *, timeout_s: float = 60.0) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"X-API-Key": api_key},
            timeout=httpx.Timeout(timeout_s, read=None),  # SSE reads wait as long as the run takes
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _send(self, method: str, path: str, **kw: Any) -> httpx.Response:
        """One request; 429 is retried after Retry-After (bounded), anything else non-2xx raises."""
        for attempt in range(MAX_RETRIES + 1):
            resp = await self._http.request(method, f"{API}{path}", **kw)
            if resp.status_code == 429 and attempt < MAX_RETRIES:
                wait = int(resp.headers.get("Retry-After", "1"))
                if wait > MAX_RETRY_AFTER_S:
                    raise _error(resp)
                await asyncio.sleep(wait)
                continue
            if resp.is_success:
                return resp
            raise _error(resp)
        raise AssertionError("unreachable")

    # ---- registration --------------------------------------------------------------------
    async def register_dataset(self, path: Path, cases: list[EvalCase]) -> str:
        """Content-addressed (version = hash prefix), so re-registering an unchanged file is a no-op and an edited
        file becomes a NEW version instead of a 409."""
        sha = hashlib.sha256(await asyncio.to_thread(path.read_bytes)).hexdigest()
        body = {
            "name": path.stem,
            "version": sha[:12],
            "content_hash": sha,
            "cases": [
                c.model_dump(
                    mode="json",
                    include={
                        "case_key",
                        "question",
                        "category",
                        "difficulty",
                        "expected",
                        "required_tools",
                        "provenance",
                        "status",
                        "tags",
                    },
                )  # fmt: skip
                for c in cases
            ],
        }
        return (await self._send("POST", "/datasets", json=body)).json()["id"]

    async def register_config(self, config: AgentConfig, prompt_sha: str) -> str:
        body = config.model_dump(
            mode="json",
            include={
                "name", "provider", "model", "system_prompt_id", "temperature", "max_steps",
                "max_cost_usd", "max_output_tokens", "tool_allowlist", "params",
            },
        )  # fmt: skip
        body["system_prompt_sha"] = prompt_sha
        return (await self._send("POST", "/configs", json=body)).json()["id"]

    async def create_eval_run(
        self, dataset_id: str, config_id: str, *, label: str, git_sha: str, repeats: int
    ) -> str:
        body = {
            "dataset_id": dataset_id, "config_id": config_id, "label": label,
            "trigger": "manual", "git_sha": git_sha, "repeats": repeats,
        }  # fmt: skip
        return (await self._send("POST", "/eval-runs", json=body)).json()["id"]

    # ---- one run -------------------------------------------------------------------------
    def run_factory(self, config_id: str, eval_run_id: str):
        """question -> async stream of agent events, the shape `run_one` consumes."""

        async def factory(question: str) -> AsyncIterator[Any]:
            run = (
                await self._send(
                    "POST",
                    "/runs",
                    json={
                        "question": question,
                        "config_id": config_id,
                        "source": "eval",
                        "eval_run_id": eval_run_id,
                    },
                )  # fmt: skip
            ).json()
            run_id = UUID(run["id"])
            finished = False
            try:
                async for ev in self._events(run["id"]):
                    finished = isinstance(ev, RunFinished)
                    yield ev
            except (httpx.HTTPError, GatewayError) as e:
                # The run EXISTS in the gateway, so record it as a failed result (results need a run_id)
                # instead of losing it to the crash path.
                yield RunFinished(
                    run_id=run_id, status="failed", termination_reason="provider_error",
                    error=f"gateway stream failed: {type(e).__name__}: {e}"[:500],
                )  # fmt: skip
                return
            if not finished:
                yield RunFinished(
                    run_id=run_id, status="failed", termination_reason="provider_error",
                    error="gateway stream ended without run.finished",
                )  # fmt: skip

        return factory

    async def _events(self, run_id: str) -> AsyncIterator[Any]:
        async with self._http.stream("GET", f"{API}/runs/{run_id}/events") as resp:
            if not resp.is_success:
                await resp.aread()
                raise _error(resp)
            buf = ""
            async for chunk in resp.aiter_text():
                buf += chunk
                while "\n\n" in buf:
                    block, buf = buf.split("\n\n", 1)
                    if not block.startswith("data:"):
                        continue  # ': keepalive' comments
                    payload = block[len("data:") :].strip()
                    try:
                        yield from_wire(payload)
                    except ValidationError:
                        ev = json.loads(payload)
                        if ev.get("kind") != "run.finished":
                            raise
                        # The gateway has terminal states the agent's enum lacks (e.g. gateway_restart, timeout).
                        yield RunFinished(
                            run_id=UUID(ev["run_id"]), status="failed",
                            termination_reason="provider_error",
                            error=f"gateway ended the run: {ev.get('termination_reason')}: {ev.get('error')}"[:500],
                        )  # fmt: skip

    # ---- results -------------------------------------------------------------------------
    async def post_results(self, eval_run_id: str, records: list[RunRecord]) -> int:
        """Publish scored results. Returns how many could NOT be published (no gateway run exists for them)."""
        items = []
        for r in records:
            if r.run_id is None:
                continue
            items.append(
                {
                    "case_key": r.case_key,
                    "repeat_idx": r.repeat,
                    "run_id": r.run_id,
                    "passed": r.passed,
                    "failure_category": r.failure_category,
                    "scores": _scores(r),
                }  # fmt: skip
            )
        for i in range(0, len(items), BATCH):
            await self._send("POST", f"/eval-runs/{eval_run_id}/results", json=items[i : i + BATCH])
        return len(records) - len(items)

    async def complete(self, eval_run_id: str, status: str, gate_passed: bool | None) -> None:
        """Finalize once. A resumed eval whose run is already finalized is left alone (the gateway answers 409)."""
        current = (await self._send("GET", f"/eval-runs/{eval_run_id}")).json()
        if current["status"] != "running":
            return
        body: dict[str, Any] = {"status": status}
        if gate_passed is not None:
            body["gate_passed"] = gate_passed
        await self._send("POST", f"/eval-runs/{eval_run_id}/complete", json=body)


def _scores(r: RunRecord) -> dict[str, Any]:
    """Keys follow the eval_results.scores column comment in V1__init.sql."""
    s: dict[str, Any] = {
        "task_success": r.passed,
        "citation_validity": r.citation_validity,
        "numeric_grounded": r.numeric_grounded,
        "required_tool_recall": r.tool_recall,
        "redundant_call_rate": r.redundant_rate,
        "error_call_rate": r.error_rate,
    }
    if r.rel_error is not None:
        s["rel_error"] = r.rel_error
    return {k: v for k, v in s.items() if v is not None}
