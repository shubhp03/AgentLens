"""Cost accounting. Prices come from config/pricing.yaml (never hardcoded: they change)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from agentlens_agent.events import Usage


class MissingPriceError(KeyError):
    pass


class ModelPrice(BaseModel):
    input_usd_per_mtok: float
    output_usd_per_mtok: float
    cache_read_usd_per_mtok: float | None = None


class PricingTable:
    def __init__(self, prices: dict[str, ModelPrice], as_of: str = "") -> None:
        self._prices = prices
        self.as_of = as_of

    @classmethod
    def from_yaml(cls, path: str | Path) -> PricingTable:
        raw = yaml.safe_load(Path(path).read_text())
        prices: dict[str, ModelPrice] = {}
        for key, val in (raw.get("models") or {}).items():
            if not val.get("input_usd_per_mtok") or not val.get("output_usd_per_mtok"):
                raise ValueError(
                    f"pricing for {key!r} is empty/zero: fill it from the pricing page"
                )
            prices[key] = ModelPrice(**val)
        return cls(prices, str(raw.get("as_of", "")))

    def cost_usd(self, provider: str, model: str, usage: Usage) -> float:
        key = f"{provider}:{model}"
        try:
            p = self._prices[key]
        except KeyError:
            raise MissingPriceError(f"no price configured for {key}") from None
        cache_rate = p.cache_read_usd_per_mtok
        if cache_rate is None:
            cache_rate = p.input_usd_per_mtok
        return (
            usage.input_tokens * p.input_usd_per_mtok
            + usage.output_tokens * p.output_usd_per_mtok
            + usage.cache_read_tokens * cache_rate
        ) / 1_000_000
