# NeuroLens — M3 Implementation Spec
**Milestone:** Authentication, Billing & Result Persistence (20 – 30 Oct)
**Builds on:** M2 (autoscaled AMI-based GPU workers, SQS with DLQ, S3-based job status and per-job claim as interim mechanisms, real polling UI). M3 replaces those S3-based interim mechanisms with a real Aurora-backed system, adds Google SSO, and implements the credit reserve/capture/release model.

## 1. Scope
### In scope
- Aurora PostgreSQL Serverless v2 cluster, schema for users, jobs, and a credit ledger
- Google OAuth2 SSO (login screen, session handling, protecting all API routes)
- Credit reservation model: soft-reserve at upload time (client-estimated duration), verified-reserve adjustment at worker claim time (ffprobe-measured duration), capture at completion (verified reservation amount recorded in Stage 2, based on ffprobe-measured duration), release of any unused reservation
- Job claiming migrated from M2's S3 conditional-write mechanism to an atomic Aurora `UPDATE ... WHERE status='queued'`
- Job status migrated from M2's S3 status objects to Aurora columns, queried directly by Flask
- DLQ-triggered Lambda: on a message landing in the dead-letter queue, mark the job failed and refund its reservation
- A scheduled reaper (EventBridge + Lambda) as a secondary safety net for jobs stuck without DLQ involvement
- Job history endpoint and CSV export
- Frontend: login screen, persistent credit balance display, job history view, and failure-state messaging with specific error codes (insufficient credit vs. processing failure vs. refunded), refining the generic error text M2 already put in place of the original `alert()`
- Real user IDs in S3 object keys, replacing M1's `placeholder-user`
- Experiment 3: cloud vs. on-premise TCO benchmark script
- A minimal, fixed-capacity HTTPS front end for Flask (§3a), required by Google OAuth2's redirect-URI constraint
- Retirement of the deprecated `/api/analyse` endpoint (see §3a)
- A shared, idempotent refund mechanism used by all failure paths (worker duration cap, DLQ handler, SLA reaper)
- Credit-accounting prototype for the evaluation: starter-credit reservation, adjustment, and audit records, plus a narrowly scoped Stripe **test-mode-only** Checkout demonstration for fixed test-credit packs. It must use only Stripe test keys and test payment methods; it never collects or processes real money, stores card details, enables live mode, or introduces subscriptions, saved cards, invoices, tax, or live Stripe refunds.

### Out of scope (deferred to M4)
- The 5-participant SUS usability study itself (this milestone should leave the product in a state ready for that study, not run it)

## 2. Aurora schema (`infra/schema.sql`)
```sql
CREATE TABLE users (
  user_id       TEXT PRIMARY KEY,        -- Google 'sub' claim
  email         TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE balances (
  user_id           TEXT PRIMARY KEY REFERENCES users(user_id),
  available_usd     NUMERIC(10,4) NOT NULL DEFAULT 0,
  reserved_usd      NUMERIC(10,4) NOT NULL DEFAULT 0
);

CREATE TABLE jobs (
  job_id            UUID PRIMARY KEY,        -- same UUID used in the S3 object key
  user_id           TEXT NOT NULL REFERENCES users(user_id),
  status            TEXT NOT NULL,           -- queued|claimed|processing|done|failed
  stage             TEXT,
  s3_object_key     TEXT NOT NULL,
  s3_result_key     TEXT,
  filename          TEXT,
  client_duration_s NUMERIC(6,2),
  verified_duration_s NUMERIC(6,2),
  reserved_usd      NUMERIC(10,4),
  captured_usd      NUMERIC(10,4),
  error_code        TEXT,
  error_message     TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ledger_transactions (
  id            BIGSERIAL PRIMARY KEY,
  user_id       TEXT NOT NULL REFERENCES users(user_id),
  job_id        UUID REFERENCES jobs(job_id),
  txn_type      TEXT NOT NULL,   -- reserve|adjust_reserve|capture|release|refund|test_topup
  amount_usd    NUMERIC(10,4) NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE refunds (
  job_id        UUID PRIMARY KEY REFERENCES jobs(job_id), -- one refund per job, ever
  reason        TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE stripe_test_events (
  stripe_event_id              TEXT PRIMARY KEY,
  stripe_checkout_session_id   TEXT NOT NULL UNIQUE,
  user_id                      TEXT NOT NULL REFERENCES users(user_id),
  credited_usd                 NUMERIC(10,4) NOT NULL,
  created_at                   TIMESTAMPTZ NOT NULL DEFAULT now()
);

```
`ledger_transactions` is the audit log the report's Data Tier section names — every balance change is recorded here, so the current `balances` row is always reconstructable/verifiable from history if needed.

