-- Billing lives in the database. Every balance change goes through one of these functions, so the
-- web tier, the worker (psycopg) and the Lambdas (RDS Data API) all share one implementation.
--
-- Lock order is always jobs row → users row, which rules out deadlocks between these functions.
-- Insufficient balance raises SQLSTATE 'NL001' (mapped to InsufficientCredits in Python).

-- ─── grant_credits: signup bonus, Stripe purchase, subscription grant ──────
-- Returns false (and changes nothing) if this (kind, ref) was already credited.
CREATE OR REPLACE FUNCTION grant_credits(p_user uuid, p_amount integer, p_kind ledger_kind, p_ref text)
RETURNS boolean LANGUAGE plpgsql AS $$
BEGIN
  IF p_amount <= 0 THEN
    RAISE EXCEPTION 'grant amount must be positive, got %', p_amount;
  END IF;
  IF p_kind NOT IN ('GRANT', 'PURCHASE', 'SUBSCRIPTION_GRANT') THEN
    RAISE EXCEPTION 'grant_credits cannot write kind %', p_kind;
  END IF;
  IF p_ref IS NULL THEN
    RAISE EXCEPTION 'grant_credits needs a ref for idempotency';
  END IF;

  PERFORM 1 FROM users WHERE id = p_user FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'unknown user %', p_user;
  END IF;

  INSERT INTO credit_ledger (user_id, kind, delta_available, delta_reserved, ref)
  VALUES (p_user, p_kind, p_amount, 0, p_ref)
  ON CONFLICT DO NOTHING;
  IF NOT FOUND THEN
    RETURN false;
  END IF;

  UPDATE users SET credits_available = credits_available + p_amount WHERE id = p_user;
  RETURN true;
END $$;

-- ─── reserve_credits: hold credits when the upload URL is minted ───────────
CREATE OR REPLACE FUNCTION reserve_credits(p_job uuid, p_amount integer)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
  v_user  uuid;
  v_held  integer;
  v_avail integer;
BEGIN
  IF p_amount <= 0 THEN
    RAISE EXCEPTION 'reserve amount must be positive, got %', p_amount;
  END IF;

  SELECT user_id, credits_reserved INTO v_user, v_held FROM jobs WHERE id = p_job FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'unknown job %', p_job;
  END IF;
  IF v_held > 0 THEN
    RAISE EXCEPTION 'job % already holds a reservation', p_job;
  END IF;

  SELECT credits_available INTO v_avail FROM users WHERE id = v_user FOR UPDATE;
  IF v_avail < p_amount THEN
    RAISE EXCEPTION 'insufficient credits: need %, have %', p_amount, v_avail USING ERRCODE = 'NL001';
  END IF;

  INSERT INTO credit_ledger (user_id, job_id, kind, delta_available, delta_reserved)
  VALUES (v_user, p_job, 'RESERVE', -p_amount, p_amount);
  UPDATE users SET credits_available = credits_available - p_amount,
                   credits_reserved  = credits_reserved  + p_amount
   WHERE id = v_user;
  UPDATE jobs SET credits_reserved = p_amount WHERE id = p_job;
END $$;

-- ─── adjust_reservation: worker resizes the hold once the real duration is known ──
CREATE OR REPLACE FUNCTION adjust_reservation(p_job uuid, p_new_amount integer)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
  v_user  uuid;
  v_held  integer;
  v_avail integer;
  v_diff  integer;
BEGIN
  IF p_new_amount <= 0 THEN
    RAISE EXCEPTION 'reservation must be positive, got %', p_new_amount;
  END IF;

  SELECT user_id, credits_reserved INTO v_user, v_held FROM jobs WHERE id = p_job FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'unknown job %', p_job;
  END IF;
  IF v_held = 0 THEN
    RAISE EXCEPTION 'job % has no live reservation to adjust', p_job;
  END IF;

  v_diff := p_new_amount - v_held;
  IF v_diff = 0 THEN
    RETURN;
  END IF;

  SELECT credits_available INTO v_avail FROM users WHERE id = v_user FOR UPDATE;
  IF v_diff > 0 AND v_avail < v_diff THEN
    RAISE EXCEPTION 'insufficient credits: need % more, have %', v_diff, v_avail USING ERRCODE = 'NL001';
  END IF;

  INSERT INTO credit_ledger (user_id, job_id, kind, delta_available, delta_reserved)
  VALUES (v_user, p_job, 'ADJUST', -v_diff, v_diff);
  UPDATE users SET credits_available = credits_available - v_diff,
                   credits_reserved  = credits_reserved  + v_diff
   WHERE id = v_user;
  UPDATE jobs SET credits_reserved = p_new_amount WHERE id = p_job;
