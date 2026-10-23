"""Deterministic scripted LLM for unit tests and replay-based CI (no network, no cost)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from agentlens_agent.llm.base import LLMClient, LLMError, LLMResponse, Message, ToolSpec


@dataclass
class ScriptedLLM(LLMClient):
    """Returns the scripted items in order. An item that is an LLMError instance is raised."""

    script: list[LLMResponse | LLMError]
    provider: str = "fake"
    model: str = "scripted"
    calls: list[dict] = field(default_factory=list)

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        self.calls.append({"system": system, "messages": list(messages), "tools": list(tools)})
        if not self.script:
            raise AssertionError("ScriptedLLM exhausted: the agent made more calls than scripted")
        item = self.script.pop(0)
        if isinstance(item, LLMError):
            raise item
        return item
