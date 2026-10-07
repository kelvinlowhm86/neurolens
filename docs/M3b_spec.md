# NeuroLens — M3b Implementation Spec
**Milestone:** Google sign-in, public HTTPS website, web tier on AWS, job history (second part of 20 – 30 Oct, plus the load balancer in the final week before the study)

**Operating model: on demand.** Nothing runs by default, including in the study week. Before a demo, a study session or a test, run `start_work.sh --study` about 15 minutes ahead; afterwards, `stop_work.sh`. While the system is off, the public address still loads the page from S3, shows a "paused" notice, and lets visitors browse the pre-computed sample results (§3c).
**Builds on:** M3a (Aurora via the Data API, three-stage billing, refunds, dead-letter and reaper Lambdas, Aurora-backed status and result endpoints, a fixed development user on `127.0.0.1`). M3b replaces the development user with Google sign-in, puts the web app on AWS behind CloudFront's free HTTPS address, and adds job history and CSV export. It ends with the product ready for M4's usability study.

**Ground rules for all of M3b:** region `us-east-1`. Python 3.12. All infrastructure is Terraform in `infra/terraform/`, tagged `Project=neurolens`, `Milestone=M3b`. Run AWS commands with the `neurolens` CLI profile only. **Tests first, as in M0 §5:** the §9 tests are written by a separate agent against §2–§5 before the implementation, and are not edited by the implementer. `/security-review` runs on the full M3 diff before M3 is merged, and its high-severity findings are fixed first.

**Day one — check the address works with Google.** Create the CloudFront distribution (§3; the origin can be added later), then add `https://<distribution>.cloudfront.net/auth/callback` as an authorized redirect URI in the Google Cloud console. Google requires HTTPS and a host under a public suffix; `cloudfront.net` should qualify, but confirm it before building anything else. If Google rejects it, stop and ask Josh: the fallbacks are a free dynamic-DNS name with Caddy, or a cheap bought domain.

## 1. Scope
### In scope
- Google sign-in (OpenID Connect) replacing M3a's development user on AWS
- The web app on AWS: the page and sample results served from S3 through CloudFront (free HTTPS, always available), and the API on a small private server started on demand; in the final week an internal load balancer with a one-machine Auto Scaling group
- On-demand start/stop commands, a paused state for when the system is off, and a pre-session checklist
- Real user IDs throughout; CORS closed; the M1 security note resolved
- Job history, re-viewing past results, CSV export, and specific failure messages
- A manual credit-grant script for the team and study participants
- Experiment 3 (cloud vs on-premise cost benchmark), an independent track
- **Stretch, only after every other acceptance criterion passes:** a Stripe test-mode credit demo (§7)

### Out of scope (M4)
The usability study itself (run with a Google Form and a moderator, no in-app study code), the results consolidation script, and final teardown.

