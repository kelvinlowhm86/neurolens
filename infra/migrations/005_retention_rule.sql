-- A consented copy without a retention curve is deleted after 90 days.
ALTER TABLE jobs ADD COLUMN retention_added_at TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN dataset_removed_at TIMESTAMPTZ;
