"""All SQL lives here. Plain parameterised queries (psycopg 3); no ORM, so every statement is visible and testable.

Conventions: every public method is one logical operation; multi-statement ones use an explicit transaction.
Decimals from NUMERIC columns are converted to float at the boundary (`_f`).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from decimal import Decimal
from typing import Any
from uuid import UUID

from agentlens_agent.events import RunFinished, SpanFinished
from agentlens_evals.stats import wilson_interval
from psycopg import errors as pgerr
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from agentlens_gateway import cursor as cursor_mod
from agentlens_gateway.hashing import config_hash
from agentlens_gateway.problems import bad_request, conflict, not_found

log = logging.getLogger("agentlens.gateway.store")

TERMINAL = ("completed", "failed", "cancelled")


def _f(x: Decimal | float | int | None) -> float | None:
    return None if x is None else float(x)


# ----------------------------------------------------------------------------- row -> API shape
def config_out(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r["id"],
        "name": r["name"],
        "config_hash": r["config_hash"],
        "provider": r["provider"],
        "model": r["model"],
        "system_prompt_id": r["system_prompt_id"],
        "system_prompt_sha": r["system_prompt_sha"],
        "temperature": _f(r["temperature"]),
        "max_steps": r["max_steps"],
        "max_cost_usd": _f(r["max_cost_usd"]),
        "max_output_tokens": r["max_output_tokens"],
        "tool_allowlist": r["tool_allowlist"],
        "params": r["params"],
        "created_at": r["created_at"],
    }


def run_out(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r["id"],
        "config_id": r["config_id"],
        "eval_run_id": r["eval_run_id"],
        "source": r["source"],
        "question": r["question"],
        "status": r["status"],
        "termination_reason": r["termination_reason"],
        "final_answer": r["final_answer"],
        "error": r["error"],
        "totals": {
            "input_tokens": r["input_tokens"],
            "output_tokens": r["output_tokens"],
            "cache_read_tokens": r["cache_read_tokens"],
            "cost_usd": _f(r["cost_usd"]),
            "latency_ms": r["latency_ms"],
            "step_count": r["step_count"],
            "tool_call_count": r["tool_call_count"],
        },
        "created_at": r["created_at"],
        "started_at": r["started_at"],
        "finished_at": r["finished_at"],
    }


def span_out(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r["id"],
        "run_id": r["run_id"],
        "parent_span_id": r["parent_span_id"],
        "seq": r["seq"],
        "type": r["type"],
        "name": r["name"],
        "status": r["status"],
        "model": r["model"],
        "started_at": r["started_at"],
        "ended_at": r["ended_at"],
        "latency_ms": r["latency_ms"],
        "input": r["input"],
        "output": r["output"],
        "error": r["error"],
        "input_tokens": r["input_tokens"],
        "output_tokens": r["output_tokens"],
        "cost_usd": _f(r["cost_usd"]),
        "attributes": r["attributes"],
    }


def dataset_out(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r["id"],
        "name": r["name"],
        "version": r["version"],
        "description": r["description"],
        "content_hash": r["content_hash"],
        "case_count": r["case_count"],
        "created_at": r["created_at"],
    }


def case_out(r: dict[str, Any]) -> dict[str, Any]:
    return {k: r[k] for k in ("id", "case_key", "question", "category", "difficulty", "expected",
                              "required_tools", "provenance", "status", "tags")}  # fmt: skip


def metrics_out(m: dict[str, Any] | None) -> dict[str, Any] | None:
    if m is None:
        return None
    n_cases, n_passed = m["n_cases"], m["n_cases_passed"]
    return {
        "n_results": m["n_results"],
        "n_cases": n_cases,
        "n_cases_passed": n_passed,
        "pass_rate": _f(m["pass_rate"]),
        "run_pass_rate": _f(m["run_pass_rate"]),
        "pass_rate_ci": list(wilson_interval(n_passed, n_cases)),
        "total_cost_usd": _f(m["total_cost_usd"]),
        "avg_cost_usd": _f(m["avg_cost_usd"]),
        "cost_per_success_usd": _f(m["cost_per_success_usd"]),
        "p50_latency_ms": _f(m["p50_latency_ms"]),
        "p95_latency_ms": _f(m["p95_latency_ms"]),
    }


def eval_run_out(r: dict[str, Any], metrics: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "id": r["id"],
        "dataset_id": r["dataset_id"],
        "config_id": r["config_id"],
        "label": r["label"],
        "trigger": r["trigger"],
        "git_sha": r["git_sha"],
        "repeats": r["repeats"],
        "status": r["status"],
        "is_baseline": r["is_baseline"],
        "gate_passed": r["gate_passed"],
        "notes": r["notes"],
        "started_at": r["started_at"],
        "finished_at": r["finished_at"],
        "metrics": metrics_out(metrics),
    }


def result_out(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r["id"],
        "case_key": r["case_key"],
        "repeat_idx": r["repeat_idx"],
        "run_id": r["run_id"],
        "passed": r["passed"],
        "scores": r["scores"],
        "failure_category": r["failure_category"],
        "judge_rationale": r["judge_rationale"],
        "latency_ms": r["latency_ms"],
        "cost_usd": _f(r["cost_usd"]) or 0.0,
    }


def run_finished_event(r: dict[str, Any]) -> dict[str, Any]:
    """`run.finished` rebuilt from the stored row (for SSE replay). A plain dict, not the pydantic event, because
    the gateway also has termination reasons (e.g. gateway_restart) that the agent's own enum does not."""
    return {
        "kind": "run.finished",
        "run_id": str(r["id"]),
        "status": r["status"],
        "termination_reason": r["termination_reason"],
        "final_answer": r["final_answer"],
        "totals": {
            "usage": {
                "input_tokens": r["input_tokens"],
                "output_tokens": r["output_tokens"],
                "cache_read_tokens": r["cache_read_tokens"],
            },
            "cost_usd": _f(r["cost_usd"]),
            "latency_ms": r["latency_ms"] or 0,
            "steps": r["step_count"],
            "tool_calls": r["tool_call_count"],
        },
        "error": r["error"],
        "ended_at": r["finished_at"].isoformat() if r["finished_at"] else None,
    }


