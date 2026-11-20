-- One AI report per video, with its claim, tries and failure code.
ALTER TABLE jobs ADD COLUMN report_state TEXT CHECK
  (report_state IS NULL OR report_state IN ('writing', 'ready', 'failed'));
ALTER TABLE jobs ADD COLUMN report_tries INTEGER NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN report_started_at TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN report_error TEXT;
