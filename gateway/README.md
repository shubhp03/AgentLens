# AgentLens gateway (Python · FastAPI · psycopg 3)

Control plane: API-key auth, per-key rate limit, daily cost cap, run orchestration, trace persistence, live SSE, dataset/eval-run registry.
Contract: `../contracts/openapi.yaml`. Schema: `migrations/` (applied automatically at start; checksum-verified, forward-only).
Code tour: `../docs/CODE_WALKTHROUGH.md` §7. Design: `../docs/ARCHITECTURE.md` §8.

```bash
uv run pytest gateway/tests -q        # 84 tests, real PostgreSQL 16 via pgserver (or TEST_DATABASE_URL=...)
uv run python scripts/stack_smoke.py  # from the repo root: gateway + agent + MCP + Postgres over real sockets
DATABASE_URL=postgresql://... ADMIN_API_KEY=<>=16 chars> AGENT_BASE_URL=http://localhost:8200 \
  uv run uvicorn agentlens_gateway.main:app --port 8080
```

Environment: `DATABASE_URL`, `AGENT_BASE_URL`, `ADMIN_API_KEY` (≥16 chars, required), `ALLOWED_ORIGINS` (default `http://localhost:4200`, `*` refused),
`DAILY_COST_CAP_USD`, `RUN_TIMEOUT_S`, `MAX_CONCURRENT_RUNS`, `DEFAULT_RATE_LIMIT_PER_MIN`, `AUTO_MIGRATE`, `MIGRATIONS_DIR`, `DB_POOL_MAX`.

Known limits: single process (in-memory rate limiter and event bus), no metrics endpoint yet, API-key auth only.
