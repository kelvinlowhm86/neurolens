"""
Apply migrations/*.sql in filename order, each once, each in its own transaction.

Runs at web-container startup. Several tasks may start together, so a Postgres advisory lock
makes sure only one of them applies migrations; the others wait and then find nothing to do.

    python -m neurolens_common.migrate
"""

import logging
from pathlib import Path

import psycopg

from .config import get_settings

logger = logging.getLogger("neurolens.migrate")

LOCK_KEY = 5224  # any constant shared by all NeuroLens processes


def apply(url: str | None = None, migrations_dir: Path | None = None) -> list[str]:
    """Apply pending migrations. Returns the versions applied by this call."""
    if url is None or migrations_dir is None:
        cfg = get_settings()
        url = url or cfg.database_url
        migrations_dir = migrations_dir or cfg.migrations_dir
    files = sorted(migrations_dir.glob("*.sql"))
    if not files:
        raise FileNotFoundError(f"No migrations found in {migrations_dir.resolve()}")

    applied = []
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
            for f in files:
                if f.stem in done:
                    continue
                logger.info("Applying migration %s", f.name)
                with conn.transaction():
                    conn.execute(f.read_text())
                    conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (f.stem,))
                applied.append(f.stem)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
    return applied


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print("applied:", apply() or "nothing (up to date)")
