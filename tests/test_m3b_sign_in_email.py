"""M3b: `sign_in` keeps `users.email` current (real PostgreSQL 16 through NEUROLENS_TEST_DSN).
Written first from docs/M3b_spec.md §2c "Account linking rule" and the "Accounts" test bullet:
a known `(issuer, subject)` signing in with a different verified email (compared lowercased)
updates the stored email in the same transaction, so an email the user has given up no longer
links strangers to the account. A change of letter case only leaves it alone; a new email that
another user already holds leaves both users unchanged and logs a warning (merging is out of scope).

The M3a ledger invariants are checked after every test by the shared fixture.
"""

import logging

from conftest import ISSUER, STARTER_CENTS
from neurolens import billing


def stored_email(pg, user_id):
    [row] = pg.rows("SELECT email FROM users WHERE user_id = %s", user_id)
    return row["email"]


def accounts(pg):
    """Users and identities as they stand (pg.snapshot covers only money and jobs)."""
    return {
        "users": pg.rows("SELECT * FROM users ORDER BY user_id"),
        "identities": pg.rows("SELECT * FROM identities ORDER BY issuer, subject"),
    }


def user_count(pg):
    return pg.rows("SELECT count(*) AS n FROM users")[0]["n"]


def test_a_known_identity_with_a_new_email_updates_the_stored_email(db, pg):
    ann = billing.sign_in(db, ISSUER, "sub-1", "ann.old@example.com", STARTER_CENTS)
    assert billing.sign_in(db, ISSUER, "sub-1", "ann.new@example.com", STARTER_CENTS) == ann
    assert billing.get_email(db, ann) == "ann.new@example.com"
    assert stored_email(pg, ann) == "ann.new@example.com"
    assert user_count(pg) == 1
    assert pg.kinds(ann) == ["starter"]  # an email change moves no credit


def test_after_the_update_the_old_email_no_longer_links_to_the_account(db, pg):
    """Someone who now controls the given-up address (a recycled university email) signing in
    under a new subject gets a fresh account, not Ann's."""
    ann = billing.sign_in(db, ISSUER, "sub-1", "ann.old@example.com", 0)
    billing.sign_in(db, ISSUER, "sub-1", "ann.new@example.com", 0)
    stranger = billing.sign_in(db, ISSUER, "sub-stranger", "ann.old@example.com", 0)
    assert stranger != ann
    assert billing.get_email(db, stranger) == "ann.old@example.com"
    assert billing.get_email(db, ann) == "ann.new@example.com"
    assert user_count(pg) == 2


def test_after_the_update_the_new_email_links_to_the_account(db, pg):
    ann = billing.sign_in(db, ISSUER, "sub-1", "ann.old@example.com", 0)
    billing.sign_in(db, ISSUER, "sub-1", "ann.new@example.com", 0)
    assert billing.sign_in(db, ISSUER, "sub-2", "Ann.New@Example.com", 0) == ann
    assert user_count(pg) == 1


def test_a_change_of_letter_case_only_leaves_the_stored_email_unchanged(db, pg):
    ann = billing.sign_in(db, ISSUER, "sub-1", "ann@example.com", 0)
    assert billing.sign_in(db, ISSUER, "sub-1", "ANN@Example.COM", 0) == ann
    assert stored_email(pg, ann) == "ann@example.com"
    assert billing.get_email(db, ann) == "ann@example.com"


def test_a_new_email_held_by_another_user_changes_nothing_and_logs_a_warning(db, pg, caplog):
    """The unique index on lower(email) would refuse the update; sign_in must not let that error
    abort its transaction or escape. Both users keep their emails, the identity still gets its
    own user, and the clash is logged (known limitation: no account merging)."""
    ann = billing.sign_in(db, ISSUER, "sub-ann", "ann@example.com", STARTER_CENTS)
    bob = billing.sign_in(db, ISSUER, "sub-bob", "bob@example.com", STARTER_CENTS)
    before = (pg.snapshot(), accounts(pg))
    with caplog.at_level(logging.WARNING, logger="neurolens"):
        assert billing.sign_in(db, ISSUER, "sub-ann", "Bob@Example.com", STARTER_CENTS) == ann
    assert stored_email(pg, ann) == "ann@example.com"
    assert stored_email(pg, bob) == "bob@example.com"
    assert (pg.snapshot(), accounts(pg)) == before
    assert any(r.name == "neurolens" and r.levelno == logging.WARNING for r in caplog.records), (
        "the email clash must be logged as a warning on the 'neurolens' logger"
    )
    # the database connection is still usable afterwards (no half-aborted transaction left)
    assert billing.sign_in(db, ISSUER, "sub-bob", "bob@example.com", STARTER_CENTS) == bob
