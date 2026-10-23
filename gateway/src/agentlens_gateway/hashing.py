"""Canonical JSON + config hashing. The gateway is the ONLY place `config_hash` is computed
(one implementation, one language: no cross-language canonicalisation drift)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any


def _norm(v: Any) -> Any:
    if isinstance(v, bool) or v is None or isinstance(v, int | str):
        return v
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("NaN/Infinity are not valid in a config")
        return int(v) if v.is_integer() else v  # 0.0 and 0 hash identically
    if isinstance(v, dict):
        return {str(k): _norm(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_norm(x) for x in v]
    raise TypeError(f"unsupported type in canonical JSON: {type(v).__name__}")


def canonical_json(value: Any) -> str:
    """Sorted keys, no whitespace, integral floats as ints, UTF-8 not escaped."""
    return json.dumps(_norm(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def config_hash(
    *,
    provider: str,
    model: str,
    system_prompt_id: str,
    system_prompt_sha: str,
    temperature: float,
    max_steps: int,
    max_cost_usd: float | None,
    max_output_tokens: int,
    tool_allowlist: Sequence[str],
    params: dict[str, Any],
) -> str:
    """Identity of an agent variant. `name` is a label, not identity. Numbers are rounded to what the DB column
    stores (temperature numeric(3,2), max_cost numeric(10,4)) so values that collapse in the DB collapse here too.
    The allow-list is a set: its order does not matter."""
    return sha256_hex(
        canonical_json(
            {
                "provider": provider,
                "model": model,
                "system_prompt_id": system_prompt_id,
                "system_prompt_sha": system_prompt_sha,
                "temperature": round(temperature, 2),
                "max_steps": max_steps,
                "max_cost_usd": None if max_cost_usd is None else round(max_cost_usd, 4),
                "max_output_tokens": max_output_tokens,
                "tool_allowlist": sorted(set(tool_allowlist)),
                "params": params,
            }
        )
    )
