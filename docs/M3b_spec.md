# NeuroLens — M3b Implementation Spec
**Milestone:** sign-in, the public HTTPS website on AWS, credit top-ups, job history, and the switch to a product that runs by itself (20 – 30 Oct)

**Operating model: product mode.** Nothing waits for a person. The website, sign-in, history and results are always available. GPU workers still scale from zero on the job queue (M2b), and the hourly reaper settles stuck jobs (§6). The only by-hand parts are operator tools: the NAT Gateway switch for days with GPU work (§5), the warm hold for demos and study sessions, and `stop_work.sh` to pause GPU work.
**Builds on:** M3a (Aurora via the Data API, three-stage billing, refunds, dead-letter and reaper Lambdas, Aurora-backed status and result endpoints, a development user on `127.0.0.1`). M3b replaces the development user with real accounts, runs the web app on Lambda behind CloudFront's free HTTPS address, replaces the NAT instance with an on-demand NAT Gateway, and adds history, CSV export, credit grants and Stripe test-mode top-ups. It ends with the product ready for the GPU batch and M4's usability study.

**Ground rules for all of M3b:** region `us-east-1`. Python 3.12. All infrastructure is Terraform in `infra/terraform/`, tagged `Project=neurolens`, `Milestone=M3b`. Run AWS commands with the `neurolens` CLI profile only. **Tests first, as in M0 §5:** the §11 tests are written by a separate agent against §2–§8 before the implementation, and are not edited by the implementer. Money and sign-in code (§2, §7) get the full treatment: tests first, 2–3 break-it checks on committed code, and an independent Opus review. `/security-review` runs on the full M3 diff before M3 is merged, and its high-severity findings are fixed first.

## Decisions (8 Oct)
| | Decision | Deciding reason |
|---|---|---|
| A | **Amazon Cognito** for sign-in: Google and email-and-password at launch, more providers later without code changes. Our own user IDs, with a table mapping each sign-in to a user; one person signing in two ways with the **same verified email is one user**. | A provider's ID as our user ID makes every later change a migration of all users. Linking on a verified email is what most products do (Slack, Notion, Atlassian). Cognito is free up to 10,000 monthly users. |
| B | **Flask on AWS Lambda** behind CloudFront (function URL with origin access control). The page and samples come from S3 through the same CloudFront address. | There is no machine to keep alive (no patching, restarts or replacement), and the website works even when the NAT Gateway is off. About $0 at our traffic. |
| C | **No load balancer.** | Nothing takes traffic on servers: Lambda runs more copies by itself, and GPU workers pull jobs from the queue. Growth is limited by Lambda's account concurrency, Aurora's maximum capacity, the GPU quota and the worker group's maximum size (the queue wait), not by a missing load balancer. |
| D | **Product mode** (above), used for the GPU batch, the study and the demo. GPU workers stay in **private subnets**; a **NAT Gateway** (managed by AWS) replaces the NAT instance and exists only on days with GPU work and in the final deployed window. The reaper runs **hourly, always**, with Aurora's auto-pause at 5 minutes. | A NAT instance is a machine we patch and a single point of failure, the wrong fit for a product nobody watches. A NAT Gateway is the standard managed choice, and deleting it between GPU days keeps the cost to about $1.20 a day of use. Every reaper query wakes Aurora, so a reaper that checks often would keep it awake forever; hourly costs about $4 a month and can never silently miss a refund. |

**Open sign-up is safe:** new accounts start with **0 credit**. Credit comes from `grant_credit.py` (§7a) or, for the team only, Stripe test top-ups (§7b). A stranger can sign up and browse the samples, but cannot run a GPU job. The Google OAuth app is **published** (not "Testing"), so anyone with Google can sign up without being added to a list; the consent screen links a one-page privacy policy (`static/privacy.html`).

**Day one: check the unknowns before building anything else.** If any check fails, stop and ask Josh.
1. A Cognito user pool with its hosted sign-in page, Google as an identity provider and the app client's callback `https://<distribution>.cloudfront.net/auth/callback` signs a Google user and an email user in end to end (create the CloudFront distribution first; its Lambda origin can be added later). Google's authorized redirect URI is Cognito's `https://<prefix>.auth.us-east-1.amazoncognito.com/oauth2/idpresponse`.
2. The ID token of a Google-federated user carries `email_verified = true` (through the attribute mapping, §2a).
3. The account's Lambda concurrency limit (new accounts can be well below 1,000). If it is low, request 1,000 through Service Quotas (free); the web, reaper, dead-letter, breaker and webhook functions share it.

## 1. Scope
### In scope
- Cognito sign-in (Google and email-and-password), our own user IDs, account linking by verified email (§2)
- The web app on Lambda behind CloudFront; the page and samples from S3 (§3)
- The page: sign-in, balance, history, CSV, top-up, specific failure messages (§4)
- The NAT Gateway switch replacing the NAT instance (§5)
- The hourly reaper and 5-minute auto-pause (§6)
- 0 starter credit, `grant_credit.py`, Stripe test-mode top-ups for an allowlist (§7)
- Experiment 3, an independent track (§9)
- Deleting the scaffolding before `aws-josh` is merged into `main` (§10)

### Out of scope (M4)
The usability study itself, the participant withdrawal script (built nearer the study), the results consolidation script, and final teardown.

