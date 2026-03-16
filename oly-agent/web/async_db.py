# web/async_db.py
"""Async Postgres helpers for the web layer (asyncpg-based).

shared/db.py is kept intact for the agent pipeline (synchronous psycopg2).
This module is used exclusively by the FastAPI web app.
"""

import json
import logging

import asyncpg

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection):
    """Register JSON/JSONB codecs so columns are decoded as Python dicts, not strings."""
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads,
        schema="pg_catalog", format="text",
    )
    await conn.set_type_codec(
        "json", encoder=json.dumps, decoder=json.loads,
        schema="pg_catalog", format="text",
    )


async def init_async_pool(dsn: str, min_size: int = 1, max_size: int = 10) -> asyncpg.Pool:
    """Create the module-level asyncpg pool. Idempotent — safe to call multiple times.

    statement_cache_size=0 disables asyncpg's prepared statement cache, which is
    required for PgBouncer transaction pooling — pooled connections are not guaranteed
    to be the same server connection, so cached prepared statements would not exist.
    """
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn,
            min_size=min_size,
            max_size=max_size,
            init=_init_connection,
            statement_cache_size=0,
        )
        logger.info(f"Async DB pool initialised (min={min_size}, max={max_size})")
    return _pool


async def close_async_pool():
    """Close the pool at app shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Async DB pool closed")


def get_async_pool() -> asyncpg.Pool:
    """Return the pool; raises RuntimeError if not yet initialised."""
    if _pool is None:
        raise RuntimeError("Async DB pool not initialised — ensure lifespan handler ran")
    return _pool


async def async_fetch_one(conn, query: str, *args) -> dict | None:
    """Execute a query and return a single row as a dict, or None."""
    row = await conn.fetchrow(query, *args)
    return dict(row) if row else None


async def async_fetch_all(conn, query: str, *args) -> list[dict]:
    """Execute a query and return all rows as a list of dicts."""
    rows = await conn.fetch(query, *args)
    return [dict(row) for row in rows]


async def async_execute(conn, query: str, *args) -> None:
    """Execute a DML statement (INSERT/UPDATE/DELETE with no return value)."""
    await conn.execute(query, *args)


async def async_execute_returning(conn, query: str, *args):
    """Execute an INSERT ... RETURNING and return the first column of the first row."""
    return await conn.fetchval(query, *args)
