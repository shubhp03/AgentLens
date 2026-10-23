"""Opaque keyset-pagination cursor over (created_at, id). Microsecond-exact (integer maths, no float drift)."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from uuid import UUID

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def encode(created_at: datetime, ident: UUID) -> str:
    micros = (created_at.astimezone(UTC) - _EPOCH) // timedelta(microseconds=1)
    return base64.urlsafe_b64encode(f"{micros}|{ident}".encode()).decode().rstrip("=")


def decode(token: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
        micros, ident = raw.split("|", 1)
        return _EPOCH + timedelta(microseconds=int(micros)), UUID(ident)
    except Exception as e:  # any malformed token is a client error, never a 500
        raise ValueError("invalid cursor") from e