Provision via `infra/provision_m3.sh`: create the Aurora Serverless v2 cluster, apply `schema.sql`, and store the resulting connection details in AWS Secrets Manager (Aurora's standard integration) rather than in `config.json` — this is a deliberate departure from M1/M2's config-file pattern, since database credentials warrant managed rotation in a way S3 bucket names and SQS queue URLs don't. Flask and the worker read the Secrets Manager ARN from `config.json`'s existing `aws` block and fetch credentials at startup. Aurora minimum capacity is 0 ACU outside the M4 study window (auto-pause with an approximately 15-second resume delay; this requires Aurora PostgreSQL 13.15+/14.12+/15.7+/16.3+ or Aurora MySQL 3.08+), raised to 0.5 ACU before the M4 study session begins to avoid participant-facing resume delay. Tag the Aurora cluster, ALB, Flask ASG, Launch Template, IAM roles, and Secrets Manager entries at creation with `Project=neurolens` and `Milestone=M3`.

In addition to the OAuth client ID/secret, provision a Flask session-signing secret (a random, sufficiently long value) as its own Secrets Manager entry. This must be a stable, persisted value fetched at boot — never generated in-process — because the Flask ASG (§3a) may replace its single instance on a failed health check, and a freshly generated secret on the replacement instance would silently invalidate active session cookies. Load it into Flask's session-signing configuration using the same Secrets-Manager-at-boot pattern used for `HF_TOKEN` in M2 §4.

For the test-only Stripe demonstration, store `STRIPE_SECRET_KEY` (test key only) and the Stripe webhook signing secret as separate Secrets Manager entries. Only the Flask web tier may read them; no browser, worker, Lambda, AMI, `config.json`, deployment bundle, or log may receive either secret. The Stripe secret key must be `sk_test_...`; fail startup if it is a live key. Configure `STRIPE_TEST_TOPUP_ALLOWED_USER_IDS` as a non-secret allowlist of the team's Google `user_id` values and `STRIPE_TEST_TOPUPS_ENABLED`. This feature is a demonstration only and must never be switched to Stripe live mode in this project.

## 2a. Aurora network access and IAM

Place Aurora in private subnets; it must not be publicly accessible. Its security group permits inbound PostgreSQL (port 5432) **only** from dedicated security groups for the Flask ASG (§3a), GPU-worker ASG (M2 §4), DLQ handler Lambda, and SLA reaper Lambda—never from a broad CIDR or `0.0.0.0/0`. Both Lambdas are VPC-attached so they reach Aurora over this private path.

Each workload retrieves database credentials from Secrets Manager at runtime; no credential may be baked into code, `config.json`, an AMI, or a deployment bundle. Give the web tier, worker, DLQ Lambda, and reaper only the required secret read and database-network access. Give the web tier, worker, DLQ Lambda, and reaper `s3:GetObject` on `results/*`: this authorizes the result-existence checks needed for duplicate prevention and completion reconciliation, as well as result serving. Add these security-group and IAM rules explicitly to `infra/provision_m3.sh`.

Since Aurora fully replaces M2's S3-based claim and status mechanism (§5, §6), `provision_m3.sh` must eventually remove all of the worker IAM role's now-unused permissions on `claims/*` and `status/*` granted in M2 §4 — this includes `s3:GetObject` on both prefixes (originally used for claim staleness checks) in addition to `s3:PutObject`/`s3:DeleteObject` on `claims/*` and `s3:PutObject` on `status/*`. Removing only the write permissions and leaving read access in place is an incomplete cleanup and still leaves the role broader than it needs to be. This does not affect `s3:GetObject` on `results/*`, which remains required permanently for duplicate-completion checks and settlement reconciliation (M2 §4, M3 §2a) and must not be removed. This full removal must happen as a separate, later step, gated on first confirming that the (at most one, per the standing min=0/max=1 ASG configuration) running worker instance is on the M3-era AMI and code revision — check via instance tags or a direct SSH/SSM check — before removing the permissions. Do not remove any of these permissions as part of the initial M3 rollout — a worker still mid-cutover on the old code path would lose S3 access mid-job and fail for an unrelated reason.

### AWS API access and internet egress for the evaluation profile

For this short study, the Flask ASG, GPU workers, and both Lambdas run in private subnets with no public IPs and use one NAT Gateway for outbound AWS API and OAuth traffic. Add the free S3 gateway endpoint, and do not provision paid interface endpoints in this evaluation profile. Security groups still deny unsolicited inbound traffic and IAM remains least-privilege; this is a simplification of egress routing, not permission to expose workloads publicly. A later production-hardening phase may replace worker/Lambda NAT egress with service-specific VPC endpoints.

## 3. Google OAuth2 SSO
- Use Authlib's Flask integration for the OAuth2 Authorization Code flow
- `GET /login` → redirect to Google's consent screen
- `GET /auth/callback` → exchange code for token, fetch the user's `sub` and `email`, upsert into `users` (on first login, also insert a `balances` row with a starter credit amount — pick a reasonable free-trial figure and note it in the response so it's not a silent magic number), create a signed Flask session cookie
- `POST /logout` → clear session
- A `login_required` decorator applied to every existing and new API route except `/login`/`/auth/callback`/static assets and `/api/stripe/webhook`; unauthenticated requests get 401 (JSON, not a redirect, since the frontend is a SPA that should handle showing the login screen itself). The webhook exception is authenticated exclusively by its verified Stripe signature (§7), never by user-session absence alone.
- Store the OAuth client secret in Secrets Manager alongside the DB credentials, not in `config.json`