def span_finished_event(r: dict[str, Any]) -> dict[str, Any]:
    attrs = dict(r["attributes"] or {})
    cache = attrs.pop("cache_read_tokens", 0)
    usage = None
    if r["input_tokens"] is not None or r["output_tokens"] is not None:
        usage = {
            "input_tokens": r["input_tokens"] or 0,
            "output_tokens": r["output_tokens"] or 0,
            "cache_read_tokens": cache,
        }
    return {
        "kind": "span.finished",
        "run_id": str(r["run_id"]),
        "span_id": str(r["id"]),
        "parent_span_id": str(r["parent_span_id"]) if r["parent_span_id"] else None,
        "seq": r["seq"],
        "type": r["type"],
        "name": r["name"],
        "started_at": r["started_at"].isoformat(),
        "ended_at": r["ended_at"].isoformat() if r["ended_at"] else r["started_at"].isoformat(),
        "status": r["status"],
        "model": r["model"],
        "input": r["input"],
        "output": r["output"],
        "error": r["error"],
        "usage": usage,
        "cost_usd": _f(r["cost_usd"]),
        "attributes": attrs,
    }


class Store:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def _one(self, sql: str, params: Any = None) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(sql, params)
            return await cur.fetchone()

    async def _all(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(sql, params)
            return await cur.fetchall()

    # ------------------------------------------------------------------ api keys
    async def seed_admin_key(self, key_hash: str, key_prefix: str, rate_per_min: int) -> None:
        """Make the configured ADMIN_API_KEY the one valid admin key. Rotating the env var revokes the old admin key."""
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                "UPDATE api_keys SET revoked_at = now() WHERE name = 'admin' AND key_hash <> %s AND revoked_at IS NULL",
                (key_hash,),
            )
            await conn.execute(
                "INSERT INTO api_keys (name, key_prefix, key_hash, rate_limit_per_min) VALUES ('admin', %s, %s, %s)"
                " ON CONFLICT (key_hash) DO UPDATE SET revoked_at = NULL",
                (key_prefix, key_hash, rate_per_min),
            )

    async def create_key(
        self, name: str, key_prefix: str, key_hash: str, rate_per_min: int
    ) -> UUID:
        row = await self._one(
            "INSERT INTO api_keys (name, key_prefix, key_hash, rate_limit_per_min) VALUES (%s,%s,%s,%s) RETURNING id",
            (name, key_prefix, key_hash, rate_per_min),
        )
        assert row is not None
        return row["id"]

    async def find_key(self, key_hash: str) -> dict[str, Any] | None:
        return await self._one(
            "SELECT id, name, rate_limit_per_min FROM api_keys WHERE key_hash = %s AND revoked_at IS NULL",
            (key_hash,),
        )

    async def touch_key(self, key_id: UUID) -> None:
        await self._one(
            "UPDATE api_keys SET last_used_at = now() WHERE id = %s RETURNING id", (key_id,)
        )

    # ------------------------------------------------------------------ configs
    async def create_config(self, c: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Idempotent on config_hash. Returns (config, created)."""
        tools = list(dict.fromkeys(c["tool_allowlist"]))
        h = config_hash(
            provider=c["provider"], model=c["model"], system_prompt_id=c["system_prompt_id"],
            system_prompt_sha=c["system_prompt_sha"], temperature=c["temperature"], max_steps=c["max_steps"],
            max_cost_usd=c["max_cost_usd"], max_output_tokens=c["max_output_tokens"],
            tool_allowlist=tools, params=c["params"],
        )  # fmt: skip
        row = await self._one(
            "INSERT INTO agent_configs (name, config_hash, provider, model, system_prompt_id, system_prompt_sha,"
            " temperature, max_steps, max_cost_usd, max_output_tokens, tool_allowlist, params)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (config_hash) DO NOTHING RETURNING *",
            (c["name"], h, c["provider"], c["model"], c["system_prompt_id"], c["system_prompt_sha"],
             c["temperature"], c["max_steps"], c["max_cost_usd"], c["max_output_tokens"], tools, Jsonb(c["params"])),
        )  # fmt: skip
        if row is not None:
            return config_out(row), True
        row = await self._one("SELECT * FROM agent_configs WHERE config_hash = %s", (h,))
        assert row is not None
        return config_out(row), False

    async def get_config(self, config_id: UUID) -> dict[str, Any] | None:
        row = await self._one("SELECT * FROM agent_configs WHERE id = %s", (config_id,))
        return config_out(row) if row else None

    async def list_configs(self) -> list[dict[str, Any]]:
        return [
            config_out(r)
            for r in await self._all(
                "SELECT * FROM agent_configs ORDER BY created_at DESC, id DESC"
            )
        ]

    # ------------------------------------------------------------------ runs
    async def insert_run(
        self, *, run_id: UUID, config_id: UUID, api_key_id: UUID | None, eval_run_id: UUID | None,
        source: str, question: str,
    ) -> dict[str, Any]:  # fmt: skip
        row = await self._one(
            "INSERT INTO runs (id, config_id, api_key_id, eval_run_id, source, question, status)"
            " VALUES (%s,%s,%s,%s,%s,%s,'queued') RETURNING *",
            (run_id, config_id, api_key_id, eval_run_id, source, question),
        )
        assert row is not None
        return row

    async def get_run(self, run_id: UUID) -> dict[str, Any] | None:
        return await self._one("SELECT * FROM runs WHERE id = %s", (run_id,))

    async def list_runs(
        self, *, status: str | None, source: str | None, config_id: UUID | None, eval_run_id: UUID | None,
        limit: int, cursor: str | None,
    ) -> tuple[list[dict[str, Any]], str | None]:  # fmt: skip
        where, params = ["TRUE"], []
        for col, val in (
            ("status", status),
            ("source", source),
            ("config_id", config_id),
            ("eval_run_id", eval_run_id),
        ):
            if val is not None:
                where.append(f"{col} = %s")
                params.append(val)
        if cursor:
            try:
                at, ident = cursor_mod.decode(cursor)
            except ValueError as e:
                raise bad_request("invalid cursor") from e
            where.append("(created_at, id) < (%s, %s)")
            params += [at, ident]
        rows = await self._all(
            f"SELECT * FROM runs WHERE {' AND '.join(where)} ORDER BY created_at DESC, id DESC LIMIT %s",
            [*params, limit + 1],
        )
        nxt = None
        if len(rows) > limit:
            rows = rows[:limit]
            nxt = cursor_mod.encode(rows[-1]["created_at"], rows[-1]["id"])
        return rows, nxt

    async def mark_running(self, run_id: UUID) -> bool:
        row = await self._one(
            "UPDATE runs SET status = 'running', started_at = now() WHERE id = %s AND status = 'queued' RETURNING id",
            (run_id,),
        )
        return row is not None

    async def upsert_span(self, ev: SpanFinished) -> None:
        """Idempotent on the client-generated span id: replays and duplicates are harmless."""
        attrs = dict(ev.attributes)
        if ev.usage and ev.usage.cache_read_tokens:
            attrs["cache_read_tokens"] = ev.usage.cache_read_tokens
        try:
            await self._one(
                "INSERT INTO spans (id, run_id, parent_span_id, seq, type, name, status, model, started_at, ended_at,"
                " input, output, error, input_tokens, output_tokens, cost_usd, attributes)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (id) DO UPDATE SET parent_span_id = EXCLUDED.parent_span_id, seq = EXCLUDED.seq,"
                " type = EXCLUDED.type, name = EXCLUDED.name, status = EXCLUDED.status, model = EXCLUDED.model,"
                " started_at = EXCLUDED.started_at, ended_at = EXCLUDED.ended_at, input = EXCLUDED.input,"
                " output = EXCLUDED.output, error = EXCLUDED.error, input_tokens = EXCLUDED.input_tokens,"
                " output_tokens = EXCLUDED.output_tokens, cost_usd = EXCLUDED.cost_usd,"
                " attributes = EXCLUDED.attributes RETURNING id",
                (ev.span_id, ev.run_id, ev.parent_span_id, ev.seq, ev.type, ev.name, ev.status, ev.model,
                 ev.started_at, ev.ended_at, Jsonb(ev.input), Jsonb(ev.output), ev.error,
                 ev.usage.input_tokens if ev.usage else None, ev.usage.output_tokens if ev.usage else None,
                 ev.cost_usd, Jsonb(attrs)),
            )  # fmt: skip
        except (
            pgerr.UniqueViolation
        ):  # same (run_id, seq), different span id: an agent bug; keep the first, keep going
            log.warning(
                "duplicate seq %s for run %s ignored (span %s)", ev.seq, ev.run_id, ev.span_id
            )

    async def list_spans(self, run_id: UUID) -> list[dict[str, Any]]:
        return await self._all("SELECT * FROM spans WHERE run_id = %s ORDER BY seq", (run_id,))

    async def finalize_run(self, ev: RunFinished) -> bool:
        """Apply the agent's own `run.finished`. Only an active run can be finalised (first terminal write wins)."""
        t = ev.totals
        row = await self._one(
            "UPDATE runs SET status = %s, termination_reason = %s, final_answer = %s, error = %s,"
            " input_tokens = %s, output_tokens = %s, cache_read_tokens = %s, cost_usd = %s, latency_ms = %s,"
            " step_count = %s, tool_call_count = %s, finished_at = %s"
            " WHERE id = %s AND status IN ('queued','running') RETURNING id",
            (ev.status, ev.termination_reason,
             Jsonb(ev.final_answer.model_dump(mode="json")) if ev.final_answer else None, ev.error,
             t.usage.input_tokens, t.usage.output_tokens, t.usage.cache_read_tokens, t.cost_usd, t.latency_ms,
             t.steps, t.tool_calls, ev.ended_at, ev.run_id),
        )  # fmt: skip
        return row is not None

    async def fail_run(
        self, run_id: UUID, *, status: str, reason: str, error: str | None
    ) -> dict[str, Any] | None:
        """Gateway-decided terminal state (timeout, cancel, agent unreachable...). Totals are rebuilt from the spans
        already stored, so money spent before the failure still counts against the daily cap."""
        return await self._one(
            "UPDATE runs r SET status = %(status)s, termination_reason = %(reason)s, error = %(error)s,"
            " finished_at = now(),"
            " input_tokens = COALESCE(s.in_tok, 0), output_tokens = COALESCE(s.out_tok, 0),"
            " cache_read_tokens = COALESCE(s.cache_tok, 0), cost_usd = COALESCE(s.cost, 0),"
            " step_count = COALESCE(s.steps, 0), tool_call_count = COALESCE(s.tools, 0),"
            " latency_ms = (EXTRACT(EPOCH FROM (now() - COALESCE(r.started_at, r.created_at))) * 1000)::int"
            " FROM (SELECT (sum(input_tokens) FILTER (WHERE type = 'llm_call'))::int AS in_tok,"
            "              (sum(output_tokens) FILTER (WHERE type = 'llm_call'))::int AS out_tok,"
            "              (sum((attributes->>'cache_read_tokens')::int) FILTER (WHERE type = 'llm_call'))::int AS cache_tok,"
            "              sum(cost_usd) FILTER (WHERE type = 'llm_call') AS cost,"
            "              (count(*) FILTER (WHERE type = 'llm_call' AND status = 'ok'))::int AS steps,"
            "              (count(*) FILTER (WHERE type = 'tool_call'))::int AS tools"
            "       FROM spans WHERE run_id = %(id)s) s"
            " WHERE r.id = %(id)s AND r.status IN ('queued','running') RETURNING r.*",
            {"status": status, "reason": reason, "error": error, "id": run_id},
        )

    async def recover_interrupted(self) -> int:
        """Startup crash recovery: anything still queued/running belonged to a previous process."""
        rows = await self._all(
            "UPDATE runs r SET status = 'failed', termination_reason = 'gateway_restart', finished_at = now(),"
            " error = 'gateway restarted while this run was in flight' WHERE status IN ('queued','running') RETURNING id"
        )
        return len(rows)

    async def spend_today(self) -> tuple[float, float]:
        """(booked cost of today's runs, budgets reserved by runs still in flight). UTC day."""
        row = await self._one(
            "SELECT"
            " COALESCE((SELECT sum(cost_usd) FROM runs WHERE (created_at AT TIME ZONE 'UTC')::date ="
            "           (now() AT TIME ZONE 'UTC')::date AND status IN ('completed','failed','cancelled')), 0) AS spent,"
            " COALESCE((SELECT sum(c.max_cost_usd) FROM runs r JOIN agent_configs c ON c.id = r.config_id"
            "           WHERE r.status IN ('queued','running')), 0) AS reserved"
        )
        assert row is not None
        return float(row["spent"]), float(row["reserved"])

    # ------------------------------------------------------------------ datasets
    async def upsert_dataset(self, d: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """Returns (dataset, 'created' | 'exists'); raises 409 if name+version exists with different content."""
        async with self.pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "INSERT INTO datasets (name, version, description, content_hash) VALUES (%s,%s,%s,%s)"
                " ON CONFLICT (name, version) DO NOTHING RETURNING id",
                (d["name"], d["version"], d.get("description"), d["content_hash"]),
            )
            inserted = await cur.fetchone()
            if inserted is None:
                cur = await conn.execute(
                    "SELECT d.*, (SELECT count(*) FROM eval_cases c WHERE c.dataset_id = d.id)::int AS case_count"
                    " FROM datasets d WHERE name = %s AND version = %s",
                    (d["name"], d["version"]),
                )
                existing = await cur.fetchone()
                assert existing is not None
                if existing["content_hash"] != d["content_hash"]:
                    raise conflict(
                        f"dataset {d['name']}@{d['version']} already exists with different content; "
                        "bump the version instead of editing a published dataset"
                    )
                return dataset_out(existing), "exists"
            ds_id = inserted["id"]
            async with conn.cursor() as c2:
                await c2.executemany(
                    "INSERT INTO eval_cases (dataset_id, case_key, question, category, difficulty, expected,"
                    " required_tools, provenance, status, tags) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [
                        (ds_id, c["case_key"], c["question"], c["category"], c["difficulty"], Jsonb(c["expected"]),
                         c["required_tools"], Jsonb(c["provenance"]) if c["provenance"] is not None else None,
                         c["status"], c["tags"])
                        for c in d["cases"]
                    ],
                )  # fmt: skip
            cur = await conn.execute(
                "SELECT d.*, (SELECT count(*) FROM eval_cases c WHERE c.dataset_id = d.id)::int AS case_count"
                " FROM datasets d WHERE id = %s",
                (ds_id,),
            )
            row = await cur.fetchone()
            assert row is not None
            return dataset_out(row), "created"

    async def list_datasets(self) -> list[dict[str, Any]]:
        rows = await self._all(
            "SELECT d.*, (SELECT count(*) FROM eval_cases c WHERE c.dataset_id = d.id)::int AS case_count"
            " FROM datasets d ORDER BY created_at DESC, id DESC"
        )
        return [dataset_out(r) for r in rows]

    async def list_cases(self, dataset_id: UUID, category: str | None) -> list[dict[str, Any]]:
        if await self._one("SELECT 1 AS x FROM datasets WHERE id = %s", (dataset_id,)) is None:
            raise not_found("dataset", dataset_id)
        sql, params = "SELECT * FROM eval_cases WHERE dataset_id = %s", [dataset_id]
        if category:
            sql += " AND category = %s"
            params.append(category)
        return [case_out(r) for r in await self._all(sql + " ORDER BY case_key", params)]

    # ------------------------------------------------------------------ eval runs
    async def _metrics_for(self, ids: Sequence[UUID]) -> dict[UUID, dict[str, Any]]:
        rows = await self._all(
            "SELECT * FROM eval_run_metrics WHERE eval_run_id = ANY(%s)", (list(ids),)
        )
        return {r["eval_run_id"]: r for r in rows}

    async def create_eval_run(self, e: dict[str, Any]) -> dict[str, Any]:
        if await self._one("SELECT 1 AS x FROM datasets WHERE id = %s", (e["dataset_id"],)) is None:
            raise not_found("dataset", e["dataset_id"])
        if (
            await self._one("SELECT 1 AS x FROM agent_configs WHERE id = %s", (e["config_id"],))
            is None
        ):
            raise not_found("config", e["config_id"])
        row = await self._one(
            "INSERT INTO eval_runs (dataset_id, config_id, label, trigger, git_sha, repeats)"
            " VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
            (
                e["dataset_id"],
                e["config_id"],
                e["label"],
                e["trigger"],
                e.get("git_sha"),
                e["repeats"],
            ),
        )
        assert row is not None
        return eval_run_out(row, None)

    async def eval_run_exists(self, eval_run_id: UUID) -> bool:
        return (
            await self._one("SELECT 1 AS x FROM eval_runs WHERE id = %s", (eval_run_id,))
            is not None
        )

    async def list_eval_runs(self, dataset_id: UUID | None, limit: int) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM eval_runs", []
        if dataset_id:
            sql += " WHERE dataset_id = %s"
            params.append(dataset_id)
        rows = await self._all(
            sql + " ORDER BY started_at DESC, id DESC LIMIT %s", [*params, limit]
        )
        m = await self._metrics_for([r["id"] for r in rows]) if rows else {}
        return [eval_run_out(r, m.get(r["id"])) for r in rows]

    async def get_eval_run(self, eval_run_id: UUID) -> dict[str, Any]:
        row = await self._one("SELECT * FROM eval_runs WHERE id = %s", (eval_run_id,))
        if row is None:
            raise not_found("eval run", eval_run_id)
        return eval_run_out(row, (await self._metrics_for([eval_run_id])).get(eval_run_id))

    async def get_eval_run_detail(self, eval_run_id: UUID) -> dict[str, Any]:
        out = await self.get_eval_run(eval_run_id)
        fails = await self._all(
            "SELECT failure_category, count(*)::int AS n FROM eval_results"
            " WHERE eval_run_id = %s AND NOT passed GROUP BY failure_category ORDER BY n DESC, failure_category",
            (eval_run_id,),
        )
        cats = await self._all(
            "SELECT c.category, avg(o.passed::int)::float AS rate FROM eval_case_outcomes o"
            " JOIN eval_cases c ON c.id = o.case_id WHERE o.eval_run_id = %s GROUP BY c.category ORDER BY c.category",
            (eval_run_id,),
        )
        out["failure_breakdown"] = {r["failure_category"]: r["n"] for r in fails}
        out["category_pass_rates"] = {r["category"]: r["rate"] for r in cats}
        return out

    async def update_eval_run(
        self, eval_run_id: UUID, *, is_baseline: bool | None, notes: str | None
    ) -> dict[str, Any]:
        async with self.pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "SELECT * FROM eval_runs WHERE id = %s FOR UPDATE", (eval_run_id,)
            )
            row = await cur.fetchone()
            if row is None:
                raise not_found("eval run", eval_run_id)
            # One baseline per dataset: demote the current one first (the DB's partial unique index enforces it).
            if is_baseline:
                await conn.execute(
                    "UPDATE eval_runs SET is_baseline = false WHERE dataset_id = %s AND is_baseline AND id <> %s",
                    (row["dataset_id"], eval_run_id),
                )
            await conn.execute(
                "UPDATE eval_runs SET is_baseline = COALESCE(%s, is_baseline), notes = COALESCE(%s, notes) WHERE id = %s",
                (is_baseline, notes, eval_run_id),
            )
        return await self.get_eval_run(eval_run_id)

    async def complete_eval_run(
        self, eval_run_id: UUID, status: str, gate_passed: bool | None
    ) -> dict[str, Any]:
        row = await self._one(
            "UPDATE eval_runs SET status = %s, gate_passed = %s, finished_at = now()"
            " WHERE id = %s AND status = 'running' RETURNING id",
            (status, gate_passed, eval_run_id),
        )
        if row is None:
            current = await self._one("SELECT status FROM eval_runs WHERE id = %s", (eval_run_id,))
            if current is None:
                raise not_found("eval run", eval_run_id)
            raise conflict(f"eval run is already {current['status']}")
        return await self.get_eval_run(eval_run_id)

    async def add_results(self, eval_run_id: UUID, items: list[dict[str, Any]]) -> None:
        """Batch upsert, idempotent on (eval_run, case, repeat). All-or-nothing: one bad item rejects the batch."""
        async with self.pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "SELECT dataset_id FROM eval_runs WHERE id = %s", (eval_run_id,)
            )
            er = await cur.fetchone()
            if er is None:
                raise not_found("eval run", eval_run_id)
            keys = sorted({i["case_key"] for i in items})
            cur = await conn.execute(
                "SELECT id, case_key FROM eval_cases WHERE dataset_id = %s AND case_key = ANY(%s)",
                (er["dataset_id"], keys),
            )
            case_ids = {r["case_key"]: r["id"] for r in await cur.fetchall()}
            unknown = [k for k in keys if k not in case_ids]
            if unknown:
                raise bad_request(
                    f"case_key not in this eval run's dataset: {', '.join(unknown[:5])}"
                )
            run_ids = sorted({i["run_id"] for i in items})
            cur = await conn.execute("SELECT id FROM runs WHERE id = ANY(%s)", (run_ids,))
            have = {r["id"] for r in await cur.fetchall()}
            missing = [str(r) for r in run_ids if r not in have]
            if missing:
                raise bad_request(f"unknown run_id: {', '.join(missing[:5])}")
            async with conn.cursor() as c2:
                await c2.executemany(
                    "INSERT INTO eval_results (eval_run_id, case_id, run_id, repeat_idx, passed, scores,"
                    " failure_category, judge_rationale, latency_ms, cost_usd)"
                    " SELECT %s, %s, r.id, %s, %s, %s, %s, %s, r.latency_ms, r.cost_usd FROM runs r WHERE r.id = %s"
                    " ON CONFLICT (eval_run_id, case_id, repeat_idx) DO UPDATE SET run_id = EXCLUDED.run_id,"
                    " passed = EXCLUDED.passed, scores = EXCLUDED.scores, failure_category = EXCLUDED.failure_category,"
                    " judge_rationale = EXCLUDED.judge_rationale, latency_ms = EXCLUDED.latency_ms,"
                    " cost_usd = EXCLUDED.cost_usd",
                    [
                        (eval_run_id, case_ids[i["case_key"]], i["repeat_idx"], i["passed"], Jsonb(i["scores"]),
                         i["failure_category"], i["judge_rationale"], i["run_id"])
                        for i in items
                    ],
                )  # fmt: skip

    async def list_results(
        self,
        eval_run_id: UUID,
        *,
        passed: bool | None,
        failure_category: str | None,
        category: str | None,
    ) -> list[dict[str, Any]]:
        if not await self.eval_run_exists(eval_run_id):
            raise not_found("eval run", eval_run_id)
        where, params = ["r.eval_run_id = %s"], [eval_run_id]
        if passed is not None:
            where.append("r.passed = %s")
            params.append(passed)
        if failure_category:
            where.append("r.failure_category = %s")
            params.append(failure_category)
        if category:
            where.append("c.category = %s")
            params.append(category)
        rows = await self._all(
            "SELECT r.*, c.case_key FROM eval_results r JOIN eval_cases c ON c.id = r.case_id"
            f" WHERE {' AND '.join(where)} ORDER BY c.case_key, r.repeat_idx",
            params,
        )
        return [result_out(r) for r in rows]

    async def case_outcomes(self, eval_run_id: UUID) -> dict[str, bool]:
        """case_key -> passed (strict majority of repeats), straight from the eval_case_outcomes view."""
        if not await self.eval_run_exists(eval_run_id):
            raise not_found("eval run", eval_run_id)
        rows = await self._all(
            "SELECT c.case_key, o.passed FROM eval_case_outcomes o JOIN eval_cases c ON c.id = o.case_id"
            " WHERE o.eval_run_id = %s",
            (eval_run_id,),
        )
        return {r["case_key"]: r["passed"] for r in rows}

    # ------------------------------------------------------------------ stats
    async def overview(self) -> dict[str, Any]:
        row = await self._one(
            "SELECT"
            " (SELECT count(*) FROM runs WHERE (created_at AT TIME ZONE 'UTC')::date = (now() AT TIME ZONE 'UTC')::date)::int AS runs_today,"
            " COALESCE((SELECT sum(cost_usd) FROM runs WHERE (created_at AT TIME ZONE 'UTC')::date = (now() AT TIME ZONE 'UTC')::date), 0) AS cost_today,"
            " (SELECT m.pass_rate FROM eval_runs e JOIN eval_run_metrics m ON m.eval_run_id = e.id"
            "   WHERE e.is_baseline ORDER BY e.started_at DESC LIMIT 1) AS baseline_rate"
        )
        assert row is not None
        return {
            "runs_today": row["runs_today"],
            "cost_today_usd": float(row["cost_today"]),
            "latest_baseline_pass_rate": _f(row["baseline_rate"]),
        }


def flipped_cases(base: dict[str, bool], cand: dict[str, bool]) -> list[dict[str, str]]:
    """Cases whose case-level outcome differs between two runs (shared keys only), sorted by key."""
    out = []
    for k in sorted(set(base) & set(cand)):
        if base[k] and not cand[k]:
            out.append({"case_key": k, "direction": "regressed"})
        elif cand[k] and not base[k]:
            out.append({"case_key": k, "direction": "improved"})
    return out
