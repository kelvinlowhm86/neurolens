# NeuroLens — M3a Implementation Spec
**Milestone:** Aurora database, credit billing and refunds (first part of 20 – 30 Oct)
**Builds on:** M2b (autoscaled Spot GPU workers with heartbeat, fast release, Spot/SIGTERM handling and self-termination; SQS with a dead-letter queue; results in `results/{job_id}.json` via conditional write; S3 status objects and status/result endpoints; real polling UI). M3a adds an Aurora PostgreSQL database that owns users, credit balances and job state, implements three-stage credit billing with guaranteed refunds, and moves job status out of S3. Sign-in, the public website and job history are M3b; until then a fixed development user stands in (§2).

**Ground rules for all of M3a:** region `us-east-1`. Python 3.12. All infrastructure is Terraform in `infra/terraform/`, tagged `Project=neurolens`, `Milestone=M3a`. Run AWS commands with the `neurolens` CLI profile only. **Tests first, as in M0 §5:** the §10 tests are written by a separate agent against §3–§4 before the implementation, and are not edited by the implementer. Billing is the riskiest code in the project: no billing function is merged without its tests passing, and the break-it check (§10) is mandatory.

## 1. Scope
### In scope
- Aurora Serverless v2 (PostgreSQL) reached through the **RDS Data API**, which scales to zero when idle
- A database layer with two interchangeable backends: the Data API (AWS) and plain PostgreSQL (tests and laptop)
- Schema: users, balances, jobs, ledger, refunds, with versioned migrations
- Three-stage credit billing: reserve at upload, verify after measuring, capture on success; refund on every failure path
- Job ownership, claiming and status in Aurora; S3 status objects retired
- A dead-letter-queue Lambda and a scheduled reaper Lambda that settle or refund stuck jobs
- Web endpoints: presign with reservation, `GET /api/me`, Aurora-backed status and result with ownership checks

### Out of scope (M3b)
Google sign-in, the public HTTPS website (CloudFront), the web server on AWS, the load balancer, job history, CSV export, the refined failure messages, Experiment 3, and the optional Stripe test-mode demo.

## 2. Development identity (until M3b)
- Config `auth.mode`: `"dev"` in M3a. `auth.dev_user_id` and `auth.dev_email` give one fixed user.
- `neurolens.web.auth.current_user() -> tuple[str, str] | None` returns `(user_id, email)`. In dev mode it always returns the dev user. M3b adds `"google"` mode behind the same function; nothing else in the web app reads identity directly.
- `create_app` **refuses to start** in dev mode unless the configured host is `127.0.0.1`, raising `neurolens.settings.UnsafeConfigError`, so a development identity can never be exposed publicly.

## 3. Database
### 3a. Aurora (Terraform)
- Aurora PostgreSQL **16.x** (16.3 or later, required for scaling to zero), Serverless v2 capacity **0–2 ACU**, auto-pause after 10 minutes idle (`seconds_until_auto_pause = 600`). Waking takes about 15 seconds.
- **Data API enabled.** The master password is managed by RDS in Secrets Manager (`manage_master_user_password`); the Data API authenticates with that secret's ARN.
- In M2a's two private subnets, with a security group that has **no inbound rules**. The Data API is an HTTPS AWS API, so no workload ever opens a network connection to port 5432, and nothing needs to be VPC-attached. Access is controlled by IAM alone.
- Terraform outputs the cluster ARN, the secret ARN and the database name. They go into `config.json`'s `aws` block as `db_cluster_arn`, `db_secret_arn`, `db_name` (identifiers, not secrets), and into the worker's templated UserData.
- Backups: the default 1-day automated backup retention (free up to the database size).
- Terraform resources: `aws_db_subnet_group` (the two private subnets), `aws_rds_cluster` (`engine = "aurora-postgresql"`, `engine_mode = "provisioned"`, `enable_http_endpoint = true`, `serverlessv2_scaling_configuration { min_capacity = 0, max_capacity = 2, seconds_until_auto_pause = 600 }`), and one `aws_rds_cluster_instance` with `instance_class = "db.serverless"`. Pin an AWS provider version recent enough to support `seconds_until_auto_pause`. RDS rotates its managed secret every 7 days by default; the Data API reads the secret on each call, so rotation needs no handling.

