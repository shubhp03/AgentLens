"""Connection pool + a tiny forward-only migration runner (replaces Flyway; the SQL files are unchanged)."""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

log = logging.getLogger("agentlens.gateway.db")
_LOCK_ID = 727_274_001  # arbitrary constant: serialises concurrent gateway starts


async def open_pool(conninfo: str, max_size: int = 10) -> AsyncConnectionPool:
    """autocommit: every statement is its own transaction unless a block opts in with `conn.transaction()`;
    no connection is ever left 'idle in transaction'."""
    pool = AsyncConnectionPool(
        conninfo,
        min_size=1,
        max_size=max_size,
        kwargs={"row_factory": dict_row, "autocommit": True},
        open=False,
    )
    await pool.open(wait=True, timeout=15)
    return pool


def discover(directory: Path) -> list[tuple[int, str, Path]]:
    found = []
    for f in directory.glob("V*__*.sql"):
        m = re.fullmatch(r"V(\d+)__(.+)\.sql", f.name)
        if m:
            found.append((int(m.group(1)), m.group(2), f))
    return sorted(found)


async def migrate(pool: AsyncConnectionPool, directory: Path) -> list[int]:
    """Apply pending `V<n>__name.sql` files in order, each in its own transaction. Returns the versions applied.
    An already-applied file whose content changed is an error: never edit an applied migration, add a new one."""
    files = discover(directory)
    if not files:
        raise RuntimeError(f"no migrations found in {directory}")
    applied_now: list[int] = []
    async with pool.connection() as conn:
        await conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_ID,))
        try:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version int PRIMARY KEY, name text NOT NULL, checksum text NOT NULL,"
                " applied_at timestamptz NOT NULL DEFAULT now())"
            )
            cur = await conn.execute("SELECT version, checksum FROM schema_migrations")
            done = {r["version"]: r["checksum"] for r in await cur.fetchall()}
            for version, name, path in files:
                sql = path.read_text(encoding="utf-8")
                checksum = hashlib.sha256(sql.encode()).hexdigest()
                if version in done:
                    if done[version] != checksum:
                        raise RuntimeError(
                            f"migration V{version} ({name}) was modified after it was applied; add a new migration instead"
                        )
                    continue
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                        (version, name, checksum),
                    )
                applied_now.append(version)
                log.info("applied migration V%s (%s)", version, name)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_ID,))
    return applied_now
