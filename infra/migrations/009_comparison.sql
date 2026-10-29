CREATE TABLE comparisons (
  job_a      UUID NOT NULL REFERENCES jobs(job_id),
  job_b      UUID NOT NULL REFERENCES jobs(job_id),
  state      TEXT NOT NULL CHECK (state IN ('writing', 'ready', 'failed')),
  tries      INTEGER NOT NULL,
  started_at TIMESTAMPTZ NOT NULL,
  error      TEXT,               -- a short code, never shown verbatim to the user
  PRIMARY KEY (job_a, job_b),
  CHECK (job_a < job_b)
);
CREATE INDEX comparisons_job_b ON comparisons (job_b);