### 3b. Money and durations
Money is stored as **integer cents** (`BIGINT`) and durations as **integer milliseconds**. This avoids decimal-type differences between the Data API and PostgreSQL, and rounding errors.
- Prices come from M1's `neurolens.pricing.estimate_cost_cents` ($0.90 per started 30-second block, with a half-second allowance so a "30-second" ad that ffprobe measures at 30.02 s is one block, not two).
- Starter credit: config `billing.starter_cents`, default **500** ($5.00, about five 30-second analyses).

### 3c. Schema (`infra/migrations/001_init.sql`)
```sql
CREATE TABLE users (
  user_id     TEXT PRIMARY KEY,               -- Google 'sub' from M3b; the dev user in M3a
  email       TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE balances (
  user_id          TEXT PRIMARY KEY REFERENCES users(user_id),
  available_cents  BIGINT NOT NULL CHECK (available_cents >= 0),
  reserved_cents   BIGINT NOT NULL CHECK (reserved_cents >= 0)
);

CREATE TABLE jobs (
  job_id                UUID PRIMARY KEY,         -- same UUID as the S3 upload key
  user_id               TEXT NOT NULL REFERENCES users(user_id),
  object_key            TEXT NOT NULL,
  filename              TEXT,                     -- display only
  status                TEXT NOT NULL CHECK (status IN ('queued','processing','done','failed')),
  stage                 TEXT,
  stages                JSONB NOT NULL DEFAULT '[]',
  attempt               INTEGER NOT NULL DEFAULT 0,
  client_duration_ms    INTEGER NOT NULL,
  verified_duration_ms  INTEGER,
  reserved_cents        BIGINT NOT NULL,
  captured_cents        BIGINT,
  error_code            TEXT,
  error_message         TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX jobs_user_created ON jobs (user_id, created_at DESC);
CREATE INDEX jobs_status_updated ON jobs (status, updated_at);

CREATE TABLE ledger (
  id                     BIGSERIAL PRIMARY KEY,
  user_id                TEXT NOT NULL REFERENCES users(user_id),
  job_id                 UUID REFERENCES jobs(job_id),
  kind                   TEXT NOT NULL CHECK (kind IN ('starter','reserve','adjust','capture','release','refund','test_topup')),
  available_delta_cents  BIGINT NOT NULL,
  reserved_delta_cents   BIGINT NOT NULL,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE refunds (
  job_id      UUID PRIMARY KEY REFERENCES jobs(job_id),   -- at most one refund per job, ever
  reason      TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```
- **Ledger invariant:** for every user, the sums of `available_delta_cents` and `reserved_delta_cents` over their ledger rows equal their `balances` row exactly. Every balance change writes its ledger row in the same transaction.
- `infra/apply_schema.py --backend data_api|postgres` applies `infra/migrations/*.sql` in order and records each in a `schema_migrations` table, so re-running it is safe. The Data API runs one statement per call, so migrations contain only plain statements (no functions or `DO` blocks), each ending with `;` at the end of a line, which is where the script splits. The tests apply the same files to their database, so the schema itself is tested.

### 3d. Database layer (`neurolens/db.py`)
One small interface, two backends, identical SQL:
- `Database.transaction()`: a context manager yielding a transaction object with `execute(sql: str, params: dict | None = None) -> list[dict]`. It commits on normal exit and rolls back on an exception.
- SQL uses `:name` parameters, and casts are written as `CAST(:job_id AS uuid)` / `CAST(:stages AS jsonb)` (never `::`), so both backends parse them the same way. The PostgreSQL backend rewrites `:name` to `%(name)s`. To keep that rewrite trivial, project SQL never contains `%` or string literals; values always go in as parameters. The rewrite raises if it finds either.
- Both backends return the same Python types per column: `int`, `str`, `bool`, `None`, `datetime` (timezone-aware, UTC) for `timestamptz`, `str` for `uuid`, and parsed Python objects for `jsonb`. The Data API backend uses the result metadata (`includeResultMetadata=True`) to convert: it returns integers as `longValue`, and `timestamptz` (as a zone-less UTC string), `uuid` and `jsonb` as `stringValue`.
- **`DataApiDatabase(rds_data_client, cluster_arn, secret_arn, database, resume_wait_s=60)`**: `begin_transaction` / `execute_statement(transactionId=...)` / `commit_transaction` / `rollback_transaction`. When the database is paused, the Data API raises `DatabaseResumingException`; `begin_transaction` and statements outside a transaction retry with backoff (1, 2, 4, 8, 16, … s) until `resume_wait_s` has passed, then raise `neurolens.db.DatabaseWaking`. Workers and Lambdas use the default 60 s; the web app uses a shorter budget (M3b) so a request never outlives its server's timeout. **Nothing else is retried**: in particular a failed `commit_transaction`, whose outcome is unknown, is raised to the caller, and the billing guards make the caller's retry safe. A Data API transaction left idle for 3 minutes is rolled back by AWS, which is far longer than any billing transaction.
- `infra/db_smoke.py` (§8) reads back a full `jobs` row through the Data API and compares the Python type of every column with what the PostgreSQL backend returns, so a wrong guess about the Data API's formats is caught on AWS, not in production.
- **`PostgresDatabase(dsn)`**: psycopg 3, imported lazily inside the class, so the Lambdas and the worker never need psycopg.
- `neurolens.db.from_config(cfg) -> Database`: config `db.backend` is `"data_api"` (AWS) or `"postgres"` (laptop and tests, with `db.dsn`).

