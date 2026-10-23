"""API-key authentication. Keys are shown once and only their SHA-256 is stored."""

from __future__ import annotations

import time
from dataclasses import dataclass
from uuid import UUID

from fastapi import Request

from agentlens_gateway.hashing import sha256_hex
from agentlens_gateway.problems import ProblemError

_TOUCH_EVERY_S = 60.0  # last_used_at is a convenience, not a write per request


@dataclass(frozen=True)
class Principal:
    key_id: UUID
    name: str
    rate_limit_per_min: int


async def authenticate(request: Request) -> Principal:
    """FastAPI dependency: every /api/v1 route requires a valid, non-revoked X-API-Key."""
    key = request.headers.get("x-api-key")
    if not key:
        raise ProblemError(
            401, "Unauthorized", "missing X-API-Key header", {"WWW-Authenticate": "ApiKey"}
        )
    state = request.app.state
    row = await state.store.find_key(sha256_hex(key))
    if row is None:
        raise ProblemError(
            401, "Unauthorized", "invalid or revoked API key", {"WWW-Authenticate": "ApiKey"}
        )
    now = time.monotonic()
    if now - state.key_touched.get(row["id"], -_TOUCH_EVERY_S) >= _TOUCH_EVERY_S:
        state.key_touched[row["id"]] = now
        await state.store.touch_key(row["id"])
    return Principal(row["id"], row["name"], row["rate_limit_per_min"])
