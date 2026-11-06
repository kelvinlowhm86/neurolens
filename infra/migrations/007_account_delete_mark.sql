-- M3c 007 (docs/M3c_spec.md §5): account deletion marks the account first, so the email is free
-- at once and the reaper can finish the purge before the rows are removed.
ALTER TABLE users ADD COLUMN deleted_at TIMESTAMPTZ;
