-- M3b (docs/M3b_spec.md §2c, §7, §8): our own user IDs with a sign-in table, one user per
-- verified email, credit grants and Stripe test-mode top-ups.
COMMENT ON COLUMN users.user_id IS 'our own UUID (text)';

CREATE TABLE identities (
  issuer      TEXT NOT NULL,
  subject     TEXT NOT NULL,
  user_id     TEXT NOT NULL REFERENCES users(user_id),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (issuer, subject)
);

CREATE UNIQUE INDEX users_email_lower ON users (lower(email));

ALTER TABLE ledger DROP CONSTRAINT ledger_kind_check;

ALTER TABLE ledger ADD CONSTRAINT ledger_kind_check
  CHECK (kind IN ('starter','reserve','adjust','capture','release','refund','test_topup','grant'));

CREATE TABLE stripe_test_events (
  event_id    TEXT PRIMARY KEY,
  session_id  TEXT UNIQUE NOT NULL,
  user_id     TEXT NOT NULL REFERENCES users(user_id),
  cents       BIGINT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
