-- M3c (docs/M3c_spec.md §2): research consent, the per-video delete marker, and Stripe's test
-- events table. The ledger's `test_topup` kind stays: existing rows use it.
ALTER TABLE jobs ADD COLUMN research_consent BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE jobs ADD COLUMN deleted_at TIMESTAMPTZ;

DROP TABLE stripe_test_events;