## 4. Billing (`neurolens/billing.py`)
Every function takes a `Database` as its first argument and does its work in **one transaction**. Row locks are taken with `SELECT ... FOR UPDATE` on the `jobs` row, then the `balances` row, always in that order, so two functions can never deadlock each other.

### 4a. Functions (fixed interfaces)
**The attempt number is the worker's proof that it still holds a job.** `claim` returns a new `attempt`; every later worker-side call passes it and acts only `WHERE status='processing' AND attempt=:attempt` under the job's row lock. If another worker has re-claimed the job, or it was refunded meanwhile, the call changes nothing and reports that the claim was lost.

- `ensure_user(db, user_id, email, starter_cents) -> None`: `INSERT INTO users ... ON CONFLICT DO NOTHING RETURNING user_id`; only if a row was returned, also insert the balance row with the starter credit and one `starter` ledger row, in the same transaction. So two simultaneous first requests grant the starter credit once and neither fails. Calling it again changes nothing (not even the email).
- `get_balance(db, user_id) -> dict`: `{"available_cents", "reserved_cents"}`.
- `reserve(db, user_id, job_id, object_key, filename, client_duration_ms) -> int` — **Stage 1**. Price = `estimate_cost_cents(client_duration_ms / 1000)`. If `available_cents` is lower, raises `InsufficientCredit` and changes nothing. Otherwise moves the price from available to reserved, inserts the job (`status='queued'`, `reserved_cents`), writes a `reserve` ledger row, and returns the price.
- `claim(db, job_id, stale_after_s=90) -> int | None`: the lock that stops two workers running the same job. In one statement:
  `UPDATE jobs SET status='processing', attempt=attempt+1, updated_at=now() WHERE job_id=CAST(:job_id AS uuid) AND (status='queued' OR (status='processing' AND updated_at < now() - make_interval(secs => :stale))) RETURNING attempt`.
  Returns the new `attempt`, or `None` if no row was updated. The staleness clause lets a crashed worker's job be re-claimed when SQS redelivers it: the heartbeat refreshes `updated_at` every ≤50 s while a worker is alive, and a redelivery arrives no sooner than 120 s after the last heartbeat, so 90 s separates "alive" from "dead" safely.
- `job_state(db, job_id) -> dict | None`: `{"status", "updated_at", "attempt"}`, or `None` for an unknown job. The worker uses it to tell apart a lost claim on a finished job from one on a job another worker is running.
- `touch(db, job_id, attempt) -> bool`: sets `updated_at = now()`. Called on every heartbeat. `False` means the claim was lost.
- `set_stage(db, job_id, attempt, stage, now=None) -> bool`: sets `stage` and appends `{"stage", "at"}` to `stages`. `False` means the claim was lost.
- `verify(db, job_id, attempt, verified_duration_ms, max_duration_s) -> str` — **Stage 2**, called after ffprobe and before any inference. Returns `"ok"`, `"lost_claim"` (nothing changed; the worker stops), or the refund reason it applied:
  1. If the verified duration exceeds `max_duration_s`: `issue_refund(..., "duration_exceeds_max_verified", attempt=attempt)`. This check comes **before** any credit logic, whatever the balance.
  2. Record `verified_duration_ms`. Verified price = `estimate_cost_cents(verified_duration_ms / 1000)`.
  3. Equal to `reserved_cents`: nothing else. Higher: move the difference from available to reserved if the balance covers it (an `adjust` ledger row), else `issue_refund(..., "insufficient_credit_for_actual_duration", attempt=attempt)`. Lower: move the difference back to available at once (an `adjust` row).
  4. After this, `reserved_cents` on the job equals the verified price. It is the job's final price.
