# NeuroLens — M3a Implementation Spec
**Milestone:** Aurora database, credit billing and refunds (first part of 20 – 30 Oct)
**Builds on:** M2b (on-demand GPU workers scaled on the queue, with heartbeat, fast release, SIGTERM handling, scale-in protection and the circuit breaker; SQS with a dead-letter queue; results in `results/{job_id}.json` via conditional write; S3 status objects and status/result endpoints; real polling UI). M3a adds an Aurora PostgreSQL database that owns users, credit balances and job state, implements three-stage credit billing with guaranteed refunds, and moves job status out of S3. Sign-in, the public website and job history are M3b; until then a fixed development user stands in (§2).

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
- `create_app` **refuses to start** in dev mode unless the configured `server.host` (§9) is `127.0.0.1`, raising `neurolens.settings.UnsafeConfigError`, so a development identity can never be exposed publicly.

## 3. Database
### 3a. Aurora (Terraform)
- Aurora PostgreSQL **16.x** (16.3 or later, required for scaling to zero), Serverless v2 capacity **0–2 ACU**, auto-pause after 10 minutes idle (`seconds_until_auto_pause = 600`). Waking takes about 15 seconds.
- **Data API enabled.** The master password is managed by RDS in Secrets Manager (`manage_master_user_password`); the Data API authenticates with that secret's ARN.
- In M2a's private subnets, with a security group that has **no inbound rules**. The Data API is an HTTPS AWS API, so no workload ever opens a network connection to port 5432, and nothing needs to be VPC-attached. Access is controlled by IAM alone.
- Terraform outputs the cluster ARN, the secret ARN and the database name. They are deployment-specific identifiers (not secrets), so, like the bucket and queue (M1 §2), they live in environment variables, never in the committed `config.json`: `NEUROLENS_DB_CLUSTER_ARN`, `NEUROLENS_DB_SECRET_ARN`, `NEUROLENS_DB_NAME` (in `.env` on a laptop, in `env.conf` on AWS through the templated UserData). `settings.load_settings` overlays them onto `aws.db_cluster_arn`, `aws.db_secret_arn`, `aws.db_name`.
- Backups: the default 1-day automated backup retention (free up to the database size). The cluster sets `skip_final_snapshot = true` and `delete_automated_backups = true`, so `terraform destroy` works without a manual step and deleting the cluster really deletes the data (M4's teardown relies on this).
- Terraform resources: `aws_db_subnet_group` (the private subnets), `aws_rds_cluster` (`engine = "aurora-postgresql"`, `engine_mode = "provisioned"`, `enable_http_endpoint = true`, `serverlessv2_scaling_configuration { min_capacity = 0, max_capacity = 2, seconds_until_auto_pause = 600 }`), and one `aws_rds_cluster_instance` with `instance_class = "db.serverless"`. Pin an AWS provider version recent enough to support `seconds_until_auto_pause`. RDS rotates its managed secret every 7 days by default; the Data API reads the secret on each call, so rotation needs no handling.

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
- SQL uses `:name` parameters, and casts are written as `CAST(:job_id AS uuid)` / `CAST(:stages AS jsonb)` (never `::`), so both backends parse them the same way. The PostgreSQL backend rewrites `:name` to `%(name)s`. To keep that rewrite trivial, application SQL never contains `%` or string literals; values, including status names such as `processing`, always go in as parameters, and the rewrite raises if it finds either. Migrations are the exception: `apply_schema.py` sends each migration statement as-is (CHECK constraints need literal values), never through the rewrite.
- Both backends return the same Python types per column: `int`, `str`, `bool`, `None`, `datetime` (timezone-aware, UTC) for `timestamptz`, `str` for `uuid`, and parsed Python objects for `jsonb`. The Data API backend uses the result metadata (`includeResultMetadata=True`) to convert: it returns integers as `longValue`, and `timestamptz` (as a zone-less UTC string), `uuid` and `jsonb` as `stringValue`.
- **`DataApiDatabase(rds_data_client, cluster_arn, secret_arn, database, resume_wait_s=60)`**: `begin_transaction` / `execute_statement(transactionId=...)` / `commit_transaction` / `rollback_transaction`. When the database is paused, the Data API raises `DatabaseResumingException`, and while it resumes it can also answer `ThrottlingException` ("insufficient resources on the database"; seen live on the hourly reaper). On either code `begin_transaction` retries with backoff (1, 2, 4, 8, 16, … s) until `resume_wait_s` has passed, then raise `neurolens.db.DatabaseWaking`. Workers and Lambdas use the default 60 s; the web app uses a shorter budget (M3b) so a request never outlives its server's timeout. **Nothing else is retried**: in particular a failed `commit_transaction`, whose outcome is unknown, is raised to the caller, and the billing guards make the caller's retry safe. The rds-data client is built by `neurolens.db.data_api_client()` with botocore's own retries off (`total_max_attempts = 1`), and `DataApiDatabase` refuses any other client: botocore would otherwise re-send a statement whose response was lost, running it twice inside the same transaction. A Data API transaction left idle for 3 minutes is rolled back by AWS, which is far longer than any billing transaction.
- `infra/db_smoke.py --backend data_api|postgres` (§8) runs one job through reserve, claim, stage, verify and capture and a second through touch, release and refund, checks the ledger invariant, and reads back a full `jobs` row comparing the Python type of every column with what the PostgreSQL backend returns (the same script passes on local PostgreSQL), so a wrong guess about the Data API's formats is caught on AWS, not in production.
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
  `UPDATE jobs SET status=:processing, attempt=attempt+1, stage=NULL, stages=jsonb_build_array(), updated_at=now() WHERE job_id=CAST(:job_id AS uuid) AND (status=:queued OR (status=:processing AND updated_at < now() - make_interval(secs => :stale))) RETURNING attempt`, with `:processing` also used in the `SET`.
  Returns the new `attempt`, or `None` if no row was updated. Each attempt starts with an empty `stages` list, so a retried job's timings describe its last attempt only. The staleness clause lets a crashed worker's job be re-claimed when SQS redelivers it: the heartbeat refreshes `updated_at` every ≤50 s while a worker is alive, and a redelivery arrives no sooner than 120 s after the last heartbeat, so 90 s separates "alive" from "dead" safely.
- `job_state(db, job_id) -> dict | None`: `{"status", "updated_at", "attempt"}`, or `None` for an unknown job. The worker uses it to tell apart a lost claim on a finished job from one on a job another worker is running.
- `touch(db, job_id, attempt) -> bool`: sets `updated_at = now()`. Called on every heartbeat. `False` means the claim was lost.
- `set_stage(db, job_id, attempt, stage, now=None) -> bool`: sets `stage` and appends `{"stage", "at"}` to `stages`, with `at` as UTC text in M2b §4's format (`2026-10-14T03:22:10Z`), so Experiment 1's tools read Aurora's stages exactly as they read the old status objects. `False` means the claim was lost.
- `verify(db, job_id, attempt, verified_duration_ms, max_duration_s) -> str` — **Stage 2**, called after ffprobe and before any inference. Returns `"ok"`, `"lost_claim"` (nothing changed; the worker stops), or the refund reason it applied:
  1. If the verified duration exceeds `max_duration_s`: `issue_refund(..., "duration_exceeds_max_verified", attempt=attempt)`. This check comes **before** any credit logic, whatever the balance.
  2. Record `verified_duration_ms`. Verified price = `estimate_cost_cents(verified_duration_ms / 1000)`.
  3. Equal to `reserved_cents`: nothing else. Higher: move the difference from available to reserved if the balance covers it (an `adjust` ledger row), else `issue_refund(..., "insufficient_credit_for_actual_duration", attempt=attempt)`. Lower: move the difference back to available at once (an `adjust` row).
  4. After this, `reserved_cents` on the job equals the verified price. It is the job's final price.
- `release_for_retry(db, job_id, attempt, error_message=None) -> bool`: sets `status='queued'`, `updated_at=now()` and records `error_message`. Money is untouched. Used when the worker hands a job back to the queue (failure or shutdown), so the next worker can claim it at once. `False` means the claim was lost.
- `settle_success(db, job_id) -> bool` — **Stage 3**. Called only when `results/{job_id}.json` exists, so it has no attempt guard: a finished result is always charged, whoever finished it. Only for a job that is not `done` or `failed`. Captures `reserved_cents` (reserved decreases by it; a `capture` ledger row with available unchanged), sets `captured_cents`, `status='done'`, `updated_at=now()` (which marks the end of the last stage for timing), clears `error_code`/`error_message`. Returns `False` and changes nothing for a job already `done` or `failed`.
- `issue_refund(db, job_id, reason, message=None, *, attempt=None, queued_before_s=None, processing_stale_s=None) -> bool`. Only for a job that is not `done` or `failed`, **and** only if at least one of the given conditions holds when re-checked under the job's row lock:
  - `attempt`: `status='processing'` and the attempt matches (the worker that holds the claim);
  - `queued_before_s`: `status='queued'` and `updated_at` older than that many seconds (0 means any queued job);
  - `processing_stale_s`: `status='processing'` and `updated_at` older than that many seconds.

  Calling it with no condition raises `ValueError`, so every caller must state why the refund is safe. If it proceeds: moves `reserved_cents` back to available (a `refund` ledger row), inserts the `refunds` row, sets `status='failed'`, `error_code=reason`, `error_message=message`. Otherwise returns `False` and changes nothing. This closes the race where a reaper or dead-letter handler decides to refund, a worker claims the job a moment later, and the refund would land on a job being processed.

**Only `settle_success` and `issue_refund` set a terminal status or `error_code`, or release a reservation at the end of a job.** Because both lock the job row and both refuse a terminal job, a job is charged or refunded exactly once, never both.

`error_message` is shown to users: store a short message (the exception class and first line, at most 200 characters), never a traceback.

Refund reasons used across the system: `file_too_large`, `unreadable_video`, `upload_missing` (worker: the upload is gone after the claim), `duration_exceeds_max_verified`, `insufficient_credit_for_actual_duration`, `processing_failed` (dead-letter queue), `stalled` (reaper), `upload_not_received` (reaper), `presign_failed`.

## 5. Worker changes
**`Outcome` from M3a on** (replaces the M2b definitions):
- `DONE`: this worker published the result and it was charged.
- `DUPLICATE`: another worker published first; charged once all the same.
- `REJECTED`: refunded before inference (oversize, unreadable, too long, not enough credit for the real length, or the upload is gone).
- `SKIPPED`: nothing to do: unknown job, result already existed, or the job is already `done`/`failed`. M2b's `GONE` is removed: a missing upload after a claim is refunded (`REJECTED`), and a duplicate notice for a job already rejected fails its claim (`SKIPPED`).
- `BUSY` (**not final**): another worker holds a fresh claim on this job. `process_message` neither deletes nor releases the message; it becomes visible again after the 120 s visibility timeout, by which time the job is usually finished (and then `SKIPPED`).
- `LOST_CLAIM` (final): this worker lost its claim part-way (a `False`/`"lost_claim"` from billing). It stops and does not touch money; whoever holds the job finishes or refunds it.

**Worker interfaces from M3a on** (replace M2b §1a's; the tests are written against these):
- `handle_record(bucket, key, *, s3, db, cfg, roi_masks, heartbeat) -> Outcome`: as M2b, plus `db`. `heartbeat(on_beat)` now takes the `on_beat` callback and returns the `Heartbeat` for the current message. If anything raises after a successful `claim` (including `ShutdownRequested`), `handle_record` itself calls `release_for_retry(db, job_id, attempt, short_error)` (for a shutdown, M2b's "The job was interrupted. Please upload it again.", so a job dead-lettered after a shutdown still shows a reason) and re-raises, so the attempt number never has to leave the function. A failing `release_for_retry` is logged and never replaces the original exception.
- `process_message(message, *, s3, sqs, db, cfg, roi_masks, shutdown, max_receives) -> None`: as M2b, plus `db`. It deletes the message only when every record returned a **final** outcome (`BUSY` is not final); on an exception (and on `ShutdownRequested`, which it re-raises as in M2b) it calls M2b's SQS `release(...)` and does not delete. It no longer writes any status itself: M2b's `_write_failure_status`, `_hand_back`'s final-attempt status and `put_status` calls go. `max_receives` is kept only for the log line ("attempt 1 of 2"); the dead-letter handler (§7) does what the final-attempt status did.
- `poll_once(*, s3, sqs, db, cfg, roi_masks, shutdown, max_receives, protection=None) -> bool`: as M2b, plus `db`, passed on to `process_message`.
- `run() -> None`: as M2b, and also builds the database with `neurolens.db.from_config(cfg)`.

Order inside `handle_record` for each record (`job_id` from the key):
1. **Unknown job** (the file name is not a job UUID, or `job_state` is `None`, e.g. a manual `aws s3 cp` or a pre-M3 upload): log and return `SKIPPED`. The dead-letter handler treats it the same way (nothing to do).
2. **Result already exists:** call `settle_success` (a no-op unless an earlier worker crashed between writing the result and settling) and return `SKIPPED`.
3. `attempt = claim(...)`. If `None`: return `SKIPPED` if the job is `done` or `failed`, otherwise `BUSY`. Never `SKIPPED` for a job still in progress: if an earlier worker's `release_for_retry` failed (for example the NAT Gateway was missing during `SIGTERM`), deleting the message would lose the job; leaving it lets the claim go stale and be re-claimed.
4. From here, everything runs inside `Heartbeat(..., on_beat=lambda: billing.touch(db, job_id, attempt))`.
5. `head_object`. Upload missing (404): `issue_refund(..., "upload_missing", attempt=attempt)`, return `REJECTED`. Oversize: `issue_refund(..., "file_too_large", attempt=attempt)`, delete the object, return `REJECTED`. Either refund returning `False` gives `LOST_CLAIM`. (A queued job can outlive its upload: uploads expire after 2 days, queue messages after 4.)
6. Download (a 404 here is handled as in step 5), `probe_duration`, then `verify(..., attempt, ...)`. `UnreadableVideo` (not a video, corrupt, audio-only): `issue_refund(..., "unreadable_video", attempt=attempt)`, delete the object, return `REJECTED`; it can never succeed, so it is not retried. `"lost_claim"` → `LOST_CLAIM`. A refund reason → delete the object and return `REJECTED` without running inference.
7. Run the pipeline, calling `set_stage(db, job_id, attempt, ...)` at each transition (`downloading`, `transcribing`, `inference_full`, `inference_noaudio`, `extracting_roi`). A `False` → stop with `LOST_CLAIM`.
8. `put_result(...)`. Whether it returns `True` (`DONE`) or `False` (`DUPLICATE`), call `settle_success`. It is idempotent and makes sure a finished job is always charged.

Also:
- `Heartbeat` gains an `on_beat: Callable[[], None] | None` parameter. Each beat **first** extends SQS visibility, **then** calls `on_beat`. The first beat happens after one interval, not immediately. An exception from `on_beat` is logged and does not stop the heartbeat thread. `on_beat` runs **after the beat releases its lock**, and exit waits only for a visibility call already in progress (by taking the lock and setting the stop flag), **not** for the thread to end, so it does not wait for a running `on_beat` (this replaces M2b's "then waits for the thread"; the thread is a daemon, and checks the flag before every call). Reason: a database call can wait up to 60 s for Aurora to wake (§3d), and exit must not wait behind it (systemd allows 110 s to stop). This is safe because a late `touch` after the job was released or settled changes nothing (it requires `status='processing'` and the same attempt). `worker.heartbeat_seconds` must be at most 50; `run()` refuses to start otherwise.
- On any exception in a record, and on shutdown (`SIGTERM`: scale-in, `stop_work.sh`, `systemctl stop`): `handle_record` calls `release_for_retry(db, job_id, attempt, error)`, then `process_message` calls M2b's SQS `release(...)`. After two receives SQS moves the message to the dead-letter queue, whose Lambda refunds it (§7).
- **S3 status objects are retired:** remove `get_status`/`put_status`, their use in the worker, and `status/*` from the worker's IAM policy and `s3:ListBucket` prefix list. `result_exists`/`get_result` stay. `experiments/latency_run.py` stops reading the S3 status object and reads `stages` from the status endpoint instead (§6 returns it, including for a `done` job), so Experiment 1 works before or after M3a; its stage-duration helper and the `runs.csv` columns are unchanged, with the job's end time taken from the endpoint's `updated_at`. The S3 lifecycle rule for `status/` is removed.
- Worker IAM additions: `rds-data:ExecuteStatement`, `BeginTransaction`, `CommitTransaction`, `RollbackTransaction` on the cluster ARN; `secretsmanager:GetSecretValue` on the database secret ARN. Data API calls go out through the NAT Gateway (small JSON requests).
- No image rebuild: the Data API needs only `boto3`, already installed.

## 6. Web changes (still on the laptop, `127.0.0.1`)
**`create_app` from M3a on:** `create_app(*, cfg=None, data_dir=None, db=None, s3_client=None)` (M3b adds what Google sign-in needs). Without `cfg`, it uses dev mode with a built-in dev user (`dev-user`, `dev@localhost`) and `server.host = 127.0.0.1`, so M0/M1's web tests keep working unchanged. `db` and `s3_client` may be passed in (tests hand in a PostgreSQL database and a stubbed or `moto` client); otherwise they are built from config. A route that needs a database or S3 client the app doesn't have returns 500 `{"error": "not_configured"}`.

Every route below calls `current_user()`. `ensure_user` runs the first time each user ID is seen by this web process (a set of known IDs belonging to each app `create_app` builds, so a new app, as in each test, starts empty), not on every request, so pages don't cost a database write each time.
- **Presign interface change** (replaces M1's `presign_upload`; M1's presign tests are updated by the test-writing agent as a spec'd change):
  - `neurolens.storage.object_key(user_id, job_id, content_type) -> str`: `uploads/{user_id}/{job_id}{ext}`, extension from `content_type` as in M1. This retires `placeholder-user`.
  - `neurolens.storage.presign_upload(s3, bucket, object_key, max_bytes, expires_in=300) -> dict`: `{"url", "fields", "expires_in"}`. The signed form still pins `Content-Type` (as M1), derived from the key's extension (the reverse of `CONTENT_TYPE_EXTENSIONS`). The route creates `job_id = uuid4()` itself.
- `POST /api/uploads/presign`: M1's validation, plus `client_duration_seconds` must be at least 1 (a zero-length job would cost nothing yet start a GPU). `client_duration_ms = round(client_duration_seconds * 1000)`. Then `reserve(...)` **before** creating the presigned POST.
  - Not enough credit: **402** `{"error": "insufficient_credit", "message": ..., "available_cents": ..., "required_cents": ...}`.
  - If creating the presigned POST fails after the reservation: `issue_refund(..., "presign_failed", queued_before_s=0)`, then 500 `presign_failed`.
  - The success body is M1's plus `reserved_cents`; `estimated_cost_usd` stays for display.
- `GET /api/me`: `{"email", "available_cents", "reserved_cents"}`.
- `GET /api/jobs/<job_id>/status` (replaces M2b's S3 version): 400 for a non-UUID. **404 unless the job exists and belongs to the current user**, checked in Aurora *before* any S3 request, so a job's existence never leaks to another user. Otherwise returns `{"job_id", "status", "stage", "stages", "attempt", "updated_at", "error_code", "error_message"}` (`updated_at` as UTC text in the same format as `stages`; for a `done` job it is the end of the last stage, §4a), except that a non-terminal job whose `results/{job_id}.json` exists is reported as `done` (the crash window before settlement).
- `GET /api/jobs/<job_id>/result`: the same ownership check, then **404 unless the job is `done`, or not yet terminal with a result in S3** (the crash window). A refunded (`failed`) job never serves a result, even if a late worker wrote one, so nobody gets a refunded result for free.
- Frontend (minimal; M3b refines the messages): show the balance from `/api/me`; on a 402, show "Not enough credit" instead of uploading; poll status as before, treating `queued` like M2b treated 404; after `done`, refresh the balance. Polling **pauses while the browser tab is hidden** (`document.visibilityState`) and resumes when it is shown, and **stops after 60 minutes** with "Still not finished. Reload this page (the link keeps your job) to check again." M2b's "taking longer than expected" note at 30 minutes stays. Reason: a job that has to start a worker takes about 17–20 minutes, so a shorter limit would stop before it finishes; the limit only stops a forgotten open tab from keeping Aurora awake after a session.

## 7. Lambdas (`neurolens/lambdas/`)
Both use Python 3.12, `DataApiDatabase`, and only pure-Python modules (`neurolens.db`, `billing`, `pricing`, `storage`, `settings`), which must not import `numpy`. They are **not** VPC-attached. Packaged the same way as M2b's breaker: one Terraform `archive_file` zip with a `source` block per file (`neurolens/__init__.py`, those modules, `neurolens/lambdas/`), so there is no separate build script and the zip changes only when those files do. Configuration comes from environment variables set by Terraform, with the same names as everywhere else: `NEUROLENS_DB_CLUSTER_ARN`, `NEUROLENS_DB_SECRET_ARN`, `NEUROLENS_DB_NAME`, `NEUROLENS_S3_BUCKET`.

- **`dlq_handler.handler(event, context)`**: SQS event source on the dead-letter queue, batch size 1. For each job in the S3 event:
  - unknown job, or already `done`/`failed`: nothing to do;
  - `results/{job_id}.json` exists: `settle_success`;
  - otherwise `issue_refund(..., "processing_failed", <the job's last error_message>, queued_before_s=0, processing_stale_s=90)`. If that returns `False` because a worker holds a fresh claim (a duplicate message for a job still running), **raise**, so the message is retried later, by which time the job has finished or gone stale.

  The handler returns normally (and SQS deletes the message) only when every job in it is settled, refunded or already terminal.
- **`reaper.handler(event, context)`**, every hour (M3b §6). Both rules measure age from **`updated_at`**, which `claim`, every heartbeat and `release_for_retry` refresh, so a job just handed back to the queue is not mistaken for an old one. For each job found:
  - `processing`, `updated_at` more than **10 minutes** ago (no heartbeat, and no redelivery re-claimed it): result exists → `settle_success`, otherwise `issue_refund(..., "stalled", processing_stale_s=600)`.
  - `queued`, `updated_at` more than **60 minutes** ago: result exists → `settle_success`; else if the upload object does not exist → `issue_refund(..., "upload_not_received", queued_before_s=3600)` (the presigned POST expired after 5 minutes, so it never will; or the upload expired after 2 days before any worker took it); **else leave it alone**. A queued job whose upload exists is waiting for a worker, not stuck: an upload between sessions waits for the next one, and a large burst can wait over an hour. Its SQS message still exists, because uploads expire after 2 days and queue messages after 4 (M2a's lifecycle rule and SQS's default retention; this rule depends on that order), so it either runs, is refunded by the worker (§5), or reaches the dead-letter handler. Refunding it here would leave a paid-back job that never runs.
  - The conditions are re-checked under the row lock inside `issue_refund`, so a job claimed by a worker between the reaper's query and its refund is left alone.
  - Any job refunded by the reaper that a worker later receives fails `claim` and is skipped, so it is never processed for free.
- **Log groups:** Terraform declares both Lambdas' CloudWatch log groups with **14-day retention** (log lines contain S3 keys with user IDs), so logs expire and `terraform destroy` removes them.
- **Lambda settings:** timeout **120 s** (the database wake-up retry alone can take about 60 s), memory 256 MB. The dead-letter queue's visibility timeout is **720 s**, six times the function timeout as AWS recommends (at least the function timeout, or AWS rejects the event-source mapping); it is also how long a message the handler cannot finish yet waits before its next try. Its message retention is **14 days**. The reaper is the backstop for anything the dead-letter handler cannot finish.
- **Lambda error alarms:** each Lambda's `Errors` metric emails Josh through M2a's SNS topic (one 5-minute period at 1 or more), like the breaker's. The dead-letter handler also errors by design when a worker still holds a job, so a single email can be expected; repeated ones cannot.
- **Forgotten-database alarm:** a CloudWatch alarm on the cluster's `ServerlessDatabaseCapacity` above 0 at some point in each of **6 hours** in a row (hourly Maximum) emails Josh through M2a's SNS topic. It catches a forgotten `stop_work.sh` or anything else keeping Aurora awake (about $1.40 a day at 0.5 ACU), including a caller every 12 minutes, such as the dead-letter handler retrying, which lets Aurora pause briefly between calls.
- **Cost trap:** a schedule that queries Aurora every 5 minutes would stop it ever pausing, costing about $1.40 a day at 0.5 ACU. So the EventBridge rule `neurolens-reaper-hourly` runs hourly and is always enabled (M3b §6): each run wakes Aurora for about 5 minutes (about $4 a month). `stop_work.sh` prints NOT CONFIRMED while the dead-letter queue still holds messages (each retry of the handler wakes Aurora). A stuck job waits at most about 70 minutes before it is settled or refunded.
- Lambda IAM (one role per function): the same `rds-data` and secret permissions as the worker; `s3:GetObject` on `results/*` and `uploads/*` with `s3:ListBucket` on the bucket without a prefix condition (so a missing object reads as 404, not 403; a HEAD carries no prefix, as for the worker); for the DLQ handler, `sqs:ReceiveMessage`, `DeleteMessage`, `GetQueueAttributes` on the dead-letter queue; CloudWatch Logs.

### 7a. IAM text for Josh to paste (prepared alongside the Terraform, before the first apply)
- **Deploy user** (`neurolens-deploy-services.json`): `rds:*` on `cluster:neurolens-*`, `db:neurolens-*`, `subgrp:neurolens-*` and `cluster-pg:neurolens-*` (plus `rds:Describe*` and `rds:ListTagsForResource` on `*`, and `rds:CreateDBCluster`/`CreateDBInstance` on the default `cluster-pg:default.*`, `pg:default.*` and `og:default:*` groups the database uses); in `neurolens-deploy-compute.json`, `rds.amazonaws.com` added to the service-linked roles the deploy user may create (RDS creates its helper role on first use in an account); for the RDS-managed password, `secretsmanager:CreateSecret` and `TagResource` on `secret:rds!*`, then `GetSecretValue`, `DescribeSecret`, `RotateSecret`, `DeleteSecret` and `UntagResource` only on a secret tagged by RDS for a NeuroLens cluster (the boundary's condition below), and `kms:DescribeKey` (AWS's listed requirement for `manage_master_user_password`; medium confidence, confirmed by the first apply); `lambda:CreateEventSourceMapping` on `*` with a `lambda:FunctionArn` condition on `function:neurolens-*` (the action has no resource type), and `Get`/`Update`/`DeleteEventSourceMapping` plus `TagResource`/`UntagResource`/`ListTags` on `event-source-mapping:*` (the provider's default tags tag the mapping); and, for `apply_schema.py`, `db_smoke.py` and the laptop web app, the worker's `rds-data` actions on the cluster (the secret read is the conditioned statement above).
- **Role boundary** (`neurolens-role-boundary.json`): the four `rds-data` actions on `cluster:neurolens-*`; `secretsmanager:GetSecretValue` on secrets tagged by RDS for a NeuroLens cluster (condition on `secretsmanager:ResourceTag/aws:rds:primaryDBClusterArn` matching `cluster:neurolens-*`), because RDS names its managed secret `rds!cluster-…`, not `neurolens-…`; `sqs:ReceiveMessage`/`DeleteMessage`/`GetQueueAttributes` are already covered by the boundary's `sqs:*` on `neurolens-*`. Each role's own policy names the exact cluster and secret ARNs from Terraform.

## 8. Rehearsal on AWS
1. `terraform apply` for Aurora and the Lambdas; `apply_schema.py --backend data_api`.
2. `infra/db_smoke.py`: runs `ensure_user`, `reserve`, `claim`, `settle_success` for a throwaway user against the real Data API, checks the ledger invariant, and prints the time of the first call (includes waking from pause).
3. Fake-mode end-to-end: laptop web with `db.backend = "data_api"`, the CPU rehearsal worker from M2a. Upload → reserve → claim → stages → result → capture, visible in `/api/me`.
4. Force the failure paths once each: an over-long video (refund before inference), a job failing twice (dead-letter refund), a worker stopped mid-job (redelivery re-claims via staleness).
5. One real GPU job, confirming the charged amount matches the verified duration.

## 9. Configuration additions (`config.json`, and `.env` / `env.conf` for deployment-specific identifiers such as the database ARNs, M1 §2)
`aws.db_cluster_arn`, `aws.db_secret_arn`, `aws.db_name` (set only through the `NEUROLENS_DB_*` environment variables in §3a, never written in `config.json`; add the three pairs to `load_settings`'s overlay and to `.env.example`); `db.backend`; `db.dsn` (postgres only; a local DSN contains a password, so it is set only through `NEUROLENS_DB_DSN` in `.env`, never in `config.json`, and is added to the overlay and `.env.example` like the others); `auth.mode`, `auth.dev_user_id`, `auth.dev_email`; `billing.starter_cents`; `server.host` (default `127.0.0.1`) and `server.port` (default 5003), which the root `app.py` launcher binds to and the dev-mode guard (§2) checks. Apart from the local DSN, none of these are secrets: the Aurora password lives only in Secrets Manager.
- The worker's templated UserData (M2a §4c) now also writes `db.backend = "data_api"` to `config.json` and the three `NEUROLENS_DB_*` variables to `env.conf`.

## 10. Tests (tests-first; `FAKE_INFERENCE=1 pytest`)
Database tests run against a real **PostgreSQL 16**: a service container in CI, `docker run postgres:16` locally. They connect through `NEUROLENS_TEST_DSN`, which CI sets; when it is unset they are skipped with a message saying how to start the container locally, except in CI (`CI=true`), where an unset DSN fails the run so the billing tests can never be silently skipped. Each test gets a fresh schema from `infra/migrations/`. `dev.txt` gains `psycopg[binary]`. Required:
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
- **Data API backend:** no scripted-reply tests. Its type conversion is checked for real by `infra/db_smoke.py` against Aurora (§3d, §8), and its wake-up retry by M3b's database-waking test.
- **SQL rewrite:** `%` or a string literal in SQL raises.
- **Heartbeat:** visibility is extended before `on_beat`; an `on_beat` exception is logged and beats continue; no beat before the first interval; leaving the heartbeat while a slow `on_beat` is running does not wait for it, and no visibility call starts after exit has begun.
- **Worker** (moto + PostgreSQL): each §5 path gives the right outcome and money movement: unknown job, result exists, claim lost on a finished job (`SKIPPED`, message deleted), claim lost on a fresh `processing` job (`BUSY`, message neither deleted nor released), upload missing after the claim, oversize, unreadable, too long, insufficient for actual duration, `LOST_CLAIM` part-way, success, duplicate. A failing record calls `release_for_retry` and leaves money reserved; a shutdown mid-job does the same with the "interrupted" message.
- **Lambdas** (moto + PostgreSQL, handlers called directly):
  - A dead-letter job with a result is settled; without one it is refunded; for a freshly claimed job the handler raises; running it twice changes nothing.
  - Each reaper rule, including "upload not received".
  - A job released for retry 5 minutes ago but created 2 hours ago is **not** reaped.
  - A `queued` job created 2 hours ago whose upload exists is **not** reaped (it is waiting for a worker).
- **Experiment 1:** `latency_run`'s stage durations come out the same from a status-endpoint response as from the old status object.
- **Web:**
  - 402 with no reservation made; `client_duration_seconds` below 1 rejected;
  - the presign key contains the user's ID;
  - status and result return 404 for another user's job and make **no S3 call** first (assert with a stubbed client);
  - `done` reported from a result during the crash window;
  - `/result` returns 404 for a `failed` job even when a result object exists;
  - dev mode on a non-local host raises `UnsafeConfigError`;
  - `ensure_user` is called once per user per process.
- **Import hygiene:** `neurolens.db`, `billing`, `pricing`, `storage`, `settings` and `neurolens.lambdas.*` import without `numpy`, `psycopg` or `neurolens.inference`.
- **Earlier tests rewritten for M3a** (a spec'd test change by the test-writing agent; the behaviour each checks stays the same except where §5 changes it, e.g. a record with no job row is now `SKIPPED`):
  - M1 §6a worker tests (oversize, too long, valid video, a raising record leaves the message), M2a §8 (`put_result` use, `DUPLICATE`, idle exit of `run()`) and M2b §11 (heartbeat, failure release, result-exists skip, duplicate, multi-record deletion, shutdown release): rewritten for the new signatures, each seeding a user and a `jobs` row first.
  - Removed with their Aurora equivalents above: M2b's `put_status` and `stages` tests; "a record that raises leads to status failed"; "a lost conditional write leaves the done status untouched"; "a record whose result exists is skipped with no status change".
  - M2b's status and result endpoint tests: rewritten against the Aurora-backed endpoints with a seeded, owned job.
  - M0/M1 web tests keep working through `create_app`'s defaults (§6).

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
neurolens/worker.py              MODIFIED: §5
neurolens/storage.py             MODIFIED: status helpers removed
neurolens/web/app.py             MODIFIED: §6
infra/migrations/001_init.sql    NEW
infra/apply_schema.py            NEW
infra/db_smoke.py                NEW
infra/iam/*.json                 MODIFIED: §7a
experiments/latency_run.py       MODIFIED: stages from the status endpoint (§5)
infra/terraform/                 MODIFIED: Aurora, Lambdas, EventBridge rule, IAM
infra/start_work.sh, stop_work.sh  MODIFIED: dead-letter queue check
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
12. With `stop_work.sh` run, Aurora pauses between the hourly reaper runs (visible as 0 ACU in CloudWatch about 5 minutes after the last use).
13. `FAKE_INFERENCE=1 pytest` passes locally and in CI, the tests were committed before the implementation, and the break-it check was done.

## 13. Cost
- Aurora: about $0.12 per ACU-hour **only while awake** (it wakes for work sessions and jobs), storage about $0.10 per GB-month (the database is tiny), backups free at this size.
- Secrets Manager: $0.40 a month for the database secret.
- Data API, Lambda and EventBridge: free tier at this volume.
- The main risk is Aurora never pausing. the hourly reaper, the database alarm (M3b §6) and acceptance criterion 12 guard it; check the ACU graph after the first session.

## 14. Explicitly not in this milestone
Everything listed for M3b in §1. No live payments of any kind. No changes to the model, the image, or the scaling rules.
