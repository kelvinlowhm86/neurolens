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
- User pool `neurolens-users`, **Lite** feature plan (free; the classic hosted sign-in page is enough). Sign-in with email; self sign-up allowed; **email verification required before the first sign-in**; Cognito's default email sender (about 50 emails a day, enough for this project); the default password policy.
- Identity provider **Google**: the Google client ID and secret come from Terraform variables set in the environment (`TF_VAR_google_client_secret`), never committed. The secret ends up in the Terraform state (encrypted, private bucket), as for any identity provider Terraform creates. Attribute mapping: `email` ← `email`, `email_verified` ← `email_verified`.
- App client: confidential (has a secret), authorization-code flow only, scopes `openid email`, providers `COGNITO` and `Google`. Callback URL `https://<distribution>.cloudfront.net/auth/callback`; sign-out URL `https://<distribution>.cloudfront.net/`. The client secret goes into Parameter Store by hand (below).
- Hosted domain prefix `neurolens-<account suffix>`.
- `prevent_destroy` is **not** set: losing the pool loses no data, because a returning user is linked back to their account by verified email (§2c).

### 2b. Modes and secrets
- `auth.mode`: `"dev"` (laptop and tests, M3a §2, including its `127.0.0.1` guard) or `"cognito"` (AWS). The Lambda sets `NEUROLENS_DEPLOYED=1`, and `create_app` raises `UnsafeConfigError` for dev mode whenever that variable is set.
- Deployment values arrive as environment variables through `settings.apply_env` (never `config.json`): `NEUROLENS_AUTH_MODE`, `NEUROLENS_COGNITO_ISSUER` (`https://cognito-idp.us-east-1.amazonaws.com/<pool id>`), `NEUROLENS_COGNITO_CLIENT_ID`, `NEUROLENS_COGNITO_DOMAIN`, `NEUROLENS_PUBLIC_BASE_URL` (`https://<distribution>.cloudfront.net`), plus M3a's `NEUROLENS_S3_BUCKET`, `NEUROLENS_AWS_REGION` and `NEUROLENS_DB_*`. They map to `auth.mode`, `auth.cognito_issuer`, `auth.cognito_client_id`, `auth.cognito_domain`, `public_base_url`.
- **Secrets in Parameter Store** (`SecureString`), read once at startup and never written to disk: `/neurolens/web/cognito_client_secret`, `/neurolens/web/flask_secret_key` (at least 32 random bytes, created once by hand and never regenerated, so sessions survive redeploys), and for §7b `/neurolens/web/stripe_secret_key`, `/neurolens/web/stripe_webhook_secret`, `/neurolens/web/topup_allowlist` (comma-separated emails; personal data stays out of git).

### 2c. Routes and accounts
- Authlib's Flask client with Cognito's OpenID configuration (`<issuer>/.well-known/openid-configuration`), scope `openid email`.
- `GET /login`: `authorize_redirect(<public_base_url>/auth/callback)`. Redirect and sign-out URLs are always built from `public_base_url`, never from the request's host (CloudFront forwards requests to Lambda under a different host name).
- `GET /auth/callback`: `authorize_access_token()`, which checks the OAuth `state` and the ID token's signature, issuer, audience and nonce. Then:
  - `email_verified` must be true; otherwise **403** `{"error": "email_not_verified"}` and no session.
  - A state mismatch, a bad token, or an error in the query (`?error=access_denied` when the user cancels) returns **400** `{"error": "sign_in_failed"}` and no session.
  - `billing.sign_in(db, issuer, subject, email, starter_cents) -> user_id` (§8) finds or creates the user, then `session.clear()`, `session.permanent = True`, store only `user_id` and `email`, and redirect to `/` (never to a URL from the request).
