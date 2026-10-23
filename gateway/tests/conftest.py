"""Gateway tests run against a REAL PostgreSQL 16.

Source of the database, in order:
  1. TEST_DATABASE_URL (e.g. a CI service container; needs CREATEDB rights), otherwise
  2. an embedded server via `pgserver` (a dev dependency; no Docker needed).
The schema is migrated once into a template database; every test gets its own fresh copy (CREATE DATABASE ... TEMPLATE),
so tests are isolated and can run in any order.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import uuid4

import httpx
import psycopg
import pytest
from agentlens_gateway.app import create_app
from agentlens_gateway.db import migrate, open_pool
from agentlens_gateway.settings import DEFAULT_MIGRATIONS_DIR, Settings
from fastapi import FastAPI
from gwutil import HEADERS, KEY, FakeAgent
from psycopg.conninfo import make_conninfo


@pytest.fixture(scope="session")
def pg_admin() -> Iterator[str]:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        yield url
        return
    import pgserver

    srv = pgserver.get_server(tempfile.mkdtemp(prefix="agentlens-pg-"))
    try:
        yield srv.get_uri()
    finally:
        srv.cleanup()


@pytest.fixture(scope="session")
def template_db(pg_admin: str) -> str:
    with psycopg.connect(pg_admin, autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS agentlens_tpl WITH (FORCE)")
        c.execute("CREATE DATABASE agentlens_tpl")

    async def build() -> None:
        pool = await open_pool(make_conninfo(pg_admin, dbname="agentlens_tpl"), 2)
        try:
            await migrate(pool, DEFAULT_MIGRATIONS_DIR)
        finally:
            await pool.close()

    asyncio.run(build())
    return "agentlens_tpl"


@pytest.fixture
def db_url(pg_admin: str, template_db: str) -> Iterator[str]:
    name = f"t_{uuid4().hex[:12]}"
    with psycopg.connect(pg_admin, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}" TEMPLATE {template_db}')
    try:
        yield make_conninfo(pg_admin, dbname=name)
    finally:
        with psycopg.connect(pg_admin, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def blank_db_url(pg_admin: str) -> Iterator[str]:
    """An empty database (no schema), for testing the migration runner itself."""
    name = f"b_{uuid4().hex[:12]}"
    with psycopg.connect(pg_admin, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    try:
        yield make_conninfo(pg_admin, dbname=name)
    finally:
        with psycopg.connect(pg_admin, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def make_settings(db_url: str) -> Callable[..., Settings]:
    def make(**over: object) -> Settings:
        base: dict[str, object] = {
            "database_url": db_url,
            "admin_api_key": KEY,
            "run_timeout_s": 10.0,
            "max_concurrent_runs": 4,
            "default_rate_limit_per_min": 10_000,
            "daily_cost_cap_usd": 100.0,
            "allowed_origins": ("http://localhost:4200",),
        }
        return Settings(**{**base, **over})  # type: ignore[arg-type]

    return make


@dataclass
class Gateway:
    app: FastAPI
    client: httpx.AsyncClient
    agent: FakeAgent
    settings: Settings


@asynccontextmanager
async def running_gateway(
    settings: Settings, agent: FakeAgent | None = None
) -> AsyncIterator[Gateway]:
    agent = agent or FakeAgent()
    app = create_app(settings, agent_client=agent)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", headers=HEADERS
        ) as client:
            yield Gateway(app, client, agent, settings)


@pytest.fixture
def gateway_factory(make_settings: Callable[..., Settings]):
    """Start a gateway with custom settings: `async with gateway_factory(max_concurrent_runs=1) as gw:`"""

    def factory(agent: FakeAgent | None = None, **over: object):
        return running_gateway(make_settings(**over), agent)

    return factory


@pytest.fixture
async def gw(gateway_factory) -> AsyncIterator[Gateway]:
    async with gateway_factory() as g:
        yield g


@pytest.fixture
def sql(db_url: str) -> Callable[..., list[dict]]:
    """Run SQL directly against the test database (for arranging state and asserting on what was stored)."""
    from psycopg.rows import dict_row

    def run(query: str, params: tuple = ()) -> list[dict]:
        with psycopg.connect(db_url, autocommit=True, row_factory=dict_row) as c:
            cur = c.execute(query, params)
            return cur.fetchall() if cur.description else []

    return run
