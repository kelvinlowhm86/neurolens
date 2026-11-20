-- When a dispatched request's GPU wait runs out.
ALTER TABLE jobs ADD COLUMN gpu_wait_until TIMESTAMPTZ;
