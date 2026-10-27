-- AI summary report (step after TRIBE): LLM reads the stats + key frames, the worker renders a PDF.
-- The job is SUCCEEDED (charts available) before the report exists; the report has its own status.

ALTER TABLE jobs
  ADD COLUMN report_status    text CHECK (report_status IN ('PENDING', 'READY', 'FAILED', 'OFF')),
  ADD COLUMN s3_report_key    text,
  ADD COLUMN report_error     text,
  ADD COLUMN report_model     text,
  ADD COLUMN report_done_at   timestamptz;
