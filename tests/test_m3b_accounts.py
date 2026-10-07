"""M3b accounts and credit in neurolens.billing (real PostgreSQL 16 through NEUROLENS_TEST_DSN).
Written first from docs/M3b_spec.md §2c, §7a, §7b and §8: `sign_in`'s linking rule (one user per
verified email, our own UUIDs, no ledger row for 0 starter credit), two simultaneous first
sign-ins ending as one user, `ensure_user` without a starter row for 0, `grant_credit`,
`credit_test_topup` crediting each Stripe event and session once, `list_jobs`, and the 002
migration. The Data API backend is checked on Aurora by infra/check_aurora.py (§7c).

The M3a ledger invariants are checked after every test by the shared fixture.
"""

import threading
import uuid

import pytest
from conftest import ISSUER, STARTER_CENTS
from neurolens import billing, storage

GOOGLE = "https://accounts.google.example"  # a second issuer, for identities across providers


def users(pg):
    return pg.rows("SELECT user_id, email FROM users ORDER BY created_at")


def identities(pg):
    return pg.rows("SELECT issuer, subject, user_id FROM identities ORDER BY created_at")


def reserve_job(db, user_id, seconds=27.4):
    job_id = str(uuid.uuid4())
    key = storage.object_key(user_id, job_id, "video/mp4")
    billing.reserve(db, user_id, job_id, key, f"{job_id}.mp4", round(seconds * 1000))
    return job_id


# ---------------------------------------------------------------- the 002 migration


def test_migration_adds_identities_and_stripe_test_events(pg):
    names = {
        r["table_name"]
        for r in pg.rows(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
        )
    }
    assert {"identities", "stripe_test_events"} <= names


def test_emails_are_unique_whatever_their_letter_case(db):
    billing.ensure_user(db, "u-1", "Ann@Example.com", 0)
    with pytest.raises(Exception):  # noqa: B017 - the database refuses it
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO users (user_id, email) VALUES (:user_id, :email)",
                {"user_id": "u-2", "email": "ann@example.COM"},
            )


def test_the_ledger_accepts_a_grant_row(db, pg):
    billing.ensure_user(db, "u-1", "u1@example.com", 0)
    with db.transaction() as tx:
        tx.execute(
            "UPDATE balances SET available_cents = available_cents + :c WHERE user_id = :u",
            {"c": 100, "u": "u-1"},
        )
        tx.execute(
            "INSERT INTO ledger (user_id, kind, available_delta_cents, reserved_delta_cents) "
            "VALUES (:u, :kind, :c, :zero)",
            {"u": "u-1", "kind": "grant", "c": 100, "zero": 0},
        )
    assert pg.kinds("u-1") == ["grant"]


# ---------------------------------------------------------------- sign_in: new users


def test_a_new_sign_in_creates_one_user_with_a_uuid_and_the_starter_balance(db, pg):
    user_id = billing.sign_in(db, ISSUER, "sub-1", "ann@example.com", STARTER_CENTS)
    assert uuid.UUID(user_id).version == 4
    assert [u["user_id"] for u in users(pg)] == [user_id]
    assert identities(pg) == [{"issuer": ISSUER, "subject": "sub-1", "user_id": user_id}]
    assert pg.balance(user_id) == (STARTER_CENTS, 0)
    assert pg.kinds(user_id) == ["starter"]


def test_a_new_sign_in_with_zero_starter_credit_writes_no_ledger_row(db, pg):
    user_id = billing.sign_in(db, ISSUER, "sub-1", "ann@example.com", 0)
    assert pg.balance(user_id) == (0, 0)
    assert pg.ledger(user_id) == []
    assert billing.get_balance(db, user_id) == {"available_cents": 0, "reserved_cents": 0}


def test_each_new_user_gets_its_own_random_id(db):
    a = billing.sign_in(db, ISSUER, "sub-1", "a@example.com", 0)
    b = billing.sign_in(db, ISSUER, "sub-2", "b@example.com", 0)
    assert a != b
    assert "sub-1" not in a and "sub-2" not in b  # our own IDs, not the provider's


# ---------------------------------------------------------------- sign_in: returning and linking


def test_the_same_identity_returns_the_same_user_and_no_more_credit(db, pg):
    first = billing.sign_in(db, ISSUER, "sub-1", "ann@example.com", STARTER_CENTS)
    again = billing.sign_in(db, ISSUER, "sub-1", "ann@example.com", STARTER_CENTS)
    assert again == first
    assert len(users(pg)) == 1
    assert len(identities(pg)) == 1
    assert pg.kinds(first) == ["starter"]


def test_the_same_identity_finds_its_user_even_with_another_email(db, pg):
    """The identity row decides first; the email matters only for a sign-in not seen before."""
    first = billing.sign_in(db, ISSUER, "sub-1", "ann@example.com", 0)
    assert billing.sign_in(db, ISSUER, "sub-1", "ann.new@example.com", 0) == first
    assert len(users(pg)) == 1


