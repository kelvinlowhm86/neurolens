# NeuroLens — M3c Implementation Spec: academic scope

**Builds on:** M1–M3b. NeuroLens becomes a free, non-commercial tool for students and researchers (the model's licence, CC BY-NC, rules out selling it). This milestone makes three changes:
- Credit becomes a free allowance that the operator grants.
- The Stripe test-mode payments are removed.
- Users can give consent for a video to be kept for future research, add its real audience-retention curve, and delete a video or their whole account.

**Full treatment** (tests first by a separate agent, break-it checks, Opus review): account deletion and per-video deletion (data loss, sign-in, credit), and the consent copy in the worker (it runs after a charge).

## Decisions (20 Oct)
- **Free allowance, granted only.** New accounts start at 0. `infra/grant_credit.py` adds credit for course members (M3b §7a; the amount is typed in dollars). This means a stranger who signs in with Google cannot spend GPU money.
- **Stripe removed from the service.** Before the removal, the last commit with Stripe (the custom GPU workers, after one real GPU job) is tagged `custom-gpu-stripe`. The report cites the tag as the working prototype of the future paid phase. No code is kept behind a switch.
- **Consent is opt-in, per upload.** The box is unticked by default, and analysis works the same either way. Only consented videos are kept beyond the normal 30 days.
- **Retention: up to 3 years (1095 days)** for a shared video and its course-study output, enforced by S3 lifecycle rules on `dataset/` and `academic/`; a shared video with no retention curve is purged 90 days after upload. Other uploads and results expire after 30 days. Teardown after the course deletes everything earlier.
- **Deletion:** per video, and the whole account. Both are refused while one of the user's jobs is queued or processing. A waiting job can be cancelled first (M3d §7). That is the only state in which the worker or reaper may still write to the job.

## 1. Scope
### In scope
Migration 003; consent at upload; the research copy in the worker (the video under `dataset/`, our output under `academic/`); the retention-curve upload; per-video and account deletion; Stripe removal (code, tests, Terraform, SSM parameters, page, privacy page, specs); the research lifecycle rules; the guide `static/retention_guide.html`.

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

## 3. Consent and the research copy
### 3a. Upload
- `POST /api/uploads/presign` takes an optional `research_consent`, which must be JSON `true` or `false`. Missing means `false`; any other value → 400 `invalid_request`. A true consent must carry the current `consent_version` (M3f §5a), else 409 `consent_outdated` and nothing is reserved. It is stored on the job by `billing.reserve(..., research_consent)`.
- The page shows one consent box per batch in the upload dialog (M3f §2a), unticked and never required: "Share for research and to train NeuroLens's own models ([details](/static/privacy.html#research)). I own the rights to it." The box links to the privacy page; that section gives the model-training purpose and disclosures.

### 3b. Worker
- **When:** after the analysis and before `put_result`, if the job has `research_consent`.
- **Claim check first:** the worker calls `set_stage(..., "saving_dataset")`. If the claim is lost (the reaper refunded a stalled job, or it settled elsewhere), it raises `_ClaimLost` and writes nothing. A late worker therefore cannot leave research files behind for a job that has been refunded and deleted. A window of a few seconds between this check and the writes remains; that is a known limitation.
- **Result check:** before `put_result`, every job calls `set_stage(..., "saving_result")`; a job that lost its claim writes no result (a consented job checks twice).
- **Writes**, through one helper, `storage.put_dataset(s3, bucket, job_id, upload_key, result, brain)`:
  - `copy_object` from the upload key to `dataset/<job_id>/video<ext>`, where `<ext>` is `PurePosixPath(upload_key).suffix`. A single copy works up to 5 GB and uploads are capped at 300 MB. No local file is needed.
  - The result to `academic/<job_id>/result.json`, serialised with `allow_nan=False`, and the brain file to `academic/<job_id>/brain`; our output never goes under `dataset/`, which is the only place training reads (M3f §5b).
  - The video's SHA-256 is recorded with `billing.record_video_hash` before the copy.
  - All writes may replace earlier copies, because a redelivered job writes the same content.
- **Failure:** a research-copy write failure enters the worker's pre-publication failure path: refund only while it still owns the claim, then delete the research prefixes and the job's own files if the refund succeeded. Failed deletes are logged for retry by the reaper. A consented job is never marked done without its research copy.
- **Data:** `billing.job_state` returns `research_consent`.

### 3c. Retention curve
- Source: the video owner's audience-retention report from the YouTube Analytics API (`reports.query`, docs updated 15 Oct 2026), saved as a file exactly as the API returns it. The API answers only in JSON, so the upload takes that JSON as is: nobody has to convert it.
- Route: `POST /api/jobs/<job_id>/retention`. The JSON body contains `report` (the file's text), the required `views` and `published` choices, and boolean `promoted` (M3f §5d). The report text is at most 64 KiB (65,536 bytes); a longer text → 413. Sending JSON keeps the app's one rule, that every `/api/*` POST is JSON, which is its cross-site form protection (`api_rules`). The page reads the chosen file and sends its text with those choices.
- Conditions:
  - The job must be owned by the user and not deleted (otherwise 404, like `owned_job`).
  - The job must be `done` (otherwise 409 `job_not_done`).
  - The job must have consent (otherwise 409 `no_consent`).
  - The research copy must not have been removed (otherwise 409 `dataset_removed`, M3f §5c).
- Report rules: the text is a JSON object whose `columnHeaders` list includes columns named `elapsedVideoTimeRatio` and `audienceWatchRatio` (other columns, such as `relativeRetentionPerformance`, are allowed and ignored; columns are found by name, not position). `rows` holds 2 to 1000 rows (a missing `rows` means YouTube had no data for the video). `elapsedVideoTimeRatio` is in 0–1 and strictly increasing. `audienceWatchRatio` is ≥ 0 and finite (it can exceed 1 when viewers rewatch). Any rule broken → 400 `invalid_retention_report` with the row number (1-based) where a row is at fault.
- Stored as a new snapshot under `dataset/<job_id>/retention/<snapshot_id>.json` (the file as uploaded) and `dataset/<job_id>/retention/<snapshot_id>.csv` (two columns, header `elapsed_ratio,watch_ratio`). A new upload adds a snapshot rather than replacing the old one; an identical curve (same hash) is refused as a duplicate (M3f §5d).
- The page shows a quiet "Add audience retention" link on a done, shared job's History row and results page until its first snapshot, and always in the "⋯" menu, with a link to the guide (M3f §5d). To know this, `list_jobs` returns `sharing` and `curve_count`, and the job status response includes `research_consent` once the job is `done` (it stays hidden before).
- The parser is a pure function, `neurolens.results.parse_retention_report(text) -> list[tuple[float, float]]`, which raises `ValueError` with the row number.
- **Guide** (`static/retention_guide.html`, served at `/static/retention_guide.html`): how the video's owner gets the report in Google's APIs Explorer for `youtubeAnalytics.reports.query` (`ids=channel==MINE`, `startDate`/`endDate`, `metrics=audienceWatchRatio,relativeRetentionPerformance`, `dimensions=elapsedVideoTimeRatio`, `filters=video==<id>`), then saves the response as a `.json` file. Written from the API docs (it states their date), not tried on a real channel: none was available (Josh, 21 Oct).

## 4. Deleting a video: `DELETE /api/jobs/<job_id>`
1. The job must be owned and not deleted (otherwise 404).
   - If it is `queued` or `processing` → 409 `job_active`: "This video is still waiting or being processed. Try again when it finishes."
   - A job whose upload never arrived stays `queued` until the user cancels it or the reaper refunds it after the 4-hour deadline (M3a §7).
2. Mark it deleted first: `billing.delete_job(db, user_id, job_id)` sets `deleted_at = now()`, `filename = NULL` and clears `dataset_purged_at`, so it is hidden from every route at once.
   - No status re-check is needed: `done` and `failed` are final, and `claim`, `settle_success` and `issue_refund` all refuse final jobs.
   - The job row and its ledger rows stay, so the credit history still adds up.
3. Purge the files with `billing.purge_job_files`: every object under `dataset/<job_id>/` and `academic/<job_id>/` (the video, retention snapshots, result and brain), the job's own keys (the upload, `results/<job_id>.json`, `.brain` and `.jpg`), then its `retention_snapshots` rows. Missing keys are fine. A failed purge is logged; the job stays hidden and the reaper retries it (M3f §5c).
4. **Hiding deleted jobs:**
   - `owned_job`'s query is the one place every job route goes through. It gains `AND deleted_at IS NULL` and also returns `object_key`, so all routes answer 404 for a deleted job.
   - `list_jobs` skips deleted jobs.
   - `job_state` (used by the worker and reaper) deliberately does not filter them.

Marking first means no curve can be added while the purge runs. A failed purge leaves the job hidden and its pending cleanup recorded, so the reaper can retry it (M3f §5c).

## 5. Deleting the account: `POST /api/account/delete`
- **Who:** Cognito mode only (404 in dev mode). The body must be `{"confirm": "DELETE"}`; anything else → 400 `confirmation_required`. The page asks the user to type DELETE.
- **Refused** while any of the user's jobs is `queued` or `processing` → 409 `job_active`, with the same message as §4.
- **Steps (mark first, then purge, as for a job: M3f §5c):**
  1. **Mark**, in one transaction, `billing.delete_account(db, user_id)`:
     - lock the `balances` row (`FOR UPDATE`, the same lock `reserve` takes), then the `users` row
       (sign-in locks the user row it finds and never finds a marked account, so a racing sign-in
       either links before the mark, and loses that link, or makes a new account after it);
     - refuse with `JobActive` if any job is queued or processing;
     - mark every job deleted (`deleted_at = now()`, `filename = NULL`, `dataset_purged_at = NULL`);
     - delete the user's `sessions` and `identities` (`RETURNING issuer, subject`), so every other tab
       is signed out at once and signing in again creates a new, empty account;
     - set `users.deleted_at = now()` and replace the email with `deleted:<user_id>` (migration 007),
       so the address is gone at once and is free for a new account.
     After this nothing can write to the account: `reserve` refuses a user with `deleted_at` under the
     same lock, a curve needs a job that is not deleted, and no worker holds a job.
  2. Delete the Cognito accounts. For each returned identity whose issuer is our Cognito pool, call `ListUsers` with filter `sub = "<subject>"`, then `AdminDeleteUser` with the returned username. No match counts as already deleted. One NeuroLens user can have both a Google and an email login.
  3. **Purge** each job with `billing.purge_job_files` (both research prefixes and the job's own
     files). A failed purge is logged and left to the reaper.
  4. **Remove the rows**, `billing.remove_deleted_account(db, user_id)`: only when the user has
     `deleted_at` and every job has `dataset_purged_at`, delete the ledger, jobs (snapshots cascade),
     balance and user rows, else change nothing. Credit is free, so no financial record needs to be kept.
  5. Clear the session and answer 200. The page then shows "Your account and data have been deleted."
- **Failures:**
  - A failure in step 1 changes nothing; repeating the request is safe.
  - After step 1 the user sees the account as deleted whatever happens next. The reaper retries every
    marked job's purge (M3f §5c) and then calls `remove_deleted_account` for every user with
    `deleted_at`, so the deletion always finishes.
  - If step 2 fails, the route logs a line starting `COGNITO_DELETE_FAILED` with the Cognito `sub` (and the username if found), then answers 500 `login_delete_failed`: "Your data has been deleted. Removing your login failed; we have been notified."
  - A log metric filter on that marker feeds the alarm `neurolens-cognito-delete-failed`, which emails the operator, who deletes the login by hand. The existing Lambda error alarms would not fire, because the function itself does not fail.
- **Other browsers** may still hold an old cookie, but account deletion removes the server-side sessions immediately. Their next authenticated request is refused; the normal session lifetime is 30 days of inactivity (M3f §6b), not continued access after deletion.

## 6. Stripe removal
- Tag first: `git tag custom-gpu-stripe <last commit with Stripe>`, then push the tag.
- Delete:
  - `neurolens/web/stripe_webhook.py`, `billing.credit_test_topup`, `/api/top-up/checkout`, `STRIPE_PARAMETERS`, `allowlist_emails`, `can_top_up` (it leaves `/api/me`), and the top-up panel and its script in `static/app.html`;
  - the `stripe` block in `config.json`, `stripe` in `requirements/web.txt`, and `tests/test_m3b_stripe.py`;
  - the Stripe parts of the other tests (named in §8).
- Terraform: delete the webhook function, its URL, role and alarm entry, the `stripe_webhook_url` output, and the webhook loop entry in `deploy_web.sh`.
- Josh deletes by hand: the SSM parameters `/neurolens/web/stripe_secret_key`, `stripe_webhook_secret` and `topup_allowlist` (created by hand, not by Terraform), and the event destination in the Stripe test dashboard.
- `static/privacy.html`: remove Stripe, and add consent, the dataset, research retention (up to 3 years, or 90 days without a curve) and the delete options. It says uploads that are not kept for research are deleted after 30 days, and at once when the job is cancelled or refunded.

## 7. Permissions and storage (Terraform)
- **Lifecycle rules:** `dataset/` and `academic/`, 1095 days each. Each file expires 3 years after it was stored; the reaper's 90-day no-curve purge is separate (M3f §5c).
- **Worker role:** `s3:PutObject` on `dataset/*` and `academic/*`. The copy is a `copy_object`, which reads the upload with the `s3:GetObject` the worker already has (last bullet).
- **Web role:**
  - `s3:DeleteObject` on `uploads/*`, `results/*`, `dataset/*` and `academic/*`;
  - `s3:PutObject` on `dataset/*/retention/*` (the raw report and parsed CSV of each snapshot);
  - `cognito-idp:ListUsers` and `cognito-idp:AdminDeleteUser` on our pool only.
- **Role boundary:** `infra/iam/neurolens-role-boundary.json` gains a statement allowing those two actions on our user pool in us-east-1. The boundary caps every role, so without this the web role's grant would be denied. Josh applies the boundary change (a paste, like earlier IAM policy edits).
- **Alarm:** a CloudWatch log metric filter on `COGNITO_DELETE_FAILED` in the web function's log group, plus the alarm `neurolens-cognito-delete-failed` (count at least 1 in 5 minutes, emails the existing alarm topic).
- **Worker:** the worker role already has `s3:GetObject` on `uploads/*`, which `copy_object` needs as its source.

## 8. Interfaces and tests
- **New interfaces:**
  - `billing.reserve(db, user_id, job_id, object_key, filename, client_duration_ms, research_consent=False)`;
  - `billing.delete_job(db, user_id, job_id) -> bool`;
  - `storage.put_dataset(s3, bucket, job_id, upload_key, result, brain)`;
  - `billing.delete_account(db, user_id) -> list[tuple[str, str]]` (marks the account; the deleted `(issuer, subject)` identities; raises `JobActive`), `billing.remove_deleted_account(db, user_id) -> bool`;
  - `billing.list_jobs` skips deleted jobs;
  - `results.parse_retention_report`.
- **Tests** (`tests/test_m3c_*.py`, written first; PostgreSQL for the billing ones):
  - consent stored, defaulting to false, with a non-boolean refused;
  - the worker copies only consented jobs; a failed research write fails the job without settling it; the research write happens before `put_result`; a worker that lost its claim writes nothing under `dataset/` or `academic/`;
  - report rules, each rule broken once; each save writes a raw JSON snapshot and its two-column CSV under `dataset/<job_id>/retention/`; a retention POST with a JSON body reaches the handler;
  - retention refused for jobs not done, without consent, or not owned;
  - per-video delete: the S3 keys are gone, the job is hidden from every route, its ledger is intact, it is refused while active, repeating it is safe, and a failed purge still leaves the job marked deleted for the reaper to finish;
  - account delete:
    - after the route every row is gone, and the ledger invariant holds for other users;
    - it is refused while active;
    - a `reserve` after the mark is refused, and one before it makes the delete answer 409 (the lock);
    - a failed purge still answers 200; the rows stay until the reaper's purge succeeds, then go;
    - signing in again with the same Google login or email gives a new, empty account;
    - Cognito is called once per Cognito identity;
    - `ListUsers` finding nothing counts as done;
    - a Cognito failure answers 500 `login_delete_failed` and logs the `COGNITO_DELETE_FAILED` line with the sub;
    - a stale session afterwards gets 401, not 500.
- **Existing tests this spec retires or changes:** `tests/test_m3b_stripe.py` (deleted); `tests/test_m3a_billing.py::test_job_state_reports_status_updated_at_and_attempt` (its key set gains `research_consent`, Josh approved 20 Oct); tests that expect `can_top_up` in `/api/me` (the key is removed, which also changes `me()`); and the Stripe settings in the Cognito-mode fixtures in `tests/conftest.py`. Any other existing test that fails is reported to Josh, not edited.

## 9. Acceptance criteria
1. Full suite passes with PostgreSQL. `ruff` is clean. `git grep -i stripe` finds only specs, git history notes and the ledger kind `test_topup`.
2. Live: a consented fake job leaves `dataset/<job_id>/video…` and `academic/<job_id>/result.json` and `brain`; an unconsented one leaves nothing under `dataset/` or `academic/`.
3. Live: a valid YouTube Analytics report is stored as a raw JSON snapshot and its two-column CSV under `dataset/<job_id>/retention/`; a broken one is refused with its row number.
4. Live: per-video delete removes every file, and the job leaves the history.
5. Live: deleting a test account removes its rows and its Cognito login, and signing in again gives a new empty account.
6. `terraform plan` shows the webhook removed, the new lifecycle rules, the new permissions and the Cognito-delete alarm, and nothing else. The role boundary has the Cognito statement.
7. The Opus review of the deletion and consent slice finds no HIGH.
