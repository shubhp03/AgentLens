"""Anthropic adapter. Translates our neutral Message/ToolSpec model to the Messages API."""

from __future__ import annotations

import inspect
import time
from collections.abc import Sequence
from typing import Any

from agentlens_agent.events import Usage
from agentlens_agent.llm.base import LLMError, LLMResponse, Message, ToolCall, ToolSpec


def to_anthropic_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "user":
            out.append({"role": "user", "content": m.text or ""})
        elif m.role == "assistant":
            blocks: list[dict[str, Any]] = []
            if m.text:
                blocks.append({"type": "text", "text": m.text})
            blocks.extend(
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                for c in m.tool_calls
            )
            out.append({"role": "assistant", "content": blocks})
        else:  # tool results travel in a user turn
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": r.tool_call_id,
                            "content": r.content,
                            "is_error": r.is_error,
                        }
                        for r in m.tool_results
                    ],
                }
            )
    return out


def parse_anthropic_response(resp: Any, latency_ms: float) -> LLMResponse:
    texts: list[str] = []
    calls: list[ToolCall] = []
    for block in resp.content:
        if block.type == "text":
            texts.append(block.text)
        elif block.type == "tool_use":
            calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))
    u = resp.usage
    usage = Usage(
        input_tokens=getattr(u, "input_tokens", 0) or 0,
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
    )
    return LLMResponse(
        text="\n".join(texts) or None,
        tool_calls=tuple(calls),
        usage=usage,
        stop_reason=str(resp.stop_reason),
        latency_ms=latency_ms,
    )


class AnthropicClient:
    provider = "anthropic"

    def __init__(self, model: str, api_key: str | None = None, client: Any | None = None) -> None:
        import anthropic

        self.model = model
        self._anthropic = anthropic
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key)

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        a = self._anthropic
        t0 = time.perf_counter()
        request: dict[str, Any] = {
            "model": self.model,
            "system": system,
            "max_tokens": max_tokens,
            "tools": [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in tools
            ],
            "messages": to_anthropic_messages(messages),
        }
        if "temperature" in inspect.signature(self._client.messages.create).parameters:
            request["temperature"] = temperature
        try:
            resp = await self._client.messages.create(**request)
        except a.APIStatusError as e:
            retryable = e.status_code in (408, 409, 429) or e.status_code >= 500
            raise LLMError(f"anthropic {e.status_code}: {e}", retryable=retryable) from e
        except a.APIConnectionError as e:
            raise LLMError(f"anthropic connection error: {e}", retryable=True) from e
        return parse_anthropic_response(resp, (time.perf_counter() - t0) * 1000)
