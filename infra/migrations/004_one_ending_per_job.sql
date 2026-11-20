-- A job ends at most once: one charge or one refund, ever.
CREATE UNIQUE INDEX ledger_one_ending_per_job ON ledger (job_id) WHERE kind IN ('capture', 'refund');
DROP TABLE refunds;
