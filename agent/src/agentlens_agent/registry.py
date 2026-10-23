"""Tool registry abstraction. Production impl = MCP-backed (see mcp_registry.py)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from agentlens_agent.llm.base import ToolSpec


@dataclass(frozen=True)
class ToolOutput:
    content: str
    is_error: bool = False


class ToolRegistry(Protocol):
    def specs(self, allowlist: Sequence[str]) -> list[ToolSpec]:
        """Specs for the allowlisted tools only (submit_answer is added by the loop)."""
        ...

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutput:
        """Execute a tool. Must not raise for tool-level failures: return is_error=True."""
        ...


class StaticToolRegistry:
    """In-memory registry for tests: name -> (spec, async handler returning str)."""

    def __init__(
        self, tools: dict[str, tuple[ToolSpec, Callable[[dict[str, Any]], Awaitable[str]]]]
    ):
        self._tools = tools

    def specs(self, allowlist: Sequence[str]) -> list[ToolSpec]:
        return [spec for name, (spec, _) in self._tools.items() if name in allowlist]

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutput:
        if name not in self._tools:
            return ToolOutput(f"unknown tool: {name}", is_error=True)
        try:
            return ToolOutput(await self._tools[name][1](arguments))
        except Exception as e:  # tool failures are data for the model, not crashes
            return ToolOutput(f"{type(e).__name__}: {e}", is_error=True)
