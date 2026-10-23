"""End-to-end smoke test of the whole backend over REAL sockets:

    HTTP client -> gateway (uvicorn) -> agent service (uvicorn) -> real calc-mcp subprocess (MCP over stdio)
                        |
                    real PostgreSQL (embedded via pgserver, or DATABASE_URL)

The ONLY fake is the LLM: a scripted model that calls `calculate` and then `submit_answer`, so no API key and no
spend are needed. Everything else (HTTP, NDJSON streaming, MCP, SQL, SSE, the eval + compare endpoints) is the real
code path. Run:  uv run python scripts/stack_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import httpx
import uvicorn

KEY = "smoke-test-admin-key-0123456789"
REPO = Path(__file__).resolve().parents[1]


def scripted_llm():
    from agentlens_agent.events import Usage
    from agentlens_agent.llm.base import LLMResponse, ToolCall
    from agentlens_agent.llm.fake import ScriptedLLM

    use = Usage(input_tokens=1000, output_tokens=100)
    return ScriptedLLM(
        [
            LLMResponse(
                text="Let me compute that.",
                tool_calls=(ToolCall("t1", "calculate", {"expression": "6*7"}),),
                usage=use,
            ),
            LLMResponse(
                text=None,
                tool_calls=(
                    ToolCall(
                        "t2",
                        "submit_answer",
                        {"value": 42, "unit": "count", "text": "6*7 = 42", "period": None},
                    ),
                ),
                usage=use,
            ),
        ],
        provider="fake",
        model="scripted",
    )


async def serve(app, port: int = 0) -> tuple[uvicorn.Server, asyncio.Task, int]:
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    task = asyncio.create_task(server.serve())
    for _ in range(300):
        if server.started:
            return server, task, server.servers[0].sockets[0].getsockname()[1]
        await asyncio.sleep(0.05)
    raise RuntimeError("server did not start")


async def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="agentlens-smoke-"))
    (work / "pricing.yaml").write_text(
        'as_of: "2026-01-01"\nmodels:\n  "fake:scripted":\n    input_usd_per_mtok: 3\n    output_usd_per_mtok: 15\n'
    )
    os.environ["PRICING_FILE"] = str(work / "pricing.yaml")
    os.environ["MCP_SERVERS"] = json.dumps({"calc": "stdio:calc-mcp"})

    if "DATABASE_URL" in os.environ:
        db_url = os.environ["DATABASE_URL"]
    else:
        import pgserver
        from psycopg.conninfo import make_conninfo

        pg = pgserver.get_server(work / "pg")
        db_url = make_conninfo(pg.get_uri(), dbname="postgres")

    from agentlens_agent import main as agent_main
    from agentlens_agent import wiring
    from agentlens_gateway.app import create_app
    from agentlens_gateway.settings import Settings

    wiring.build_llm = lambda cfg: scripted_llm()  # the one fake: no API key, no spend
    a_server, a_task, a_port = await serve(agent_main.app)
    g_app = create_app(
        Settings(
            database_url=db_url, admin_api_key=KEY, agent_base_url=f"http://127.0.0.1:{a_port}"
        )
    )
    g_server, g_task, g_port = await serve(g_app)
    base = f"http://127.0.0.1:{g_port}"
    print(f"agent :{a_port}  gateway :{g_port}  db: {db_url.split('@')[-1][:40]}")

    failures: list[str] = []

    def check(label: str, ok: bool, detail: object = "") -> None:
        print(
            ("  ok   " if ok else "  FAIL ")
            + label
            + (f"  [{detail}]" if not ok and detail != "" else "")
        )
        if not ok:
            failures.append(label)

    cfg = {
        "name": "smoke", "provider": "fake", "model": "scripted", "system_prompt_id": "system_v1",
        "system_prompt_sha": "x", "tool_allowlist": ["calculate"], "max_cost_usd": 0.5,
    }  # fmt: skip
    try:
        async with httpx.AsyncClient(base_url=base, headers={"X-API-Key": KEY}, timeout=30) as c:
            check("healthz", (await c.get("/healthz")).json() == {"status": "UP"})
            check(
                "401 without key",
                (await httpx.AsyncClient(base_url=base).get("/api/v1/runs")).status_code == 401,
            )

            r = await c.post("/api/v1/runs", json={"question": "What is 6*7?", "config": cfg})
            check(
                "POST /runs -> 202 queued",
                r.status_code == 202 and r.json()["status"] == "queued",
                r.text,
            )
            rid = r.json()["id"]

            events: list[dict] = []
            async with c.stream("GET", f"/api/v1/runs/{rid}/events") as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        events.append(json.loads(line[5:]))
            kinds = [e["kind"] for e in events]
            check("SSE ends with run.finished", kinds[-1] == "run.finished", kinds)

            run = (await c.get(f"/api/v1/runs/{rid}", params={"include": "spans"})).json()
            check(
                "run completed/answered",
                run["status"] == "completed" and run["termination_reason"] == "answered",
                run,
            )
            check("final answer 42", run["final_answer"]["value"] == 42, run["final_answer"])
            types = [s["type"] for s in run["spans"]]
            check(
                "trace = llm, tool, llm, agent(root last)",
                types == ["llm_call", "tool_call", "llm_call", "agent"],
                types,
            )
            tool = next(s for s in run["spans"] if s["type"] == "tool_call")
            check(
                "REAL MCP calc-mcp executed the tool",
                json.loads(tool["output"]["content"])["result"] == 42,
                tool["output"],
            )
            expected_cost = (2 * 1000 * 3 + 2 * 100 * 15) / 1_000_000
            check(
                "cost priced from pricing table",
                abs(run["totals"]["cost_usd"] - expected_cost) < 1e-9,
                run["totals"],
            )
            check(
                "totals: 2 steps, 1 tool call",
                (run["totals"]["step_count"], run["totals"]["tool_call_count"]) == (2, 1),
            )

            # eval flow on top of real runs
            body = {"name": "smoke", "version": "v1", "content_hash": "h", "cases": [
                {"case_key": "smoke-1", "question": "q", "category": "calc", "difficulty": "easy",
                 "expected": {"kind": "numeric", "value": 42, "unit": "count"}},
                {"case_key": "smoke-2", "question": "q", "category": "lookup", "difficulty": "easy",
                 "expected": {"kind": "numeric", "value": 1, "unit": "count"}}]}  # fmt: skip
            ds = (await c.post("/api/v1/datasets", json=body)).json()
            cid = (await c.post("/api/v1/configs", json=cfg)).json()["id"]
            ers = []
            for outcomes in ((True, True), (True, False)):
                er = (
                    await c.post(
                        "/api/v1/eval-runs", json={"dataset_id": ds["id"], "config_id": cid}
                    )
                ).json()
                res = [{"case_key": k, "run_id": rid, "passed": p, "failure_category": None if p else "retrieval_miss"}
                       for k, p in zip(("smoke-1", "smoke-2"), outcomes, strict=True)]  # fmt: skip
                assert (
                    await c.post(f"/api/v1/eval-runs/{er['id']}/results", json=res)
                ).status_code == 204
                ers.append(er["id"])
            cmp = (
                await c.get(
                    "/api/v1/eval-runs/compare", params={"base": ers[0], "candidate": ers[1]}
                )
            ).json()
            check(
                "compare: 1 regressed case, delta -0.5",
                cmp["base_only"] == 1 and cmp["delta"] == -0.5,
                cmp,
            )
            ov = (await c.get("/api/v1/stats/overview")).json()
            check(
                "overview counts the run + its spend",
                ov["runs_today"] == 1 and ov["cost_today_usd"] > 0,
                ov,
            )
    finally:
        g_server.should_exit = a_server.should_exit = True
        await asyncio.gather(g_task, a_task, return_exceptions=True)

    print("\nSTACK SMOKE " + ("FAILED: " + ", ".join(failures) if failures else "PASSED"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
