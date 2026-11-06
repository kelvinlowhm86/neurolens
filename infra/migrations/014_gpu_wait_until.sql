-- M3l 014 (docs/M3l_spec.md §2a): when a dispatched request's GPU wait runs out.
ALTER TABLE jobs ADD COLUMN gpu_wait_until TIMESTAMPTZ;
