# NeuroLens — M3b Implementation Spec
**Milestone:** sign-in, the public HTTPS website on AWS, credit grants, job history, and the switch to a product that runs by itself (20 – 30 Oct)

**Operating model: product mode.** Nothing waits for a person. The website, sign-in, history and results are always available. GPU work runs on a SageMaker asynchronous endpoint that starts a GPU when a job is waiting and stops it after 7 idle minutes (M3e), and the reaper settles stuck jobs every 3 hours (§6). The only by-hand parts are operator tools: `start_work.sh --keep-warm` for demos and study sessions, and `stop_work.sh` to pause GPU work (§5).
**Builds on:** M3a (Aurora via the Data API, three-stage billing, refunds, dead-letter and reaper Lambdas, Aurora-backed status and result endpoints, a development user on `127.0.0.1`). M3b replaces the development user with real accounts, runs the web app on Lambda behind CloudFront's free HTTPS address, and adds history, CSV export and credit grants. It ends with the product ready for the GPU batch and M4's usability study.

**Ground rules for all of M3b:** region `us-east-1`. Python 3.12. All infrastructure is Terraform in `infra/terraform/`, tagged `Project=neurolens`, `Milestone=M3b`. Run AWS commands with the `neurolens` CLI profile only. **Tests first, as in M0 §5:** the §11 tests are written by a separate agent against §2–§8 before the implementation, and are not edited by the implementer. Money and sign-in code (§2, §7) get the full treatment: tests first, 2–3 break-it checks on committed code, and an independent Opus review. `/security-review` runs on the full M3 diff before M3 is merged, and its high-severity findings are fixed first.

## Decisions (8 Oct)
| | Decision | Deciding reason |
|---|---|---|
| A | **Amazon Cognito** for sign-in: Google and email-and-password at launch, more providers later without code changes. Our own user IDs, with a table mapping each sign-in to a user; one person signing in two ways with the **same verified email is one user**. | A provider's ID as our user ID makes every later change a migration of all users. Linking on a verified email is what most products do (Slack, Notion, Atlassian). Cognito is free up to 10,000 monthly users. |
| B | **Flask on AWS Lambda** behind CloudFront (function URL with origin access control). The page and samples come from S3 through the same CloudFront address. | There is no machine to keep alive (no patching, restarts or replacement), and the website works even when GPU work is paused. About $0 at our traffic. |
| C | **No load balancer.** | Nothing takes traffic on servers: Lambda runs more copies by itself, and SageMaker starts a GPU when a job is waiting. Growth is limited by Lambda's account concurrency, Aurora's maximum capacity and the GPU quota, not by a missing load balancer. |
| D | **Product mode** (above), used for the GPU batch, the study and the demo. The GPU runs on a SageMaker asynchronous endpoint in AWS's own network; the VPC holds only the database, and there is no NAT Gateway. The reaper runs **every 3 hours, always**, with Aurora's auto-pause at 5 minutes and its maximum at 1 ACU. | Nothing is a machine we patch and nobody has to switch the GPU on: an upload starts it, and it stops after 7 idle minutes. Every reaper query wakes Aurora (measured: about 11 minutes at its maximum capacity per run), so a reaper that checks often would keep it awake forever; hourly measured about $15 a month, every 3 hours at 1 ACU is about $3 a month (estimate, to be measured: each run measured 0.18 ACU-hours at a 2 ACU maximum; at 1 ACU it should be about half), and it can never silently miss a refund. |

**Open sign-up is safe:** new accounts start with **0 credit**. Credit comes from `grant_credit.py` (§7a). A stranger can sign up and browse the samples, but cannot run a GPU job. The Google OAuth app is **published** (not "Testing"), so anyone with Google can sign up without being added to a list; the consent screen links a one-page privacy policy (`static/privacy.html`).

**Day one: check the unknowns before building anything else.** If any check fails, stop and ask Josh.
1. A Cognito user pool with its hosted sign-in page, Google as an identity provider and the app client's callback `https://<distribution>.cloudfront.net/auth/callback` signs a Google user and an email user in end to end (create the CloudFront distribution first; its Lambda origin can be added later). Google's authorized redirect URI is Cognito's `https://<prefix>.auth.us-east-1.amazoncognito.com/oauth2/idpresponse`.
2. The ID token of a Google-federated user carries `email_verified = true` (through the attribute mapping, §2a).
3. The account's Lambda concurrency limit (new accounts can be well below 1,000). If it is low, request 1,000 through Service Quotas (free); the web, reaper, dead-letter and GPU job functions share it.

## 1. Scope
### In scope
- Cognito sign-in (Google and email-and-password), our own user IDs, account linking by verified email (§2)
- The web app on Lambda behind CloudFront; the page and samples from S3 (§3)
- The page: sign-in, balance, history, CSV, specific failure messages (§4)
- The database-only network and the GPU work switch (§5)
- The reaper every 3 hours, 5-minute auto-pause and 1 ACU maximum (§6)
- 0 starter credit and `grant_credit.py` (§7)
- Experiment 3, an independent track (§9)
- Deleting the scaffolding before `aws-josh` is merged into `main` (§10)

### Out of scope (M4)
The usability study itself, the participant withdrawal script (built nearer the study), the results consolidation script, and final teardown.

