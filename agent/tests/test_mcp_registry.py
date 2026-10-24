"""McpToolRegistry against real MCP servers (in-process and stdio subprocess)."""

from __future__ import annotations

import pytest
from agentlens_agent.mcp_registry import FORWARDED_ENV, McpToolRegistry, _target
from mcp.server.mcpserver import MCPServer


def make_server(name: str = "demo") -> MCPServer:
    server = MCPServer(name)

    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    @server.tool()
    def boom() -> str:
        """Always fails."""
        raise RuntimeError("kaput")

    return server


async def test_specs_and_call_in_process() -> None:
    reg = McpToolRegistry({"demo": make_server()})
    await reg.connect()
    try:
        assert {s.name for s in reg.specs(["add", "boom"])} == {"add", "boom"}
        assert [s.name for s in reg.specs(["add"])] == ["add"]  # allowlist filters
        out = await reg.call("add", {"a": 2, "b": 3})
        assert not out.is_error and "5" in out.content
    finally:
        await reg.aclose()


async def test_tool_exception_becomes_error_output() -> None:
    reg = McpToolRegistry({"demo": make_server()})
    await reg.connect()
    try:
        out = await reg.call("boom", {})
        assert out.is_error
    finally:
        await reg.aclose()


async def test_unknown_tool_is_error_not_raise() -> None:
    reg = McpToolRegistry({"demo": make_server()})
    await reg.connect()
    try:
        out = await reg.call("nope", {})
        assert out.is_error and "unknown" in out.content
    finally:
        await reg.aclose()


async def test_duplicate_tool_names_rejected() -> None:
    reg = McpToolRegistry({"a": make_server("a"), "b": make_server("b")})
    with pytest.raises(ValueError, match="duplicate tool name"):
        await reg.connect()


async def test_stdio_calc_server_end_to_end() -> None:
    """Spawns the real calc-mcp console script over stdio — same path docker-compose exercises over HTTP."""
    reg = McpToolRegistry({"calc": "stdio:calc-mcp"})
    await reg.connect()
    try:
        assert [t.name for t in reg.specs(["calculate"])] == ["calculate"]
        out = await reg.call("calculate", {"expression": "2 + 3 * 4"})
        assert not out.is_error and "14" in out.content
    finally:
        await reg.aclose()


async def test_reconnects_after_session_dies() -> None:
    reg = McpToolRegistry({"calc": "stdio:calc-mcp"})
    await reg.connect()
    try:
        await reg._close("calc")  # simulate the transport dying
        out = await reg.call("calculate", {"expression": "1+1"})
        assert not out.is_error and "2" in out.content
    finally:
        await reg.aclose()


def test_stdio_target_forwards_tool_server_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: the SDK does not pass our environment to stdio servers; SEC_USER_AGENT must be forwarded."""
    monkeypatch.setenv("SEC_USER_AGENT", "Jane Doe jane@example.com")
    for k in FORWARDED_ENV:
        if k != "SEC_USER_AGENT":
            monkeypatch.delenv(k, raising=False)
    params = _target("stdio:edgar-mcp --flag")
    assert params.command == "edgar-mcp" and params.args == ["--flag"]
    assert params.env == {"SEC_USER_AGENT": "Jane Doe jane@example.com"}
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:3128")
    monkeypatch.setenv("SSL_CERT_FILE", "/etc/ca.pem")
    assert _target("stdio:edgar-mcp").env == {
        "SEC_USER_AGENT": "Jane Doe jane@example.com",
        "HTTPS_PROXY": "http://proxy:3128",
        "SSL_CERT_FILE": "/etc/ca.pem",
    }
    for k in ("SEC_USER_AGENT", "HTTPS_PROXY", "SSL_CERT_FILE"):
        monkeypatch.delenv(k)
    assert _target("stdio:calc-mcp").env is None
    assert _target("http://x:1/mcp") == "http://x:1/mcp"