## 2. Google sign-in (`neurolens/web/auth.py`)
- Config `auth.mode = "google"` on AWS; `"dev"` stays available for laptop work and tests (M3a §2, including its `127.0.0.1` guard). On AWS, the systemd units of the web server and the worker set `NEUROLENS_DEPLOYED=1`, and `create_app` raises `UnsafeConfigError` for dev mode whenever that variable is set, whatever the configured host. gunicorn binds to `0.0.0.0` regardless of the config's host, so the host check alone would not stop a mistyped config exposing the dev identity.
- Uses **Authlib**'s Flask client with Google's OpenID configuration (`https://accounts.google.com/.well-known/openid-configuration`), scope `openid email`.
- **Secrets in Parameter Store** (`SecureString`), read once by `create_app` with the web role and never written to disk: `/neurolens/web/google_client_secret`, `/neurolens/web/flask_secret_key` (at least 32 random bytes, created once by hand and never regenerated, so sessions survive a replaced server). The Google client ID and the redirect URI are ordinary config: `auth.google_client_id`, `auth.redirect_uri`.
- Routes:
  - `GET /login`: `authorize_redirect(cfg auth.redirect_uri)`. The redirect URI always comes from config, never from the request's host, because CloudFront forwards requests to the server under a different host name.
  - `GET /auth/callback`: `authorize_access_token()`, which checks the OAuth `state` and the ID token's signature, issuer, audience and nonce. Require `email_verified`; otherwise 403. Then `session.clear()`, `session.permanent = True`, store only `user_id` (Google `sub`) and `email`, `ensure_user(...)`, and redirect to `/` (never to a URL taken from the request, so there is no open redirect).
    - A state mismatch (Authlib's `MismatchingStateError`) or a Google error in the query (such as `?error=access_denied` when the user cancels) returns **400** `{"error": "sign_in_failed"}` and creates no session.
  - `POST /logout`: `session.clear()`, 204.
- Session cookie: Flask's signed cookie with `SESSION_COOKIE_SECURE=True`, `SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SAMESITE="Lax"`, `PERMANENT_SESSION_LIFETIME` of 12 hours. On the laptop over plain `http://localhost`, `SESSION_COOKIE_SECURE` is `False` only when `auth.mode = "dev"`. Known and accepted limit: sessions live in the signed cookie, so logout only clears that browser, and a copied cookie (or a user removed from Google's test-user list) stays valid until the 12 hours run out.
- `current_user()` returns the session's user in Google mode, or `None`.
- **Every `/api/*` route requires a signed-in user**, returning **401** `{"error": "not_signed_in"}` (JSON, not a redirect; the page shows the sign-in screen itself). The only unauthenticated Flask routes are `/`, `/static/*`, `/data/*` (the fixed public page, sample results, videos and thumbnails; on AWS CloudFront serves these from S3 and they never reach Flask), `/login`, `/auth/callback`, `/healthz`, and (stretch) `/api/stripe/webhook`.
- **Database waking up:** the web app builds its database with `resume_wait_s=45` (M3a §3d). If Aurora is still waking after that, the route returns **503** `{"error": "database_waking"}`, and the page retries after 5 seconds, up to 3 times, showing "Starting up…". This keeps every request under gunicorn's 90 s timeout and CloudFront's 60 s origin timeout (§3a).
- **Cross-site request protection:** `SameSite=Lax` stops other sites sending the session cookie with a POST, and every `POST /api/*` except the webhook requires `Content-Type: application/json` (415 otherwise), which browsers cannot send cross-site without a CORS check that the app never grants.
- **CORS removed:** the page and the API share one origin, so `flask-cors` and `CORS(app)` are deleted. The S3 bucket's allowed origins become the CloudFront address plus `http://localhost:5003` (M1's Terraform variable). This resolves the M1 security note.
- Google consent screen stays in **Testing** mode for the whole project. The team, the TA and the five study participants are added as test users (up to 100 are allowed), which avoids Google's app review.
- Real user IDs: presign already builds `uploads/{user_id}/{job_id}{ext}` from `current_user()` (M3a §6); nothing else changes.

## 3. Web tier on AWS
### 3a. Development phase (`web_mode = "instance"`, until the final week)
- One `t4g.micro` Amazon Linux 2023 instance in a **private subnet**, no public IP, started and stopped with the rest of the system by `start_work.sh` / `stop_work.sh` (it needs the NAT instance for Google, SSM and the Data API).
- **CloudFront** distribution with the default `*.cloudfront.net` certificate and **two origins**. HTTP to HTTPS redirect at CloudFront; price class 100 (cheapest regions).
  1. **Site origin: the S3 bucket's `site/` prefix**, through Origin Access Control (the bucket stays private; its policy lets only this distribution read `site/*`, alongside M1's HTTPS-only rule). It serves the default route (`/`, with default root object `index.html`), `/static/*` and `/data/*` with the managed `CachingOptimized` policy. These are fixed public files, so they load whether or not the server is running, and sample videos never stream through the small server. `site/*` is not covered by any lifecycle rule.
  2. **API origin: the web server** through a **VPC origin** (free; the VPC already has an internet gateway, which VPC origins require). It serves `/api/*`, `/login`, `/logout`, `/auth/*` and `/healthz` with `CachingDisabled` and the `AllViewerExceptHostHeader` origin request policy, so cookies, query strings and headers reach Flask and `Set-Cookie` comes back. All HTTP methods allowed. Origin read timeout **60 s** (the maximum without a quota request). The server's security group allows port 8000 **only** from the CloudFront VPC-origin security group AWS creates.