## 2. Sign-in (`neurolens/web/auth.py`)
### 2a. Cognito (Terraform)
- User pool `neurolens-users`, **Lite** feature plan, set explicitly (`user_pool_tier = "LITE"`; new pools otherwise default to Essentials). Sign-in with email; self sign-up allowed; **email verification required before the first sign-in**; `attributes_require_verification_before_update = ["email"]`, so a changed email is not used until verified; Cognito's default email sender (about 50 emails a day, enough for this project; spam sign-ups could use them up, while Google sign-in keeps working); the default password policy.
- Identity provider **Google**: the Google client ID is a Terraform variable (not secret); the secret is stored once by hand in Parameter Store as `/neurolens/terraform/google_client_secret` (`SecureString`) and read by Terraform, so no apply needs it in the shell. Never committed. The secret ends up in the Terraform state (encrypted, private bucket), as for any identity provider Terraform creates. Attribute mapping: `email` ← `email`, `email_verified` ← `email_verified`.
- App client: confidential (has a secret), authorization-code flow only, scopes `openid email`, providers `COGNITO` and `Google`. Callback URL `https://<distribution>.cloudfront.net/auth/callback`; sign-out URL `https://<distribution>.cloudfront.net/`. **Its read and write attributes stay at Cognito's defaults** (every standard attribute): mapped attributes must be writable or Cognito skips them, and Cognito refuses `email_verified` in an explicit list. Day-one check 2 confirms Google users arrive with `email_verified`. The client secret goes into Parameter Store by hand (below).
- Hosted domain prefix `neurolens-<account suffix>`, with the classic hosted pages set explicitly (`managed_login_version = 1`; the newer managed login needs the Essentials plan).
- Linking by verified email trusts every provider's verification. A provider added later must be checked for that before it is enabled.
- `prevent_destroy` is **not** set: losing the pool loses no data, because a returning user is linked back to their account by verified email (§2c).

### 2b. Modes and secrets
- `auth.mode`: `"dev"` (laptop and tests, M3a §2, including its `127.0.0.1` guard) or `"cognito"` (AWS). The Lambdas set `NEUROLENS_DEPLOYED=1`, which `settings.apply_env` maps to `deployed: true`; `create_app` raises `UnsafeConfigError` for dev mode whenever `deployed` is true (the web code reads it through settings, never from `os.environ`).
- **Dev mode keeps M3a's fixed development user** (`dev-user`, created with `billing.ensure_user`, no `identities` row). It is a local stand-in, not an account, so it needs no UUID; every request in dev mode is signed in as it.
- Deployment values arrive as environment variables through `settings.apply_env` (never `config.json`): `NEUROLENS_AUTH_MODE`, `NEUROLENS_COGNITO_ISSUER` (`https://cognito-idp.us-east-1.amazonaws.com/<pool id>`), `NEUROLENS_COGNITO_CLIENT_ID`, `NEUROLENS_COGNITO_DOMAIN`, `NEUROLENS_PUBLIC_BASE_URL` (`https://<distribution>.cloudfront.net`; on AWS the launcher reads this and the client ID from Parameter Store instead, §3a), `NEUROLENS_GPU_ENDPOINT` (`neurolens`, §4a), plus M3a's `NEUROLENS_S3_BUCKET`, `NEUROLENS_AWS_REGION` and `NEUROLENS_DB_*`. They map to `auth.mode`, `auth.cognito_issuer`, `auth.cognito_client_id`, `auth.cognito_domain`, `public_base_url`, `aws.gpu_endpoint`. In Cognito mode every one is required, and startup fails if one is missing.
- **Secrets in Parameter Store** (`SecureString`), read once at startup and never written to disk: `/neurolens/web/cognito_client_secret` and `/neurolens/web/flask_secret_key` (at least 32 random bytes, created once by hand and never regenerated, so sessions survive redeploys).

### 2c. Routes and accounts
- Authlib's Flask client with Cognito's OpenID configuration (`<issuer>/.well-known/openid-configuration`), scope `openid email`.
- `GET /login`: `authorize_redirect(<public_base_url>/auth/callback)`. Redirect and sign-out URLs are always built from `public_base_url`, never from the request's host (CloudFront forwards requests to Lambda under a different host name).
- `GET /auth/callback`: `authorize_access_token()`, which checks the OAuth `state` and the ID token's signature, issuer, audience and nonce. Then:
  - `email_verified` must be exactly `True` or the string `"true"` (federated attributes can arrive as strings); anything else, including a missing claim, gives **403** `{"error": "email_not_verified"}` and no session.
  - A state mismatch, a bad token, or an error in the query (`?error=access_denied` when the user cancels) returns **400** `{"error": "sign_in_failed"}` and no session.
  - `billing.sign_in(db, issuer, subject, email, starter_cents) -> user_id` (§8) finds or creates the user, then `session.clear()`, `session.permanent = True`, store only `user_id`, `email` and `signed_in_at` (Unix seconds), and redirect to `/` (never to a URL from the request).
