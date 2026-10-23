"""MCP-backed ToolRegistry (requirement AG-05).

One `mcp.Client` session per configured server. Each server is a name -> target mapping:

    {"edgar": "http://edgar-mcp:8101/mcp", "calc": "stdio:calc-mcp"}

* `http(s)://...`  -> streamable HTTP transport (what docker-compose uses)
* `stdio:<command> [args...]` -> spawn a local subprocess (handy for dev and tests)

Behaviour
---------
* `connect()` opens every session, lists tools, and builds name -> server. Tool names must be unique
  across servers; a clash is a configuration bug and raises immediately.
* `call()` never raises for tool-level problems: MCP `is_error`, timeouts and transport failures all
  become `ToolOutput(is_error=True)` so the model can react. If the transport itself dies we
  reconnect that one server lazily and retry the call ONCE (tools are read-only, so a retry is safe).
* Sessions are owned by an AsyncExitStack per server so a dead one can be torn down and rebuilt
  without touching the others.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
from collections.abc import Sequence
from contextlib import AsyncExitStack
from typing import Any

import mcp
from mcp import StdioServerParameters

from agentlens_agent.llm.base import ToolSpec
from agentlens_agent.registry import ToolOutput

log = logging.getLogger(__name__)


# The MCP SDK starts stdio servers with a minimal environment (HOME, PATH, USER, ...), NOT a copy of ours.
# Settings the tool servers need must be forwarded explicitly, or e.g. edgar-mcp dies for lack of SEC_USER_AGENT.
# Egress settings are forwarded too: behind an HTTP(S) proxy / custom CA, the tool servers cannot reach SEC without them.
FORWARDED_ENV = (
    "SEC_USER_AGENT",
    "EDGAR_CACHE_DIR",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "https_proxy",
    "http_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
)


def _target(spec: Any) -> Any:
    """Turn a config string into something `mcp.Client` accepts."""
    if not isinstance(spec, str):  # already a Client target, e.g. an in-process MCPServer (tests)
        return spec
    if spec.startswith("stdio:"):
        command, *args = shlex.split(spec[len("stdio:") :])
        env = {k: os.environ[k] for k in FORWARDED_ENV if k in os.environ}
        return StdioServerParameters(command=command, args=args, env=env or None)
    return spec


class McpToolRegistry:
    def __init__(self, servers: dict[str, Any], call_timeout_s: float = 30.0) -> None:
        self.servers = servers
        self.call_timeout_s = call_timeout_s
        self._stacks: dict[str, AsyncExitStack] = {}
        self._clients: dict[str, mcp.Client] = {}
        self._specs: dict[str, ToolSpec] = {}
        self._owner: dict[str, str] = {}  # tool name -> server name
        self._locks: dict[str, asyncio.Lock] = {}

    # ---- lifecycle ---------------------------------------------------------------------
    async def _open(self, server: str) -> list[ToolSpec]:
        stack = AsyncExitStack()
        try:
            client = await stack.enter_async_context(mcp.Client(_target(self.servers[server])))
            listing = await client.list_tools()
        except BaseException:
            await stack.aclose()
            raise
        self._stacks[server], self._clients[server] = stack, client
        return [
            ToolSpec(name=t.name, description=t.description or "", input_schema=t.input_schema)
            for t in listing.tools
        ]

    async def _close(self, server: str) -> None:
        self._clients.pop(server, None)
        stack = self._stacks.pop(server, None)
        if stack is not None:
            try:
                await stack.aclose()
            except Exception:  # a dead transport can fail to close cleanly; nothing to do about it
                log.debug("error closing MCP server %s", server, exc_info=True)

    async def connect(self) -> None:
        try:
            for server in self.servers:
                self._locks[server] = asyncio.Lock()
                for spec in await self._open(server):
                    if spec.name in self._owner:
                        raise ValueError(
                            f"duplicate tool name {spec.name!r} on servers "
                            f"{self._owner[spec.name]!r} and {server!r}"
                        )
                    self._specs[spec.name] = spec
                    self._owner[spec.name] = server
        except BaseException:
            await self.aclose()
            raise

    async def aclose(self) -> None:
        for server in list(self._stacks):
            await self._close(server)

    # ---- ToolRegistry ------------------------------------------------------------------
    def specs(self, allowlist: Sequence[str]) -> list[ToolSpec]:
        return [s for name, s in self._specs.items() if name in allowlist]

    async def _call_once(self, server: str, name: str, arguments: dict[str, Any]) -> ToolOutput:
        client = self._clients[server]
        result = await asyncio.wait_for(
            client.call_tool(name, arguments), timeout=self.call_timeout_s
        )
        text = "\n".join(b.text for b in result.content if getattr(b, "type", None) == "text") or (
            "" if result.structured_content is None else str(result.structured_content)
        )
        return ToolOutput(text, is_error=bool(result.is_error))

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutput:
        server = self._owner.get(name)
        if server is None:
            return ToolOutput(f"unknown tool: {name}", is_error=True)
        stale = self._clients.get(server)
        try:
            if stale is None:  # a previous reconnect failed; try again
                await self._reconnect(server)
            return await self._call_once(server, name, arguments)
        except TimeoutError:
            return ToolOutput(
                f"tool {name!r} timed out after {self.call_timeout_s:.0f}s", is_error=True
            )
        except asyncio.CancelledError:
            raise
        except Exception as first:
            log.warning("MCP call %s failed (%s); reconnecting %s", name, first, server)
            try:
                await self._reconnect(server, stale)
                return await self._call_once(server, name, arguments)
            except TimeoutError:
                return ToolOutput(
                    f"tool {name!r} timed out after {self.call_timeout_s:.0f}s", is_error=True
                )
            except asyncio.CancelledError:
                raise
            except Exception as second:
                return ToolOutput(
                    f"tool server {server!r} unavailable: {type(second).__name__}: {second}",
                    is_error=True,
                )

    async def _reconnect(self, server: str, stale: mcp.Client | None = None) -> None:
        """Rebuild one server's session. `stale` lets concurrent failing callers share one reconnect."""
        async with self._locks[server]:
            current = self._clients.get(server)
            if current is not None and current is not stale:
                return  # someone else already replaced the broken client
            await self._close(server)
            await self._open(server)