- **Account linking rule** (inside `sign_in`, one transaction): the sign-in `(issuer, subject)` already in `identities` → that user. Otherwise a user whose email equals the verified email (compared lowercased) → link this sign-in to that user. Otherwise create a user with a new random UUID as `user_id`, a balance of `starter_cents` (0 on AWS; no ledger row when 0), and the identity row. Two simultaneous first sign-ins with the same email end as one user (unique constraints; the loser re-reads the winner).
- `POST /logout`: `session.clear()`, then **200** `{"logout_url": "https://<cognito domain>/logout?client_id=<id>&logout_uri=<public_base_url>/"}`. The page navigates there, so Cognito's own session ends too; without this, the next sign-in would happen silently.
- Session cookie: Flask's signed cookie, `SESSION_COOKIE_SECURE=True`, `HTTPONLY=True`, `SAMESITE="Lax"`, 12-hour `PERMANENT_SESSION_LIFETIME`. `SECURE` is `False` only in dev mode (plain `http://localhost`). Known and accepted limit: logout clears only that browser, and a copied cookie stays valid until its 12 hours run out.
- `current_user()` returns `(user_id, email)` from the session in Cognito mode, or `None`.
- **Every `/api/*` route requires a signed-in user**, returning **401** `{"error": "not_signed_in"}`. The only unauthenticated Flask routes are `/`, `/static/*`, `/data/*` (laptop only; on AWS these come from S3), `/login`, `/auth/callback`, `/logout`, `/healthz`.
- **Database waking:** the web app's database uses `resume_wait_s=45` (M3a §3d). If Aurora is still waking after that, the route returns **503** `{"error": "database_waking"}`; the page retries after 5 s, up to 3 times, showing "Starting up…". This keeps every request under the Lambda's 60 s timeout and CloudFront's 60 s origin timeout.
- **Cross-site request protection:** `SameSite=Lax`, and every `POST /api/*` requires `Content-Type: application/json` (415 otherwise), which browsers cannot send cross-site without a CORS check the app never grants.
- **CORS removed:** `flask-cors` and `CORS(app)` are deleted. The S3 bucket's allowed origins become the CloudFront address plus `http://localhost:5003`.

## 3. Web tier on AWS
### 3a. Lambda
- Function `neurolens-web`: Python 3.12, arm64, 512 MB, timeout 60 s, **outside the VPC** (it reaches Cognito, Parameter Store, S3 and the Data API over AWS's public endpoints, so it never depends on the NAT Gateway).
- `neurolens/web/lambda_handler.py` is the AWS launcher (the laptop's is `app.py`): it loads settings with `settings.load_settings()`, builds the app once per container with `create_app(cfg=...)`, and adapts function-URL events (payload 2.0, including several `Set-Cookie` values) to WSGI with **apig-wsgi** (confirm at build that it handles function-URL events and multiple cookies; the fallback is AWS's Lambda Web Adapter).
- **Function URL** with `AuthType = AWS_IAM`; a resource policy lets only this CloudFront distribution invoke it.
- **Packaging:** `infra/build_web_lambda.sh` builds `build/web_lambda.zip` from `neurolens/`, the handler and `requirements/web.txt`, installed for Linux arm64 (`pip install --platform manylinux2014_aarch64 --only-binary=:all: --target ...`). Terraform deploys the zip. The web code never imports `neurolens.inference`, `torch` or `numpy` (import-hygiene test).
- Web IAM role: `ssm:GetParameter` on `/neurolens/web/*` with `kms:Decrypt` for the default key; M3a's `rds-data` and database-secret permissions; `s3:PutObject` on `uploads/*` (presigned POSTs are signed with the role's own credentials); `s3:GetObject` on `results/*`; its own log group.
- `GET /healthz`: unauthenticated, 200 `{"ok": true}`, **never touches the database**.

### 3b. CloudFront
- One distribution, default `*.cloudfront.net` certificate, HTTP→HTTPS redirect, price class 100.
- **Site origin: the bucket's `site/` prefix** through origin access control for S3 (the bucket stays private). Default behaviour (`/`, default root object `index.html`), `/static/*` and `/data/*`, managed `CachingOptimized` policy. `site/*` has no lifecycle rule.
- **API origin: the function URL** through origin access control for Lambda (SigV4). Behaviours `/api/*`, `/login`, `/logout`, `/auth/*`, `/healthz`: `CachingDisabled`, `AllViewerExceptHostHeader` origin request policy (cookies, query strings and headers reach Flask; Lambda needs its own Host), all methods, origin read timeout 60 s.
- **POST bodies must carry their hash.** With origin access control, Lambda accepts a POST only with `x-amz-content-sha256` set to the hex SHA-256 of the body. The page sends every POST through one helper, `postJSON(url, body)`, which computes it with `crypto.subtle.digest`. On the laptop Flask ignores the header. Plain HTML form POSTs are therefore not used anywhere.