- **Account linking rule** (inside `sign_in`, one transaction): the sign-in `(issuer, subject)` already in `identities` → that user, and if the verified email differs from the user's stored `users.email` (compared lowercased), the stored email is **updated to it** (as the token gives it) in the same transaction. Reason: linking matches the stored email, so an email the user has since given up (changed at Google, a recycled university address) must not keep pointing at their account; after the update, whoever controls the user's *current* verified email controls the account, the same trust as a password reset by email. If another user already has the new email (the unique index), the stored email is left unchanged and a warning is logged (known limitation: merging two accounts is out of scope). Otherwise a user whose email equals the verified email (compared lowercased) → link this sign-in to that user. Otherwise create a user with a new random UUID as `user_id`, a balance of `starter_cents` (0 on AWS; no ledger row when 0), and the identity row. Two simultaneous first sign-ins with the same email end as one user: every insert uses `ON CONFLICT DO NOTHING`, and when the user insert creates nothing, the same transaction reads the winner's row by email. (Catching a unique-violation error instead would not work: the error aborts the whole transaction, on Postgres and the Data API alike.)
- `POST /logout`: `session.clear()`, then **200** `{"logout_url": "https://<cognito domain>/logout?client_id=<id>&logout_uri=<public_base_url>/"}`. The page navigates there, so Cognito's own session ends too; without this, the next sign-in would happen silently.
- Session cookie: Flask's signed cookie, `SESSION_COOKIE_SECURE=True`, `HTTPONLY=True`, `SAMESITE="Lax"`, 12-hour `PERMANENT_SESSION_LIFETIME`, `SESSION_REFRESH_EACH_REQUEST=False`. `SECURE` is `False` only in dev mode (plain `http://localhost`). **A session is refused 12 hours after `signed_in_at`**, however often it is used: Flask re-issues the cookie with a fresh signature, so the cookie's own age alone would let a regularly used cookie live forever. Known and accepted limit: logout clears only that browser, and a copied cookie stays valid until 12 hours after that sign-in. Next to it, `/auth/callback` sets the landing page's hint cookie `nl_signed_in=1` (M3d §8b); everything that clears the session also deletes the hint.
- `current_user()` returns `(user_id, email)` from the session in Cognito mode (or `None`), and the development user in dev mode.
- **Every `/api/*` route requires a signed-in user**, returning **401** `{"error": "not_signed_in"}`. The only unauthenticated Flask routes are `/`, `/static/*`, `/data/*` (laptop only; on AWS these come from S3), `/login`, `/auth/callback`, `/logout`, `/healthz`.
- **Database waking:** the web app's database uses `resume_wait_s=45` (M3a §3d). If Aurora is still waking after that, the route returns **503** `{"error": "database_waking"}`; the page retries after 5 s, up to 3 times, showing "Starting up…". This keeps every request under the Lambda's 60 s timeout and CloudFront's 60 s origin timeout. Known limitation (stopping rule: needs a hostile user): a signed-in stranger calling the API every few minutes keeps Aurora awake (about $1.40 a day); the 6-hour database alarm emails, and the response is to disable that user in Cognito or turn off self sign-up.
- **Cross-site request protection:** `SameSite=Lax`, and every `POST /api/*` requires `Content-Type: application/json` (415 otherwise), which browsers cannot send cross-site without a CORS check the app never grants.
- **CORS removed:** `flask-cors` and `CORS(app)` are deleted. The S3 bucket's allowed origins become the CloudFront address plus `http://localhost:5003`.