CSRF and session safety: rely on Authlib's built-in OAuth `state` parameter for CSRF protection on the authorization-code flow — confirm this is active by default and is never disabled for convenience during testing. Configure Flask's session cookie with `SESSION_COOKIE_SECURE=True`, `SESSION_COOKIE_HTTPONLY=True`, and `SESSION_COOKIE_SAMESITE='Lax'`. On `/auth/callback`: first validate Authlib's `state` parameter against the pending authorization request; only after that check passes, clear any existing session data (`session.clear()`) and populate a fresh session containing only the newly authenticated user's data — since Flask's default session is a client-side signed cookie with no server-side session ID to rotate, this clear-then-repopulate step is what prevents a stale or pre-existing session from persisting into the authenticated one. If a server-side session store (e.g. Flask-Session backed by Redis or a database) is introduced instead of the default signed-cookie session, that store's session ID must be explicitly regenerated at login, in addition to the clear-then-repopulate step above. On `/logout`, fully clear the session (`session.clear()`), not just delete the client-side cookie.

## 3a. Minimal HTTPS front end for OAuth

Google OAuth2 requires an exact-match HTTPS redirect URI in production. Provision:

1. Register a custom (sub)domain (for example, `app.<yourdomain>.com`) — do **not** use the ALB's auto-generated DNS name as the OAuth redirect target. Issue an ACM TLS certificate for this exact domain and point DNS at the ALB.
1a. Update the S3 bucket's CORS configuration (`provision_m3.sh`) to add `https://app.<yourdomain>.com` to the existing `AllowedOrigins` list — following the same read-merge-write approach specified in M1 §3 point 3, so M1's dev origin is preserved rather than overwritten. Verify with `aws s3api get-bucket-cors` that both origins are present before the M4 study begins.
2. Register the exact callback URL — for example, `https://app.<yourdomain>.com/auth/callback` — in Google's API console and in `config.json`. This must match character-for-character.
3. Create a shared, lightweight `settings.py` that reads `config.json` and exposes non-GPU values — `MAX_DURATION`, `aws.region`, and bucket/queue identifiers — with no side effects: no HF environment-variable setup and no model or atlas loading. Both `app.py` and `inference.py` import it for configuration they share. This supersedes M1 §1: `inference.py` must import `MAX_DURATION` from `settings.py` rather than reading `config.json` directly, so there is a single source of truth for this value across the web and worker tiers.
4. Build a **separate** deployment bundle for the Flask ASG. Remove the deprecated `/api/analyse` route from `app.py` entirely, including its imports of `run_inference`, `strip_audio`, and `extract_engagement` from `inference.py`. No path in the Flask bundle may import `inference.py`, directly or transitively.
5. Add an unauthenticated `GET /healthz` endpoint returning 200 with a minimal body and no auth check. Configure it as the ALB target group's health-check path.
6. Restrict the Flask instances' security group to inbound traffic **only** from the ALB security group, not `0.0.0.0/0`.
7. Redirect all HTTP (port 80) traffic to HTTPS (port 443); serve nothing over plain HTTP.
8. Use a separate target group, Launch Template, and ASG with `min=max=desired=1` for the Flask bundle. It replaces unhealthy instances but has no scaling policy; it is not M2's GPU-worker ASG.
9. Fetch the OAuth client secret and the session-signing secret (§2) from Secrets Manager at boot.
10. Defer a scaling policy for this tier.
11. Keep the OAuth consent screen in Testing publishing status (not Production) for the duration of this project — add the team, TA, and the 5 study participants as test users in the Google API console. This avoids Google's production-verification review cycle entirely, which is unnecessary at this scale and would risk stalling M3/M4. Skip the homepage/privacy-policy requirement that only applies to production verification.
12. Provision this ALB and Flask ASG shortly before the M4 study window, not for the whole M3–M4 span. Do not replace it with a tunnel or direct-to-EC2 exposure, which would omit the HTTPS termination, health checks, and replacement behavior being evaluated.