- Locally nothing changes: Flask still serves `/`, `/static/*` and `/data/*` itself, so the same page works on the laptop.
- **Terraform ordering for the VPC origin** (medium-high confidence in these details; check on first apply): CloudFront creates the security group `CloudFront-VPCOrigins-Service-SG` itself when the first VPC origin is deployed. The server's inbound rule therefore looks that group up by name (a `data` source with `depends_on` the VPC origin). Creating or changing a VPC origin takes about 10–15 minutes. At teardown the VPC can only be deleted after CloudFront has removed that group.
- **Service `infra/neurolens-web.service`:**
  - `WorkingDirectory=/opt/neurolens/app`
  - `EnvironmentFile=/opt/neurolens/env.conf` (`NEUROLENS_ROOT=/opt/neurolens/app`, `NEUROLENS_DEPLOYED=1`, and the identifiers listed under UserData below, including `NEUROLENS_S3_BUCKET`)
  - `ExecStartPre=/opt/neurolens/bin/pull_web_code.sh`
  - `ExecStart=/opt/neurolens/venv/bin/gunicorn --workers 2 --timeout 90 --bind 0.0.0.0:8000 "neurolens.web.app:create_app()"`
  - `Restart=on-failure`
  
  Never Flask's development server. `pull_web_code.sh` works like M2a's worker pull, but from `code-web/latest.zip`; it installs `requirements/web.txt` into `/opt/neurolens/venv` only when that file's hash has changed.
- `infra/deploy_web_code.sh` (refuses uncommitted changes, like `deploy_code.sh`) does two things:
  1. **Server bundle:** `git archive` of `app.py`, `neurolens`, `pyproject.toml`, `requirements`, `infra/neurolens-web.service` and `infra/pull_web_code.sh` to `code-web/latest.zip` and `code-web/latest.revision`. The worker and web bundles never overwrite each other.
  2. **Site files:** syncs `static/index.html` to `site/index.html`, `static/` to `site/static/`, and `data/samples.json`, `data/output/` and only the clips listed in `data/videos/SOURCES.md` (open-licensed; third-party ads are never published) to `site/data/`, then invalidates the CloudFront cache (`/*`; the first 1,000 invalidation paths each month are free).
- UserData (templated, first boot):
  - Install Python 3.12 with `dnf` and create the venv.
  - Download `code-web/latest.zip` once, and install the service unit and `pull_web_code.sh` from it.
  - Write `env.conf` and `config.json` (mode 600, no secrets). `env.conf` holds the deployment-specific identifiers as environment variables (M1 §2): `NEUROLENS_AWS_REGION`, `NEUROLENS_S3_BUCKET` and the three `NEUROLENS_DB_*` (M3a §9). `config.json` holds the settings: `auth.mode = "google"`, `auth.google_client_id`, `auth.redirect_uri`, `public_base_url = https://<distribution>.cloudfront.net`, `db.backend = "data_api"`, `max_upload_bytes`, `billing.starter_cents`. Then enable and start the service.
  - The same `ERR`-trap pattern as M2a logs failures; there is no self-termination for the web server.
  - UserData runs only on first boot, so the instance sets `user_data_replace_on_change = true` in Terraform: changing UserData replaces the instance, and the VPC origin follows it to the new one.
