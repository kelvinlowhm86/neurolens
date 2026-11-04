-- M3k 012 (docs/M3k_spec.md §3): one screened request for free minutes per account.
CREATE TABLE access_requests (
  user_id     TEXT PRIMARY KEY REFERENCES users(user_id),
  channel_url TEXT NOT NULL,
  platform    TEXT NOT NULL CHECK (platform IN ('youtube','facebook','tiktok','instagram')),
  note        TEXT,
  status      TEXT NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting','granted','declined')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at  TIMESTAMPTZ
);