### 3c. Deploying
`infra/deploy_web.sh` (refuses uncommitted changes, like `deploy_code.sh`):
1. builds the Lambda zip and runs `terraform apply -target` on the web function only (printing the plan first), and
2. syncs the site files: `static/index.html` → `site/index.html`, `static/` → `site/static/`, and `data/samples.json`, `data/output/` and only the clips listed in `data/videos/SOURCES.md` (open-licensed; third-party ads are never published) → `site/data/`; then invalidates the CloudFront cache (`/*`).

## 4. The page, history and messages
### 4a. Endpoints
- `GET /api/me`: `{"user_id", "email", "balance": {...}, "can_top_up": bool}`.
- `GET /api/jobs?limit=50`: the user's jobs, newest first, at most `limit` (default 50; anything but an integer from 1 to 100 returns 400 `{"error": "bad_limit"}`): `[{"job_id", "filename", "status", "created_at", "verified_duration_ms", "captured_cents", "error_code", "result_available"}]`. `result_available` is true for a `done` job created less than 30 days ago (results expire after 30 days).
- `GET /api/jobs/<job_id>/result.csv`: the same ownership check as `/result` (404 for others, no S3 call first). Columns in order: `t, engagement_overall, ffa_faces, eba_bodies, ppa_scenes, sts_social, auditory, auditory_with_audio, auditory_without_audio`, one row per timestep, `None` as an empty cell, numbers exactly as stored in the result JSON. `Content-Disposition: attachment; filename="neurolens-<job_id>.csv"`.
- Not-available cases, shared by `/result` and `/result.csv`: another user's or unknown job → 404 `not_found`; a `failed` job → 404 `not_found`; still `queued`/`processing` → 404 `result_not_ready`; a `done` job whose result has expired → 404 `result_expired`.
- The README's results section notes that `auditory` and the two `auditory_with/without_audio` columns are normalised over different windows (M0 §2), not the CSV.

### 4b. Frontend
- **Start-up:** the page calls `/api/me` once: **200** → the app; **401** → the sign-in screen; **503 `database_waking`** → "Starting up…" with retries; **anything else** → "Something went wrong. Please try again in a minute." with a retry button.
- **Samples** load from `/data/samples.json` in every state (signed in or not).
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
  - Any other code: "Processing failed." (never the raw code alone).

