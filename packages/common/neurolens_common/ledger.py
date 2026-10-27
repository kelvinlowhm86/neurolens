"""
Thin Python wrappers over the billing functions in migrations/002_ledger_functions.sql.

No code anywhere else may UPDATE users.credits_* directly — always go through these.
"""

from uuid import UUID

import psycopg

INSUFFICIENT_CREDITS_SQLSTATE = "NL001"


class InsufficientCredits(Exception):
    """The user's available balance cannot cover the requested hold."""


def _call(conn: psycopg.Connection, sql: str, params: tuple):
    try:
        row = conn.execute(sql, params).fetchone()
    except psycopg.Error as e:
        if e.sqlstate == INSUFFICIENT_CREDITS_SQLSTATE:
            raise InsufficientCredits(e.diag.message_primary) from e
        raise
    return next(iter(row.values())) if isinstance(row, dict) else row[0]


def grant(conn, user_id: UUID, amount: int, kind: str, ref: str) -> bool:
    """Add credits (signup bonus / purchase / subscription). False if `ref` was already credited."""
    return _call(conn, "SELECT grant_credits(%s, %s, %s::ledger_kind, %s)", (user_id, amount, kind, ref))


def reserve(conn, job_id: UUID, amount: int) -> None:
    """Hold `amount` credits for a job. Raises InsufficientCredits."""
    _call(conn, "SELECT reserve_credits(%s, %s)", (job_id, amount))


def adjust(conn, job_id: UUID, new_amount: int) -> None:
    """Resize a job's hold once the real duration is known. Raises InsufficientCredits."""
    _call(conn, "SELECT adjust_reservation(%s, %s)", (job_id, new_amount))


def capture(conn, job_id: UUID, amount: int) -> bool:
    """Charge `amount` (≤ hold) and return the rest. False if nothing was held."""
    return _call(conn, "SELECT capture_credits(%s, %s)", (job_id, amount))


def release(conn, job_id: UUID, reason: str) -> bool:
    """Return the whole hold. False if nothing was held (safe to repeat)."""
    return _call(conn, "SELECT release_credits(%s, %s)", (job_id, reason))


def balance(conn, user_id: UUID) -> dict:
    return conn.execute(
        "SELECT credits_available, credits_reserved FROM users WHERE id = %s", (user_id,)
    ).fetchone()
