# shared/db.py
"""
Postgres connection helpers shared by the ingestion pipeline and agent.

Two connection modes:
  - connection()         — creates a fresh connection (ingestion pipeline / CLI)
  - pooled_connection()  — borrows from a ThreadedConnectionPool (web app)

Call init_pool() once at application startup before using pooled_connection().
"""

import logging
import threading
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

logger = logging.getLogger(__name__)

_pool: ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()


def init_pool(database_url: str, minconn: int = 1, maxconn: int = 10) -> ThreadedConnectionPool:
    """Initialise the module-level connection pool. Idempotent — safe to call on every request."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ThreadedConnectionPool(minconn, maxconn, dsn=database_url)
                logger.info(f"DB pool initialised (min={minconn}, max={maxconn})")
    return _pool


def get_pool() -> ThreadedConnectionPool:
    """Return the module-level pool; raises if not yet initialised."""
    if _pool is None:
        raise RuntimeError("DB pool not initialised — call init_pool() at startup")
    return _pool


@contextmanager
def pooled_connection():
    """Borrow a connection from the pool, commit on clean exit, return to pool on exit."""
    pool = get_pool()
    conn = pool.getconn()
    conn.autocommit = False
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def get_connection(database_url: str):
    """Open a psycopg2 connection. Caller is responsible for closing."""
    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    return conn


@contextmanager
def connection(database_url: str):
    """Context manager that opens, yields, and closes a DB connection.

    Commits on clean exit, rolls back on exception.

    Usage:
        with connection(settings.database_url) as conn:
            cursor = conn.cursor()
            ...
    """
    conn = get_connection(database_url)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetch_one(conn, query: str, params=None) -> dict | None:
    """Execute a query and return a single row as a dict, or None."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, params)
        row = cur.fetchone()
        return dict(row) if row else None


def fetch_all(conn, query: str, params=None) -> list[dict]:
    """Execute a query and return all rows as a list of dicts."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, params)
        return [dict(row) for row in cur.fetchall()]


def execute(conn, query: str, params=None) -> int:
    """Execute a DML statement. Returns rowcount."""
    with conn.cursor() as cur:
        cur.execute(query, params)
        return cur.rowcount


def execute_returning(conn, query: str, params=None):
    """Execute an INSERT ... RETURNING and return the first column of the first row."""
    with conn.cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
        return row[0] if row else None