## 2. Sign-in (`neurolens/web/auth.py`)
### 2a. Cognito (Terraform)
- User pool `neurolens-users`, **Lite** feature plan, set explicitly (`user_pool_tier = "LITE"`; new pools otherwise default to Essentials). Sign-in with email; self sign-up allowed; **email verification required before the first sign-in**; `attributes_require_verification_before_update = ["email"]`, so a changed email is not used until verified; Cognito's default email sender (about 50 emails a day, enough for this project; spam sign-ups could use them up, while Google sign-in keeps working); the default password policy.
- Identity provider **Google**: the Google client ID and secret come from Terraform variables set in the environment (`TF_VAR_google_client_secret`), never committed. The secret ends up in the Terraform state (encrypted, private bucket), as for any identity provider Terraform creates. Attribute mapping: `email` ← `email`, `email_verified` ← `email_verified`.
- App client: confidential (has a secret), authorization-code flow only, scopes `openid email`, providers `COGNITO` and `Google`. Callback URL `https://<distribution>.cloudfront.net/auth/callback`; sign-out URL `https://<distribution>.cloudfront.net/`. **Its read and write attributes stay at Cognito's defaults** (every standard attribute): mapped attributes must be writable or Cognito skips them, and Cognito refuses `email_verified` in an explicit list. Day-one check 2 confirms Google users arrive with `email_verified`. The client secret goes into Parameter Store by hand (below).
- Hosted domain prefix `neurolens-<account suffix>`, with the classic hosted pages set explicitly (`managed_login_version = 1`; the newer managed login needs the Essentials plan).
- Linking by verified email trusts every provider's verification. A provider added later must be checked for that before it is enabled.
- `prevent_destroy` is **not** set: losing the pool loses no data, because a returning user is linked back to their account by verified email (§2c).

### 2b. Modes and secrets
- `auth.mode`: `"dev"` (laptop and tests, M3a §2, including its `127.0.0.1` guard) or `"cognito"` (AWS). The Lambdas set `NEUROLENS_DEPLOYED=1`, which `settings.apply_env` maps to `deployed: true`; `create_app` raises `UnsafeConfigError` for dev mode whenever `deployed` is true (the web code reads it through settings, never from `os.environ`).
- **Dev mode keeps M3a's fixed development user** (`dev-user`, created with `billing.ensure_user`, no `identities` row). It is a local stand-in, not an account, so it needs no UUID; every request in dev mode is signed in as it.
- Deployment values arrive as environment variables through `settings.apply_env` (never `config.json`): `NEUROLENS_AUTH_MODE`, `NEUROLENS_COGNITO_ISSUER` (`https://cognito-idp.us-east-1.amazonaws.com/<pool id>`), `NEUROLENS_COGNITO_CLIENT_ID`, `NEUROLENS_COGNITO_DOMAIN`, `NEUROLENS_PUBLIC_BASE_URL` (`https://<distribution>.cloudfront.net`), `NEUROLENS_WORKER_GROUP` (`neurolens-workers`, §4a), plus M3a's `NEUROLENS_S3_BUCKET`, `NEUROLENS_AWS_REGION` and `NEUROLENS_DB_*`. They map to `auth.mode`, `auth.cognito_issuer`, `auth.cognito_client_id`, `auth.cognito_domain`, `public_base_url`, `aws.worker_group`. In Cognito mode every one is required, and startup fails if one is missing.
- **Secrets in Parameter Store** (`SecureString`), read once at startup and never written to disk: `/neurolens/web/cognito_client_secret`, `/neurolens/web/flask_secret_key` (at least 32 random bytes, created once by hand and never regenerated, so sessions survive redeploys), and for §7b `/neurolens/web/stripe_secret_key`, `/neurolens/web/stripe_webhook_secret`, `/neurolens/web/topup_allowlist` (comma-separated emails; personal data stays out of git). The three Stripe parameters are read, and required, only in Cognito mode with `stripe.enabled` true.

### 2c. Routes and accounts
- Authlib's Flask client with Cognito's OpenID configuration (`<issuer>/.well-known/openid-configuration`), scope `openid email`.
- `GET /login`: `authorize_redirect(<public_base_url>/auth/callback)`. Redirect and sign-out URLs are always built from `public_base_url`, never from the request's host (CloudFront forwards requests to Lambda under a different host name).
- `GET /auth/callback`: `authorize_access_token()`, which checks the OAuth `state` and the ID token's signature, issuer, audience and nonce. Then:
  - `email_verified` must be exactly `True` or the string `"true"` (federated attributes can arrive as strings); anything else, including a missing claim, gives **403** `{"error": "email_not_verified"}` and no session.
  - A state mismatch, a bad token, or an error in the query (`?error=access_denied` when the user cancels) returns **400** `{"error": "sign_in_failed"}` and no session.
  - `billing.sign_in(db, issuer, subject, email, starter_cents) -> user_id` (§8) finds or creates the user, then `session.clear()`, `session.permanent = True`, store only `user_id`, `email` and `signed_in_at` (Unix seconds), and redirect to `/` (never to a URL from the request).
- **Account linking rule** (inside `sign_in`, one transaction): the sign-in `(issuer, subject)` already in `identities` → that user. Otherwise a user whose email equals the verified email (compared lowercased) → link this sign-in to that user. Otherwise create a user with a new random UUID as `user_id`, a balance of `starter_cents` (0 on AWS; no ledger row when 0), and the identity row. Two simultaneous first sign-ins with the same email end as one user: every insert uses `ON CONFLICT DO NOTHING`, and when the user insert creates nothing, the same transaction reads the winner's row by email. (Catching a unique-violation error instead would not work: the error aborts the whole transaction, on Postgres and the Data API alike.)
- `POST /logout`: `session.clear()`, then **200** `{"logout_url": "https://<cognito domain>/logout?client_id=<id>&logout_uri=<public_base_url>/"}`. The page navigates there, so Cognito's own session ends too; without this, the next sign-in would happen silently.
- Session cookie: Flask's signed cookie, `SESSION_COOKIE_SECURE=True`, `HTTPONLY=True`, `SAMESITE="Lax"`, 12-hour `PERMANENT_SESSION_LIFETIME`, `SESSION_REFRESH_EACH_REQUEST=False`. `SECURE` is `False` only in dev mode (plain `http://localhost`). **A session is refused 12 hours after `signed_in_at`**, however often it is used: Flask re-issues the cookie with a fresh signature, so the cookie's own age alone would let a regularly used cookie live forever. Known and accepted limit: logout clears only that browser, and a copied cookie stays valid until 12 hours after that sign-in.
- `current_user()` returns `(user_id, email)` from the session in Cognito mode (or `None`), and the development user in dev mode.
- **Every `/api/*` route requires a signed-in user**, returning **401** `{"error": "not_signed_in"}`. The only unauthenticated Flask routes are `/`, `/static/*`, `/data/*` (laptop only; on AWS these come from S3), `/login`, `/auth/callback`, `/logout`, `/healthz`.
- **Database waking:** the web app's database uses `resume_wait_s=45` (M3a §3d). If Aurora is still waking after that, the route returns **503** `{"error": "database_waking"}`; the page retries after 5 s, up to 3 times, showing "Starting up…". This keeps every request under the Lambda's 60 s timeout and CloudFront's 60 s origin timeout. Known limitation (stopping rule: needs a hostile user): a signed-in stranger calling the API every few minutes keeps Aurora awake (about $1.40 a day); the 6-hour database alarm emails, and the response is to disable that user in Cognito or turn off self sign-up.
- **Cross-site request protection:** `SameSite=Lax`, and every `POST /api/*` requires `Content-Type: application/json` (415 otherwise), which browsers cannot send cross-site without a CORS check the app never grants.
- **CORS removed:** `flask-cors` and `CORS(app)` are deleted. The S3 bucket's allowed origins become the CloudFront address plus `http://localhost:5003`.