- `release_for_retry(db, job_id, attempt, error_message=None) -> bool`: sets `status='queued'`, `updated_at=now()` and records `error_message`. Money is untouched. Used when the worker hands a job back to the queue (failure, Spot interruption, shutdown), so the next worker can claim it at once. `False` means the claim was lost.
- `settle_success(db, job_id) -> bool` — **Stage 3**. Called only when `results/{job_id}.json` exists, so it has no attempt guard: a finished result is always charged, whoever finished it. Only for a job that is not `done` or `failed`. Captures `reserved_cents` (reserved decreases by it; a `capture` ledger row with available unchanged), sets `captured_cents`, `status='done'`, clears `error_code`/`error_message`. Returns `False` and changes nothing for a job already `done` or `failed`.
- `issue_refund(db, job_id, reason, message=None, *, attempt=None, queued_before_s=None, processing_stale_s=None) -> bool`. Only for a job that is not `done` or `failed`, **and** only if at least one of the given conditions holds when re-checked under the job's row lock:
  - `attempt`: `status='processing'` and the attempt matches (the worker that holds the claim);
  - `queued_before_s`: `status='queued'` and `updated_at` older than that many seconds (0 means any queued job);
  - `processing_stale_s`: `status='processing'` and `updated_at` older than that many seconds.

  Calling it with no condition raises `ValueError`, so every caller must state why the refund is safe. If it proceeds: moves `reserved_cents` back to available (a `refund` ledger row), inserts the `refunds` row, sets `status='failed'`, `error_code=reason`, `error_message=message`. Otherwise returns `False` and changes nothing. This closes the race where a reaper or dead-letter handler decides to refund, a worker claims the job a moment later, and the refund would land on a job being processed.

**Only `settle_success` and `issue_refund` set a terminal status or `error_code`, or release a reservation at the end of a job.** Because both lock the job row and both refuse a terminal job, a job is charged or refunded exactly once, never both.

`error_message` is shown to users: store a short message (the exception class and first line, at most 200 characters), never a traceback.

Refund reasons used across the system: `file_too_large`, `duration_exceeds_max_verified`, `insufficient_credit_for_actual_duration`, `processing_failed` (dead-letter queue), `stalled` (reaper), `upload_not_received` (reaper), `stuck_in_queue` (reaper), `presign_failed`.

## 5. Worker changes
**`Outcome` from M3a on** (replaces the M2b definitions):
- `DONE`: this worker published the result and it was charged.
- `DUPLICATE`: another worker published first; charged once all the same.
- `REJECTED`: refunded before inference (oversize, too long, not enough credit for the real length).
- `SKIPPED`: nothing to do: unknown job, result already existed, or the job is already `done`/`failed`.
- `BUSY` (**not final**): another worker holds a fresh claim on this job. `process_message` neither deletes nor releases the message; it becomes visible again after the 120 s visibility timeout, by which time the job is usually finished (and then `SKIPPED`).
- `LOST_CLAIM` (final): this worker lost its claim part-way (a `False`/`"lost_claim"` from billing). It stops and does not touch money; whoever holds the job finishes or refunds it.

Order inside `handle_record` for each record (`job_id` from the key):
1. **Unknown job** (`job_state` is `None`, e.g. a manual `aws s3 cp` or a pre-M3 upload): log and return `SKIPPED`.
2. **Result already exists:** call `settle_success` (a no-op unless an earlier worker crashed between writing the result and settling) and return `SKIPPED`.
3. `attempt = claim(...)`. If `None`: return `SKIPPED` if the job is `done` or `failed`, otherwise `BUSY`. Never `SKIPPED` for a job still in progress: if an earlier worker's `release_for_retry` failed (for example the NAT instance was down during `SIGTERM`), deleting the message would lose the job; leaving it lets the claim go stale and be re-claimed.
4. From here, everything runs inside `Heartbeat(..., on_beat=lambda: billing.touch(db, job_id, attempt))`.
5. Oversize (`head_object`): `issue_refund(..., "file_too_large", attempt=attempt)`, delete the object, return `REJECTED` (or `LOST_CLAIM` if the refund returned `False`).
6. Download, `probe_duration`, then `verify(..., attempt, ...)`. `"lost_claim"` → `LOST_CLAIM`. A refund reason → delete the object and return `REJECTED` without running inference.
7. Run the pipeline, calling `set_stage(db, job_id, attempt, ...)` at each transition (`downloading`, `inference_full`, `stripping_audio`, `inference_noaudio`, `extracting_roi`). A `False` → stop with `LOST_CLAIM`.
8. `put_result(...)`. Whether it returns `True` (`DONE`) or `False` (`DUPLICATE`), call `settle_success`. It is idempotent and makes sure a finished job is always charged.

