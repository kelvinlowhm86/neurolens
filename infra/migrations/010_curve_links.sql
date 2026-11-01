-- M3i 010 (docs/M3i_spec.md §5): each curve names the published video it belongs to, the form's
-- Same / Lightly edited / Re-cut answer, and the Facebook report source.
ALTER TABLE retention_snapshots ADD COLUMN video_ref TEXT;
ALTER TABLE retention_snapshots ADD COLUMN same_cut TEXT
  CHECK (same_cut IS NULL OR same_cut IN ('same', 'light_edit', 'recut'));
CREATE INDEX retention_snapshots_video_ref ON retention_snapshots (video_ref);
ALTER TABLE retention_snapshots ADD CONSTRAINT retention_snapshots_source_check
  CHECK (source IN ('youtube_api', 'facebook_api'));
