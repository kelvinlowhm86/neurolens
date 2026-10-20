# NeuroLens — M3c Implementation Spec: academic scope

**Builds on:** M1–M3b. NeuroLens becomes a free, non-commercial tool for students and researchers (the model's licence, CC BY-NC, rules out selling it). This milestone makes three changes:
- Credit becomes a free allowance that the operator grants.
- The Stripe test-mode payments are removed.
- Users can give consent for a video to be kept for future research, add its real audience-retention curve, and delete a video or their whole account.

**Full treatment** (tests first by a separate agent, break-it checks, Opus review): account deletion and per-video deletion (data loss, sign-in, credit), and the consent copy in the worker (it runs after a charge).

## Decisions (20 Oct)
- **Free allowance, granted only.** New accounts start at 0. `infra/grant_credit.py` adds credit for course members (M3b §7a; the amount is typed in dollars). This means a stranger who signs in with Google cannot spend GPU money.
- **Stripe removed from the service.** Before the removal, the last commit with Stripe (the custom GPU workers, after one real GPU job) is tagged `custom-gpu-stripe`. The report cites the tag as the working prototype of the future paid phase. No code is kept behind a switch.
- **Consent is opt-in, per upload.** The box is unticked by default, and analysis works the same either way. Only consented videos are kept beyond the normal 2 days.
- **Retention: 12 months**, enforced by an S3 lifecycle rule on `dataset/`, so nobody has to remember to delete anything. Teardown after the course deletes everything earlier.
- **Deletion:** per video, and the whole account. Both are refused while one of the user's jobs is queued or processing. That is the only state in which the worker or reaper may still write to the job.

## 1. Scope
### In scope
Migration 003; consent at upload; the dataset copy in the worker; the retention-curve upload; per-video and account deletion; Stripe removal (code, tests, Terraform, SSM parameters, page, privacy page, specs); the dataset lifecycle rule; the guide `docs/retention_curve_guide.md`.

### Out of scope
- Drawing the real curve next to the predicted one (a possible later addition).
- An export script: the `dataset/` prefix is the dataset.
- Training a model.
- The account-switch handover kit and the teardown script (their own steps in the plan).

## 2. Schema: `infra/migrations/003_academic.sql`
- `ALTER TABLE jobs ADD COLUMN research_consent BOOLEAN NOT NULL DEFAULT false;`
- `ALTER TABLE jobs ADD COLUMN deleted_at TIMESTAMPTZ;`
- `DROP TABLE stripe_test_events;`
- The ledger `kind` check keeps `test_topup`, because existing rows use it.

## 3. Consent and the dataset copy
### 3a. Upload
- `POST /api/uploads/presign` takes an optional `research_consent`, which must be JSON `true` or `false`. Missing means `false`; any other value → 400 `invalid_request`. It is stored on the job by `billing.reserve(..., research_consent)`.
- The page shows the unticked box under the upload area: "Keep this video and its analysis for up to 12 months to help build a future engagement model. I own the rights to this video. Optional: the analysis works either way." The box links to the privacy page.

### 3b. Worker
- **When:** after the analysis and before `put_result`, if the job has `research_consent`.
- **Claim check first:** the worker calls `set_stage(..., "saving_dataset")`. If the claim is lost (the reaper refunded a stalled job, or it settled elsewhere), it raises `_ClaimLost` and writes nothing. A late worker therefore cannot leave dataset files behind for a job that has been refunded and deleted. A window of a few seconds between this check and the writes remains; that is a known limitation.
- **Writes**, through one helper, `storage.put_dataset(s3, bucket, job_id, upload_key, result)`:
  - `copy_object` from the upload key to `dataset/<job_id>/video<ext>`, where `<ext>` is `PurePosixPath(upload_key).suffix`. A single copy works up to 5 GB and uploads are capped at 300 MB. No local file is needed.
  - The result to `dataset/<job_id>/result.json`, serialised with `allow_nan=False`.
  - Both writes may replace earlier copies, because a redelivered job writes the same content.
- **Failure:** if either write fails, the error goes down the worker's normal failure path (retry, then refund). A consented job is never marked done without its dataset copy.
- **Data:** `billing.job_state` returns `research_consent`.

### 3c. Retention curve
- Route: `POST /api/jobs/<job_id>/retention`. The body is JSON, `{"csv": "<the file's text>"}`, with the text at most 64 KiB (65,536 bytes); a longer text → 400 or 413. Sending JSON keeps the app's one rule, that every `/api/*` POST is JSON, which is its cross-site form protection (`api_rules`). The page reads the chosen file and sends its text.
- Conditions:
  - The job must be owned by the user and not deleted (otherwise 404, like `owned_job`).
  - The job must be `done` (otherwise 409 `job_not_done`).
  - The job must have consent (otherwise 409 `no_consent`).
- CSV rules: the header is exactly `elapsed_ratio,watch_ratio`. There are 2 to 1000 rows. `elapsed_ratio` is in 0–1 and strictly increasing. `watch_ratio` is ≥ 0 and finite (YouTube's ratio can exceed 1 when viewers rewatch). Any rule broken → 400 `invalid_retention_csv` with the line number.
- Stored at `dataset/<job_id>/retention.csv`. A new upload replaces the old one.
- The page shows "Add real audience retention (CSV)" on a finished consented job, with a link to the guide. To know this, the job status response includes `research_consent` once the job is `done` (it stays hidden before).
- The parser is a pure function, `neurolens.results.parse_retention_csv(text) -> list[tuple[float, float]]`, which raises `ValueError` with the line number.
- **Guide** (`docs/retention_curve_guide.md`, also served at `/static/retention_guide.html`): how the video's owner gets the curve from the YouTube Analytics API. Use the API page's in-browser "Try this API" panel: dimension `elapsedVideoTimeRatio`, metric `audienceWatchRatio`, filter `video==<id>`, then save the two columns. Claude tries the steps on a real channel before the guide is written. If the in-browser route does not work, Josh decides between a small helper script and dropping the guide.

## 4. Deleting a video: `DELETE /api/jobs/<job_id>`
1. The job must be owned and not deleted (otherwise 404).
   - If it is `queued` or `processing` → 409 `job_active`: "This video is still being processed. If you closed the page before the upload finished, it is released within about 4 hours; try again then."
   - Known limitation: a job whose upload never arrived stays `queued` until the reaper refunds it (60 minutes, with the reaper running every 3 hours).
2. Delete from S3 with one `delete_objects` call: the upload key, `results/<job_id>.json`, and the three `dataset/<job_id>/` files (`video<ext>`, `result.json`, `retention.csv`). Missing keys are fine. Any reported error → 500 with nothing marked, so the user can try again.
3. `billing.delete_job(db, user_id, job_id)` sets `deleted_at = now()` and `filename = NULL`.
   - No status re-check is needed: `done` and `failed` are final, and `claim`, `settle_success` and `issue_refund` all refuse final jobs.
   - The job row and its ledger rows stay, so the credit history still adds up.
4. **Hiding deleted jobs:**
   - `owned_job`'s query is the one place every job route goes through. It gains `AND deleted_at IS NULL` and also returns `object_key`, so all routes answer 404 for a deleted job.
   - `list_jobs` skips deleted jobs.
   - `job_state` (used by the worker and reaper) deliberately does not filter them.

S3 is deleted first, so a failure part-way leaves a job that still shows but whose files are already gone. Repeating the request finishes the deletion. The opposite order would leave files with no job pointing at them, which nobody can find to delete.

## 5. Deleting the account: `POST /api/account/delete`
- **Who:** Cognito mode only (404 in dev mode). The body must be `{"confirm": "DELETE"}`; anything else → 400 `confirmation_required`. The page asks the user to type DELETE.
- **Refused** while any of the user's jobs is `queued` or `processing` → 409 `job_active`, with the same message as §4.
- **Steps:**
  1. List the user's jobs. If none is active, delete all their S3 files as in §4 step 2, using `delete_objects` batches of up to 1000 keys.
  2. In one transaction, `billing.delete_account(db, user_id)`:
     - lock the `balances` row (`FOR UPDATE`, the same lock `reserve` takes, so no new job can start in between);
     - refuse with `JobActive` if any job is active. A job created after step 1 is necessarily `queued`, so it is refused too, and no job's files are left behind;
     - delete the user's `refunds`, `ledger` and `jobs` rows, then `balances`, then `identities` (`DELETE … RETURNING issuer, subject`), then `users`.

     Credit is free, so no financial record needs to be kept.

     Lock order: billing.py locks jobs before balances. This function locks only `balances` and only reads job statuses before deleting, so it cannot deadlock with them; the code comment says so.
  3. Delete the Cognito accounts. For each returned identity whose issuer is our Cognito pool, call `ListUsers` with filter `sub = "<subject>"`, then `AdminDeleteUser` with the returned username. No match counts as already deleted. One NeuroLens user can have both a Google and an email login.
  4. Clear the session and answer 200. The page then shows "Your account and data have been deleted."
- **Failures:**
  - Steps 1 and 2 behave like §4: a failure leaves the account in place, and repeating the request finishes the deletion.
  - If step 3 fails, the user's data is already gone. The route logs a line starting `COGNITO_DELETE_FAILED` with the Cognito `sub` (and the username if found), then answers 500 `login_delete_failed`: "Your data has been deleted. Removing your login failed; we have been notified."
  - A log metric filter on that marker feeds the alarm `neurolens-cognito-delete-failed`, which emails the operator, who deletes the login by hand. The existing Lambda error alarms would not fire, because the function itself does not fail.
  - Signing in again later creates a new, empty account.
- **Other browsers** still hold a valid session cookie for up to 12 hours. Any route that finds no `users` row for the signed-in user answers 401 `not_signed_in` and clears the session, instead of a server error.

## 6. Stripe removal
- Tag first: `git tag custom-gpu-stripe <last commit with Stripe>`, then push the tag.
- Delete:
  - `neurolens/web/stripe_webhook.py`, `billing.credit_test_topup`, `/api/top-up/checkout`, `STRIPE_PARAMETERS`, `allowlist_emails`, `can_top_up` (it leaves `/api/me`), and the top-up panel and its script in `static/index.html`;
  - the `stripe` block in `config.json`, `stripe` in `requirements/web.txt`, and `tests/test_m3b_stripe.py`;
  - the Stripe parts of the other tests (named in §8).
- Terraform: delete the webhook function, its URL, role and alarm entry, the `stripe_webhook_url` output, and the webhook loop entry in `deploy_web.sh`.
- Josh deletes by hand: the SSM parameters `/neurolens/web/stripe_secret_key`, `stripe_webhook_secret` and `topup_allowlist` (created by hand, not by Terraform), and the event destination in the Stripe test dashboard.
- `static/privacy.html`: remove Stripe, and add consent, the dataset, 12-month retention and the delete options. It says uploads that are not kept are deleted within 3 days: an upload that arrives after its job was refunded is deleted only by the 2-day rule.

## 7. Permissions and storage (Terraform)
- **Lifecycle rule `expire-dataset`:** prefix `dataset/`, 365 days. Each file expires 12 months after it was stored.
- **Worker role:** `s3:PutObject` on `dataset/*`. The copy is done as a put, which needs no new read permission.
- **Web role:**
  - `s3:DeleteObject` on `uploads/*`, `results/*` and `dataset/*`;
  - `s3:PutObject` on `dataset/*/retention.csv`;
  - `cognito-idp:ListUsers` and `cognito-idp:AdminDeleteUser` on our pool only.
- **Role boundary:** `infra/iam/neurolens-role-boundary.json` gains a statement allowing those two actions on `userpool/*` in us-east-1. The boundary caps every role, so without this the web role's grant would be denied. Josh applies the boundary change (a paste, like earlier IAM policy edits).
- **Alarm:** a CloudWatch log metric filter on `COGNITO_DELETE_FAILED` in the web function's log group, plus the alarm `neurolens-cognito-delete-failed` (count at least 1 in 5 minutes, emails the existing alarm topic).
- **Worker:** the worker role already has `s3:GetObject` on `uploads/*`, which `copy_object` needs as its source.

## 8. Interfaces and tests
- **New interfaces:**
  - `billing.reserve(db, user_id, job_id, object_key, filename, client_duration_ms, research_consent=False)`;
  - `billing.delete_job(db, user_id, job_id) -> None`;
  - `storage.put_dataset(s3, bucket, job_id, upload_key, result)`;
  - `billing.delete_account(db, user_id) -> list[tuple[str, str]]` (the deleted `(issuer, subject)` identities; raises `JobActive`);
  - `billing.list_jobs` skips deleted jobs;
  - `results.parse_retention_csv`.
- **Tests** (`tests/test_m3c_*.py`, written first; PostgreSQL for the billing ones):
  - consent stored, defaulting to false, with a non-boolean refused;
  - the worker copies only consented jobs; a failed dataset write fails the job without settling it; the dataset write happens before `put_result`; a worker that lost its claim writes nothing to `dataset/`;
  - CSV rules, each rule broken once; a retention POST with a JSON body reaches the handler;
  - retention refused for jobs not done, without consent, or not owned;
  - per-video delete: the S3 keys are gone, the job is hidden from every route, its ledger is intact, it is refused while active, repeating it is safe, and a failed S3 delete leaves `deleted_at` empty;
  - account delete:
    - every row is gone, and the ledger invariant holds for other users;
    - it is refused while active;
    - a `reserve` racing the delete cannot leave a job behind (the lock);
    - Cognito is called once per Cognito identity;
    - `ListUsers` finding nothing counts as done;
    - a Cognito failure answers 500 `login_delete_failed` and logs the `COGNITO_DELETE_FAILED` line with the sub;
    - a stale session afterwards gets 401, not 500.
- **Existing tests this spec retires or changes:** `tests/test_m3b_stripe.py` (deleted); `tests/test_m3a_billing.py::test_job_state_reports_status_updated_at_and_attempt` (its key set gains `research_consent`, Josh approved 20 Oct); tests that expect `can_top_up` in `/api/me` (the key is removed, which also changes `me()`); and the Stripe settings in the Cognito-mode fixtures in `tests/conftest.py`. Any other existing test that fails is reported to Josh, not edited.

## 9. Acceptance criteria
1. Full suite passes with PostgreSQL. `ruff` is clean. `git grep -i stripe` finds only specs, git history notes and the ledger kind `test_topup`.
2. Live: a consented fake job leaves `dataset/<job_id>/video…` and `result.json`; an unconsented one leaves nothing under `dataset/`.
3. Live: a valid YouTube-format CSV is stored; a broken one is refused with its line number.
4. Live: per-video delete removes every file, and the job leaves the history.
5. Live: deleting a test account removes its rows and its Cognito login, and signing in again gives a new empty account.
6. `terraform plan` shows the webhook removed, the new lifecycle rule, the new permissions and the Cognito-delete alarm, and nothing else. The role boundary has the Cognito statement.
7. The Opus review of the deletion and consent slice finds no HIGH.