END $$;

-- ─── capture_credits: charge on success, return any unused hold ────────────
-- Returns false if the job holds nothing (already captured or released).
CREATE OR REPLACE FUNCTION capture_credits(p_job uuid, p_amount integer)
RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE
  v_user uuid;
  v_held integer;
BEGIN
  SELECT user_id, credits_reserved INTO v_user, v_held FROM jobs WHERE id = p_job FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'unknown job %', p_job;
  END IF;
  IF v_held = 0 THEN
    RETURN false;
  END IF;
  IF p_amount < 0 OR p_amount > v_held THEN
    RAISE EXCEPTION 'capture % outside held %', p_amount, v_held;
  END IF;

  PERFORM 1 FROM users WHERE id = v_user FOR UPDATE;
  INSERT INTO credit_ledger (user_id, job_id, kind, delta_available, delta_reserved)
  VALUES (v_user, p_job, 'CAPTURE', v_held - p_amount, -v_held);
  UPDATE users SET credits_available = credits_available + (v_held - p_amount),
                   credits_reserved  = credits_reserved  - v_held
   WHERE id = v_user;
  UPDATE jobs SET credits_reserved = 0, credits_captured = p_amount WHERE id = p_job;
  RETURN true;
END $$;

-- ─── release_credits: give the whole hold back (failure, expiry, cancel) ───
-- Returns false if the job holds nothing, so repeated calls are harmless.
CREATE OR REPLACE FUNCTION release_credits(p_job uuid, p_reason text)
RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE
  v_user uuid;
  v_held integer;
BEGIN
  SELECT user_id, credits_reserved INTO v_user, v_held FROM jobs WHERE id = p_job FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'unknown job %', p_job;
  END IF;
  IF v_held = 0 THEN
    RETURN false;
  END IF;

  PERFORM 1 FROM users WHERE id = v_user FOR UPDATE;
  INSERT INTO credit_ledger (user_id, job_id, kind, delta_available, delta_reserved, ref)
  VALUES (v_user, p_job, 'RELEASE', v_held, -v_held, p_reason);
  UPDATE users SET credits_available = credits_available + v_held,
                   credits_reserved  = credits_reserved  - v_held
   WHERE id = v_user;
  UPDATE jobs SET credits_reserved = 0 WHERE id = p_job;
  RETURN true;
END $$;

-- ─── close_job: end a job unsuccessfully and refund its hold ───────────────
-- Used by the worker (bad media), the web tier (cancel), the reaper and the DLQ Lambda.
-- Returns false if the job was already in a final state.
CREATE OR REPLACE FUNCTION close_job(p_job uuid, p_status job_status, p_code text, p_message text)
RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE
  v_status job_status;
BEGIN
  IF p_status NOT IN ('FAILED', 'EXPIRED', 'CANCELLED') THEN
    RAISE EXCEPTION 'close_job cannot set status %', p_status;
  END IF;

  SELECT status INTO v_status FROM jobs WHERE id = p_job FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'unknown job %', p_job;
  END IF;
  IF v_status IN ('SUCCEEDED', 'FAILED', 'EXPIRED', 'CANCELLED') THEN
    RETURN false;
  END IF;

  PERFORM release_credits(p_job, p_code);
  UPDATE jobs SET status = p_status, error_code = p_code, error_message = left(p_message, 500),
                  progress_stage = NULL, completed_at = now()
   WHERE id = p_job;
  RETURN true;
END $$;
