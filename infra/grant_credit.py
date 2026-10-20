"""Give a user credit by email (docs/M3b_spec.md §7a): for the team and study participants.

    python infra/grant_credit.py 5 --backend data_api     # $5.00, Aurora (NEUROLENS_DB_* in .env)
    python infra/grant_credit.py 2.50 --backend postgres  # $2.50, local Postgres (NEUROLENS_DB_DSN)

The amount is in dollars, with at most two decimals. Asks for the email at a prompt, so
participants' emails never land in shell history, shows the user's current balance, then asks
to confirm. The credit is added to what they have. The user must have signed in once (unknown
emails stop with an error). The credit and its `grant` ledger row are written in one
transaction. Runs from a laptop with the `neurolens` profile, never from the web app.
"""

import argparse
import re
import sys

from neurolens import billing, settings
from neurolens import db as dbmod


def dollars_to_cents(text):
    """'5' -> 500, '2.5' -> 250, '2.50' -> 250. Anything else, or zero, raises ValueError."""
    match = re.fullmatch(r"(\d+)(?:\.(\d{1,2}))?", text.strip())
    if not match:
        raise ValueError(f"not a dollar amount with at most two decimals: {text!r}")
    cents = int(match[1]) * 100 + int((match[2] or "0").ljust(2, "0"))
    if cents <= 0:
        raise ValueError("the amount must be more than $0")
    return cents


def _dollars(cents):
    return f"${cents / 100:.2f}"


def main(argv=None, ask=input, db=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dollars", help="credit to add, in dollars, e.g. 5 or 2.50")
    parser.add_argument("--backend", required=True, choices=["data_api", "postgres"])
    args = parser.parse_args(argv)
    try:
        cents = dollars_to_cents(args.dollars)
    except ValueError as exc:
        parser.error(str(exc))

    email = ask("Email of the user to credit: ").strip()
    if not email:
        print("No email given: nothing granted.", file=sys.stderr)
        return 1

    if db is None:
        cfg = settings.load_settings()
        cfg.setdefault("db", {})["backend"] = args.backend
        db = dbmod.from_config(cfg)
    user_id = billing.user_by_email(db, email)
    if user_id is None:
        print(f"No user with email {email}: they must sign in once first.", file=sys.stderr)
        return 1
    before = billing.get_balance(db, user_id)["available_cents"]
    print(f"{email} has {_dollars(before)} available now.")
    if ask(f"Add {_dollars(cents)} to {email}? [y/N] ").strip() != "y":
        print("Not confirmed: nothing granted.")
        return 1

    billing.grant_credit(db, email, cents)
    after = billing.get_balance(db, user_id)["available_cents"]
    print(f"Added {_dollars(cents)} to {email}. Available now: {_dollars(after)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