### Flask ASG boot contract

The Flask deployment bundle must contain `app.py`, `settings.py`, `billing.py`, the database/OAuth/session modules, `static/`, `requirements-web.txt`, and `infra/neurolens-web.service`. `requirements-web.txt` is scoped only to Flask, boto3, the OAuth library, the DB driver, and other web runtime dependencies; it explicitly excludes torch, tribev2, and nilearn. The service runs a production WSGI server such as gunicorn, never Flask's development server.

`infra/deploy_web_code.sh` packages this bundle and uploads it to `code-web/latest.zip`, a prefix distinct from M2's `code/latest.zip`; the two deployment processes must never overwrite one another. At boot, UserData fetches this bundle; fetches the OAuth and session secrets plus non-secret configuration (including `MAX_DURATION`) into the configuration file read by `settings.py`; validates those inputs; and only then enables and starts the web service. The web tier must not receive the worker's HF token or model artifacts.

M3 also updates M2's `infra/deploy_code.sh` worker bundle to include `worker.py`, `inference.py`, `settings.py`, `billing.py`, and the shared Aurora access module. M3 updates `infra/build_ami.sh` (and creates a versioned replacement worker AMI) to install the Aurora PostgreSQL driver and any new worker-side database dependencies into the pre-baked virtualenv; do not install them at boot. `provision_m2.sh` then updates the worker Launch Template to use that AMI. Otherwise the M3 worker would fail after importing its new shared modules or attempting an Aurora connection.

Add this infrastructure provisioning to `infra/provision_m3.sh` alongside Aurora and Secrets Manager setup; the separate web bundle and `deploy_web_code.sh` are code-packaging work, not infrastructure provisioning.

## 4. Credit reservation flow
This has three stages — soft reserve, verified adjustment, and capture — deliberately separated because client-reported duration can't be trusted for pricing, but requiring full verification before any reservation exists would let an attacker queue unlimited jobs before ever being GPU-charged (the "denial-of-wallet" vector the report names in Section 3).

**Stage 1 — soft reserve, at `/api/uploads/presign`:**
- Compute `estimated_usd = 0.90 * ceil(client_duration_seconds / 30)` from the client-reported duration
- Inside one transaction: check `available_usd >= estimated_usd`; if not, return 402 with an `insufficient_credit` error code (not a generic 400/500 — the frontend needs to distinguish this case, see §7); if sufficient, move `estimated_usd` from `available_usd` to `reserved_usd`, insert the `jobs` row (`status='queued'`, `reserved_usd=estimated_usd`), insert a `reserve` row in `ledger_transactions`
- The S3 object key now uses the real `user_id` from the session: `uploads/{user_id}/{job_id}.{ext}` — this replaces M1's `placeholder-user` TODO

**Stage 2 — verified adjustment, in the worker after claiming (see §5) and running ffprobe:**
Before any credit-adjustment logic, compare the ffprobe-measured duration with the hard `MAX_DURATION` cap imported from `settings.py`. If `verified_duration_s > MAX_DURATION`, call `issue_refund(job_id, 'duration_exceeds_max_verified')` and do not run inference, regardless of credit balance. Only if `verified_duration_s <= MAX_DURATION` does the adjustment logic below apply.