@pytest.mark.parametrize("second_email", ["ann@example.com", "ANN@example.com", "Ann@Example.Com"])
def test_another_identity_with_the_same_email_links_to_the_same_user(db, pg, second_email):
    first = billing.sign_in(db, GOOGLE, "google-1", "Ann@example.com", STARTER_CENTS)
    second = billing.sign_in(db, ISSUER, "cognito-1", second_email, STARTER_CENTS)
    assert second == first
    assert len(users(pg)) == 1
    assert {(i["issuer"], i["subject"]) for i in identities(pg)} == {
        (GOOGLE, "google-1"),
        (ISSUER, "cognito-1"),
    }
    assert {i["user_id"] for i in identities(pg)} == {first}
    assert pg.kinds(first) == ["starter"]  # linking grants nothing
    assert billing.sign_in(db, ISSUER, "cognito-1", second_email, STARTER_CENTS) == first


def test_a_linked_account_keeps_its_balance(db, pg):
    first = billing.sign_in(db, GOOGLE, "google-1", "ann@example.com", STARTER_CENTS)
    reserve_job(db, first)
    second = billing.sign_in(db, ISSUER, "cognito-1", "ann@example.com", STARTER_CENTS)
    assert second == first
    assert pg.balance(first) == (STARTER_CENTS - 90, 90)


def test_a_different_email_creates_a_different_user(db, pg):
    a = billing.sign_in(db, ISSUER, "sub-1", "ann@example.com", 0)
    b = billing.sign_in(db, ISSUER, "sub-2", "bob@example.com", 0)
    assert a != b
    assert len(users(pg)) == 2


def test_a_sign_in_links_to_the_development_style_user_with_that_email(db, pg):
    """A user created by ensure_user (no identity yet) is found by email like any other."""
    billing.ensure_user(db, "existing-user", "carol@example.com", STARTER_CENTS)
    assert billing.sign_in(db, ISSUER, "sub-9", "Carol@example.com", STARTER_CENTS) == (
        "existing-user"
    )
    assert pg.kinds("existing-user") == ["starter"]


@pytest.mark.parametrize("same_identity", [False, True], ids=["two_providers", "one_identity"])
def test_two_simultaneous_first_sign_ins_with_one_email_make_one_user(
    db, pg, row_lock, same_identity
):
    """Deterministic (M3a §10's method): a third connection locks `users` against inserts, both
    sign-ins find nothing and wait at their insert, then the lock is released. The loser's
    `ON CONFLICT DO NOTHING` inserts nothing, and it reads the winner's row by email in the same
    transaction (a caught unique-violation would abort the transaction instead)."""
    lock = row_lock()
    lock.execute("LOCK TABLE users IN EXCLUSIVE MODE")
    subjects = ["sub-a", "sub-a"] if same_identity else ["sub-a", "sub-b"]
    emails = ["dana@example.com", "Dana@Example.com"]
    results, errors = [None, None], []

    def run(i):
        try:
            results[i] = billing.sign_in(db, ISSUER, subjects[i], emails[i], STARTER_CENTS)
        except Exception as err:  # reported below
            errors.append(err)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    lock.wait_for_blocked(2)
    lock.release()
    for t in threads:
        t.join(timeout=20)
    assert not errors, errors
    assert results[0] == results[1] and results[0] is not None
    assert len(users(pg)) == 1
    assert {i["user_id"] for i in identities(pg)} == {results[0]}
    assert len(identities(pg)) == (1 if same_identity else 2)
    assert pg.balance(results[0]) == (STARTER_CENTS, 0)
    assert pg.kinds(results[0]) == ["starter"]


# ---------------------------------------------------------------- ensure_user (dev mode)


def test_ensure_user_with_zero_starter_credit_writes_no_ledger_row(db, pg):
    billing.ensure_user(db, "dev-user", "dev@localhost", 0)
    assert pg.balance("dev-user") == (0, 0)
    assert pg.ledger("dev-user") == []


def test_ensure_user_with_starter_credit_still_writes_one_starter_row(db, pg):
    billing.ensure_user(db, "dev-user", "dev@localhost", STARTER_CENTS)
    billing.ensure_user(db, "dev-user", "dev@localhost", STARTER_CENTS)
    assert pg.balance("dev-user") == (STARTER_CENTS, 0)
    assert pg.kinds("dev-user") == ["starter"]


# ---------------------------------------------------------------- grant_credit (§7a)


def test_grant_credit_adds_exactly_the_amount_with_a_grant_row(db, pg):
    user_id = billing.sign_in(db, ISSUER, "sub-1", "ann@example.com", 0)
    assert billing.grant_credit(db, "ann@example.com", 750) == user_id
    assert pg.balance(user_id) == (750, 0)
    [row] = pg.ledger(user_id)
    assert row["kind"] == "grant"
    assert (row["available_delta_cents"], row["reserved_delta_cents"]) == (750, 0)
    assert row["job_id"] is None


