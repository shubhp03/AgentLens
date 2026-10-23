"""Client for the agent service: POST /v1/agent/run, parse the NDJSON event stream."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol
from uuid import UUID

import httpx
from agentlens_agent.events import RunFinished, RunStarted, SpanFinished, SpanStarted, from_wire

Event = RunStarted | SpanStarted | SpanFinished | RunFinished


class AgentError(Exception):
    """The agent service could not be reached, rejected the request, or broke the stream protocol."""


class AgentClient(Protocol):
    def stream(
        self, *, run_id: UUID, question: str, config: dict[str, Any]
    ) -> AsyncIterator[Event]: ...

    async def aclose(self) -> None: ...


class HttpAgentClient:
    def __init__(self, base_url: str, http: httpx.AsyncClient | None = None) -> None:
        self._base = base_url.rstrip("/")
        # connect fast, but never time out a healthy long-running stream (the executor enforces the wall-clock cap)
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(None, connect=3.0))

    async def stream(
        self, *, run_id: UUID, question: str, config: dict[str, Any]
    ) -> AsyncIterator[Event]:
        body = {"run_id": str(run_id), "question": question, "config": config}
        try:
            async with self._http.stream("POST", f"{self._base}/v1/agent/run", json=body) as r:
                if r.status_code != 200:
                    detail = (await r.aread()).decode(errors="replace")[:300]
                    raise AgentError(f"agent service returned HTTP {r.status_code}: {detail}")
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        yield from_wire(line)
                    except ValueError as e:  # pydantic ValidationError is a ValueError
                        raise AgentError(f"agent sent an invalid event: {str(e)[:200]}") from e
        except httpx.HTTPError as e:
            raise AgentError(f"agent service unavailable: {type(e).__name__}: {e}") from e

    async def aclose(self) -> None:
        await self._http.aclose()