- Compute `verified_usd = 0.90 * ceil(verified_duration_s / 30)`
- Persist `verified_duration_s` and the resulting verified reservation in this transaction before inference begins; this is the authoritative final pricing basis for the job.
- If `verified_usd == reserved_usd` (the common case — client estimate matched), no ledger change needed
- If `verified_usd > reserved_usd` (client under-reported, honestly or otherwise): attempt to reserve the difference from `available_usd`. If insufficient, call `issue_refund(job_id, 'insufficient_credit_for_actual_duration')` and do **not** proceed to GPU inference — this is what actually bounds the abuse case, since a worker never runs a full inference pass on a job whose real cost the user can't cover
- If `verified_usd < reserved_usd` (client over-estimated): release the difference back to `available_usd` immediately, don't wait for job completion
- Record an `adjust_reserve` ledger row for any change in this stage

**Stage 3 — capture, on successful completion:** after the conditional S3 result write succeeds, capture the verified reservation amount recorded in Stage 2 by calling `settle_success(job_id, captured_usd)`, where `captured_usd = 0.90 * ceil(verified_duration_s / 30)`. The customer is billed in predictable 30-second blocks of the ffprobe-verified uploaded-video duration, not according to TRIBE's internal output cadence or dual-pass timestep alignment.

### Two complementary, mutually-exclusive settlement functions

Define exactly two functions in `billing.py`, both using `SELECT ... FOR UPDATE` on the same job row:

- `settle_success(job_id, captured_usd)` succeeds only for an active, unrefunded job. In the same transaction it records the capture, releases unused reservation, sets `status='done'`, and commits. A job already failed or done is a no-op.
- `issue_refund(job_id, reason)` succeeds only for an active, uncaptured job. In the same transaction it releases the reservation, records a refund row uniquely keyed by `job_id`, sets `status='failed'`, records `reason` as the terminal `error_code`, and commits. A job already done or failed is a no-op, even if no refund row exists.

These functions alone own terminal status transitions and terminal error-code writes. Callers must not separately set `status`, `error_code`, or release balances. This makes capture and refund mutually exclusive under the same row lock.

## 5. Job claiming (migrated from M2's S3 mechanism)
Replace the M2 S3-conditional-write claim with an atomic Aurora update:
```sql
UPDATE jobs SET status='claimed', updated_at=now()
WHERE job_id = %s AND status='queued'
RETURNING job_id;
```
If this returns zero rows, another worker already claimed the job — delete the SQS message and exit without processing, exactly as M2's S3-claim fallback did. This directly replaces the `claims/{job_id}.json` object approach; remove that code path entirely rather than running both. This migration only changes who owns the job; M2 §8's SQS visibility heartbeat and fast-failure-release logic is unrelated infrastructure and continues to run unchanged.

## 6. Job status (migrated from M2's S3 mechanism) + failure handling
- Aurora replaces M2's `status/{job_id}.json` objects, but an immutable S3 result remains authoritative for completion. For `GET /api/jobs/{job_id}/status`, first authenticate and authorize the caller against Aurora job ownership; only then check `results/{job_id}.json`. If it exists, return `status='done'` regardless of Aurora status. If it does not, return the Aurora status. Do not look up S3 before authorization.
- **DLQ Lambda** (`infra/dlq_handler.py`) and the **SLA reaper** (`infra/sla_reaper.py`, every five minutes) must each check `results/{job_id}.json` before deciding a job is failed or abandoned. If it exists, call `settle_success` using the `verified_duration_s` and verified reservation already persisted in Aurora during Stage 2; never call `issue_refund` in this case. If it does not exist, the DLQ handler or reaper calls `issue_refund` through the normal failure path. This preserves M2's result-write/Aurora-write crash-window protection without making billing depend on the result's internal timestep cadence.
- The reaper considers `queued`, `claimed`, or `processing` jobs whose `updated_at` exceeds the max-SLA threshold (for example, 15 minutes). The DLQ handler deletes its message only after terminal handling succeeds.

