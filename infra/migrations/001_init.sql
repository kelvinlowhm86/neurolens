CREATE TABLE users (
  user_id     TEXT PRIMARY KEY,               -- Google 'sub' from M3b; the dev user in M3a
  email       TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE balances (
  user_id          TEXT PRIMARY KEY REFERENCES users(user_id),
  available_cents  BIGINT NOT NULL CHECK (available_cents >= 0),
  reserved_cents   BIGINT NOT NULL CHECK (reserved_cents >= 0)
);

CREATE TABLE jobs (
  job_id                UUID PRIMARY KEY,         -- same UUID as the S3 upload key
  user_id               TEXT NOT NULL REFERENCES users(user_id),
  object_key            TEXT NOT NULL,
  filename              TEXT,                     -- display only
  status                TEXT NOT NULL CHECK (status IN ('queued','processing','done','failed')),
  stage                 TEXT,
  stages                JSONB NOT NULL DEFAULT '[]',
  attempt               INTEGER NOT NULL DEFAULT 0,
  client_duration_ms    INTEGER NOT NULL,
  verified_duration_ms  INTEGER,
  reserved_cents        BIGINT NOT NULL,
  captured_cents        BIGINT,
  error_code            TEXT,
  error_message         TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX jobs_user_created ON jobs (user_id, created_at DESC);
CREATE INDEX jobs_status_updated ON jobs (status, updated_at);

CREATE TABLE ledger (
  id                     BIGSERIAL PRIMARY KEY,
  user_id                TEXT NOT NULL REFERENCES users(user_id),
  job_id                 UUID REFERENCES jobs(job_id),
  kind                   TEXT NOT NULL CHECK (kind IN ('starter','reserve','adjust','capture','release','refund','test_topup')),
  available_delta_cents  BIGINT NOT NULL,
  reserved_delta_cents   BIGINT NOT NULL,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE refunds (
  job_id      UUID PRIMARY KEY REFERENCES jobs(job_id),   -- at most one refund per job, ever
  reason      TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
