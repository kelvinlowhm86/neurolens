"""
Billing-integrity rules, written once as SQL and run by two callers:

  * AWS: the `sla_reaper` / `dlq_refund` Lambdas via the RDS Data API (`:name` parameters)
  * local: `python -m neurolens_common.reaper_loop` via psycopg (`as_psycopg()` converts params)

Each statement is a single SELECT that calls close_job(), which releases the hold and is a no-op
on jobs that are already final — so every rule is safe to run repeatedly and concurrently.
"""

import re

# name → statement returning one row: {"closed": <number of jobs closed>}
REAPER_RULES: dict[str, str] = {
    # Upload URL minted but nothing arrived (URL expires after 15 min).
    "expire_uploads": """
        SELECT count(*) FILTER (WHERE close_job(id, 'EXPIRED', 'UPLOAD_TIMEOUT',
                                'No upload received within 30 minutes')) AS closed
          FROM jobs
         WHERE status = 'AWAITING_UPLOAD' AND created_at < now() - interval '30 minutes'""",
    # Worker stopped heart-beating and the job already used its retries.
    "stuck_processing": """
        SELECT count(*) FILTER (WHERE close_job(id, 'FAILED', 'STUCK',
                                'Worker stopped responding')) AS closed
          FROM jobs
         WHERE status = 'PROCESSING' AND attempts >= 2
           AND heartbeat_at < now() - interval '10 minutes'""",
    # Uploaded but never picked up — no GPU capacity within the SLA.
    "sla_breach": """
        SELECT count(*) FILTER (WHERE close_job(id, 'FAILED', 'SLA_TIMEOUT',
                                'No GPU became available within 60 minutes')) AS closed
          FROM jobs
         WHERE status = 'UPLOADED' AND created_at < now() - interval '60 minutes'""",
    # Backstop: nothing should stay unfinished for two hours, whatever happened to its message.
    "abandoned": """
        SELECT count(*) FILTER (WHERE close_job(id, 'FAILED', 'ABANDONED',
                                'Job did not finish within 2 hours')) AS closed
          FROM jobs
         WHERE status IN ('UPLOADED', 'PROCESSING') AND created_at < now() - interval '2 hours'""",
    # The AI report never arrived (worker lost mid-report). Charts are unaffected; nothing to refund.
    "stale_reports": """
        WITH failed AS (
            UPDATE jobs SET report_status = 'FAILED', report_error = 'Report generation timed out'
             WHERE report_status = 'PENDING' AND completed_at < now() - interval '15 minutes'
         RETURNING 1)
        SELECT count(*) AS closed FROM failed""",
}

# A message reached the DLQ: two genuine processing failures. Parameter: :job_id (uuid as text).
DLQ_REFUND = """
    SELECT close_job(CAST(:job_id AS uuid), 'FAILED', 'RETRIES_EXHAUSTED',
                     'Processing failed twice; credits refunded') AS closed"""


def as_psycopg(sql: str) -> str:
    """Convert Data API `:name` parameters to psycopg `%(name)s` (leaves `::casts` alone)."""
    return re.sub(r"(?<![:\w]):([A-Za-z_]\w*)", r"%(\1)s", sql)


def run_rules(conn) -> dict[str, int]:
    """Run every reaper rule via psycopg. Returns jobs closed per rule."""
    return {name: conn.execute(sql).fetchone()["closed"] for name, sql in REAPER_RULES.items()}


def refund_from_dlq(conn, job_id: str) -> bool:
    return conn.execute(as_psycopg(DLQ_REFUND), {"job_id": job_id}).fetchone()["closed"]