## 7. New/changed Flask endpoints
- `GET /api/me` → `{ "email": ..., "available_usd": ..., "reserved_usd": ..., "can_use_test_topups": ... }`, where `can_use_test_topups` is calculated server-side from `STRIPE_TEST_TOPUPS_ENABLED` and `STRIPE_TEST_TOPUP_ALLOWED_USER_IDS`
- `GET /api/jobs` → job history for the authenticated user: `[{ job_id, filename, status, created_at, captured_usd, verified_duration_s }, ...]`
- `GET /api/jobs/{job_id}/status` → now Aurora-backed (see §6)
- `GET /api/jobs/{job_id}/result` → unchanged from M2 (reads from S3), but now also checks the job belongs to the requesting user
- `GET /api/jobs/{job_id}/result.csv` → new. Reads the same S3 result JSON, converts `timesteps` to CSV (columns: `t, engagement_overall, ffa_faces, eba_bodies, ppa_scenes, sts_social, auditory, auditory_with_audio, auditory_without_audio`), returns as a file download (`Content-Disposition: attachment`). Since S3 results expire after 48h, surface this expiry in the job history UI so users know to export before then — don't let this be a surprise 404
- The primary `auditory` ROI signal is normalized over the full timeline, like the other four ROI columns. `auditory_with_audio` and `auditory_without_audio` are a separate dual-pass comparison normalized over a shorter shared window (see the comment above `extract_engagement` in `inference.py`). Thus `auditory` and `auditory_with_audio` may legitimately differ at the same row even though they share an underlying signal. Document this in the CSV itself (a header comment row) or in `docs/`.
- `POST /api/uploads/presign` → now requires auth, uses the real `user_id`, and implements Stage 1 of §4 (balance check + soft reserve) instead of M1's estimate-only response
- `POST /api/test-credit/checkout` → requires auth and, only while `STRIPE_TEST_TOPUPS_ENABLED=true`, permits only a `user_id` in `STRIPE_TEST_TOPUP_ALLOWED_USER_IDS` to choose a pack ID from two server-defined USD packs: `$5` or `$10`. The server must never accept a price, currency, user ID, credit amount, Checkout Session ID, or Stripe event ID from the browser. Create the hosted Checkout Session with `payment_method_types=['card']`, the server-defined pack amount/currency, and metadata containing the authenticated allowlisted `user_id` and pack ID. Return only Stripe's hosted Checkout URL. Use Stripe test keys only.
- `POST /api/stripe/webhook` → unauthenticated only because Stripe calls it, but it must verify Stripe's webhook signature against the exact raw request bytes before JSON parsing or trusting an event. If `STRIPE_TEST_TOPUPS_ENABLED=false`, return 2xx without credit. Otherwise accept only test-mode card `checkout.session.completed` events for an allowlisted `user_id` and one of the server-defined `$5`/`$10` packs; reject `livemode=true`, unknown metadata, unexpected amount/currency, and all other event types. In one Aurora transaction, insert the event into `stripe_test_events` with `INSERT ... ON CONFLICT DO NOTHING RETURNING`; if it was already recorded, return 2xx with no balance change. Otherwise add the fixed credited amount to `balances.available_usd` and insert one `ledger_transactions(txn_type='test_topup')` row. A duplicate event ID or Checkout Session ID must be a no-op: it must never grant credit twice. The success redirect is display-only and must never credit a balance.

