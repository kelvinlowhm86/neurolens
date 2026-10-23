-- M3e 005 (docs/M3e_spec.md §7): a consented copy without a retention curve is deleted after 60 days.
ALTER TABLE jobs ADD COLUMN retention_added_at TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN dataset_removed_at TIMESTAMPTZ;
