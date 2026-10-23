"""Application factory + lifespan (startup: validate settings, migrate, seed admin key, recover crashed runs)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agentlens_gateway import problems
from agentlens_gateway.agent_client import AgentClient, HttpAgentClient
from agentlens_gateway.bus import RunBus
from agentlens_gateway.db import migrate, open_pool
from agentlens_gateway.executor import RunExecutor
from agentlens_gateway.hashing import sha256_hex
from agentlens_gateway.ratelimit import RateLimiter
from agentlens_gateway.routes import configs, datasets, evals, runs
from agentlens_gateway.settings import Settings
from agentlens_gateway.store import Store

log = logging.getLogger("agentlens.gateway")


def create_app(
    settings: Settings | None = None, *, agent_client: AgentClient | None = None
) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.validate()  # refuse to start misconfigured
        pool = await open_pool(settings.database_url, settings.pool_max_size)
        agent = agent_client or HttpAgentClient(settings.agent_base_url)
        executor: RunExecutor | None = None
        try:
            if settings.auto_migrate:
                await migrate(pool, settings.migrations_dir)
            store = Store(pool)
            key = settings.admin_api_key
            await store.seed_admin_key(
                sha256_hex(key), key[:8], settings.default_rate_limit_per_min
            )
            recovered = await store.recover_interrupted()
            if recovered:
                log.warning("marked %d interrupted run(s) as failed/gateway_restart", recovered)
            bus = RunBus()
            executor = RunExecutor(
                store,
                agent,
                bus,
                max_concurrent=settings.max_concurrent_runs,
                run_timeout_s=settings.run_timeout_s,
            )
            app.state.settings, app.state.store, app.state.bus, app.state.executor = (
                settings,
                store,
                bus,
                executor,
            )
            app.state.limiter, app.state.key_touched = RateLimiter(), {}
            yield
        finally:
            if executor is not None:
                await executor.shutdown()
            await agent.aclose()
            await pool.close()

    app = FastAPI(title="AgentLens Gateway", version="0.1.0", lifespan=lifespan)
    problems.install(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["X-API-Key", "Content-Type"],
        expose_headers=["Retry-After"],
    )
    for module in (runs, configs, datasets, evals):
        app.include_router(module.router, prefix="/api/v1")

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        """Liveness + database reachability. Unauthenticated by design (load balancers and Docker call it)."""
        await app.state.store._one("SELECT 1 AS ok")
        return {"status": "UP"}

    return app