## 3. Web tier on AWS
### 3a. Lambda
- Function `neurolens-web`: Python 3.12, arm64, 1769 MB (one full vCPU: a cold start's setup of AWS clients is CPU-bound and took 4.6-5.5 s at 512 MB; the cost stays inside the free tier), timeout 60 s, **outside the VPC** (it reaches Cognito, Parameter Store, S3 and the Data API over AWS's public endpoints).
- `neurolens/web/lambda_handler.py` is the AWS launcher (the laptop's is `app.py`): it loads settings with `settings.load_settings()`, builds the app once per container with `create_app(cfg=...)`, and adapts function-URL events (payload 2.0, including several `Set-Cookie` values) to WSGI with **apig-wsgi** (confirm at build that it handles function-URL events and multiple cookies; the fallback is AWS's Lambda Web Adapter).
- **Function URL** with `AuthType = AWS_IAM`. Its resource policy lets only this CloudFront distribution invoke it, in **two statements** (function URLs created since October 2025 need both): `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction`, each for principal `cloudfront.amazonaws.com` with `SourceArn` = this distribution. Sources: [Lambda function URL access](https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html), [CloudFront OAC for Lambda](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-lambda.html).
- Known limitation (stopping rule: needs heavy traffic): all functions share the account's concurrency, so a flood of web requests could delay the reaper, dead-letter handler or GPU job functions. The production fix is reserved concurrency on `neurolens-web` (one line, once the account limit is 1,000).
- **Packaging:** `infra/build_web_lambda.sh` builds `build/web_lambda.zip` from `neurolens/`, the handler and `requirements/web.txt`, installed for Linux arm64 (`pip install --platform manylinux2014_aarch64 --only-binary=:all: --target ...`). Terraform deploys the zip. The web code never imports `neurolens.inference`, `torch` or `numpy` (import-hygiene test).
- Web IAM role: `ssm:GetParameter` on `/neurolens/web/*` with `kms:Decrypt` for the default key; M3a's `rds-data` and database-secret permissions; `s3:PutObject` on `uploads/*` (presigned POSTs are signed with the role's own credentials); `s3:GetObject` on `results/*` with `s3:ListBucket` on the bucket (without it S3 answers 403, not 404, for a result not written yet); `application-autoscaling:DescribeScalableTargets` (it cannot be scoped to one resource; read-only, for §4a's paused check); its own log group. The public address and the Cognito client ID are read from `/neurolens/web/public_base_url` and `/neurolens/web/cognito_client_id` (plain parameters Terraform writes), not environment variables: CloudFront's address depends on this function's URL, and the client's callback address contains CloudFront's, so environment variables would be a Terraform cycle.
- `GET /healthz`: unauthenticated, 200 `{"ok": true}`, **never touches the database**.

### 3b. CloudFront
- One distribution, default `*.cloudfront.net` certificate, HTTP→HTTPS redirect, price class 100.
- **Site origin: the bucket's `site/` prefix** through origin access control for S3 (the bucket stays private). The bucket policy allows `s3:GetObject` on `site/*` only, for principal `cloudfront.amazonaws.com` with `SourceArn` = this distribution; uploads and results stay unreachable through CloudFront. Default behaviour (`/`, default root object `index.html`), `/static/*` and `/data/*`, managed `CachingOptimized` policy. `site/*` has no lifecycle rule.
- **API origin: the function URL** through origin access control for Lambda (SigV4). Behaviours `/api/*`, `/login`, `/logout`, `/auth/*`, `/healthz`: `CachingDisabled`, `AllViewerExceptHostHeader` origin request policy (cookies, query strings and headers reach Flask; Lambda needs its own Host), all methods, origin read timeout 60 s.
- **POST bodies must carry their hash.** With origin access control, Lambda accepts a POST only with `x-amz-content-sha256` set to the hex SHA-256 of the body. The page sends every POST through one helper, `postJSON(url, body)`, which computes it with `crypto.subtle.digest`. On the laptop Flask ignores the header. Plain HTML form POSTs are therefore not used anywhere.

### 3c. Deploying
`infra/deploy_web.sh` (refuses uncommitted changes):
1. builds the Lambda zip and ships it to `neurolens-web` with `aws lambda update-function-code`, waiting until the update is live. Terraform uploads the zip only when it creates the function and ignores its code afterwards, so an apply for anything else never needs a build or changes the running code; build the zip once before the first apply, and
2. syncs the site files: `static/index.html` → `site/index.html` and `static/app.html` → `site/app` (both uploaded with `Cache-Control: no-cache`, so a browser checks for a new page on every load instead of running an old one after a deploy), `static/` → `site/static/`, and `site/data/samples.json` holding **only the samples whose clip is listed in `data/videos/SOURCES.md`** (open-licensed; third-party ads are never published), with only those samples' videos, thumbnails and results; then invalidates the CloudFront cache (`/*`). Both current samples are listed, so the public list is not empty. The laptop keeps the same samples.

## 4. The page, history and messages
### 4a. Endpoints
- `GET /api/me`: `{"user_id", "email", "balance": {...}}`.
- `GET /api/jobs?limit=50`: the user's jobs, newest first, at most `limit` (default 50; anything but an integer from 1 to 100 returns 400 `{"error": "bad_limit"}`): `[{"job_id", "filename", "status", "created_at", "verified_duration_ms", "captured_cents", "error_code", "result_available"}]`. `result_available` is true for a `done` job created less than 30 days ago (results expire after 30 days).
- `GET /api/jobs/<job_id>/result.csv`: the same ownership check as `/result` (404 for others, no S3 call first). Columns in order: `t, engagement_overall, ffa_faces, eba_bodies, ppa_scenes, sts_social, auditory, auditory_with_audio, auditory_without_audio`, one row per timestep, `None` as an empty cell, numbers exactly as stored in the result JSON. `Content-Disposition: attachment; filename="neurolens-<job_id>.csv"`. **Superseded by M3d:** results now use format 2 (signed signals, no 0-1 rescaling) from `extract_response`, see `docs/M3d_spec.md` §2-§3 and §7.
- `/result` and `/result.csv` serve the result for a `done` job only (M3e §8). Not-available cases: another user's or unknown job → 404 `not_found`; a `failed` job → 404 `not_found` (even if a late worker wrote a result); `queued`/`processing` → 404 `result_not_ready`; a `done` job whose result object is gone (the 30-day expiry) → 404 `result_expired`.
- `POST /api/uploads/presign` gains one check, **before reserving any credit**: when the SageMaker endpoint's scaling maximum is 0 (GPU work stopped by `stop_work.sh`), it returns **503** `{"error": "processing_paused"}` and reserves nothing. It reads the endpoint's scalable target itself (`aws.gpu_endpoint`), not a separate flag, so the two can never disagree. If the read fails, it refuses with `presign_failed` (fails closed). In dev mode (no endpoint) there is no check. Known limitation: jobs already waiting when GPU work stops wait until it restarts or the 4-hour deadline refunds them (M3e §5); the user can cancel a waiting job (M3d §7).
- `GET /api/samples` is **removed** (the page reads `/data/samples.json`). `GET /api/limits` stays and is the one `/api/*` route open signed out (it holds no user data; M3d §6 needs `colour_range` to colour samples' brains); every other `/api/*` route needs sign-in.
- The README's results section notes that `auditory` and the two `auditory_with/without_audio` columns are normalised over different windows (M0 §2), not the CSV.

### 4b. Frontend
- **Start-up:** the page calls `/api/me` once: **200** → the app (then `/api/limits`); **401** → the sign-in screen; **503 `database_waking`** → "Starting up…" with retries; **anything else** → "Something went wrong. Please try again in a minute." with a retry button.
- **Samples** load from `/data/samples.json` in every state (signed in or not). With an empty list, the section shows "Sample videos coming soon." (no empty grid, no broken thumbnails).
- **Sign-in screen:** a "Sign in" button to `/login` (Cognito's page offers Google or email), the sample browser below it. Uploads, balance and history appear only when signed in. "Sign out" in the header (§2c).
- **Balance** in `.header-meta`, from cents, refreshed after each job ends. With 0 credit, the upload area says "You have no credit yet. Ask the NeuroLens team for test credit."
- **History** panel from `/api/jobs`: each row re-opens its stored result in the existing chart code, links to the CSV, and says when results expire. Filenames and `error_message` come from users: inserted with `textContent`, never `innerHTML`.
- **Failure messages** by `error_code`, each ending "You have not been charged." where money was refunded:
  - `insufficient_credit` (402 at presign): "Not enough credit for this video."
  - `insufficient_credit_for_actual_duration`: "Your video is longer than the length we estimated, and your credit doesn't cover the difference."
  - `duration_exceeds_max_estimated` (400 at presign) and `duration_exceeds_max_verified`: "This video is longer than the 120-second maximum."
  - `file_too_large`: "This file is larger than the upload limit."
  - `unreadable_video`: "This file is not a readable video."
  - `processing_failed`, `stalled`: "Processing failed after retrying." plus the job's `error_message` when present.
  - `upload_not_received`: "The upload didn't finish. Please try again."
  - `upload_missing`: "The uploaded file was no longer available (uploads are kept 30 days). Please upload it again."
  - `deadline_passed`, `cancelled`: M3d §8.
  - `presign_failed`: "The upload couldn't be started. Please try again."
  - `processing_paused` (503 at presign): "Processing is paused right now, so no video can be analysed. You have not been charged."
  - Any other code: "Processing failed." (never the raw code alone).

## 5. Network and the work switch
- The VPC holds only Aurora: one private subnet per zone, and no internet gateway, NAT Gateway, route table or S3 gateway endpoint. Every Lambda runs outside the VPC, and the web app reaches Aurora through the Data API and S3 over its public HTTPS endpoint. The GPU container runs in SageMaker's network, also outside our VPC.
- `start_work.sh` sets the SageMaker endpoint's scaling maximum to 1 (its normal state), so an upload starts a GPU by itself and it stops after 7 idle minutes. `--keep-warm [--hours N]` first writes a one-off scheduled action that sets the minimum back to 0 at the end of the window, then raises the minimum to 1 for N hours (1 to 4, default 3); in that order, a failure never leaves a GPU on without an end.
- `stop_work.sh` is the emergency pause: it deletes any keep-warm action first, sets the scalable target's minimum and maximum to 0 and reads them back, sets the endpoint's desired instance count to 0, then waits until the endpoint shows no instance. It prints ALL STOPPED only when every step succeeded, otherwise NOT CONFIRMED with each problem. The website stays up.
- Neither script touches Aurora's capacity; Terraform owns it.
### Operator commands in product mode
Options are named by what they change; the help text says when to use each.
| Command | Does |
|---|---|
| `start_work.sh` | sets the endpoint's scaling maximum to 1, so an upload starts a GPU by itself; it stops after 7 idle minutes. |
| `start_work.sh --keep-warm [--hours N]` | the above plus a warm GPU now, kept for N hours (1 to 4, default 3); AWS ends it at the end of the window. For demos and study sessions. |
| `stop_work.sh` | sets the scalable target to 0/0 and reads it back, deletes any keep-warm action, sets the endpoint's desired count to 0 and waits until no instance remains. The website stays up. |
| `build_image.sh` | zips the last commit (refuses uncommitted changes), builds the worker image in CodeBuild, and prints its digest; put it in `terraform.tfvars` as `worker_image` and apply. |

- Deploy-user permissions (`neurolens-deploy-compute`): VPC, private subnets, the database security group and tagged EC2 changes; SageMaker, Application Auto Scaling and CodeBuild role actions as in M3e §9. It has no NAT Gateway, Elastic IP or route-table creation rights.

## 6. Reaper: every 3 hours, always on
- The reaper rule `neurolens-reaper-schedule` runs `rate(3 hours)` and is always enabled (Terraform). It **replaces** M3a's `neurolens-reaper-every-5-min` (created disabled, with `ignore_changes = [state]` so the scripts could switch it): that rule and its `ignore_changes` are removed, so Terraform alone owns the new rule's state. `start_work.sh` and `stop_work.sh` no longer touch it.
- Aurora's `seconds_until_auto_pause` becomes **300** (the minimum) and its `max_capacity` becomes **1 ACU** (M3a had 2; our load is a few small queries). Terraform owns both capacities: neither `start_work.sh` nor `stop_work.sh` changes Aurora. Each run wakes Aurora for about 11 minutes (measured: it resumes straight to its maximum, and the 300 s count starts only when the last connection closes; about 15 s to resume, M3a's resume wait covers it). Cost: about $3 a month (estimate, to be measured: each run measured 0.18 ACU-hours at a 2 ACU maximum; at 1 ACU it should be about half). Hourly at 2 ACU measured about $15 a month, which is why the interval is 3 hours.
- What it guards against (M3e §5, one 4-hour deadline): an upload that never arrived (credit reserved, nothing will run), a job no GPU ever took, a job whose container died and whose queue message is gone (`processing` forever), and a published result whose charge was not recorded. Worst case, such a job waits about 7 hours (the deadline plus the 3-hour interval) before it is settled or refunded; the dead-letter handler still refunds the common failures within seconds, and a user can cancel a waiting job.
- **The forgotten-database alarm changes what it measures.** M3a's `neurolens-db-awake-6h` fires when Aurora was awake *at some point* in each of 6 hours in a row; the reaper wakes it every 3 hours, so that rule would fire every day with nothing wrong. It now fires when Aurora was awake for **a large share of each hour**: the hourly **70th percentile** of `ServerlessDatabaseCapacity` above **0** in each of 6 hours in a row: the metric reads 0 while paused, so this means awake in more than 30% of the hour's minutes, whatever capacity it ran at. A reaper wake (about 11 minutes, measured) stays below it, while a caller every 12 minutes (about 45%, M3a's example) and a client polling every few minutes (100%) are above it.

## 6b. Alarms
- The EC2 worker group, its idle alarm and the breaker are gone (M3e §6c). The GPU guard's alarms replace them: `neurolens-gpu-guard-stopped` emails when the guard had to stop a GPU, and `neurolens-gpu-guard-not-running` fires when the guard has not run for 30 minutes.
- **One error alarm for every Lambda.** A single `for_each` block covers every function: one email on any failed run. These catch a function that runs and fails, not one that never runs.

## 7. Credit
### 7a. Grants
- `billing.starter_cents` is **0** in the committed `config.json`; laptop tests may set their own.
- `infra/grant_credit.py <dollars>`: the amount is in dollars with at most two decimals (`5`, `2.50`), turned into whole cents. It prompts for the email (so participants' emails never land in shell history), finds the user by email (unique since §8's migration), shows their available balance, asks to confirm, and adds the credit (never sets the balance) in one transaction with a `grant` ledger row. The amount must be positive; an unknown email stops with an error before the confirm. Runs from a laptop with the `neurolens` profile, never from the web app.

### 7b. Checking the database code on Aurora
- `infra/check_aurora.py` is an operator tool: CI tests only the PostgreSQL backend, so this is the one check of the Data API backend's value formats on the real database (M3a §3d). Run it after changing `neurolens/db.py`'s Data API code or adding a migration, and once on a new account.
- It gains one `sign_in` round trip for a throwaway identity: a first sign-in creates the user, a second with the same identity returns the same `user_id`, and a third with another subject and the same email in different letter case links to it. Its existing checks are unchanged, and its clean-up of the throwaway rows, pass or fail, now covers their `identities` rows too.

## 8. Interfaces fixed by this spec (the §11 tests are written against exactly these)
- Migration `002_accounts.sql`: table `identities (issuer TEXT, subject TEXT, user_id TEXT NOT NULL REFERENCES users, created_at, PRIMARY KEY (issuer, subject))`; a unique index on `lower(users.email)`; the ledger `kind` check gains `grant`. The comment on `users.user_id` becomes "our own UUID (text)". Kinds and other values are passed as parameters (project SQL has no string literals).
- `neurolens.billing`: `sign_in(db, issuer, subject, email, starter_cents) -> str`; `list_jobs(db, user_id, limit) -> list[dict]`; `user_by_email(db, email) -> str | None`; `grant_credit(db, email, cents) -> str` (returns the user ID; `ValueError` for `cents <= 0`, `LookupError` for an unknown email). `ensure_user` **stays** (dev mode and existing tests) with one change: no `starter` ledger row when `starter_cents` is 0. `sign_in` creates new users through the same internal step inside its own transaction.
- **Existing tests this spec retires or changes** (AGENTS.md: tests change only where a spec says so):
  - `tests/test_web.py::test_samples_equals_samples_json` and `tests/test_presign.py::test_create_app_with_cfg_needs_no_config_json`'s `/api/samples` request: `/api/samples` is removed. The second test keeps its purpose (no `config.json` needed) and requests another route instead.
  - `/api/me`'s new shape (§4a): `tests/test_m3a_web.py::test_me_returns_email_and_balance`, `test_me_shows_a_reservation` and `test_me_uses_create_apps_built_in_dev_user_without_a_cfg` expect `{user_id, email, balance: {available_cents, reserved_cents}}`.
  - `result_not_ready` (§4a): `tests/test_m3a_web.py::test_result_of_an_unfinished_job_without_a_result_is_404` (both cases) and `tests/test_m2b_web.py`'s job-in-progress result test (renamed for it).
  - Starter credit 0 (§7a): `tests/test_env_settings.py`'s committed-config test (renamed for it).
  - Tests that build Cognito-mode apps get the new required settings; dev-mode tests (`dev-user`, `ensure_user`, `/api/limits`) are unchanged, because dev mode is always signed in.
  - `tests/test_m3a_db.py`'s module docstring names `infra/check_aurora.py` instead of `infra/db_smoke.py` (text only).
  - Any other existing test that fails against this spec is reported to Josh, not edited.
- `neurolens.web.auth`: `current_user() -> tuple[str, str] | None`; `init_auth(app, cfg, ssm_client, *, server_metadata=None)`. The sign-in rule for `/api/*` is one `before_request` check in `create_app`, not a per-route decorator, so a new route cannot be left open. With `server_metadata` (a dict with `issuer`, `authorization_endpoint`, `token_endpoint` and `jwks_uri`), Authlib uses it instead of downloading the OpenID configuration.
- **Sign-in test seam:** tests pass `server_metadata` with fake URLs and fake only HTTP with `responses`: the token endpoint returns an ID token the test signs with its own RSA key; the JWKS URL returns that key. Authlib's real state, nonce and signature checks run. Nothing in the tests contacts Cognito or Google. `dev.txt` gains `responses`.
- `neurolens.settings.get_parameter(ssm_client, name) -> str` (one `SecureString`, decrypted).
- `neurolens.web.app.create_app(..., ssm_client=None)`: in Cognito mode reads the parameters at startup and fails fast if one is missing.
- `neurolens.web.lambda_handler.handler(event, context)`.
- `neurolens.results.to_csv(result: dict) -> str`.
- `requirements/web.txt` gains `authlib`, `requests`, `apig-wsgi`; loses `flask-cors`.

## 9. Experiment 3 (cloud vs on-premise cost)
An independent track.
- `experiments/tco_benchmark.py` times the full two-pass pipeline for 15 s, 30 s and 60 s clips and writes a CSV of wall-clock times.
- **On-premise leg:** the school's GPU cluster via Slurm (`experiments/slurm/tco_benchmark.sbatch`, a GPU with 40 GB or more), falling back to a teammate's 2× RTX 4090 machine; 10 runs per length. Power cost from published GPU TDP figures, not measured. The cluster has no AWS credentials: copy the output to a laptop and upload it with the manifest.
- **Cloud leg:** reuses Experiment 1's runs (3 per clip length) instead of new GPU time; the report states the run counts of each leg.
- Output under `experiments/experiment-3/<run_id>/` with a `manifest.json` (M2b §10's contract). Costs use the **measured on-demand price** ($1.861 an hour for `g6e.xlarge`) and measured runtimes; the consolidation recomputes cost per video and the break-even volume from them and shows the original planned figures beside them, labelled as planned. Never presented as observed fully-loaded costs.

## 10. Scaffolding deleted before the merge into `main`
Scaffolding is gone: the worker's shutdown script and systemd unit, the EC2 worker group and its network, and the old restart/connect scripts.

What stays: **product** (dispatcher, SageMaker error handler, GPU guard, dead-letter handler, reaper, one error alarm per Lambda, the web function), **operator tools** (`deploy_web.sh`, `build_image.sh`, `make_model_data.sh`, `build_ffprobe_layer.sh`, `apply_schema.py`, `check_aurora.py`, `grant_credit.py`, `aws_env.sh`, `start_work.sh` / `stop_work.sh`, the email-only alerts) and **experiment tools**, deleted after the report (boot records, `cold_start.py`, `latency_*`, `export_cloudwatch.py`, `finish_run.py`, `FAKE_INFERENCE_SECONDS`).

## 11. Tests (tests-first; `FAKE_INFERENCE=1 pytest`)
- **Sign-in**, through the §8 seam: the callback clears any session, stores only `user_id`, `email` and `signed_in_at`; `email_verified` `True` and `"true"` are accepted, while `False`, `"false"`, `"True"` and a missing claim give 403 `email_not_verified`; a wrong `state`, a token signed by the wrong key, or `?error=access_denied` gives 400 `sign_in_failed` and no session; `/login` redirects with exactly `<public_base_url>/auth/callback`; logout clears the session and returns the Cognito logout URL built from config; with a fake clock, a session used every hour still works at 11 hours after sign-in and gets 401 at 13 hours.
- **Accounts** (`sign_in`, Postgres backend; CI cannot reach Aurora, so the Data API backend is checked by `check_aurora.py`, §7b): a new sign-in creates one user with a UUID `user_id`, the starter balance and no ledger row when it is 0; the same `(issuer, subject)` returns the same user; a second sign-in with a different subject and the same email (any letter case) links to the same user; a different email creates a different user; two simultaneous first sign-ins with one email produce one user; a known identity signing in with a new verified email updates `users.email` (so a new subject with the **old** email creates a different user, and `get_email` returns the new one), a change of letter case only leaves it unchanged, and a new email already held by another user leaves both users' emails unchanged, still returns the identity's own user, and logs a warning.
- **Every `/api/*` route** returns 401 JSON when signed out (enumerating the URL map, so no new route is forgotten); each listed public route stays public.
- **Cookie flags** in Cognito mode: `Secure`, `HttpOnly`, `SameSite=Lax`.
- **POST without JSON content type** returns 415; responses carry **no** CORS headers.
- **Startup:** Cognito mode with a missing parameter fails fast; dev mode on a non-local host, or with `NEUROLENS_DEPLOYED=1`, raises `UnsafeConfigError`.
- **Lambda handler:** a function-URL event for `GET /healthz` returns 200; a response setting two cookies returns both; the handler builds the app once per container.
- **Database waking:** a stubbed Data API that keeps raising `DatabaseResumingException` makes `/api/me` return 503 `database_waking` within the 45 s budget (fake clock).
- `/healthz` returns 200 without calling the database.
- **History:** only the user's own jobs, newest first; `limit` 1 and 100 accepted, 0, 101 and `abc` give 400; `result_available` false after 30 days.
- **CSV and result:** columns in order, one row per timestep, empty cells for `None`, values identical to the JSON; a `processing` job whose result object exists serves it (both routes); each not-available case returns its exact error with no S3 call before the ownership check.
- **Paused processing:** with the stubbed endpoint's scalable target at max 0, presign returns 503 `processing_paused` and reserves nothing (balance and ledger unchanged); at max 1 it proceeds; when reading the target raises, it returns `presign_failed` and reserves nothing; in dev mode it never reads a target.
- **Grants:** exactly the amount with a `grant` ledger row, M3a invariants kept; zero or negative cents and an unknown email raise.
- **Import hygiene:** the web code never imports `neurolens.inference`, `torch` or `numpy`.

## 12. Acceptance criteria
1. The day-one checks pass (Google and email sign-in through Cognito with the CloudFront callback; `email_verified` for Google users; the Lambda concurrency limit known, and raised if low).
2. A new user signs up with Google, and another with email and password, through `https://<distribution>.cloudfront.net`; each lands with 0 credit and sees the samples section ("Sample videos coming soon." while the public list is empty); every API route returns 401 when signed out.
3. Signing in with Google and with email and password using the same verified email reaches the same account and balance.
4. `GET /api/jobs` and the CSV return only the signed-in user's own jobs; the CSV matches the JSON result exactly.
5. The function URL refuses a direct request (403 without CloudFront's signature); the site works through CloudFront.
6. The website, sign-in and history work while GPU work is paused and no GPU exists.
7. With `start_work.sh`, one fake job runs end to end through the deployed website (upload, GPU, result, history, CSV). After `stop_work.sh`, an upload through the deployed site is refused with "Processing is paused" and the balance is unchanged.
8. `stop_work.sh` prints ALL STOPPED with the endpoint at 0 and no keep-warm action, and NOT CONFIRMED when any step cannot be proven.
9. The reaper runs every 3 hours with nothing else active, Aurora pauses again within about 15 minutes of each run (visible in CloudWatch), and the forgotten-database alarm stays OK through 6 idle hours.
10. `grant_credit.py` gives a participant credit by email.
11. The S3 bucket and the web app no longer allow arbitrary origins.
12. The full M3 diff passed `/security-review` with no unresolved high-severity findings.
13. Experiment 3 artifacts exist under `experiments/experiment-3/` with a manifest.
14. `FAKE_INFERENCE=1 pytest` passes locally and in CI, and the tests were committed before the implementation.

## 13. File layout additions
```
neurolens/web/auth.py              MODIFIED: Cognito mode, login_required, init_auth
neurolens/web/app.py               MODIFIED: /api/me, history, CSV, healthz, CORS removed, JSON-only POSTs, 503 while the database wakes, presign refused while processing is paused, /api/samples removed
neurolens/web/lambda_handler.py    NEW: the AWS launcher (apig-wsgi)
neurolens/results.py               NEW: to_csv
neurolens/billing.py               MODIFIED: sign_in, list_jobs, grant_credit; ensure_user writes no ledger row for 0
neurolens/settings.py              MODIFIED: get_parameter, the new environment variables (incl. NEUROLENS_DEPLOYED, NEUROLENS_GPU_ENDPOINT)
config.json                        MODIFIED: billing.starter_cents 0
requirements/web.txt, dev.txt      MODIFIED: authlib, requests, apig-wsgi (flask-cors removed); responses
infra/migrations/002_accounts.sql  NEW
infra/build_web_lambda.sh          NEW
infra/deploy_web.sh                NEW
infra/grant_credit.py              NEW
infra/start_work.sh, stop_work.sh  MODIFIED: SageMaker endpoint keep-warm and emergency pause, no Aurora changes
infra/check_aurora.py              NEW: the Data API check, with a sign_in round trip
infra/terraform/                   MODIFIED: Cognito, CloudFront, web Lambda, database-only VPC, SageMaker endpoint and scaling, reaper every 3 hours, auto-pause 300 s, Aurora maximum 1 ACU, database alarm on hourly average, GPU guard alarms, one error alarm per Lambda
infra/iam/neurolens-deploy-*.json  MODIFIED: Cognito, CloudFront, SageMaker, ECR, CodeBuild, Application Auto Scaling, new Lambdas
static/                            MODIFIED: sign-in, balance, history, CSV, messages, postJSON, empty samples state; privacy.html NEW
docs/session_checklist.md          NEW: start_work.sh --keep-warm, sign-in check, grant credit, stop_work.sh (in that order)
experiments/tco_benchmark.py       NEW
experiments/slurm/tco_benchmark.sbatch  NEW
tests/                             MODIFIED: §11
```

## 14. Cost
- Lambda, CloudFront, Cognito and Parameter Store standard parameters: free at our use.
- Network: no NAT Gateway or Elastic IP bills; the VPC and subnets are free.
- Reaper: about $3.50 a month in Aurora wake-ups (measured over 67 runs: 0.12 ACU-hours a run, 8 runs a day, $0.12 per ACU-hour). Measure it again from the ACU graph in the first day after any change to the schedule or capacity.
- S3 site files: cents a month.
- Experiment 3: no new GPU time (the cloud leg reuses Experiment 1).

## 15. Explicitly not in this milestone
No live payments, subscriptions, saved cards or card data. No study consent, event logging or consolidation (M4). No load balancer or web servers.
