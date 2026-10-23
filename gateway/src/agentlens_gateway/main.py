"""ASGI entry point: `uvicorn agentlens_gateway.main:app`. Configuration comes from the environment."""

from __future__ import annotations

import logging

from agentlens_gateway.app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