## 5. Network: the NAT Gateway switch
- The NAT instance, its role, security group and start/stop steps are **removed**.
- Terraform variable `nat_gateway` (bool, default `false`). When `true`: an Elastic IP and a NAT Gateway in the public subnet, and the private route table's `0.0.0.0/0` route to it. When `false`: neither exists, and private subnets reach only S3 (the existing free gateway endpoint). Creating takes about 2 minutes. Cost: about $0.045 an hour for the gateway plus $0.005 for its address, only while it exists.
- Set to `true` for days with GPU work and for the final deployed window (GPU batch, study, demo); `false` otherwise. The website never needs it (§3a).
- `start_work.sh` refuses to let workers start (any form that raises the group's max) unless the NAT Gateway exists and is `available`: a worker without it cannot reach SQS or the Data API and would only wait for the idle alarm.
- `stop_work.sh` reports a NAT Gateway or an unattached `neurolens` Elastic IP that still exists as a problem (NOT CONFIRMED), with the fix: set `nat_gateway = false` and apply.
### Operator commands in product mode
| Command | Does |
|---|---|
| `start_work.sh` | checks the NAT Gateway (above), then the worker group's max to 1: an upload starts a worker. |
| `start_work.sh --worker [--hours N]` | the above plus a warm hold (M2b §2d). |
| `start_work.sh --study` | `--worker`, plus Aurora's minimum capacity 0.5 ACU so nobody waits for the database to wake (about $0.06 an hour). For demos and study sessions. |
| `stop_work.sh` | workers to 0/0/0 and the warm hold released; Aurora's minimum back to 0; then the checks (no GPU instance; the NAT Gateway and Elastic IP, above). The website stays up. |

Terraform ignores changes to Aurora's `min_capacity`, so the scripts and Terraform don't fight.

- Deploy-user permissions (paste into `neurolens-deploy-compute`): create, delete and describe NAT Gateways, allocate, release and associate Elastic IPs, all limited to `Project=neurolens` tags as for other EC2 resources.

## 6. Reaper: hourly, always on
- The reaper rule `neurolens-reaper-hourly` runs `rate(1 hour)` and is always enabled (Terraform). `start_work.sh` and `stop_work.sh` no longer touch it.
- Aurora's `seconds_until_auto_pause` becomes **300** (the minimum). Each hourly run wakes Aurora for about 5 minutes (about 15 s to resume, M3a's resume wait covers it): about $4 a month.
- What it guards against (unchanged rules, M3a §7): an upload that never arrived (credit reserved, nothing will run), a job whose worker died and whose queue message is gone (`processing` forever), and a published result whose charge was not recorded. Worst case, such a job waits about 70 minutes (its 10- or 60-minute threshold plus the hour) before it is settled or refunded; the dead-letter handler still refunds the common failures within seconds.
- The forgotten-database alarm (M3a) stays: with hourly 5-minute wakes, Aurora awake for 6 hours still means something else is keeping it up.

## 7. Credit
### 7a. Grants
- `billing.starter_cents` is **0** in the committed `config.json`; laptop tests may set their own.
- `infra/grant_credit.py <cents>`: prompts for the email (so participants' emails never land in shell history), finds the user by email (unique since §8's migration), and adds the credit in one transaction with a `grant` ledger row. `cents` must be positive; an unknown email stops with an error. Runs from a laptop with the `neurolens` profile, never from the web app.