def test_grant_credit_adds_to_an_existing_balance_and_reservation(db, pg):
    billing.ensure_user(db, "u-1", "u1@example.com", STARTER_CENTS)
    reserve_job(db, "u-1")
    billing.grant_credit(db, "u1@example.com", 100)
    billing.grant_credit(db, "u1@example.com", 1)
    assert pg.balance("u-1") == (STARTER_CENTS - 90 + 101, 90)
    assert pg.kinds("u-1") == ["starter", "reserve", "grant", "grant"]


@pytest.mark.parametrize("cents", [0, -1, -500])
def test_grant_credit_refuses_zero_or_negative_cents(db, pg, cents):
    billing.ensure_user(db, "u-1", "u1@example.com", STARTER_CENTS)
    before = pg.snapshot()
    with pytest.raises(ValueError):
        billing.grant_credit(db, "u1@example.com", cents)
    assert pg.snapshot() == before


def test_grant_credit_to_an_unknown_email_raises_lookup_error(db, pg):
    billing.ensure_user(db, "u-1", "u1@example.com", STARTER_CENTS)
    before = pg.snapshot()
    with pytest.raises(LookupError):
        billing.grant_credit(db, "nobody@example.com", 100)
    assert pg.snapshot() == before


# ---------------------------------------------------------------- credit_test_topup (§7b)


def stripe_events(pg):
    return pg.rows("SELECT event_id, session_id, user_id, cents FROM stripe_test_events")


def test_a_test_topup_credits_the_cents_with_a_test_topup_row(db, pg):
    user_id = billing.sign_in(db, ISSUER, "sub-1", "team@example.com", 0)
    assert billing.credit_test_topup(db, "evt_1", "cs_test_1", user_id, 500) is True
    assert pg.balance(user_id) == (500, 0)
    [row] = pg.ledger(user_id)
    assert row["kind"] == "test_topup"
    assert (row["available_delta_cents"], row["reserved_delta_cents"]) == (500, 0)
    assert stripe_events(pg) == [
        {"event_id": "evt_1", "session_id": "cs_test_1", "user_id": user_id, "cents": 500}
    ]


def test_the_same_event_twice_credits_once(db, pg):
    user_id = billing.sign_in(db, ISSUER, "sub-1", "team@example.com", 0)
    assert billing.credit_test_topup(db, "evt_1", "cs_test_1", user_id, 500) is True
    assert billing.credit_test_topup(db, "evt_1", "cs_test_1", user_id, 500) is False
    assert pg.balance(user_id) == (500, 0)
    assert pg.kinds(user_id) == ["test_topup"]


def test_the_same_session_under_another_event_credits_once(db, pg):
    user_id = billing.sign_in(db, ISSUER, "sub-1", "team@example.com", 0)
    assert billing.credit_test_topup(db, "evt_1", "cs_test_1", user_id, 500) is True
    assert billing.credit_test_topup(db, "evt_2", "cs_test_1", user_id, 500) is False
    assert pg.balance(user_id) == (500, 0)
    assert len(stripe_events(pg)) == 1


def test_two_different_sessions_both_credit(db, pg):
    user_id = billing.sign_in(db, ISSUER, "sub-1", "team@example.com", 0)
    assert billing.credit_test_topup(db, "evt_1", "cs_test_1", user_id, 500) is True
    assert billing.credit_test_topup(db, "evt_2", "cs_test_2", user_id, 1000) is True
    assert pg.balance(user_id) == (1500, 0)
    assert pg.kinds(user_id) == ["test_topup", "test_topup"]


def test_two_simultaneous_deliveries_of_one_event_credit_once(db, pg, row_lock):
    user_id = billing.sign_in(db, ISSUER, "sub-1", "team@example.com", 0)
    lock = row_lock()
    lock.execute("LOCK TABLE stripe_test_events IN EXCLUSIVE MODE")
    results, errors = [], []

    def run():
        try:
            results.append(billing.credit_test_topup(db, "evt_1", "cs_test_1", user_id, 500))
        except Exception as err:
            errors.append(err)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    lock.wait_for_blocked(2)
    lock.release()
    for t in threads:
        t.join(timeout=20)
    assert not errors, errors
    assert sorted(results) == [False, True]
    assert pg.balance(user_id) == (500, 0)


# ---------------------------------------------------------------- list_jobs (§4a)


def test_list_jobs_returns_only_the_users_jobs_newest_first(db, pg):
    billing.ensure_user(db, "u-1", "u1@example.com", 10_000)
    billing.ensure_user(db, "u-2", "u2@example.com", 10_000)
    old, middle, new = (reserve_job(db, "u-1") for _ in range(3))
    reserve_job(db, "u-2")
    pg.age(old, created_s=300)
    pg.age(middle, created_s=200)
    pg.age(new, created_s=100)
    jobs = billing.list_jobs(db, "u-1", 50)
    assert [j["job_id"] for j in jobs] == [new, middle, old]
    assert [j["job_id"] for j in billing.list_jobs(db, "u-1", 2)] == [new, middle]
    assert billing.list_jobs(db, "nobody", 50) == []