## 8. Frontend additions
- **Login screen:** shown whenever `/api/me` returns 401; a simple "Sign in with Google" button linking to `/login`. Everything else (upload zone, sample carousel, dashboard) stays hidden until authenticated
- **Persistent balance display:** in the existing header (`.header-meta` area already exists — reuse the pattern rather than inventing new chrome), showing `available_usd`, refreshed after each job completes
- **Test-credit control:** show the control only while `/api/me` returns `can_use_test_topups=true`. Offer the `$5` and `$10` packs, label it “Add test credit — no real charge,” and open the hosted Stripe Checkout URL returned by `POST /api/test-credit/checkout`. Do not collect card details in NeuroLens. Disable this control before M4 and keep it absent from the usability-study flow; the study evaluates the existing starter-credit product, not payment UX.
- **Job history view:** a new panel or tab listing past jobs from `GET /api/jobs`, each clickable to re-view its results (reuse the existing chart-rendering code, feeding it the historical result JSON instead of a freshly-completed one) and a CSV download link
- **Real failure-state messaging** — M2 already replaced the original `alert()` with a generic error-field display; M3 refines this into specific, distinguishable codes:
  - `insufficient_credit` (from presign, 402) → "Not enough credit — top up to continue" (a top-up flow is a reasonable stretch goal but not required for M3's acceptance criteria; a static "contact support" message is acceptable if a real top-up UI doesn't fit the timeline)
  - `insufficient_credit_for_actual_duration` → "Your video's actual length exceeds what your estimate covered — reservation released, no charge"
  - `duration_exceeds_max_estimated` (from presign, 400) and `duration_exceeds_max_verified` (from a failed job) → both map to: "This video exceeds the 120-second maximum — reservation released, no charge". The frontend must handle both codes, since the estimated check can pass while the verified check later fails.
  - generic processing failure → the real `error_message` from the job row, plus "you have not been charged" (since §4 guarantees a refund on failure — say so, don't make the user wonder)

## 9. Experiment 3 (TCO benchmark)
- A benchmark script that runs 10–15 timed inference passes per duration bucket (15s/30s/60s) both against the cloud Spot worker path and on available local/team GPU hardware, logging wall-clock duration and, for the on-premise leg, drawing power consumption from published hardware TDP figures (not measured wattage, per the report's stated method)
- Output: a CSV/table of measured runtimes and scenario-based cost estimates. Use the measured runtimes and stated assumptions—including the fully-loaded $0.10/video model—to compare with the ~1,475 videos/month analytical breakeven in Section 2.2. Do not label these small benchmark runs as directly observed fully-loaded per-video cost: cold-start frequency, idle periods, and payment-fee amortisation remain assumptions.
- Write the Experiment 3 manifest and outputs using M2 §10's shared `experiments/experiment-3/<run_id>/` artifact contract so M4 can consume them without path discovery.

## 10. M3-to-M4 operating handoff

Do **not** tear down the Flask ASG, ALB, Aurora cluster, or study data at the end of M3: M4 needs the live authenticated product and Aurora records for the usability study and consolidation. Between M3 tests, scale only the GPU ASG to its standing `min=0`, `desired=0`, `max=1` configuration. Final teardown belongs to M4 and occurs only after its consolidation script and required exports have completed.

Before M4 begins, set `STRIPE_TEST_TOPUPS_ENABLED=false` in the web-tier configuration and verify the test-credit control and `POST /api/test-credit/checkout` are unavailable. The webhook must still return 2xx for a late delivery but must not credit it while the feature is disabled. M4's scope and consent material remain unchanged because the study does not expose or evaluate this demonstration.

## 11. File layout additions
```
neurolens/
├── infra/
│   ├── provision_m3.sh        # NEW — Aurora, Secrets Manager, ALB, and Flask ASG
│   ├── deploy_web_code.sh     # NEW — packages/uploads the isolated web bundle to code-web/latest.zip
│   ├── deploy_code.sh         # MODIFIED — M3 worker bundle ships its shared DB/settings/billing modules
│   ├── build_ami.sh           # MODIFIED — installs worker-side Aurora driver; produces replacement AMI
│   ├── neurolens-web.service  # NEW — production WSGI service for the web tier
│   ├── schema.sql             # NEW
│   ├── dlq_handler.py         # NEW — Lambda, SQS event source on the DLQ
│   └── sla_reaper.py          # NEW — Lambda, EventBridge scheduled trigger
├── settings.py                 # NEW — lightweight shared config reader; no HF/model side effects
├── database.py                 # NEW — shared Aurora connection/access module
├── billing.py                  # NEW — locked, mutually-exclusive settle_success and issue_refund functions
├── requirements-web.txt        # NEW — CPU/web dependencies only, including the Stripe SDK; excludes worker ML packages
├── app.py                      # MODIFIED — CPU-only Flask bundle; no inference.py imports; /api/analyse removed; includes /healthz
├── worker.py                   # MODIFIED — Aurora claim, Stage 2/3 reservation logic, Aurora status writes
├── experiments/
│   └── tco_benchmark.py        # NEW — Experiment 3
└── static/                      # MODIFIED — login screen, balance display, job history view, failure messaging
```

## 12. Acceptance criteria
1. A new user can sign in with Google, lands with a starter credit balance, and cannot access any API route while unauthenticated (401 on `/api/uploads/presign` without a session).
2. Uploading a video whose real (ffprobe) duration matches the client estimate results in a reservation, a capture on completion for the same amount, and an unchanged `available_usd` net of the capture — verified against `ledger_transactions` rows, not just the final balance number.
3. Uploading a video with an intentionally wrong client-reported duration (shorter than actual) triggers Stage 2's adjustment; if the user's balance can't cover the adjustment, the job fails with `insufficient_credit_for_actual_duration` and the original reservation is fully released, with **zero** GPU inference having run (verify via worker logs — the rejection happens before `run_inference` is called).
4. Forcing a job to exhaust its SQS retries and land in the DLQ results in the job's reservation being fully refunded and `status='failed'`, without manual intervention.
5. A job manually stuck in `processing` past the SLA threshold (simulate by not updating its `updated_at`) gets reaped and refunded by the scheduled Lambda within one run interval.
6. `GET /api/jobs` returns only the authenticated user's own jobs, never another user's.
7. CSV export produces a file whose rows match the JSON `timesteps` data exactly, for a job still within the 48h S3 lifecycle window.
8. Job claiming via the new Aurora `UPDATE ... WHERE status='queued'` correctly rejects a second, duplicate claim attempt on the same job — this replaces and must fully retire M2's S3-based claim object, not run alongside it.
9. `GET /healthz` returns 200 with no authentication required; the ALB correctly marks an instance unhealthy and replaces it if this endpoint stops responding.
10. A freshly launched Flask-tier instance does not attempt to load TRIBE v2 or the Destrieux atlas (confirm from absent log lines) and reaches healthy status in seconds, not minutes.
11. Replacing the Flask instance through a forced health-check failure does not invalidate an active user session, proving the session secret was fetched from Secrets Manager rather than generated at boot.
12. A video whose ffprobe-measured duration exceeds 120 seconds fails with `duration_exceeds_max_verified` before inference and produces exactly one refund for its `job_id`, even if a second failure path (such as simulated DLQ redelivery) is triggered.
13. The full Google OAuth2 flow succeeds against the ALB's public HTTPS custom domain and registered callback URL, establishing a session signed with the Secrets-Manager-sourced key.
14. Stage 2 persists the ffprobe-verified duration before inference, and Stage 3 captures the corresponding 30-second-block amount; neither path uses TRIBE timestep count or output cadence for billing.
15. Simulating a crash after writing `results/{job_id}.json` but before Aurora settlement causes an authorized owner request to report `done`; a non-owner is rejected before an S3 lookup. On the next reaper run, the job is reconciled through `settle_success` using the persisted verified duration and no refund.
16. The same completed-but-unreconciled scenario, forced into the DLQ, is reconciled through `settle_success` rather than refunded.
17. Aurora rejects a connection attempt from an EC2 instance or IP outside the Flask, worker, and Lambda security-group allowlist.
18. Concurrent `settle_success` and `issue_refund` attempts for one job always produce exactly one terminal outcome: either captured/`done` with no refund, or refunded/`failed` with no capture.
19. The insufficient-actual-credit Stage 2 path calls `issue_refund(job_id, 'insufficient_credit_for_actual_duration')`, which alone writes the terminal error code and issues exactly one refund.
20. All web, worker, and Lambda workloads have no public IP or inbound public access; the Flask ASG completes a real Google OAuth token exchange through the shared NAT route, and least-privilege IAM still restricts each workload to its required AWS actions.
21. The M3 replacement worker AMI, launched through the updated M2 Launch Template, imports the shared Aurora modules and completes a database connection without boot-time dependency installation.
22. At the end of M3, the Flask ASG, ALB, Aurora cluster, and study data remain available for M4; only the GPU ASG is restored to its standing scale-to-zero configuration.
23. With Stripe test keys and cards only, an allowlisted team user can complete a `$5` or `$10` Checkout Session and receive exactly one matching `test_topup` ledger entry and available-balance increase, based on a verified `checkout.session.completed` webhook—not the success redirect. A non-allowlisted authenticated user cannot create a Checkout Session.
24. Replaying the same verified webhook event, or delivering a different event for the same Checkout Session, does not add credit a second time; raw-body signature verification, `livemode=false`, card-only Checkout, an allowlisted user, a server-defined pack/currency, and transaction-safe `ON CONFLICT DO NOTHING` event insertion are all checked.
25. Startup fails if the configured Stripe secret key is not a test key. With `STRIPE_TEST_TOPUPS_ENABLED=false`, the checkout endpoint and its UI control are unavailable; a late webhook delivery returns 2xx without crediting the balance.

## 13. Explicitly not in this milestone
No live Stripe payments, subscriptions, saved cards, invoices, tax, payment data storage, or real-money collection. No SUS usability study, task-performance data collection, or qualitative feedback synthesis — those are M4's work, using the product this milestone produces.
