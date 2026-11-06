"""Screen early-access requests by email (docs/M3k_spec.md §9).

    python infra/access_requests.py list --backend data_api
    python infra/access_requests.py decline --backend postgres

`list` prints the waiting requests, oldest first. `decline` asks for the requester's email,
shows the request, then declines it only after a "y" at the prompt. Granting stays in
`infra/grant_credit.py`, which also marks the request as granted. Runs from a laptop with the
`neurolens` profile, never from the web app.
"""

import argparse
import sys

from neurolens import access, settings
from neurolens import db as dbmod


def _when(created_at):
    return created_at.strftime("%Y-%m-%d %H:%M UTC")


def _show(row):
    note = row["note"] or "-"
    print(
        f"{_when(row['created_at'])}  {row['email']}  {row['platform']}  "
        f"{row['channel_url']}  {note}"
    )


def _list(db):
    rows = access.waiting_requests(db)
    if not rows:
        print("No waiting access requests.")
        return 0
    for row in rows:
        _show(row)
    return 0


def _decline(db, ask):
    email = ask("Email of the requester to decline: ").strip()
    if not email:
        print("No email given: nothing declined.", file=sys.stderr)
        return 1
    waiting = [row for row in access.waiting_requests(db) if row["email"].lower() == email.lower()]
    if not waiting:
        print(f"No waiting access request from {email}.", file=sys.stderr)
        return 1
    _show(waiting[0])
    if ask("Decline this request? [y/N] ").strip() != "y":
        print("Not confirmed: nothing declined.")
        return 1
    access.decline(db, email)
    print(f"Declined the access request from {email}.")
    return 0


def main(argv=None, ask=input, db=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["list", "decline"])
    parser.add_argument("--backend", required=True, choices=["data_api", "postgres"])
    args = parser.parse_args(argv)

    if db is None:
        cfg = settings.load_settings()
        cfg.setdefault("db", {})["backend"] = args.backend
        db = dbmod.from_config(cfg)
    if args.command == "list":
        return _list(db)
    return _decline(db, ask)


if __name__ == "__main__":
    sys.exit(main())