- Web IAM role: `ssm:GetParameter` on `/neurolens/web/*` (with `kms:Decrypt` for the default key); the M3a `rds-data` and database-secret permissions; `s3:PutObject` on `uploads/*` (a presigned POST is signed with the server's own credentials, so the role must be allowed to write what it signs); `s3:GetObject` on `results/*` and `code-web/*`, with `s3:ListBucket` for those prefixes; the SSM core policy (Session Manager, no SSH).
- `GET /healthz`: unauthenticated, 200 `{"ok": true}`, and **never touches the database**, so health checks cannot keep Aurora awake.

### 3b. Final week (`web_mode = "alb"`)
Switched on shortly before the study, by changing one Terraform variable:
- An **internal** Application Load Balancer in the two private subnets, listening on **HTTP port 80**; a target group on port 8000 with health check `/healthz` (every 15 s, 2 failures); and a web **Auto Scaling group**, standing at **min 0 / max 1 / desired 0**, with health-check type `ELB` and a **600 s health-check grace period** (longer than UserData needs to install Python and packages, so a booting server is not replaced in a loop), using a Launch Template with the same UserData. `start_work.sh` sets min and desired to 1, `stop_work.sh` back to 0; Terraform ignores changes to those sizes. While running, the ASG replaces a failed server automatically. Each start launches a fresh server (about 5 minutes including installs), which the 15-minute lead time covers.
- The load balancer itself stays up for the whole final week (creating it per session would be too slow: CloudFront takes 10–15 minutes to re-point).
- CloudFront's VPC origin switches to the load balancer; the development instance is removed. The address users see does not change.
- Security groups: the ALB allows port 80 only from the CloudFront VPC-origin group; the servers allow port 8000 only from the ALB.
- **Rehearse `alb` mode early** (a few hours in the M3b build window, about $0.10, then switch back) so the security groups, health checks and grace period are proven before the study week.
- Cost: about $0.55 a day for the load balancer (about $4 for the week); the web server and NAT instance only while started.

### 3c. On-demand operation
`start_work.sh` and `stop_work.sh` (from M2a, extended by M2b and M3a) control everything, in both web modes. The script reads `terraform output web_mode` to know whether to start the instance or set the web ASG.

| Command | Starts |
|---|---|
| `start_work.sh` | NAT instance; web server; reaper rule on (M3a). Waits until `https://<distribution>.cloudfront.net/healthz` returns 200, then prints "ready". |
| `start_work.sh --worker` | The above, plus one GPU worker held warm (M2b §2d). |
| `start_work.sh --study` | `--worker`, plus Aurora minimum capacity 0.5 ACU so nobody waits for the database to wake. Use it for demos and study sessions. |
| `stop_work.sh` | Reverses all of it: GPU workers to zero and the warm hold released; Aurora minimum back to 0; reaper run once, then off; web server stopped; NAT stopped. It then checks that no GPU, web or NAT instance is running and prints the Aurora minimum capacity. |

Terraform ignores changes to Aurora's `min_capacity` so the scripts and Terraform don't fight. The Aurora change is inside `stop_work.sh`, so it can't be forgotten separately.

**Before a demo or study session** (`docs/session_checklist.md`, one page):
1. About 15 minutes ahead: `start_work.sh --study` and wait for "ready".
2. Open the site, sign in, and check the balance loads.
3. For a new participant: after they first sign in, `grant_credit.py <cents>` (it asks for the email).
4. Afterwards: `stop_work.sh`, and check it reports everything stopped.

**Paused state.** When the web server is off, the page still loads from S3. Its first `/api/me` call then fails with something other than a JSON response from Flask (a CloudFront or load-balancer error, or a network error). The page shows: "NeuroLens is paused between sessions to save cost. The sample results below are still available. To try an upload, contact the NeuroLens team." It hides the sign-in button and keeps the sample browser working (samples load from `/data/samples.json`). This is distinct from the 503 `database_waking` JSON (§2), which means the server is up and the page should retry.

## 4. Job history, CSV and messages
### 4a. Endpoints
- `GET /api/jobs?limit=50`: the current user's jobs, newest first, at most `limit` (default 50; a value that is not an integer from 1 to 100 returns 400 `{"error": "bad_limit"}`): `[{"job_id", "filename", "status", "created_at", "verified_duration_ms", "captured_cents", "error_code", "result_available"}]`. `result_available` is true for a `done` job created less than 30 days ago (results expire after 30 days, M1 §3).
- `GET /api/jobs/<job_id>/result.csv`: the same ownership check as `/result` (404 for others, no S3 call first). Columns in this order: `t, engagement_overall, ffa_faces, eba_bodies, ppa_scenes, sts_social, auditory, auditory_with_audio, auditory_without_audio`, one row per timestep, `None` written as an empty cell, numbers exactly as stored in the result JSON. `Content-Disposition: attachment; filename="neurolens-<job_id>.csv"`.
- Not-available cases, shared by `/result` and `/result.csv`: another user's or unknown job → 404 `{"error": "not_found"}`; a `failed` job → 404 `{"error": "not_found"}` (M3a §6); a job still `queued`/`processing` without a result → 404 `{"error": "result_not_ready"}`; a `done` job whose result object has expired → 404 `{"error": "result_expired"}`.
- The note that `auditory` and the two `auditory_with/without_audio` columns are normalised over different windows (M0 §2) goes in the README's results section, not inside the CSV, so the file stays readable by any spreadsheet.

### 4b. Frontend
- **Page start-up:** the page calls `/api/me` once and shows one of four states:
  - **200:** the app.
  - **401:** the sign-in screen.
  - **503 `database_waking`:** "Starting up…", retrying (§2).
  - **Anything else:** the paused state (§3c).
- **Samples** load from `/data/samples.json` (served by Flask locally, by S3 on AWS) instead of `/api/samples`, so they work in every state. The `/api/samples` route stays but, like every `/api/*` route, requires sign-in in Google mode; M0's samples test runs in dev mode (M3a §6 defaults) and is unaffected.
- **Sign-in screen** (401): a "Sign in with Google" button linking to `/login`, with the sample browser still available below it. Uploads, the balance and history stay hidden until signed in. A "Sign out" control in the header.
- **Balance** in the existing header area (`.header-meta`), formatted from cents, refreshed after each job ends.
- **History** panel from `GET /api/jobs`: each row re-opens its result in the existing chart code (feeding it the stored JSON instead of a fresh one), links to the CSV, and says when results expire. The filename and `error_message` come from users, so they are inserted with `textContent`, never inside HTML template strings or `innerHTML` (the existing sample carousel's pattern), which would allow script injection.
- **Failure messages** by `error_code`, each ending "You have not been charged." where money was refunded:
  - `insufficient_credit` (402 at presign): "Not enough credit for this video. Ask the NeuroLens team for more test credit."
  - `insufficient_credit_for_actual_duration`: "Your video is longer than the length we estimated, and your credit doesn't cover the difference."
  - `duration_exceeds_max_estimated` (400 at presign) and `duration_exceeds_max_verified`: "This video is longer than the 120-second maximum."
  - `file_too_large`: "This file is larger than the upload limit."
  - `unreadable_video`: "This file is not a readable video."
  - `processing_failed`, `stalled`: "Processing failed after retrying." plus the job's `error_message` when present.
  - `upload_not_received`: "The upload didn't finish. Please try again."
  - `upload_missing`: "The uploaded file was no longer available (uploads are kept 2 days). Please upload it again."
  - `presign_failed`: "The upload couldn't be started. Please try again."

### 4c. Credit grants
`infra/grant_credit.py <cents>`: prompts for the email (so participants' emails never land in shell history), then adds credit to an existing user through the Data API, in one transaction with a `grant` ledger row (migration `002_grant.sql` adds `grant` to the ledger `kind` check; the kind is passed as a parameter, since project SQL has no string literals). `cents` must be positive. `users.email` is not unique, so if the email matches zero users or more than one, it stops with an error listing the matches. Used for the team and study participants; it runs from a laptop with the `neurolens` profile, never from the web app.

## 5. Interfaces fixed by this spec (the §9 tests are written against exactly these)
- `neurolens.web.auth`: `current_user() -> tuple[str, str] | None`; `login_required` decorator giving the 401 JSON above; `init_auth(app, cfg, ssm_client, *, server_metadata=None) -> None` registers the Google client and routes and sets the session secret. When `server_metadata` (a dict with `issuer`, `authorization_endpoint`, `token_endpoint`, `jwks_uri`) is given, Authlib uses it instead of downloading Google's OpenID configuration.
- **Sign-in test seam:** tests pass `server_metadata` pointing at fake URLs, and fake only the HTTP layer with the `responses` library: the token endpoint returns an ID token that the test signs with its own RSA key, and the JWKS URL returns that key. Authlib's real `state`, nonce and signature checks therefore run in the tests. Nothing in the test suite contacts Google. `dev.txt` gains `responses`.
- `neurolens.settings.get_parameter(ssm_client, name) -> str`: reads one `SecureString` with decryption.
- `neurolens.web.app.create_app(..., ssm_client=None)`: in Google mode, reads both parameters at startup (building `boto3.client("ssm", region_name=cfg aws.region)` when `ssm_client` is `None`) and fails fast if either is missing.
- `neurolens.billing.list_jobs(db, user_id, limit) -> list[dict]` and `grant_credit(db, email, cents) -> str` (returns the user ID; raises `ValueError` for `cents <= 0`, and `LookupError` for zero or several matching users).
- New config keys: `auth.google_client_id`, `auth.redirect_uri`, `public_base_url`.
- `requirements/web.txt` gains `authlib`, `requests` (Authlib's Flask client uses it) and `gunicorn`, and loses `flask-cors`.
- `neurolens.results.to_csv(result: dict) -> str` for the CSV body.

## 6. Experiment 3 (cloud vs on-premise cost)
An independent track: it can run any time after M2b.
- `experiments/tco_benchmark.py` times the full two-pass pipeline for 15 s, 30 s and 60 s clips, 10 runs each, and writes a CSV of wall-clock times.
- **On-premise leg:** on the school's GPU cluster via Slurm (`experiments/slurm/tco_benchmark.sbatch`, a GPU with 40 GB or more), falling back to a teammate's 2× RTX 4090 machine. Power cost is estimated from published GPU TDP figures, not measured. The cluster has no AWS credentials: copy the output to a laptop and upload it with the manifest.
- **Cloud leg:** reuse Experiment 1's per-stage timings where it has 10 runs per clip length; run only the missing ones on a Spot worker (about 1–2 hours of GPU at most; flag the estimate before running).
- Output under `experiments/experiment-3/<run_id>/` with a `manifest.json`, following M2b §10's contract. Cost comparisons are scenario estimates built from measured runtimes and stated assumptions (including the report's $0.10-per-video model and the ~1,475 videos/month breakeven), never presented as observed fully-loaded costs.

## 7. Stretch: Stripe test-mode credit demo
Attempt only after every other acceptance criterion in §10 passes. It demonstrates a payment flow **without real money** and must never be switched to live mode.
- Keys in Parameter Store: `/neurolens/web/stripe_secret_key` (must start with `sk_test_`; startup fails otherwise) and `/neurolens/web/stripe_webhook_secret`. Config: `stripe.enabled`, `stripe.allowed_user_ids` (the team's Google IDs only).
- `POST /api/test-credit/checkout` (signed in, allowlisted, JSON body `{"pack": "5" | "10"}`): creates a hosted Stripe Checkout Session with the server-defined amount in USD, card only, metadata `user_id` and `pack`, `success_url` and `cancel_url` built from config `public_base_url` (never from the request's host, which behind CloudFront is the server's internal name), and returns only its URL. The browser never sends a price, amount, user ID or session ID.
- `POST /api/stripe/webhook` (unauthenticated; CloudFront passes it uncached): verifies Stripe's signature over the **raw request body** before parsing. With `stripe.enabled` false, returns 200 and does nothing. Otherwise accepts only `checkout.session.completed` with `livemode = false`, an allowlisted user and a known pack. In one transaction: `INSERT` into `stripe_test_events` (migration `003_stripe.sql`: event ID primary key, session ID unique) with `ON CONFLICT DO NOTHING`; only if a row was inserted, add the pack's cents and a `test_topup` ledger row. A repeated event or session never credits twice. The success redirect never credits anything.
- Frontend: a control labelled "Add test credit — no real charge", shown only when `/api/me` returns `can_use_test_topups = true`.
- Before M4, set `stripe.enabled = false` and confirm the control and checkout endpoint are gone.

## 8. Operating handoff to M4
- Keep the Terraform resources (CloudFront, the site files, Aurora, and in the final week the load balancer) in place into M4. Everything that runs by the hour stays stopped between sessions and is started with §3c's commands.
- Study sessions and the presentation demo follow the §3c checklist.

## 9. Tests (tests-first; `FAKE_INFERENCE=1 pytest`)
- **Sign-in**, through the §5 seam (real Authlib checks, fake HTTP):
  - the callback clears any existing session, then stores only `user_id` and `email`, and calls `ensure_user`;
  - an unverified email gives 403; a wrong `state`, a token signed by the wrong key, or `?error=access_denied` gives 400 `sign_in_failed` and no session;
  - `/login` redirects with exactly `auth.redirect_uri`; logout clears the session;
  - a session cookie older than 12 hours gets 401.
- **Every `/api/*` route** returns 401 JSON when signed out (the test enumerates the app's URL map, so a new route can't be forgotten); each listed public route, including `/data/*`, stays public.
- **Cookie flags** in Google mode: `Secure`, `HttpOnly`, `SameSite=Lax`.
- **POST without JSON content type** returns 415; responses carry **no** CORS headers.
- **Startup:** Google mode with a missing parameter fails fast; dev mode on a non-local host, or with `NEUROLENS_DEPLOYED=1` set, raises `UnsafeConfigError`.
- **Database waking:** a stubbed Data API that keeps raising `DatabaseResumingException` makes `/api/me` return 503 `database_waking` within the 45 s budget (use a fake clock).
- `/healthz` returns 200 without calling the database (assert with a stub).
- **History:** only the user's own jobs, newest first; `limit` 1 and 100 accepted, 0, 101 and `abc` give 400 `bad_limit`; `result_available` false after 30 days.
- **CSV:** columns in order, one row per timestep, empty cells for `None`, values identical to the JSON; each not-available case in §4a returns its exact error, with no S3 call before the ownership check.
- `grant_credit` adds exactly the amount with a `grant` ledger row, keeping the M3a invariants; zero or negative cents, an unknown email, and an email shared by two users each raise.
- **Import hygiene:** the web app in Google mode never imports `neurolens.inference`, `torch` or `numpy`-dependent inference code.
- **Stretch (only if §7 is built):** live key rejected at startup; bad signature rejected; `livemode = true`, unknown pack and non-allowlisted user rejected; the same event or session twice credits once; disabled mode returns 200 without credit.

## 10. Acceptance criteria
1. Google accepts the CloudFront callback URL (day-one check).
2. A new user signs in with Google through `https://<distribution>.cloudfront.net`, lands with the starter credit, and every API route returns 401 when signed out.
3. `GET /api/jobs` and the CSV return only the signed-in user's own jobs; the CSV matches the JSON result exactly.
4. The web server has no public IP and accepts traffic only from CloudFront (or the load balancer); a direct request from elsewhere in the VPC is refused.
5. A freshly started web server never loads the model or the atlas (no such log lines) and is healthy within a minute or two.
6. In `alb` mode (rehearsed early, then again in the study week), stopping gunicorn makes the load balancer mark the server unhealthy, and the Auto Scaling group replaces it; a user signed in before the replacement is still signed in after it.
6a. After `stop_work.sh`, the public address shows the paused notice and the sample results, and no GPU, web or NAT instance is running; Aurora's minimum capacity is 0. `start_work.sh --study` brings the site back to signed-in use within 15 minutes, with a warm GPU.
6b. The first sign-in after Aurora has paused succeeds, or shows "Starting up…" and then succeeds, never a 504.
6c. The session checklist exists and was followed once end to end in a rehearsal.
7. `/healthz` checks every 15 s do not keep Aurora awake (it still pauses when idle, visible in CloudWatch).
8. The S3 bucket and the web app no longer allow arbitrary origins.
9. The full M3 diff passed `/security-review` with no unresolved high-severity findings.
10. Experiment 3 artifacts exist under `experiments/experiment-3/` with a manifest.
11. `FAKE_INFERENCE=1 pytest` passes locally and in CI, and the tests were committed before the implementation.
12. At the end of M3, CloudFront, the site files and Aurora remain in place for M4, and `stop_work.sh` has left nothing running.
13. **Stretch only:** a team member completes a Stripe test Checkout and receives exactly one matching credit from the verified webhook; replays never credit twice; with the feature disabled, a late webhook returns 200 and credits nothing.

## 11. File layout additions
```
neurolens/web/auth.py              MODIFIED: Google mode, login_required, init_auth
neurolens/web/app.py               MODIFIED: history, CSV, healthz, CORS removed, JSON-only POSTs, 503 while the database wakes
requirements/web.txt, dev.txt      MODIFIED: authlib, requests, gunicorn (flask-cors removed); responses
neurolens/results.py               NEW: to_csv
neurolens/billing.py               MODIFIED: list_jobs, grant_credit
neurolens/settings.py              MODIFIED: get_parameter
infra/migrations/002_grant.sql     NEW
infra/migrations/003_stripe.sql    NEW (stretch only)
infra/neurolens-web.service        NEW
infra/pull_web_code.sh             NEW
infra/deploy_web_code.sh           NEW
infra/grant_credit.py              NEW
infra/start_work.sh, stop_work.sh  MODIFIED: web server, --study, Aurora minimum capacity (§3c)
infra/terraform/                   MODIFIED: CloudFront (S3 site origin + VPC origin), web instance, ALB/ASG (alb mode), web IAM
docs/session_checklist.md          NEW
experiments/tco_benchmark.py       NEW
experiments/slurm/tco_benchmark.sbatch  NEW
static/                            MODIFIED: sign-in, balance, history, CSV, messages
tests/                             MODIFIED: §9
```

## 12. Cost
- CloudFront: free tier (1 TB out and 10 million requests a month).
- Web `t4g.micro` and NAT `t4g.micro`: about $0.017 an hour together, only while started.
- Load balancer: about $0.55 a day while it exists (it bills by the hour even when no server is behind it), from shortly before the study sessions until after the presentation (M4 §3).
- S3 site files: a few tens of MB of open-licensed sample clips, cents a month.
- Parameter Store standard parameters: free.
- Experiment 3 cloud leg: up to 1–2 GPU hours on Spot if Experiment 1 doesn't already cover it; flagged before running.

## 13. Explicitly not in this milestone
No live payments, subscriptions, saved cards or card data. No study consent, event logging or consolidation (M4). No scaling policy for the web tier beyond the single replaceable server.