Also:
- `Heartbeat` gains an `on_beat: Callable[[], None] | None` parameter. Each beat **first** extends SQS visibility, **then** calls `on_beat`. The first beat happens after one interval, not immediately. An exception from `on_beat` is logged and does not stop the heartbeat thread. `worker.heartbeat_seconds` must be at most 50; `run()` refuses to start otherwise.
- On any exception in a record, and on Spot interruption or `SIGTERM`: `release_for_retry(db, job_id, attempt, error)` and then M2b's SQS `release(...)`. After two receives SQS moves the message to the dead-letter queue, whose Lambda refunds it (§7).
- **S3 status objects are retired:** remove `get_status`/`put_status`, their use in the worker, and `status/*` from the worker's IAM policy and `s3:ListBucket` prefix list. `result_exists`/`get_result` stay. Before this lands, Experiment 1's stage timings must already be saved under `experiments/experiment-1/` (M2b §8), because `status/` objects expire after 48 hours. Later runs read `jobs.stages` from Aurora.
- Worker IAM additions: `rds-data:ExecuteStatement`, `BeginTransaction`, `CommitTransaction`, `RollbackTransaction` on the cluster ARN; `secretsmanager:GetSecretValue` on the database secret ARN. Data API calls go out through the NAT instance (small JSON requests).
- No image rebuild: the Data API needs only `boto3`, already installed.

