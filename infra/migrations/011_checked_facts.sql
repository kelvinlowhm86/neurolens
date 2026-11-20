-- The facts we checked for a video link, when it could be checked.
ALTER TABLE retention_snapshots ADD COLUMN checked_views BIGINT;
ALTER TABLE retention_snapshots ADD COLUMN published_at TIMESTAMPTZ;
ALTER TABLE retention_snapshots ADD COLUMN video_seconds INTEGER;
