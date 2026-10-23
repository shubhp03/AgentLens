"""Pure-logic and migration-runner tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from agentlens_gateway import costcap, cursor
from agentlens_gateway.db import discover, migrate, open_pool
from agentlens_gateway.hashing import canonical_json, config_hash, sha256_hex
from agentlens_gateway.ratelimit import RateLimiter
from agentlens_gateway.settings import DEFAULT_MIGRATIONS_DIR, Settings

# ------------------------------------------------------------------ hashing


def test_canonical_json_sorts_keys_and_normalises_numbers() -> None:
    assert canonical_json({"b": 1, "a": [True, None, "x"]}) == '{"a":[true,null,"x"],"b":1}'
    assert canonical_json({"t": 0.0}) == canonical_json({"t": 0}) == '{"t":0}'
    assert canonical_json({"t": 0.5}) == '{"t":0.5}'
    assert canonical_json("é\n") == '"é\\n"'  # UTF-8 kept, control chars escaped
    with pytest.raises(TypeError):
        canonical_json({"x": object()})
    with pytest.raises(ValueError):
        canonical_json(float("nan"))


def test_sha256_known_answer() -> None:
    assert sha256_hex("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


BASE = dict(provider="p", model="m", system_prompt_id="s", system_prompt_sha="x", temperature=0.0, max_steps=12,
            max_cost_usd=None, max_output_tokens=2048, tool_allowlist=["a", "b"], params={"k": 1, "j": 2})  # fmt: skip


def test_config_hash_ignores_order_and_number_format() -> None:
    h = config_hash(**BASE)
    assert len(h) == 64
    assert h == config_hash(
        **{**BASE, "tool_allowlist": ["b", "a", "a"], "params": {"j": 2, "k": 1}}
    )
    assert h == config_hash(**{**BASE, "temperature": 0, "max_cost_usd": None})
    # values the DB column would collapse (numeric(3,2), numeric(10,4)) collapse in the hash too
    assert config_hash(**{**BASE, "temperature": 0.501}) == config_hash(
        **{**BASE, "temperature": 0.5}
    )
    assert config_hash(**{**BASE, "max_cost_usd": 0.50001}) == config_hash(
        **{**BASE, "max_cost_usd": 0.5}
    )


@pytest.mark.parametrize(
    "change",
    [{"provider": "q"}, {"model": "n"}, {"system_prompt_id": "t"}, {"system_prompt_sha": "y"}, {"temperature": 0.2},
     {"max_steps": 13}, {"max_cost_usd": 1.0}, {"max_output_tokens": 1024}, {"tool_allowlist": ["a"]}, {"params": {}}],
)  # fmt: skip
def test_config_hash_changes_with_every_identity_field(change: dict) -> None:
    assert config_hash(**BASE) != config_hash(**{**BASE, **change})


# ------------------------------------------------------------------ cursor


def test_cursor_round_trip_is_microsecond_exact() -> None:
    at, ident = datetime(2026, 10, 18, 22, 1, 2, 123456, tzinfo=UTC), uuid4()
    token = cursor.encode(at, ident)
    assert token == token.replace("=", "") and token.isascii()
    assert cursor.decode(token) == (at, ident)


@pytest.mark.parametrize("bad", ["", "not-a-cursor", "YWJj", "!!!"])
def test_cursor_rejects_garbage(bad: str) -> None:
    with pytest.raises(ValueError):
        cursor.decode(bad)


# ------------------------------------------------------------------ rate limiter


def test_rate_limiter_burst_then_block_then_refill() -> None:
    now = [0.0]
    rl = RateLimiter(clock=lambda: now[0])
    assert all(rl.try_acquire("k", 3).allowed for _ in range(3))
    d = rl.try_acquire("k", 3)
    assert not d.allowed and d.retry_after_s == 20  # 3/min = one token per 20 s
    now[0] += 20
    assert rl.try_acquire("k", 3).allowed
    assert not rl.try_acquire("k", 3).allowed


def test_rate_limiter_keys_are_independent_and_idle_time_is_capped() -> None:
    now = [0.0]
    rl = RateLimiter(clock=lambda: now[0])
    assert rl.try_acquire("a", 1).allowed and not rl.try_acquire("a", 1).allowed
    assert rl.try_acquire("b", 1).allowed
    now[0] += 3600  # an idle hour must not bank an hour of tokens
    assert rl.try_acquire("a", 1).allowed and not rl.try_acquire("a", 1).allowed


# ------------------------------------------------------------------ cost cap


def test_cost_cap_allows_under_and_blocks_at_cap_counting_reservations() -> None:
    now = datetime(2026, 10, 18, 23, 0, tzinfo=UTC)
    assert costcap.check(4.0, 0.5, 5.0, now).allowed
    assert not costcap.check(4.6, 0.4, 5.0, now).allowed  # in-flight budgets count
    d = costcap.check(5.0, 0.0, 5.0, now)
    assert not d.allowed and d.retry_after_s == 3600  # one hour to UTC midnight


# ------------------------------------------------------------------ settings


def test_settings_from_env_and_validation() -> None:
    s = Settings.from_env(
        {"ADMIN_API_KEY": "k" * 20, "ALLOWED_ORIGINS": "https://a.example, https://b.example",
         "DAILY_COST_CAP_USD": "2.5", "AGENT_BASE_URL": "http://agent:8200/", "AUTO_MIGRATE": "false"}
    )  # fmt: skip
    s.validate()
    assert s.allowed_origins == ("https://a.example", "https://b.example")
    assert (
        s.daily_cost_cap_usd == 2.5
        and s.agent_base_url == "http://agent:8200"
        and not s.auto_migrate
    )
    for bad in ({"ADMIN_API_KEY": "short"}, {"ADMIN_API_KEY": "k" * 20, "DAILY_COST_CAP_USD": "0"},
                {"ADMIN_API_KEY": "k" * 20, "ALLOWED_ORIGINS": "*"}):  # fmt: skip
        with pytest.raises(ValueError):
            Settings.from_env(bad).validate()


# ------------------------------------------------------------------ migrations


def test_migrations_discovered_in_numeric_order() -> None:
    assert [v for v, _, _ in discover(DEFAULT_MIGRATIONS_DIR)][:2] == [1, 2]


async def test_migrate_is_idempotent_and_detects_edited_migrations(
    blank_db_url: str, tmp_path: Path
) -> None:
    (tmp_path / "V1__a.sql").write_text("CREATE TABLE t1 (id int);")
    (tmp_path / "V2__b.sql").write_text("CREATE TABLE t2 (id int);")
    pool = await open_pool(blank_db_url, 2)
    try:
        assert await migrate(pool, tmp_path) == [1, 2]
        assert await migrate(pool, tmp_path) == []  # second start applies nothing
        (tmp_path / "V10__c.sql").write_text("CREATE TABLE t10 (id int);")
        assert await migrate(pool, tmp_path) == [10]
        (tmp_path / "V1__a.sql").write_text(
            "CREATE TABLE t1 (id int, extra int);"
        )  # editing an applied file
        with pytest.raises(RuntimeError, match="modified after it was applied"):
            await migrate(pool, tmp_path)
    finally:
        await pool.close()


async def test_a_failing_migration_rolls_back_completely(blank_db_url: str, tmp_path: Path) -> None:
    (tmp_path / "V1__bad.sql").write_text("CREATE TABLE ok_t (id int); SELECT 1/0;")
    pool = await open_pool(blank_db_url, 2)
    try:
        with pytest.raises(Exception, match="division by zero"):
            await migrate(pool, tmp_path)
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT to_regclass('ok_t') AS t, (SELECT count(*) FROM schema_migrations) AS n"
            )
            row = await cur.fetchone()
        assert row == {"t": None, "n": 0}  # neither the table nor the bookkeeping row survived
    finally:
        await pool.close()
