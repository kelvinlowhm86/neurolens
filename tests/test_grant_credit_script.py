"""infra/grant_credit.py: the amount is typed in dollars, and the current balance is shown
before the operator confirms."""

import importlib.util
from pathlib import Path

import pytest
from conftest import ISSUER
from neurolens import billing

SCRIPT = Path(__file__).resolve().parent.parent / "infra" / "grant_credit.py"
_spec = importlib.util.spec_from_file_location("grant_credit_under_test", SCRIPT)
grant_credit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(grant_credit)


@pytest.mark.parametrize(
    ("text", "cents"), [("5", 500), ("2.5", 250), ("2.50", 250), ("0.01", 1), (" 12.34 ", 1234)]
)
def test_dollars_become_whole_cents(text, cents):
    assert grant_credit.dollars_to_cents(text) == cents


@pytest.mark.parametrize("text", ["0", "0.00", "-5", "2.505", "1e2", "nan", "$5", "5,00", ""])
def test_bad_or_zero_amounts_are_refused(text):
    with pytest.raises(ValueError):
        grant_credit.dollars_to_cents(text)


def _answers(*replies):
    prompts = []
    replies = iter(replies)

    def ask(prompt):
        prompts.append(prompt)
        return next(replies)

    return ask, prompts


def test_shows_the_balance_before_confirming_then_adds(db, pg, capsys):
    user_id = billing.sign_in(db, ISSUER, "sub-1", "ann@example.com", 0)
    billing.grant_credit(db, "ann@example.com", 300)
    ask, prompts = _answers("Ann@Example.com", "y")
    assert grant_credit.main(["2.50", "--backend", "postgres"], ask=ask, db=db) == 0
    out = capsys.readouterr().out
    assert "$3.00 available now" in out.splitlines()[0]
    assert "$2.50" in prompts[1]
    assert "Available now: $5.50" in out
    assert pg.balance(user_id) == (550, 0)


def test_not_confirmed_grants_nothing(db, pg):
    billing.sign_in(db, ISSUER, "sub-1", "ann@example.com", 0)
    before = pg.snapshot()
    ask, _ = _answers("ann@example.com", "n")
    assert grant_credit.main(["5", "--backend", "postgres"], ask=ask, db=db) == 1
    assert pg.snapshot() == before


def test_unknown_email_stops_before_asking_to_confirm(db, pg):
    before = pg.snapshot()
    ask, prompts = _answers("nobody@example.com")
    assert grant_credit.main(["5", "--backend", "postgres"], ask=ask, db=db) == 1
    assert len(prompts) == 1
    assert pg.snapshot() == before