## 6. Web changes (still on the laptop, `127.0.0.1`)
Every route below calls `current_user()`. `ensure_user` runs the first time each user ID is seen by this web process (a per-process set of known IDs), not on every request, so pages don't cost a database write each time.
- **Presign interface change** (replaces M1's `presign_upload`; M1's presign tests are updated by the test-writing agent as a spec'd change):
  - `neurolens.storage.object_key(user_id, job_id, content_type) -> str`: `uploads/{user_id}/{job_id}{ext}`, extension from `content_type` as in M1. This retires `placeholder-user`.
  - `neurolens.storage.presign_upload(s3, bucket, object_key, max_bytes, expires_in=300) -> dict`: `{"url", "fields", "expires_in"}`. The route creates `job_id = uuid4()` itself.
- `POST /api/uploads/presign`: M1's validation, plus `client_duration_seconds` must be at least 1 (a zero-length job would cost nothing yet start a GPU). `client_duration_ms = round(client_duration_seconds * 1000)`. Then `reserve(...)` **before** creating the presigned POST.
  - Not enough credit: **402** `{"error": "insufficient_credit", "message": ..., "available_cents": ..., "required_cents": ...}`.
  - If creating the presigned POST fails after the reservation: `issue_refund(..., "presign_failed", queued_before_s=0)`, then 500 `presign_failed`.
  - The success body is M1's plus `reserved_cents`; `estimated_cost_usd` stays for display.
- `GET /api/me`: `{"email", "available_cents", "reserved_cents"}`.
- `GET /api/jobs/<job_id>/status` (replaces M2b's S3 version): 400 for a non-UUID. **404 unless the job exists and belongs to the current user**, checked in Aurora *before* any S3 request, so a job's existence never leaks to another user. Otherwise returns `{"job_id", "status", "stage", "stages", "attempt", "error_code", "error_message"}`, except that a non-terminal job whose `results/{job_id}.json` exists is reported as `done` (the crash window before settlement).
- `GET /api/jobs/<job_id>/result`: the same ownership check, then **404 unless the job is `done`, or not yet terminal with a result in S3** (the crash window). A refunded (`failed`) job never serves a result, even if a late worker wrote one, so nobody gets a refunded result for free.
- Frontend (minimal; M3b refines the messages): show the balance from `/api/me`; on a 402, show "Not enough credit" instead of uploading; poll status as before, treating `queued` like M2b treated 404; after `done`, refresh the balance. **Polling stops** after 15 minutes, and pauses while the browser tab is hidden (`document.visibilityState`), so a forgotten tab cannot keep Aurora awake.

## 7. Lambdas (`neurolens/lambdas/`)
Both use Python 3.12, `DataApiDatabase`, and only pure-Python modules (`neurolens.db`, `billing`, `pricing`, `storage`, `settings`), which must not import `numpy`. They are **not** VPC-attached. `infra/build_lambdas.sh` zips `neurolens/__init__.py`, those modules and `neurolens/lambdas/` from the last commit (`git archive`), and Terraform deploys the zip. Configuration comes from environment variables set by Terraform: cluster ARN, secret ARN, database name, bucket.

- **`dlq_handler.handler(event, context)`**: SQS event source on the dead-letter queue, batch size 1. For each job in the S3 event:
  - unknown job, or already `done`/`failed`: nothing to do;
  - `results/{job_id}.json` exists: `settle_success`;
  - otherwise `issue_refund(..., "processing_failed", <the job's last error_message>, queued_before_s=0, processing_stale_s=90)`. If that returns `False` because a worker holds a fresh claim (a duplicate message for a job still running), **raise**, so the message is retried later, by which time the job has finished or gone stale.

  The handler returns normally (and SQS deletes the message) only when every job in it is settled, refunded or already terminal.
- **`reaper.handler(event, context)`**, every 5 minutes. Both rules measure age from **`updated_at`**, which `claim`, every heartbeat and `release_for_retry` refresh, so a job just handed back to the queue is not mistaken for an old one. For each job found:
  - `processing`, `updated_at` more than **10 minutes** ago (no heartbeat, and no redelivery re-claimed it): result exists → `settle_success`, otherwise `issue_refund(..., "stalled", processing_stale_s=600)`.
  - `queued`, `updated_at` more than **60 minutes** ago: result exists → `settle_success`; else if the upload object does not exist → `issue_refund(..., "upload_not_received", queued_before_s=3600)` (the presigned POST expired after 5 minutes, so it never will); else `issue_refund(..., "stuck_in_queue", queued_before_s=3600)`.
  - The conditions are re-checked under the row lock inside `issue_refund`, so a job claimed by a worker between the reaper's query and its refund is left alone.
  - Any job refunded by the reaper that a worker later receives fails `claim` and is skipped, so it is never processed for free.
- **Lambda settings:** timeout **120 s** (the database wake-up retry alone can take about 60 s), memory 256 MB. The dead-letter queue's visibility timeout is at least the function timeout (AWS rejects the event-source mapping otherwise) and its message retention is **14 days**. The reaper is the backstop for anything the dead-letter handler cannot finish.
- **Cost trap:** a schedule that queries Aurora every 5 minutes would stop it ever pausing, costing about $1.40 a day at 0.5 ACU. So the EventBridge rule is created **disabled** (Terraform ignores changes to its state). `start_work.sh` enables it; `stop_work.sh` invokes the reaper once, then disables it. Between sessions, abandoned reservations simply wait for the next session.
- Lambda IAM: the same `rds-data` and secret permissions as the worker; `s3:GetObject` on `results/*` and `uploads/*` with `s3:ListBucket` for those prefixes (so a missing object reads as 404, not 403); for the DLQ handler, `sqs:ReceiveMessage`, `DeleteMessage`, `GetQueueAttributes` on the dead-letter queue; CloudWatch Logs.

## 8. Rehearsal on AWS
1. `terraform apply` for Aurora and the Lambdas; `apply_schema.py --backend data_api`.
2. `infra/db_smoke.py`: runs `ensure_user`, `reserve`, `claim`, `settle_success` for a throwaway user against the real Data API, checks the ledger invariant, and prints the time of the first call (includes waking from pause).
3. Fake-mode end-to-end: laptop web with `db.backend = "data_api"`, the CPU rehearsal worker from M2a. Upload → reserve → claim → stages → result → capture, visible in `/api/me`.
4. Force the failure paths once each: an over-long video (refund before inference), a job failing twice (dead-letter refund), a worker stopped mid-job (redelivery re-claims via staleness).
5. One real GPU job, confirming the charged amount matches the verified duration.

## 9. Configuration additions (`config.sample.json`)
`aws.db_cluster_arn`, `aws.db_secret_arn`, `aws.db_name`; `db.backend`, `db.dsn` (postgres only); `auth.mode`, `auth.dev_user_id`, `auth.dev_email`; `billing.starter_cents`. None of these are secrets: the database password lives only in Secrets Manager.

## 10. Tests (tests-first; `FAKE_INFERENCE=1 pytest`)
Database tests run against a real **PostgreSQL 16**: a service container in CI, `docker run postgres:16` locally (`NEUROLENS_TEST_DSN`). Each test gets a fresh schema from `infra/migrations/`. `dev.txt` gains `psycopg[binary]`. Required:
- **Invariants, checked after every test** in a shared fixture teardown, for every user:
  1. ledger sums equal the `balances` row;
  2. no balance is negative;
  3. `reserved_cents` equals the sum of `reserved_cents` over the user's `queued` and `processing` jobs;
  4. every `done` job has exactly one `capture` ledger row and no `refunds` row, and every `failed` job has exactly one `refund` ledger row and one `refunds` row.
- **Stage 1:** exact reservation and ledger row; `InsufficientCredit` leaves everything unchanged; two threads calling `ensure_user` for a new user at once both succeed and grant the starter credit once.
- **Stage 2:** all branches (equal, higher and covered, higher and not covered, lower, over the maximum). The over-maximum case refunds even when the balance is large, and before any adjustment.
- **Stage 3:** capture of exactly the verified price.
- **Exclusivity:** `settle_success` then `issue_refund` (and the reverse) gives one terminal outcome; repeated calls are no-ops; `issue_refund` with no condition raises `ValueError`.
- **Attempt guards:** after a job is re-claimed (attempt 2), every call with attempt 1 (`touch`, `set_stage`, `verify`, `release_for_retry`, `issue_refund(attempt=1)`) returns `False`/`"lost_claim"` and moves no money. After a reaper refund, `verify` with the old attempt returns `"lost_claim"` and moves no money.
- **Refund guards:** a reaper-style `issue_refund(queued_before_s=3600)` on a job that a worker has just claimed returns `False`; the DLQ-style call on a freshly claimed `processing` job returns `False`, on a stale one succeeds.
- **Concurrency, made deterministic:** a third connection holds `SELECT ... FOR UPDATE` on the job row; two threads then call `settle_success` and `issue_refund` (and, separately, two `claim`s); the lock is released and both finish. Exactly one terminal outcome, and exactly one claim returns an attempt.
- **Claim staleness:** a `processing` job touched 30 s ago cannot be claimed; one touched 120 s ago can (set `updated_at` directly).
- **Data API backend** with botocore's `Stubber`, using the response formats in §3d:
  - parameters and `CAST` sent correctly;
  - every column type converted to the same Python types as the PostgreSQL backend;
  - `DatabaseResumingException` on `begin_transaction` retried and then succeeding;
  - a failing `commit_transaction` raised, not retried;
  - rollback on an exception.
- **SQL rewrite:** `%` or a string literal in SQL raises.
- **Heartbeat:** visibility is extended before `on_beat`; an `on_beat` exception is logged and beats continue; no beat before the first interval.
- **Worker** (moto + PostgreSQL): each §5 path gives the right outcome and money movement: unknown job, result exists, claim lost on a finished job (`SKIPPED`, message deleted), claim lost on a fresh `processing` job (`BUSY`, message neither deleted nor released), oversize, too long, insufficient for actual duration, `LOST_CLAIM` part-way, success, duplicate. A failing record calls `release_for_retry` and leaves money reserved.
- **Lambdas** (moto + PostgreSQL, handlers called directly):
  - A dead-letter job with a result is settled; without one it is refunded; for a freshly claimed job the handler raises; running it twice changes nothing.
  - Each reaper rule, including "upload not received".
  - A job released for retry 5 minutes ago but created 2 hours ago is **not** reaped.
- **Web:**
  - 402 with no reservation made; `client_duration_seconds` below 1 rejected;
  - the presign key contains the user's ID;
  - status and result return 404 for another user's job and make **no S3 call** first (assert with a stubbed client);
  - `done` reported from a result during the crash window;
  - `/result` returns 404 for a `failed` job even when a result object exists;
  - dev mode on a non-local host raises `UnsafeConfigError`;
  - `ensure_user` is called once per user per process.
- **Import hygiene:** `neurolens.db`, `billing`, `pricing`, `storage`, `settings` and `neurolens.lambdas.*` import without `numpy`, `psycopg` or `neurolens.inference`.
- **Replaced M2b tests** (a spec'd test change by the test-writing agent): `put_status` and its `stages` test; "a record that raises leads to status failed"; "a lost conditional write leaves the done status untouched"; "a record whose result exists is skipped with no status change"; the S3-backed status endpoint tests. Each gets its Aurora equivalent above.

**Break-it check** (mandatory), one change at a time, then undo:
- remove the terminal-status guard **and** `FOR UPDATE` from `settle_success`: the concurrency and exclusivity tests fail;
- drop the staleness clause from `claim`: the staleness test fails;
- drop the attempt condition from `verify`: the attempt-guard test fails;
- drop the re-check in `issue_refund`: the refund-guard test fails;
- move the Stage 2 duration check after the credit check: the over-maximum test fails.

## 11. File layout additions
```
neurolens/db.py                  NEW: Database interface, DataApiDatabase, PostgresDatabase
neurolens/billing.py             NEW: §4 functions
neurolens/lambdas/dlq_handler.py NEW
neurolens/lambdas/reaper.py      NEW
neurolens/web/auth.py            NEW: current_user() (dev mode)
neurolens/pricing.py             MODIFIED: estimate_cost_cents
neurolens/worker.py              MODIFIED: §5
neurolens/storage.py             MODIFIED: status helpers removed
neurolens/web/app.py             MODIFIED: §6
infra/migrations/001_init.sql    NEW
infra/apply_schema.py            NEW
infra/build_lambdas.sh           NEW
infra/db_smoke.py                NEW
infra/terraform/                 MODIFIED: Aurora, Lambdas, EventBridge rule, IAM
infra/start_work.sh, stop_work.sh  MODIFIED: reaper rule on/off
static/                          MODIFIED: balance, 402, queued
tests/                           MODIFIED: §10
```

## 12. Acceptance criteria
1. `apply_schema.py` creates the schema on Aurora and on local PostgreSQL, and a second run changes nothing.
2. A job whose verified duration matches the estimate is reserved, then captured for the same amount; `available_cents` falls by exactly that price, confirmed against `ledger` rows, not just the balance.
3. A job whose client-reported duration was too short, for a user who cannot cover the difference, fails with `insufficient_credit_for_actual_duration`, is fully refunded, and runs **no** inference (worker logs show no `run_inference` call).
4. A video whose verified duration exceeds 120 s fails with `duration_exceeds_max_verified` before inference, whatever the balance, with exactly one refund even if the dead-letter path also fires.
5. A job that fails twice reaches the dead-letter queue and is refunded by the Lambda without manual steps.
6. A job left `processing` with no heartbeat for 10 minutes is refunded by the reaper within one run; a job left `queued` with no upload is refunded as `upload_not_received`.
7. A crash after writing the result but before settling is reported `done` to its owner and charged (not refunded) by the reaper or the dead-letter handler.
8. Two workers claiming one job: exactly one runs it. A worker stopped mid-job has its job re-claimed and completed after redelivery, and the stopped worker's later calls (if it comes back) move no money.
8a. A refunded job never serves its result, and a job claimed between a reaper's query and its refund is not refunded.
9. Concurrent `settle_success` and `issue_refund` on one job always end in exactly one outcome (the concurrency test, and one manual run against Aurora).
10. Another user's job returns 404 from the status and result endpoints, with no S3 request made.
11. An IAM principal without `rds-data` permissions cannot read the database; Aurora's security group has no inbound rules.
12. With `stop_work.sh` run, the reaper rule is disabled and Aurora pauses (visible as 0 ACU in CloudWatch after about 10 minutes).
13. `FAKE_INFERENCE=1 pytest` passes locally and in CI, the tests were committed before the implementation, and the break-it check was done.

## 13. Cost
- Aurora: about $0.12 per ACU-hour **only while awake** (it wakes for work sessions and jobs), storage about $0.10 per GB-month (the database is tiny), backups free at this size.
- Secrets Manager: $0.40 a month for the database secret.
- Data API, Lambda and EventBridge: free tier at this volume.
- The main risk is Aurora never pausing. §7's reaper switch and acceptance criterion 12 guard it; check the ACU graph after the first session.

## 14. Explicitly not in this milestone
Everything listed for M3b in §1. No live payments of any kind. No changes to the model, the image, or the scaling rules.
