"""
Postgres access: one connection pool per process, dict rows, plain SQL.

Functions elsewhere take an explicit `conn` so callers decide the transaction boundary:

    with db.transaction() as conn:
        jobs.create_job(conn, ...)
        ledger.reserve(conn, job_id, credits)
"""

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import get_settings

_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    """Process-wide pool, opened on first use. Waits for Aurora to resume from 0 ACU."""
    global _pool
    if _pool is None:
        cfg = get_settings()
        _pool = ConnectionPool(
            cfg.database_url,
            min_size=1,
            max_size=8,
            kwargs={"row_factory": dict_row},
            open=True,
        )
        _pool.wait(timeout=cfg.db_connect_timeout_s)
    return _pool


@contextmanager
def transaction() -> Iterator[psycopg.Connection]:
    """A pooled connection inside one transaction; commits on success, rolls back on error."""
    with pool().connection() as conn:
        with conn.transaction():
            yield conn


def connect(url: str | None = None, **kwargs) -> psycopg.Connection:
    """A standalone connection (migrations, scripts, tests)."""
    return psycopg.connect(url or get_settings().database_url, row_factory=dict_row, **kwargs)
