"""Apply infra/migrations/*.sql in order, once each (M3a §3c).

    python infra/apply_schema.py --backend data_api   # Aurora (NEUROLENS_DB_* in .env)
    python infra/apply_schema.py --backend postgres   # local PostgreSQL (NEUROLENS_DB_DSN in .env)

Each applied file is recorded in `schema_migrations`, so a second run changes nothing. A file is
applied in one transaction with its record, so a failing migration leaves no half-built schema.
The Data API runs one statement per call, so a statement ends with `;` at the end of a line, and
migrations hold only plain statements (no functions, no DO blocks). Statements are sent as-is,
not through the application's SQL checks (CHECK constraints need literal values).
"""

import argparse
import sys
from pathlib import Path

from neurolens import db as dbmod
from neurolens import settings

MIGRATIONS = Path(__file__).resolve().parent / "migrations"

CREATE_TRACKING = (
    "CREATE TABLE IF NOT EXISTS schema_migrations ("
    "name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
)


def split_statements(text, name="migration"):
    """Statements in file order; each ends with `;` at the end of a line."""
    statements, current = [], []
    for line in text.splitlines():
        current.append(line)
        if line.rstrip().endswith(";"):
            statements.append("\n".join(current).strip())
            current = []
    if "\n".join(current).strip():
        raise ValueError(f"{name} ends with text after its last `;`")
    return statements


def apply_migrations(database, migrations=MIGRATIONS):
    """Apply every file not yet recorded. Returns the names applied this run."""
    paths = sorted(migrations.glob("*.sql"))
    if not paths:
        raise FileNotFoundError(f"no migrations found in {migrations}")
    with database.transaction() as tx:
        tx.execute_unchecked(CREATE_TRACKING)
    applied = []
    for path in paths:
        statements = split_statements(path.read_text(), path.name)
        with database.transaction() as tx:
            done = tx.execute(
                "SELECT name FROM schema_migrations WHERE name = :name", {"name": path.name}
            )
            if done:
                continue
            for statement in statements:
                tx.execute_unchecked(statement)
            tx.execute("INSERT INTO schema_migrations (name) VALUES (:name)", {"name": path.name})
        applied.append(path.name)
    return applied


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backend", required=True, choices=["data_api", "postgres"])
    args = parser.parse_args(argv)
    cfg = settings.load_settings()
    cfg.setdefault("db", {})["backend"] = args.backend
    applied = apply_migrations(dbmod.from_config(cfg))
    print(f"applied: {', '.join(applied)}" if applied else "already up to date: nothing applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
