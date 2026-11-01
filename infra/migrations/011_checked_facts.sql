-- M3j 011 (docs/M3j_spec.md §7): the facts we checked for a video link, when it could be checked.
ALTER TABLE retention_snapshots ADD COLUMN checked_views BIGINT;
ALTER TABLE retention_snapshots ADD COLUMN published_at TIMESTAMPTZ;
ALTER TABLE retention_snapshots ADD COLUMN video_seconds INTEGER;
