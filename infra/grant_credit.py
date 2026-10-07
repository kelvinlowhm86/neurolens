"""Give a user credit by email (docs/M3b_spec.md §7a): for the team and study participants.

    python infra/grant_credit.py 500 --backend data_api   # Aurora (NEUROLENS_DB_* in .env)
    python infra/grant_credit.py 500 --backend postgres   # local PostgreSQL (NEUROLENS_DB_DSN)

Asks for the email at a prompt, so participants' emails never land in shell history, then asks
to confirm. The user must have signed in once (unknown emails stop with an error). The credit
and its `grant` ledger row are written in one transaction. Runs from a laptop with the
`neurolens` profile, never from the web app.
"""

import argparse
import sys

from neurolens import billing, settings
from neurolens import db as dbmod


def main(argv=None, ask=input):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cents", type=int, help="credit to add, in cents (positive)")
    parser.add_argument("--backend", required=True, choices=["data_api", "postgres"])
    args = parser.parse_args(argv)
    if args.cents <= 0:
        parser.error("cents must be positive")

    email = ask("Email of the user to credit: ").strip()
    if not email:
        print("No email given: nothing granted.", file=sys.stderr)
        return 1
    if ask(f"Grant {args.cents} cents (${args.cents / 100:.2f}) to {email}? [y/N] ").strip() != "y":
        print("Not confirmed: nothing granted.")
        return 1

    cfg = settings.load_settings()
    cfg.setdefault("db", {})["backend"] = args.backend
    db = dbmod.from_config(cfg)
    try:
        user_id = billing.grant_credit(db, email, args.cents)
    except LookupError:
        print(f"No user with email {email}: they must sign in once first.", file=sys.stderr)
        return 1
    balance = billing.get_balance(db, user_id)
    print(
        f"Granted {args.cents} cents to {email}. Available now: {balance['available_cents']} cents."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
