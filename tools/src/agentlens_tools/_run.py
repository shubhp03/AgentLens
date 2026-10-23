"""Shared server runner. Transport/host/port come from env so the same code runs under stdio
(local dev, spawned by a client) and streamable HTTP (docker compose)."""

from __future__ import annotations

import os
from typing import Any


def run_server(server: Any) -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        server.run(transport="stdio")
    else:
        # NOTE (mcp 2.x): if the agent gets 403/421 over docker networking, configure
        # TransportSecuritySettings (allowed hosts): DNS-rebinding protection is host-based.
        server.run(
            transport=transport,
            host=os.environ.get("MCP_HOST", "127.0.0.1"),
            port=int(os.environ.get("MCP_PORT", "8000")),
        )
