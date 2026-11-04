-- M3i 013 (docs/M3i_spec.md §6): the rewarded link is kept on the job, so a purged copy cannot
-- forget the reward and pay the same published video again.
ALTER TABLE jobs ADD COLUMN curve_reward_ref TEXT;
UPDATE jobs j
SET curve_reward_ref = s.video_ref
FROM retention_snapshots s
WHERE s.job_id = j.job_id
  AND s.rewarded_cents > 0
  AND s.video_ref IS NOT NULL
  AND s.snapshot_id = (
    SELECT earliest.snapshot_id
    FROM retention_snapshots earliest
    WHERE earliest.video_ref = s.video_ref
      AND earliest.rewarded_cents > 0
    ORDER BY earliest.added_at, earliest.snapshot_id
    LIMIT 1
  );
CREATE UNIQUE INDEX jobs_curve_reward_ref ON jobs (curve_reward_ref)
  WHERE curve_reward_ref IS NOT NULL;
