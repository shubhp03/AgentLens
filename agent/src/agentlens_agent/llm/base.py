"""Provider-agnostic LLM interface. The agent loop depends ONLY on this module."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from agentlens_agent.events import Usage


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant", "tool"]
    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()  # assistant messages
    tool_results: tuple[ToolResult, ...] = ()  # tool messages


@dataclass(frozen=True)
class LLMResponse:
    text: str | None
    tool_calls: tuple[ToolCall, ...]
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = "end_turn"
    latency_ms: float = 0.0


class LLMError(Exception):
    """Raised by adapters. `retryable` drives the loop's retry/backoff policy."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMClient(Protocol):
    provider: str
    model: str

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse: ...
