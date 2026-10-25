-- M3f 006 (docs/M3f_spec.md §5a-§5e, §6b): claims, research consent versions, video hashes,
-- curve snapshots and rewards, the copy-removal reason, and server-side sign-in sessions.
ALTER TABLE jobs ADD COLUMN claimed_at TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN consent_version TEXT;
ALTER TABLE jobs ADD COLUMN consented_at TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN video_sha256 TEXT;
ALTER TABLE jobs ADD COLUMN curve_rewarded_at TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN dataset_purged_at TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN dataset_removed_reason TEXT;
ALTER TABLE jobs ADD CONSTRAINT jobs_dataset_removed_reason_check
  CHECK (dataset_removed_reason IS NULL OR dataset_removed_reason IN ('no_curve', 'stopped'));
UPDATE jobs SET dataset_removed_reason = 'no_curve' WHERE dataset_removed_at IS NOT NULL;

CREATE TABLE retention_snapshots (
  snapshot_id      UUID PRIMARY KEY,
  job_id           UUID NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
  added_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  source           TEXT NOT NULL DEFAULT 'youtube_api',
  raw_key          TEXT,
  csv_key          TEXT,
  curve_sha256     TEXT NOT NULL UNIQUE,
  points           INTEGER,
  quality_flags    TEXT[],
  views            TEXT,
  published        TEXT,
  promoted         BOOLEAN,
  rewarded_cents   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX retention_snapshots_job ON retention_snapshots (job_id);

ALTER TABLE ledger DROP CONSTRAINT ledger_kind_check;
ALTER TABLE ledger ADD CONSTRAINT ledger_kind_check
  CHECK (kind IN ('starter','reserve','adjust','capture','release','refund','test_topup','grant','curve_reward'));
CREATE UNIQUE INDEX ledger_one_curve_reward_per_job ON ledger (job_id) WHERE kind = 'curve_reward';

CREATE TABLE sessions (
  session_hash   TEXT PRIMARY KEY,
  user_id        TEXT NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX sessions_user ON sessions (user_id);