### 7b. Stripe test-mode top-ups (full treatment: money)
Demonstrates a payment flow **without real money**; it must never be switched to live mode (the model's licence, CC BY-NC, forbids commercial use).
- `/neurolens/web/stripe_secret_key` must start with `sk_test_` (startup fails otherwise). Config `stripe.enabled`, `stripe.packs` (`{"5": 500, "10": 1000}`, pack name → cents).
- `can_top_up` is true only for a signed-in user whose email is in `/neurolens/web/topup_allowlist` and when `stripe.enabled`. The allowlist matters because Stripe's public test card would otherwise give anyone free GPU credit.
- `POST /api/top-up/checkout` (signed in, allowlisted, JSON `{"pack": "5" | "10"}`): creates a hosted Stripe Checkout Session with the server-defined amount in USD, card only, metadata `user_id` and `pack`, `success_url`/`cancel_url` from `public_base_url`, and returns only its URL. The browser never sends an amount, user ID or session ID.
- **Webhook: its own function** `neurolens-stripe-webhook` (same code bundle, handler `neurolens/web/stripe_webhook.py`), with a function URL of `AuthType = NONE`, called by Stripe directly (not through CloudFront, because Stripe cannot send the body-hash header). Its security is Stripe's signature, verified over the **raw request body** before parsing. With `stripe.enabled` false it returns 200 and does nothing. Otherwise it accepts only `checkout.session.completed` with `livemode = false`, an allowlisted user and a known pack. In one transaction: insert into `stripe_test_events` (event ID primary key, session ID unique) with `ON CONFLICT DO NOTHING`; only if a row was inserted, add the pack's cents and a `test_topup` ledger row. A repeated event or session never credits twice; the success redirect never credits anything.
- The webhook role has only `ssm:GetParameter` on the Stripe and allowlist parameters, `rds-data` and the database secret, and its log group.

## 8. Interfaces fixed by this spec (the §11 tests are written against exactly these)
- Migration `002_accounts.sql`: table `identities (issuer TEXT, subject TEXT, user_id TEXT NOT NULL REFERENCES users, created_at, PRIMARY KEY (issuer, subject))`; a unique index on `lower(users.email)`; the ledger `kind` check gains `grant`; table `stripe_test_events (event_id TEXT PRIMARY KEY, session_id TEXT UNIQUE NOT NULL, user_id TEXT NOT NULL REFERENCES users, cents BIGINT NOT NULL, created_at)`. The comment on `users.user_id` becomes "our own UUID (text)". Kinds and other values are passed as parameters (project SQL has no string literals).
- `neurolens.billing`: `sign_in(db, issuer, subject, email, starter_cents) -> str`; `list_jobs(db, user_id, limit) -> list[dict]`; `grant_credit(db, email, cents) -> str` (returns the user ID; `ValueError` for `cents <= 0`, `LookupError` for an unknown email); `credit_test_topup(db, event_id, session_id, user_id, cents) -> bool` (True only when it credited). `ensure_user` is replaced by `sign_in`; the dev user is created through `sign_in` with issuer `"dev"`.
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
- `infra/self_terminate.sh` and `infra/neurolens-self-terminate.service` (already masked on workers; removed from the image at the next build);
- `infra/db_smoke.py` (the M3a rehearsal check), unless Josh keeps it as an operator tool.

What stays: **product** (scale-out and scale-in alarms, idle alarm and breaker with its error alarm, dead-letter handler, reaper, heartbeat and scale-in protection, Lambda error alarms, `pull_code.sh`, the web and webhook functions), **operator tools** (`deploy_code.sh`, `deploy_web.sh`, `restart_workers.sh`, `connect_worker.sh`, `build_ami.sh`, `apply_schema.py`, `grant_credit.py`, `aws_env.sh`, `start_work.sh` / `stop_work.sh`, the email-only alerts) and **experiment tools** until the report (boot records, `cold_start.py`, `latency_*`, `export_cloudwatch.py`, `finish_run.py`, `FAKE_INFERENCE_SECONDS`).

## 11. Tests (tests-first; `FAKE_INFERENCE=1 pytest`)
- **Sign-in**, through the §8 seam: the callback clears any session, stores only `user_id` and `email`; an unverified email gives 403 `email_not_verified`; a wrong `state`, a token signed by the wrong key, or `?error=access_denied` gives 400 `sign_in_failed` and no session; `/login` redirects with exactly `<public_base_url>/auth/callback`; logout clears the session and returns the Cognito logout URL built from config; a cookie older than 12 hours gets 401.
- **Accounts** (`sign_in`, Postgres and Data API backends): a new sign-in creates one user with a UUID `user_id`, the starter balance and no ledger row when it is 0; the same `(issuer, subject)` returns the same user; a second sign-in with a different subject and the same email (any letter case) links to the same user; a different email creates a different user; two simultaneous first sign-ins with one email produce one user.
- **Every `/api/*` route** returns 401 JSON when signed out (enumerating the URL map, so no new route is forgotten); each listed public route stays public.
- **Cookie flags** in Cognito mode: `Secure`, `HttpOnly`, `SameSite=Lax`.
- **POST without JSON content type** returns 415; responses carry **no** CORS headers.
- **Startup:** Cognito mode with a missing parameter fails fast; dev mode on a non-local host, or with `NEUROLENS_DEPLOYED=1`, raises `UnsafeConfigError`.
- **Lambda handler:** a function-URL event for `GET /healthz` returns 200; a response setting two cookies returns both; the handler builds the app once per container.
- **Database waking:** a stubbed Data API that keeps raising `DatabaseResumingException` makes `/api/me` return 503 `database_waking` within the 45 s budget (fake clock).
- `/healthz` returns 200 without calling the database.
- **History:** only the user's own jobs, newest first; `limit` 1 and 100 accepted, 0, 101 and `abc` give 400; `result_available` false after 30 days.
- **CSV:** columns in order, one row per timestep, empty cells for `None`, values identical to the JSON; each not-available case returns its exact error with no S3 call before the ownership check.
- **Grants:** exactly the amount with a `grant` ledger row, M3a invariants kept; zero or negative cents and an unknown email raise.
- **Stripe (full treatment):** a live key is rejected at startup; `can_top_up` false for a non-allowlisted user and when disabled; checkout refuses non-allowlisted users and unknown packs and never takes an amount from the request; the webhook rejects a bad signature, `livemode = true`, an unknown pack and a non-allowlisted user; the same event or the same session twice credits once; disabled mode returns 200 without credit.
- **Import hygiene:** the web and webhook code never import `neurolens.inference`, `torch` or `numpy`.

## 12. Acceptance criteria
1. The day-one checks pass (Google and email sign-in through Cognito with the CloudFront callback; `email_verified` for Google users; the Lambda concurrency limit known, and raised if low).
2. A new user signs up with Google, and another with email and password, through `https://<distribution>.cloudfront.net`; each lands with 0 credit and sees the samples; every API route returns 401 when signed out.
3. Signing in with Google and with email and password using the same verified email reaches the same account and balance.
4. `GET /api/jobs` and the CSV return only the signed-in user's own jobs; the CSV matches the JSON result exactly.
5. The function URL refuses a direct request (403 without CloudFront's signature); the site works through CloudFront.
6. The website, sign-in and history work while `nat_gateway = false` and no GPU worker exists.
7. With `nat_gateway = true` and `start_work.sh`, one fake job runs end to end through the deployed website (upload, worker, result, history, CSV). With `nat_gateway = false`, `start_work.sh` refuses to start workers.
8. `stop_work.sh` reports NOT CONFIRMED while a NAT Gateway or an unattached Elastic IP exists, and ALL STOPPED after `nat_gateway = false` is applied.
9. The reaper runs hourly with nothing else active, and Aurora pauses again within about 10 minutes of each run (visible in CloudWatch).
10. A team member on the allowlist completes a Stripe test Checkout and receives exactly one matching credit; replays never credit twice; a non-allowlisted user sees no top-up control and the checkout endpoint refuses them.
11. `grant_credit.py` gives a participant credit by email.
12. The S3 bucket and the web app no longer allow arbitrary origins.
13. The full M3 diff passed `/security-review` with no unresolved high-severity findings.
14. Experiment 3 artifacts exist under `experiments/experiment-3/` with a manifest.
15. `FAKE_INFERENCE=1 pytest` passes locally and in CI, and the tests were committed before the implementation.

## 13. File layout additions
```
neurolens/web/auth.py              MODIFIED: Cognito mode, login_required, init_auth
neurolens/web/app.py               MODIFIED: /api/me, history, CSV, healthz, top-up, CORS removed, JSON-only POSTs, 503 while the database wakes
neurolens/web/lambda_handler.py    NEW: the AWS launcher (apig-wsgi)
neurolens/web/stripe_webhook.py    NEW
neurolens/results.py               NEW: to_csv
neurolens/billing.py               MODIFIED: sign_in (replaces ensure_user), list_jobs, grant_credit, credit_test_topup
neurolens/settings.py              MODIFIED: get_parameter, the new environment variables
config.json                        MODIFIED: billing.starter_cents 0, stripe block
requirements/web.txt, dev.txt      MODIFIED: authlib, requests, apig-wsgi, stripe (flask-cors removed); responses
infra/migrations/002_accounts.sql  NEW
infra/build_web_lambda.sh          NEW
infra/deploy_web.sh                NEW
infra/grant_credit.py              NEW
infra/start_work.sh, stop_work.sh  MODIFIED: NAT Gateway checks, reaper steps and NAT instance removed
infra/terraform/                   MODIFIED: Cognito, CloudFront, web and webhook Lambdas, NAT Gateway switch (NAT instance removed), reaper hourly, auto-pause 300 s
infra/iam/neurolens-deploy-*.json  MODIFIED: Cognito, CloudFront, NAT Gateway, Elastic IP, new Lambdas
static/                            MODIFIED: sign-in, balance, history, CSV, top-up, messages, postJSON; privacy.html NEW
docs/session_checklist.md          NEW: nat_gateway on, start_work.sh --study, sign-in check, grant credit, stop_work.sh, nat_gateway off
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
