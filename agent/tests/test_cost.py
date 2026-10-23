import pytest
from agentlens_agent.cost import MissingPriceError, ModelPrice, PricingTable
from agentlens_agent.events import Usage


def table():
    return PricingTable(
        {
            "p:m": ModelPrice(
                input_usd_per_mtok=3.0, output_usd_per_mtok=15.0, cache_read_usd_per_mtok=0.3
            )
        }
    )


def test_cost_math():
    u = Usage(input_tokens=1_000_000, output_tokens=100_000, cache_read_tokens=2_000_000)
    assert table().cost_usd("p", "m", u) == pytest.approx(3.0 + 1.5 + 0.6)


def test_missing_price_is_loud():
    with pytest.raises(MissingPriceError):
        table().cost_usd("p", "other", Usage())


def test_yaml_rejects_unfilled_prices(tmp_path):
    f = tmp_path / "p.yaml"
    f.write_text(
        'as_of: "2026-01-01"\nmodels:\n  "p:m": {input_usd_per_mtok: null, output_usd_per_mtok: null}\n'
    )
    with pytest.raises(ValueError):
        PricingTable.from_yaml(f)