## 3. Web tier on AWS
### 3a. Lambda
- Function `neurolens-web`: Python 3.12, arm64, 512 MB, timeout 60 s, **outside the VPC** (it reaches Cognito, Parameter Store, S3 and the Data API over AWS's public endpoints, so it never depends on the NAT Gateway).
- `neurolens/web/lambda_handler.py` is the AWS launcher (the laptop's is `app.py`): it loads settings with `settings.load_settings()`, builds the app once per container with `create_app(cfg=...)`, and adapts function-URL events (payload 2.0, including several `Set-Cookie` values) to WSGI with **apig-wsgi** (confirm at build that it handles function-URL events and multiple cookies; the fallback is AWS's Lambda Web Adapter).
- **Function URL** with `AuthType = AWS_IAM`. Its resource policy lets only this CloudFront distribution invoke it, in **two statements** (function URLs created since October 2025 need both): `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction`, each for principal `cloudfront.amazonaws.com` with `SourceArn` = this distribution. Sources: [Lambda function URL access](https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html), [CloudFront OAC for Lambda](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-lambda.html).
- Known limitation (stopping rule: needs heavy traffic): all functions share the account's concurrency, so a flood of web requests could delay the breaker, reaper or dead-letter handler. The production fix is reserved concurrency on `neurolens-web` (one line, once the account limit is 1,000).
- **Packaging:** `infra/build_web_lambda.sh` builds `build/web_lambda.zip` from `neurolens/`, the handler and `requirements/web.txt`, installed for Linux arm64 (`pip install --platform manylinux2014_aarch64 --only-binary=:all: --target ...`). Terraform deploys the zip. The web code never imports `neurolens.inference`, `torch` or `numpy` (import-hygiene test).
- Web IAM role: `ssm:GetParameter` on `/neurolens/web/*` with `kms:Decrypt` for the default key; M3a's `rds-data` and database-secret permissions; `s3:PutObject` on `uploads/*` (presigned POSTs are signed with the role's own credentials); `s3:GetObject` on `results/*`; `autoscaling:DescribeAutoScalingGroups` (it needs `*`; read-only, for §4a's paused check); its own log group.
- `GET /healthz`: unauthenticated, 200 `{"ok": true}`, **never touches the database**.

### 3b. CloudFront
- One distribution, default `*.cloudfront.net` certificate, HTTP→HTTPS redirect, price class 100.
- **Site origin: the bucket's `site/` prefix** through origin access control for S3 (the bucket stays private). The bucket policy allows `s3:GetObject` on `site/*` only, for principal `cloudfront.amazonaws.com` with `SourceArn` = this distribution; uploads and results stay unreachable through CloudFront. Default behaviour (`/`, default root object `index.html`), `/static/*` and `/data/*`, managed `CachingOptimized` policy. `site/*` has no lifecycle rule.
- **API origin: the function URL** through origin access control for Lambda (SigV4). Behaviours `/api/*`, `/login`, `/logout`, `/auth/*`, `/healthz`: `CachingDisabled`, `AllViewerExceptHostHeader` origin request policy (cookies, query strings and headers reach Flask; Lambda needs its own Host), all methods, origin read timeout 60 s.
- **POST bodies must carry their hash.** With origin access control, Lambda accepts a POST only with `x-amz-content-sha256` set to the hex SHA-256 of the body. The page sends every POST through one helper, `postJSON(url, body)`, which computes it with `crypto.subtle.digest`. On the laptop Flask ignores the header. Plain HTML form POSTs are therefore not used anywhere.

### 3c. Deploying
`infra/deploy_web.sh` (refuses uncommitted changes, like `deploy_code.sh`):
1. builds the Lambda zip and runs `terraform apply -target` on the web function only (printing the plan first), and
2. syncs the site files: `static/index.html` → `site/index.html`, `static/` → `site/static/`, and `site/data/samples.json` holding **only the samples whose clip is listed in `data/videos/SOURCES.md`** (open-licensed; third-party ads are never published), with only those samples' videos, thumbnails and results; then invalidates the CloudFront cache (`/*`). None of today's 13 samples is listed, so the public samples list is empty until open-licensed clips with their own precomputed results are added (a GPU-batch task). The laptop keeps all 13.

## 4. The page, history and messages
### 4a. Endpoints
- `GET /api/me`: `{"user_id", "email", "balance": {...}, "can_top_up": bool}`.
- `GET /api/jobs?limit=50`: the user's jobs, newest first, at most `limit` (default 50; anything but an integer from 1 to 100 returns 400 `{"error": "bad_limit"}`): `[{"job_id", "filename", "status", "created_at", "verified_duration_ms", "captured_cents", "error_code", "result_available"}]`. `result_available` is true for a `done` job created less than 30 days ago (results expire after 30 days).
- `GET /api/jobs/<job_id>/result.csv`: the same ownership check as `/result` (404 for others, no S3 call first). Columns in order: `t, engagement_overall, ffa_faces, eba_bodies, ppa_scenes, sts_social, auditory, auditory_with_audio, auditory_without_audio`, one row per timestep, `None` as an empty cell, numbers exactly as stored in the result JSON. `Content-Disposition: attachment; filename="neurolens-<job_id>.csv"`.
- `/result` and `/result.csv` serve the result for a `done` job **and for a `queued`/`processing` job whose result object exists** (M3a's crash window: the worker wrote the result, then died before settling). Not-available cases: another user's or unknown job → 404 `not_found`; a `failed` job → 404 `not_found` (even if a late worker wrote a result); `queued`/`processing` with no result object → 404 `result_not_ready`; a `done` job whose result object is gone (the 30-day expiry) → 404 `result_expired`.
- `POST /api/uploads/presign` gains one check, **before reserving any credit**: when the worker group's maximum is 0 (GPU work stopped by `stop_work.sh` or the breaker), it returns **503** `{"error": "processing_paused"}` and reserves nothing. It reads the group itself (`aws.worker_group`), not a separate flag, so the two can never disagree. If the read fails, it refuses with `presign_failed` (fails closed). In dev mode (no worker group) there is no check. Known limitation: jobs already waiting when GPU work stops wait until it restarts or their upload expires (2 days, then refunded).
- `GET /api/samples` is **removed** (the page reads `/data/samples.json`). `GET /api/limits` stays and needs sign-in like every `/api/*` route; the page calls it after `/api/me` returns 200.
- The README's results section notes that `auditory` and the two `auditory_with/without_audio` columns are normalised over different windows (M0 §2), not the CSV.

### 4b. Frontend
- **Start-up:** the page calls `/api/me` once: **200** → the app (then `/api/limits`); **401** → the sign-in screen; **503 `database_waking`** → "Starting up…" with retries; **anything else** → "Something went wrong. Please try again in a minute." with a retry button.
- **Samples** load from `/data/samples.json` in every state (signed in or not). With an empty list, the section shows "Sample videos coming soon." (no empty grid, no broken thumbnails).
- **Sign-in screen:** a "Sign in" button to `/login` (Cognito's page offers Google or email), the sample browser below it. Uploads, balance and history appear only when signed in. "Sign out" in the header (§2c).
- **Balance** in `.header-meta`, from cents, refreshed after each job ends. With 0 credit, the upload area says "You have no credit yet. Ask the NeuroLens team for test credit."
- **History** panel from `/api/jobs`: each row re-opens its stored result in the existing chart code, links to the CSV, and says when results expire. Filenames and `error_message` come from users: inserted with `textContent`, never `innerHTML`.
- **Top-up** (§7b): a control "Add test credit — no real charge", shown only when `can_top_up` is true.
- **Failure messages** by `error_code`, each ending "You have not been charged." where money was refunded:
  - `insufficient_credit` (402 at presign): "Not enough credit for this video."
  - `insufficient_credit_for_actual_duration`: "Your video is longer than the length we estimated, and your credit doesn't cover the difference."
  - `duration_exceeds_max_estimated` (400 at presign) and `duration_exceeds_max_verified`: "This video is longer than the 120-second maximum."
  - `file_too_large`: "This file is larger than the upload limit."
  - `unreadable_video`: "This file is not a readable video."
  - `processing_failed`, `stalled`: "Processing failed after retrying." plus the job's `error_message` when present.
  - `upload_not_received`: "The upload didn't finish. Please try again."
  - `upload_missing`: "The uploaded file was no longer available (uploads are kept 2 days). Please upload it again."
  - `presign_failed`: "The upload couldn't be started. Please try again."
  - `processing_paused` (503 at presign): "Processing is paused right now, so no video can be analysed. You have not been charged."
  - Any other code: "Processing failed." (never the raw code alone).

## 5. Network: the NAT Gateway switch
- The NAT instance, its role, security group and start/stop steps are **removed**.
- Terraform variable `nat_gateway` (bool, default `false`). When `true`: an Elastic IP and a NAT Gateway in the public subnet, and the private route table's `0.0.0.0/0` route to it. When `false`: neither exists, and private subnets reach only S3 (the existing free gateway endpoint). Creating takes about 2 minutes. Cost: about $0.045 an hour for the gateway plus $0.005 for its address, only while it exists.
- Set to `true` for days with GPU work and for the final deployed window (GPU batch, study, demo); `false` otherwise. The website never needs it (§3a).
- `start_work.sh` refuses to let workers start (any form that raises the group's max) unless the NAT Gateway exists and is `available`: a worker without it cannot reach SQS or the Data API and would only wait for the idle alarm.
- The opposite order is guarded too: **Terraform refuses to apply `nat_gateway = false` while the worker group's max is above 0** (a precondition reading the group through a data source, when the group exists), with the message "run infra/stop_work.sh first". Otherwise workers could start with no network and do nothing but bill until the idle alarm.
- `stop_work.sh` reports a NAT Gateway or an unattached `neurolens` Elastic IP that still exists as a problem (NOT CONFIRMED), with the fix: set `nat_gateway = false` and apply.
### Operator commands in product mode
Options are named by what they change; the help text says when to use each.
| Command | Does |
|---|---|
| `start_work.sh` | checks the NAT Gateway (above), then sets the worker group's max to 1: automatic scaling on, so an upload starts a worker. (`--max 2` exists only for Experiment 2 and is deleted with the experiment tools, §10.) |
| `start_work.sh --keep-worker [--hours N]` | the above plus a warm hold (M2b §2d): one worker on now and kept for N hours (1 to 4, default 3). When working alone, for example debugging. |
| `start_work.sh --keep-worker-and-db [--hours N]` | `--keep-worker`, plus Aurora's minimum capacity 0.5 ACU so nobody waits about 15 s for the database to wake (about $0.06 an hour). When other people are watching: demos and study sessions. |
| `stop_work.sh` | workers to 0/0/0 and the warm hold released; Aurora's minimum back to 0; then the checks (no GPU instance; the NAT Gateway and Elastic IP, above). The website stays up. |
| `deploy_code.sh` | uploads the last commit as the worker code bundle (refuses uncommitted changes), then restarts every running worker that has no job, so it runs the new code. A worker with a job is named and left alone (it gets the new code at its next start); `--now` restarts it anyway (its job is handed back and retried). Replaces `restart_workers.sh`. |
| `debug_worker.sh` | opens a shell on a worker through Session Manager. Refuses unless a warm hold is on (group minimum 1 or more), with "run infra/start_work.sh --keep-worker first", so AWS cannot remove the worker mid-session. Replaces `connect_worker.sh`. |

Terraform ignores changes to Aurora's `min_capacity`, so the scripts and Terraform don't fight. Known limitation: the warm hold's scheduled end resets only the worker group, not Aurora's minimum, so a forgotten `--keep-worker-and-db` keeps Aurora awake (about $1.40 a day) until `stop_work.sh` or the 6-hour database email.

- Deploy-user permissions (paste into `neurolens-deploy-compute`): create, delete and describe NAT Gateways, allocate, release and associate Elastic IPs, all limited to `Project=neurolens` tags as for other EC2 resources.

## 6. Reaper: hourly, always on
- The reaper rule `neurolens-reaper-hourly` runs `rate(1 hour)` and is always enabled (Terraform). It **replaces** M3a's `neurolens-reaper-every-5-min` (created disabled, with `ignore_changes = [state]` so the scripts could switch it): that rule and its `ignore_changes` are removed, so Terraform alone owns the new rule's state. `start_work.sh` and `stop_work.sh` no longer touch it.
- Aurora's `seconds_until_auto_pause` becomes **300** (the minimum). Each hourly run wakes Aurora for about 5 minutes (about 15 s to resume, M3a's resume wait covers it): about $4 a month.
- What it guards against (unchanged rules, M3a §7): an upload that never arrived (credit reserved, nothing will run), a job whose worker died and whose queue message is gone (`processing` forever), and a published result whose charge was not recorded. Worst case, such a job waits about 70 minutes (its 10- or 60-minute threshold plus the hour) before it is settled or refunded; the dead-letter handler still refunds the common failures within seconds.
- **The forgotten-database alarm changes what it measures.** M3a's `neurolens-db-awake-6h` fires when Aurora was awake *at some point* in each of 6 hours in a row; the hourly reaper wakes it every hour, so that rule would fire every day with nothing wrong. It now fires when Aurora was awake for **a large share of each hour**: the hourly **average** of `ServerlessDatabaseCapacity` above **0.15 ACU** in each of 6 hours in a row. At the 0.5 ACU minimum that means awake for more than about 30% of the hour. The reaper's wake (about 6 minutes, 10%) stays below it, while a caller every 12 minutes (about 45%, M3a's example) and a forgotten `--keep-worker-and-db` (100%) are above it.

## 6b. Alarms simplified
- **The idle alarm only detects and emails.** Its "set to 0" action and its direct call to the breaker are removed, with the breaker's alarm-invoke permission. Reasons:
  - The "set to 0" action, an M2a leftover, never helps now. A worker idle with an empty queue was already removed by scale-in. A worker stuck with a job is protected, so only the breaker (which removes protection first) can end it. A worker that cannot fetch jobs while jobs wait is ended, then replaced a minute later by scale-out, which is the loop the breaker exists to stop.
  - The direct call existed only to beat that action's race (M2b §2c, rehearsal R8).
  - The breaker now acts only on its 5-minute schedule, which still covers a warm hold ending while the alarm stays in `ALARM`. The cost: a broken worker may run up to 5 more minutes (about $0.15).
  - The `workers_to_zero` policy stays: scale-in uses it.
- **One error alarm for every Lambda.** A single `for_each` block covers the breaker, dead-letter handler, reaper, web and webhook functions (replacing the breaker's separate alarm and M3a's block): one email on any failed run. These catch a function that runs and fails, not one that never runs (a disabled schedule is caught by `start_work.sh`'s check).

## 7. Credit
### 7a. Grants
- `billing.starter_cents` is **0** in the committed `config.json`; laptop tests may set their own.
- `infra/grant_credit.py <cents>`: prompts for the email (so participants' emails never land in shell history), finds the user by email (unique since §8's migration), and adds the credit in one transaction with a `grant` ledger row. `cents` must be positive; an unknown email stops with an error. Runs from a laptop with the `neurolens` profile, never from the web app.

### 7b. Stripe test-mode top-ups (full treatment: money)
Demonstrates a payment flow **without real money**; it must never be switched to live mode (the model's licence, CC BY-NC, forbids commercial use).
- `/neurolens/web/stripe_secret_key` must start with `sk_test_` (startup fails otherwise). Config `stripe.enabled` (committed `true`; it applies only in Cognito mode, so the laptop never needs Stripe), `stripe.packs` (`{"5": 500, "10": 1000}`, pack name → cents).
- `can_top_up` is true only in Cognito mode with `stripe.enabled`, for a signed-in user whose email is in `/neurolens/web/topup_allowlist` (emails compared after trimming spaces and lowercasing, here and in the webhook). The allowlist matters because Stripe's public test card would otherwise give anyone free GPU credit.
- `POST /api/top-up/checkout` (signed in, allowlisted, JSON `{"pack": "5" | "10"}`): creates a hosted Stripe Checkout Session with the server-defined amount in USD, card only, metadata `user_id` and `pack`, `success_url`/`cancel_url` from `public_base_url`, and returns only its URL. The browser never sends an amount, user ID or session ID. Refusals: top-ups off (dev mode or `stripe.enabled` false) → 404 `not_found`; a user not on the allowlist → 403 `top_up_not_allowed`; an unknown pack → 400 `unknown_pack`.
- **Webhook: its own function** `neurolens-stripe-webhook` (same code bundle, handler `neurolens/web/stripe_webhook.py`), with a function URL of `AuthType = NONE`, called by Stripe directly (not through CloudFront, because Stripe cannot send the body-hash header). Its resource policy has the two statements function URLs need (§3a): `lambda:InvokeFunctionUrl` for principal `*` with `FunctionUrlAuthType = NONE`, and `lambda:InvokeFunction` for principal `*` with `InvokedViaFunctionUrl = true`. Its security is Stripe's signature, verified over the **raw request body** (base64-decoded first when the event says `isBase64Encoded`) before parsing.
- **Webhook answers**, chosen so Stripe retries only what a retry can fix (Stripe retries any non-2xx answer for up to 3 days):
  - bad or missing signature → **400**, nothing done;
  - `stripe.enabled` false → **200**, nothing done;
  - a valid event that is refused (`livemode = true`, another event type than `checkout.session.completed`, a session whose `payment_status` is not `"paid"`, an unknown pack, an unknown user, a user whose `users.email` is not on the allowlist; the user is found from the metadata `user_id`) → **200**, nothing credited, logged with the reason;
  - Aurora still waking after the resume wait → **503**, so Stripe retries later;
  - accepted (`checkout.session.completed`, paid, test mode; the cents come from the pack in the metadata our server set, never from Stripe's `amount_total`) → in one transaction, insert into `stripe_test_events` (event ID primary key, session ID unique) with `ON CONFLICT DO NOTHING`; only if a row was inserted, add the pack's cents and a `test_topup` ledger row; then **200**. A repeated event or session never credits twice; the success redirect never credits anything.
- The webhook role has only `ssm:GetParameter` on the Stripe and allowlist parameters, `rds-data` and the database secret, and its log group.

### 7c. Checking the database code on Aurora
- `infra/db_smoke.py` is **renamed `infra/check_aurora.py`** and stays as an operator tool: CI tests only the PostgreSQL backend, so this is the one check of the Data API backend's value formats on the real database (M3a §3d). Run it after changing `neurolens/db.py`'s Data API code or adding a migration, and once on a new account.
- It gains one `sign_in` round trip for a throwaway identity: a first sign-in creates the user, a second with the same identity returns the same `user_id`, and a third with another subject and the same email in different letter case links to it. Its existing checks are unchanged, and its clean-up of the throwaway rows, pass or fail, now covers their `identities` rows too.

## 8. Interfaces fixed by this spec (the §11 tests are written against exactly these)
- Migration `002_accounts.sql`: table `identities (issuer TEXT, subject TEXT, user_id TEXT NOT NULL REFERENCES users, created_at, PRIMARY KEY (issuer, subject))`; a unique index on `lower(users.email)`; the ledger `kind` check gains `grant`; table `stripe_test_events (event_id TEXT PRIMARY KEY, session_id TEXT UNIQUE NOT NULL, user_id TEXT NOT NULL REFERENCES users, cents BIGINT NOT NULL, created_at)`. The comment on `users.user_id` becomes "our own UUID (text)". Kinds and other values are passed as parameters (project SQL has no string literals).
- `neurolens.billing`: `sign_in(db, issuer, subject, email, starter_cents) -> str`; `list_jobs(db, user_id, limit) -> list[dict]`; `grant_credit(db, email, cents) -> str` (returns the user ID; `ValueError` for `cents <= 0`, `LookupError` for an unknown email); `credit_test_topup(db, event_id, session_id, user_id, cents) -> bool` (True only when it credited). `ensure_user` **stays** (dev mode and existing tests) with one change: no `starter` ledger row when `starter_cents` is 0. `sign_in` creates new users through the same internal step inside its own transaction.
- **Existing tests this spec retires or changes** (AGENTS.md: tests change only where a spec says so):
  - `tests/test_web.py::test_samples_equals_samples_json` and `tests/test_presign.py::test_create_app_with_cfg_needs_no_config_json`'s `/api/samples` request: `/api/samples` is removed. The second test keeps its purpose (no `config.json` needed) and requests another route instead.
  - `/api/me`'s new shape (§4a): `tests/test_m3a_web.py::test_me_returns_email_and_balance`, `test_me_shows_a_reservation` and `test_me_uses_create_apps_built_in_dev_user_without_a_cfg` expect `{user_id, email, balance: {available_cents, reserved_cents}, can_top_up}`.
  - `result_not_ready` (§4a): `tests/test_m3a_web.py::test_result_of_an_unfinished_job_without_a_result_is_404` (both cases) and `tests/test_m2b_web.py`'s job-in-progress result test (renamed for it).
  - Starter credit 0 (§7a): `tests/test_env_settings.py`'s committed-config test (renamed for it).
  - Tests that build Cognito-mode apps get the new required settings; dev-mode tests (`dev-user`, `ensure_user`, `/api/limits`) are unchanged, because dev mode is always signed in.
  - `tests/test_m3a_db.py`'s module docstring names `infra/check_aurora.py` instead of `infra/db_smoke.py` (text only).
  - Any other existing test that fails against this spec is reported to Josh, not edited.
- `neurolens.web.auth`: `current_user() -> tuple[str, str] | None`; `login_required`; `init_auth(app, cfg, ssm_client, *, server_metadata=None)`. With `server_metadata` (a dict with `issuer`, `authorization_endpoint`, `token_endpoint` and `jwks_uri`), Authlib uses it instead of downloading the OpenID configuration.
- **Sign-in test seam:** tests pass `server_metadata` with fake URLs and fake only HTTP with `responses`: the token endpoint returns an ID token the test signs with its own RSA key; the JWKS URL returns that key. Authlib's real state, nonce and signature checks run. Nothing in the tests contacts Cognito, Google or Stripe. `dev.txt` gains `responses`.
- `neurolens.settings.get_parameter(ssm_client, name) -> str` (one `SecureString`, decrypted).
- `neurolens.web.app.create_app(..., ssm_client=None)`: in Cognito mode reads the parameters at startup and fails fast if one is missing.
- `neurolens.web.lambda_handler.handler(event, context)`; `neurolens.web.stripe_webhook.handler(event, context)`.
- `neurolens.results.to_csv(result: dict) -> str`.
- `requirements/web.txt` gains `authlib`, `requests`, `apig-wsgi`, `stripe`; loses `flask-cors`.

## 9. Experiment 3 (cloud vs on-premise cost)
An independent track.
- `experiments/tco_benchmark.py` times the full two-pass pipeline for 15 s, 30 s and 60 s clips and writes a CSV of wall-clock times.
- **On-premise leg:** the school's GPU cluster via Slurm (`experiments/slurm/tco_benchmark.sbatch`, a GPU with 40 GB or more), falling back to a teammate's 2× RTX 4090 machine; 10 runs per length. Power cost from published GPU TDP figures, not measured. The cluster has no AWS credentials: copy the output to a laptop and upload it with the manifest.
- **Cloud leg:** reuses Experiment 1's runs (3 per clip length) instead of new GPU time; the report states the run counts of each leg.
- Output under `experiments/experiment-3/<run_id>/` with a `manifest.json` (M2b §10's contract). Costs use the **measured on-demand price** ($1.861 an hour for `g6e.xlarge`) and measured runtimes; the consolidation recomputes cost per video and the break-even volume from them and shows the original planned figures beside them, labelled as planned. Never presented as observed fully-loaded costs.

## 10. Scaffolding deleted before the merge into `main`
Deleted once the system runs end to end on the GPU and is reviewed, before the experiments and user testing (one clean merge):
- the NAT instance and its start/stop steps (§5, done in M3b);
- the reaper's on/off steps in `start_work.sh`/`stop_work.sh` (§6, done in M3b);
- `infra/self_terminate.sh` and `infra/neurolens-self-terminate.service` (already masked on workers; removed from the image at the next build), **and `self_terminate.sh` from `pull_code.sh`'s `install` line**: that script stops at the first error, so deleting the file alone would stop every worker from starting.

Removed in M3b itself (not waiting for the merge): `infra/restart_workers.sh` (folded into `deploy_code.sh`, §5); `build_ami.sh --dlami` (a fallback never needed: both images built on plain Ubuntu).

What stays: **product** (scale-out and scale-in alarms, idle alarm and breaker, dead-letter handler, reaper, heartbeat and scale-in protection, one error alarm per Lambda, `pull_code.sh`, the web and webhook functions), **operator tools** (`deploy_code.sh`, `deploy_web.sh`, `debug_worker.sh`, `check_aurora.py`, `build_ami.sh` with `--cpu-rehearsal` and `--refresh-weights`, `apply_schema.py`, `grant_credit.py`, `aws_env.sh`, `start_work.sh` / `stop_work.sh`, the email-only alerts) and **experiment tools**, deleted after the report (boot records, `cold_start.py`, `latency_*`, `export_cloudwatch.py`, `finish_run.py`, `FAKE_INFERENCE_SECONDS`, `start_work.sh --max 2` for Experiment 2).

## 11. Tests (tests-first; `FAKE_INFERENCE=1 pytest`)
- **Sign-in**, through the §8 seam: the callback clears any session, stores only `user_id`, `email` and `signed_in_at`; `email_verified` `True` and `"true"` are accepted, while `False`, `"false"`, `"True"` and a missing claim give 403 `email_not_verified`; a wrong `state`, a token signed by the wrong key, or `?error=access_denied` gives 400 `sign_in_failed` and no session; `/login` redirects with exactly `<public_base_url>/auth/callback`; logout clears the session and returns the Cognito logout URL built from config; with a fake clock, a session used every hour still works at 11 hours after sign-in and gets 401 at 13 hours.
- **Accounts** (`sign_in`, Postgres backend; CI cannot reach Aurora, so the Data API backend is checked by `check_aurora.py`, §7c): a new sign-in creates one user with a UUID `user_id`, the starter balance and no ledger row when it is 0; the same `(issuer, subject)` returns the same user; a second sign-in with a different subject and the same email (any letter case) links to the same user; a different email creates a different user; two simultaneous first sign-ins with one email produce one user.
- **Every `/api/*` route** returns 401 JSON when signed out (enumerating the URL map, so no new route is forgotten); each listed public route stays public.
- **Cookie flags** in Cognito mode: `Secure`, `HttpOnly`, `SameSite=Lax`.
- **POST without JSON content type** returns 415; responses carry **no** CORS headers.
- **Startup:** Cognito mode with a missing parameter fails fast; dev mode on a non-local host, or with `NEUROLENS_DEPLOYED=1`, raises `UnsafeConfigError`.
- **Lambda handler:** a function-URL event for `GET /healthz` returns 200; a response setting two cookies returns both; the handler builds the app once per container.
- **Database waking:** a stubbed Data API that keeps raising `DatabaseResumingException` makes `/api/me` return 503 `database_waking` within the 45 s budget (fake clock).
- `/healthz` returns 200 without calling the database.
- **History:** only the user's own jobs, newest first; `limit` 1 and 100 accepted, 0, 101 and `abc` give 400; `result_available` false after 30 days.
- **CSV and result:** columns in order, one row per timestep, empty cells for `None`, values identical to the JSON; a `processing` job whose result object exists serves it (both routes); each not-available case returns its exact error with no S3 call before the ownership check.
- **Paused processing:** with the stubbed worker group at max 0, presign returns 503 `processing_paused` and reserves nothing (balance and ledger unchanged); at max 1 it proceeds; when reading the group raises, it returns `presign_failed` and reserves nothing; in dev mode it never reads a group.
- **Webhook answers:** bad signature 400; a valid but refused event (each case in §7b) 200 with no credit; `DatabaseResumingException` past the wait 503; a base64-encoded body verifies.
- **Checkout refusals:** 404 when top-ups are off, 403 `top_up_not_allowed` for a non-allowlisted user, 400 `unknown_pack`.
- **Grants:** exactly the amount with a `grant` ledger row, M3a invariants kept; zero or negative cents and an unknown email raise.
- **Stripe (full treatment):** a live key is rejected at startup; `can_top_up` false for a non-allowlisted user and when disabled; checkout refuses non-allowlisted users and unknown packs and never takes an amount from the request; the webhook rejects a bad signature, `livemode = true`, an unknown pack and a non-allowlisted user; the same event or the same session twice credits once; disabled mode returns 200 without credit.
- **Import hygiene:** the web and webhook code never import `neurolens.inference`, `torch` or `numpy`.

## 12. Acceptance criteria
1. The day-one checks pass (Google and email sign-in through Cognito with the CloudFront callback; `email_verified` for Google users; the Lambda concurrency limit known, and raised if low).
2. A new user signs up with Google, and another with email and password, through `https://<distribution>.cloudfront.net`; each lands with 0 credit and sees the samples section ("Sample videos coming soon." while the public list is empty); every API route returns 401 when signed out.
3. Signing in with Google and with email and password using the same verified email reaches the same account and balance.
4. `GET /api/jobs` and the CSV return only the signed-in user's own jobs; the CSV matches the JSON result exactly.
5. The function URL refuses a direct request (403 without CloudFront's signature); the site works through CloudFront.
6. The website, sign-in and history work while `nat_gateway = false` and no GPU worker exists.
7. With `nat_gateway = true` and `start_work.sh`, one fake job runs end to end through the deployed website (upload, worker, result, history, CSV). With `nat_gateway = false`, `start_work.sh` refuses to start workers, and `terraform apply` refuses `nat_gateway = false` while the group's max is above 0. After `stop_work.sh`, an upload through the deployed site is refused with "Processing is paused" and the balance is unchanged.
8. `stop_work.sh` reports NOT CONFIRMED while a NAT Gateway or an unattached Elastic IP exists, and ALL STOPPED after `nat_gateway = false` is applied.
9. The reaper runs hourly with nothing else active, Aurora pauses again within about 10 minutes of each run (visible in CloudWatch), and the forgotten-database alarm stays OK through 6 such hours.
10. A team member on the allowlist completes a Stripe test Checkout and receives exactly one matching credit; replays never credit twice; a non-allowlisted user sees no top-up control and the checkout endpoint refuses them.
11. `grant_credit.py` gives a participant credit by email.
12. The S3 bucket and the web app no longer allow arbitrary origins.
13. The full M3 diff passed `/security-review` with no unresolved high-severity findings.
14. Experiment 3 artifacts exist under `experiments/experiment-3/` with a manifest.
15. `FAKE_INFERENCE=1 pytest` passes locally and in CI, and the tests were committed before the implementation.

## 13. File layout additions
```
neurolens/web/auth.py              MODIFIED: Cognito mode, login_required, init_auth
neurolens/web/app.py               MODIFIED: /api/me, history, CSV, healthz, top-up, CORS removed, JSON-only POSTs, 503 while the database wakes, presign refused while processing is paused, /api/samples removed
neurolens/web/lambda_handler.py    NEW: the AWS launcher (apig-wsgi)
neurolens/web/stripe_webhook.py    NEW
neurolens/results.py               NEW: to_csv
neurolens/billing.py               MODIFIED: sign_in, list_jobs, grant_credit, credit_test_topup; ensure_user writes no ledger row for 0
neurolens/settings.py              MODIFIED: get_parameter, the new environment variables (incl. NEUROLENS_DEPLOYED, NEUROLENS_WORKER_GROUP)
config.json                        MODIFIED: billing.starter_cents 0, stripe block
requirements/web.txt, dev.txt      MODIFIED: authlib, requests, apig-wsgi, stripe (flask-cors removed); responses
infra/migrations/002_accounts.sql  NEW
infra/build_web_lambda.sh          NEW
infra/deploy_web.sh                NEW
infra/grant_credit.py              NEW
infra/start_work.sh, stop_work.sh  MODIFIED: NAT Gateway checks, reaper steps and NAT instance removed, --keep-worker / --keep-worker-and-db
infra/deploy_code.sh               MODIFIED: restarts idle running workers (--now: busy ones too)
infra/restart_workers.sh           DELETED (folded into deploy_code.sh)
infra/connect_worker.sh            RENAMED infra/debug_worker.sh, refuses without a warm hold
infra/build_ami.sh                 MODIFIED: --dlami removed
infra/db_smoke.py                  RENAMED infra/check_aurora.py, gains a sign_in round trip
infra/terraform/                   MODIFIED: Cognito, CloudFront, web and webhook Lambdas, NAT Gateway switch (NAT instance removed) with its max-0 precondition, reaper hourly, auto-pause 300 s, database alarm on hourly average, idle alarm detect-and-email only, one error alarm per Lambda
infra/iam/neurolens-deploy-*.json  MODIFIED: Cognito, CloudFront, NAT Gateway, Elastic IP, new Lambdas
static/                            MODIFIED: sign-in, balance, history, CSV, top-up, messages, postJSON, empty samples state; privacy.html NEW
docs/session_checklist.md          NEW: nat_gateway on, start_work.sh --keep-worker-and-db, sign-in check, grant credit, stop_work.sh, nat_gateway off (in that order)
experiments/tco_benchmark.py       NEW
experiments/slurm/tco_benchmark.sbatch  NEW
tests/                             MODIFIED: §11
```

## 14. Cost
- Lambda, CloudFront, Cognito, Parameter Store standard parameters, Stripe test mode: free at our use.
- NAT Gateway: about $1.20 a day while it exists (GPU days and the deployed window only).
- Reaper: about $4 a month in Aurora wake-ups.
- S3 site files: cents a month.
- Experiment 3: no new GPU time (the cloud leg reuses Experiment 1).

## 15. Explicitly not in this milestone
No live payments, subscriptions, saved cards or card data. No study consent, event logging or consolidation (M4). No load balancer or web servers.
