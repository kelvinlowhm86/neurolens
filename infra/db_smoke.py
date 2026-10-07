"""Database smoke test (M3a §3d, §8 step 2): throwaway jobs through billing on a real database.

    python infra/db_smoke.py --backend data_api   # Aurora (NEUROLENS_DB_* in .env)
    python infra/db_smoke.py --backend postgres   # local PostgreSQL (NEUROLENS_DB_DSN in .env)

Runs ensure_user, reserve, claim, set_stage, verify and settle_success for a throwaway user,
then a second job through touch, release_for_retry (no message) and issue_refund (whose re-check
reads boolean columns), checks the ledger invariant, reads the whole `jobs` row back and checks
the Python type of every column against what the PostgreSQL backend returns (§3d), so a wrong
guess about the Data API's formats is caught here, not in production. Prints how long the first
call took (it includes waking Aurora from pause). The throwaway user's rows are deleted at the
end, pass or fail. Exit code 0 only if every check passed.
"""

import argparse
import sys
import time
import uuid
from datetime import datetime, timedelta

from neurolens import billing, settings
from neurolens import db as dbmod

STRS = (str,)
OPTIONAL_STR = (str, type(None))
# Every jobs column after the run below (stage set, verified, settled, no error).
EXPECTED_TYPES = {
    "job_id": STRS,
    "user_id": STRS,
    "object_key": STRS,
    "filename": STRS,
    "status": STRS,
    "stage": STRS,
    "stages": (list,),
    "attempt": (int,),
    "client_duration_ms": (int,),
    "verified_duration_ms": (int,),
    "reserved_cents": (int,),
    "captured_cents": (int,),
    "error_code": OPTIONAL_STR,
    "error_message": OPTIONAL_STR,
    "created_at": (datetime,),
    "updated_at": (datetime,),
}
CLEANUP = [  # children before parents
    "DELETE FROM ledger WHERE user_id = :user_id",
    "DELETE FROM refunds WHERE job_id IN (SELECT job_id FROM jobs WHERE user_id = :user_id)",
    "DELETE FROM jobs WHERE user_id = :user_id",
    "DELETE FROM balances WHERE user_id = :user_id",
    "DELETE FROM users WHERE user_id = :user_id",
]


def check_row(row):
    """Problems with the jobs row's columns and types (empty list when all is well)."""
    problems = []
    if set(row) != set(EXPECTED_TYPES):
        problems.append(f"columns differ: {sorted(set(row) ^ set(EXPECTED_TYPES))}")
    for name, types in EXPECTED_TYPES.items():
        value = row.get(name)
        if type(value) not in types:  # exact: bool must not pass as int
            problems.append(f"{name}: {type(value).__name__} {value!r}, expected {types}")
        elif isinstance(value, datetime) and value.utcoffset() != timedelta(0):
            problems.append(f"{name}: not UTC ({value!r})")
    stages = row.get("stages")
    if isinstance(stages, list) and not all(isinstance(s, dict) for s in stages):
        problems.append(f"stages: not a list of objects ({stages!r})")
    return problems


def check_invariant(db, user_id):
    with db.transaction() as tx:
        [balance] = tx.execute(
            "SELECT available_cents, reserved_cents FROM balances WHERE user_id = :user_id",
            {"user_id": user_id},
        )
        ledger = tx.execute(
            "SELECT available_delta_cents, reserved_delta_cents FROM ledger "
            "WHERE user_id = :user_id",
            {"user_id": user_id},
        )
    sums = (
        sum(r["available_delta_cents"] for r in ledger),
        sum(r["reserved_delta_cents"] for r in ledger),
    )
    if sums != (balance["available_cents"], balance["reserved_cents"]):
        return [f"ledger sums {sums} differ from the balance {balance}"]
    return []


def smoke(db):
    user_id = f"smoke-{uuid.uuid4()}"
    job_id = str(uuid.uuid4())
    problems = []
    try:
        started = time.monotonic()
        billing.ensure_user(db, user_id, "smoke@localhost", 500)
        print(f"first call (includes any wake-up): {time.monotonic() - started:.1f} s")
        key = f"uploads/{user_id}/{job_id}.mp4"
        price = billing.reserve(db, user_id, job_id, key, "smoke.mp4", 27400)
        attempt = billing.claim(db, job_id)
        if attempt != 1:
            problems.append(f"claim returned {attempt!r}, expected 1")
        billing.set_stage(db, job_id, attempt, "downloading")
        verdict = billing.verify(db, job_id, attempt, 61000, 120)
        if verdict != billing.OK:
            problems.append(f"verify returned {verdict!r}")
        if not billing.settle_success(db, job_id):
            problems.append("settle_success returned False")
        refunded = str(uuid.uuid4())
        billing.reserve(db, user_id, refunded, f"uploads/{user_id}/{refunded}.mp4", None, 27400)
        attempt = billing.claim(db, refunded)
        steps = {
            "touch": billing.touch(db, refunded, attempt),
            "release_for_retry": billing.release_for_retry(db, refunded, attempt, None),
            "issue_refund": billing.issue_refund(db, refunded, "presign_failed", queued_before_s=0),
        }
        problems += [f"{name} returned False" for name, ok in steps.items() if not ok]
        balance = billing.get_balance(db, user_id)
        if balance != {"available_cents": 230, "reserved_cents": 0}:
            problems.append(f"balance {balance} after reserving {price}, capturing 270, refunding")
        problems += check_invariant(db, user_id)
        with db.transaction() as tx:
            [row] = tx.execute(
                "SELECT * FROM jobs WHERE job_id = CAST(:job_id AS uuid)", {"job_id": job_id}
            )
        problems += check_row(row)
    finally:
        with db.transaction() as tx:
            for statement in CLEANUP:
                tx.execute(statement, {"user_id": user_id})
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backend", required=True, choices=["data_api", "postgres"])
    args = parser.parse_args(argv)
    cfg = settings.load_settings()
    cfg.setdefault("db", {})["backend"] = args.backend
    problems = smoke(dbmod.from_config(cfg))
    for problem in problems:
        print(f"FAIL {problem}")
    print("PASS: billing round trip, ledger invariant and column types" if not problems else "")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
