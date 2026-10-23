"""Agent service HTTP surface: POST /v1/agent/run streams NDJSON events."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agentlens_agent.config import AgentConfig
from agentlens_agent.cost import MissingPriceError
from agentlens_agent.events import to_wire
from agentlens_agent.mcp_registry import McpToolRegistry
from agentlens_agent.wiring import build_runner, mcp_servers_from_env


class RunRequest(BaseModel):
    run_id: UUID
    question: str
    config: AgentConfig


@asynccontextmanager
async def lifespan(app: FastAPI):
    registry = McpToolRegistry(mcp_servers_from_env())
    await registry.connect()  # fail fast: an agent without tools is a broken deployment
    app.state.registry = registry
    try:
        yield
    finally:
        await registry.aclose()


app = FastAPI(title="agentlens-agent", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/agent/run")
async def run(req: RunRequest) -> StreamingResponse:
    try:
        runner = build_runner(req.config, req.run_id, app.state.registry)
    except MissingPriceError as e:  # config error: reject before spending anything
        raise HTTPException(422, str(e)) from e
    except (
        FileNotFoundError
    ) as e:  # pricing.yaml / prompt file absent: say so instead of a bare 500
        raise HTTPException(503, f"agent misconfigured, file not found: {e.filename}") from e
    except ValueError as e:  # e.g. unsupported provider, zero/empty price in pricing.yaml
        raise HTTPException(422, str(e)) from e

    async def stream() -> AsyncIterator[str]:
        async for ev in runner.run(req.question):
            yield to_wire(ev)

    return StreamingResponse(stream(), media_type="application/x-ndjson")
