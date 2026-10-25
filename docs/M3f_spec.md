# NeuroLens — M3f Implementation Spec: batch upload, research sharing, sign-in sessions

Status: current design. Builds on M3d (page) and M3e (SageMaker). Where this spec and an older one
disagree, this one wins, and the older section is updated to match.

## Decisions (25 Oct, Josh)
- Built as a real public product: no simplification for a small audience or for less work.
- Batch upload of up to 5 files in one dialog. Each file stays its own job, with its own price,
  15-s cancel window and refund.
- Research sharing asks for consent to train NeuroLens's own models, keeps shared videos 3 years, and
  rewards a valid audience-retention curve with that video's analysis for free.
- Starting the GPU from the dispatcher is measured first, not built (§6).
- Sign-in lasts 30 days from the last use, recorded on the server so that signing out ends it (§6b).
- `python app.py` on a laptop runs only against the local fake stack.

## 1. Scope
### In scope
Sections 2–8 below, their tests (§10), one real run (§11) and the docs (§8).
### Out of scope
Connect YouTube or any platform API (YouTube API policy forbids derived data and models), TikTok,
Instagram, Meta Ads, reminders, email, browser notifications, tutorial mode, an agency bulk route, a
place in line among other users, waking the database from the landing page.

## 2. Batch upload
### 2a. Dialog
- Choosing or dropping files (the upload box, or "Upload" in the top bar) opens one dialog over
  whatever is on screen. On touch screens the box reads "Choose a video"; elsewhere "Drop videos or
  click to choose". The file input accepts several files.
- **Sign-in first:** picking files calls `/api/me`. A 401 shows "You've been signed out." with a
  "Sign in" button (to `/login`) in the dialog, before any file is checked.
- One row per file: a frame from the file (taken locally), the name, its length and its price, and
  a × that removes it. A file that fails the local checks (type, length limit, unreadable within
  10 s) stays listed with its reason and no price, and is left out of the total. Files beyond
  `max_batch_files` (`config.json` `upload.max_batch_files`, 5; served by `/api/limits`) are listed
  as "5 at a time" and left out.
- **Server limit:** presign refuses with 429 `too_many_waiting` ("Too many videos waiting. Try again
  when some finish.") when the user already has `upload.max_waiting_jobs` (10, `config.json`) jobs
  queued. The page shows it on the row like any presign refusal.
- Below the rows: one total, "$3.60 · charged only for videos that finish · cancel within 15 s", the
  consent box (§5a, one for the whole batch), and Upload.
- If the total is more than the available credit: "Needs $4.50; you have $3.70" and Upload stays
  disabled until rows are removed. With no valid row Upload is disabled.
- When the consent box is ticked, each valid row shows a quiet "Add audience retention" link (the
  video is already on YouTube). It opens the curve form (§5d) for that row; the curve is held by the
  page and sent when that job is done (§5d). Once a curve is added the link reads "Audience retention added".
- Upload closes the dialog and any open result (the player stops, the brain stops drawing) and shows
  History. The upload box is free at once: a new dialog can add more files, which queue behind.

### 2b. Uploading
- The page keeps one upload queue per tab and uploads one file at a time. For each file in turn it
  calls `POST /api/uploads/presign` (unchanged: it reserves that file's price and creates its job),
  then uploads to S3. So nothing is reserved for a file that has not started uploading, and a
  closed tab leaves no held credit for files that never started.
- A presign refusal affects only its row: "Not enough credit", "Processing is paused" (then every
  file still waiting in the queue gets the same reason and nothing more is sent), or "Signed out"
  (likewise for the rest of the queue, with a "Sign in" link). Nothing is charged for such a row.
- History rows for the batch appear at once, newest first, in the dialog's order:
  - waiting to upload: "Waiting to upload", with × (removes it from the queue; no job exists yet;
    if its presign is already in flight, the job it creates is cancelled at once and nothing uploads);
  - uploading: a progress bar, with × (aborts the upload, then cancels the reserved job, as now);
  - uploaded: "Cancel 15", counting down each second in place of the "⋯" menu, then the "⋯" menu
    returns. A 409 on click (already dispatched) removes the button with no error.
- The panel's old countdown button, the "cancel within 15 s" sentence under it and the top bar's
  "Ready to upload: <name>" text are removed.
- **Leave warning:** the browser's "leave site?" prompt shows only while a file in this tab is
  uploading or waiting to upload.
- History keeps rows keyed by job id and local queue key; the upload dialog keeps rows keyed by
  file key. Refreshes update changed contents in place, preserve unchanged buttons, remove gone rows
  and retain the list's order.

### 2c. Credit notes
- The "no credit" note shows whenever the available credit is below the price of the shortest
  allowed video, not only at $0.00.
- Delete account names the credit that will be lost: "…every video, analysis and login, and your
  $4.60 credit." (no credit part at $0.00).

## 3. Waiting, progress and the tab title
- **Migration 006** adds `jobs.claimed_at TIMESTAMPTZ`; `billing.claim` sets it to `now()` on every
  successful claim. `list_jobs` returns it (UTC text) and `cancellable` as now.
- History status texts:
  - the user's oldest unfinished job, queued: "Waiting for the GPU · 3 min" (minutes since
    `created_at`, updated each poll), and from 15 min "Waiting for the GPU · 17 min · slower than
    usual";
  - any other queued job of the same user: "In line", with no clock;
  - processing: "Analysing · 2 min" (since `claimed_at`).
  This replaces "up to 10 min" and the slow-queue sentence.
- Waiting rows show the frame taken from the local file while this tab has it; otherwise the grey
  square, as now.
- **Tab title** (jobs uploaded from this tab since the title was last plain): "Waiting · NeuroLens"
  while none has finished, "2 of 5 done · NeuroLens" while some have, and "✓ 5 done · NeuroLens"
  when all have ("✓ 4 done · 1 failed · NeuroLens" if any failed; a cancelled job leaves the count).
  Once all are finished, the plain "NeuroLens" returns on the next visit to the tab (`visibilitychange`
  to visible) or the next click or key press in it. The long default title goes.
  Starting a new batch acknowledges an earlier completion before counting the new files; its
  click and later visits cannot clear the new batch's waiting title.

## 4. The rest of the page (UX review of 25 Oct)
1. **Phone top bar** fits in 390 px with no sideways scroll: below 600 px the credit shows as the
   amount only, the email moves behind a small account button (the same menu), and the button reads
   "Upload".
2. **Landing hero:** the video keeps its own 16:9 height and the brain panel takes the rest of the
   column, so nothing covers the video or its sound button. Check at 1280×720 and 1440×900.
3. **Sound share at 0:** the line reads "Responds as strongly with the sound off". In the compare
   table the cell shows "—" with that text on hover. Values unchanged. While comparing, the line is
   hidden (the table row has it).
4. **Results opened from History** get moment pictures while the stored video exists (30 days): the
   player's video element uses `crossOrigin="anonymous"` (the bucket's CORS already allows GET) and
   the moments are rebuilt once the video link arrives. The title row shows the video name only
   ("analysed in" and "no local video" go).
5. **History tidy-up:** failure texts are short and end "· not charged": "Not a readable video",
   "Didn't finish in time", "Failed", "Upload didn't finish", "Video too long", "Not enough credit",
   "Video replaced after checking", "Processing paused". A cancelled job is not listed (`list_jobs`
   leaves out `error_code = 'cancelled'`). "Deleted in 3 days" shows only in a result's last 7 days.
   The cost stays on done rows.
6. **Samples in the empty report area:** when no result is open, the right side shows "Samples"
   with the sample cards. The "Your analyses / Samples" tabs, the placeholder sentence and the
   signed-out left grid are removed. Signed out, the left column is the sign-in box.
7. **CSV name:** `/api/jobs/<id>/result.csv` sends `Content-Disposition: attachment;
   filename="<video name without extension> – NeuroLens.csv"` (with an RFC 5987 `filename*` for
   non-ASCII names; a missing name falls back to "NeuroLens result").
8. **A failed or unknown job from a link** (`?job=`) opens History, not the player; the row shows the
   reason.
9. **Phone results:** the moments sit as two rows of two pictures under their label; the scrubber
   has its own full-width line above the buttons.
10. **Retention on a result:** one quiet link "Add audience retention" (§5d), no box, title or
    sentence.
11. **Privacy page and retention guide** use the app's dark look and font, plain words ("up to 31
    days", not S3 rule wording) and the contact address joshualumzy@gmail.com as a mailto link.
12. **Landing:** "How it works" step 2 ends "A result takes about 10 minutes."; a gap between "How it
    works" and the closing band; the sample credit only in the footer.
13. Start-up text: "Starting up; this can take up to a minute."
14. The 3D brain stops drawing while the player is hidden.
15. `docs/M3d_spec.md` §8: the `deadline_passed` error message reads "within 4 hours"; its History
    label is the short "Didn't finish in time" (item 5).
16. **Opening a result from History** switches to the player at once with a loading line. A result
    that keeps failing to load (5xx) shows "Couldn't open this result. Try again later." instead of
    retrying silently.
17. **Results page from a `?job=` link:** its title and "Add audience retention" link are re-checked
    once History has loaded (the job may not be in the first list).
18. A row's curve-failure note clears when a later send for that job succeeds; "Show if muted" is
    enabled again when a comparison is cleared; a job the page itself cancels leaves the tab title's
    count.

## 5. Research sharing
### 5a. Consent
- One box per batch, unticked, never required:
  "Share for research and to train NeuroLens's own models ([details](/static/privacy.html#research)).
  I own the rights to it."
- The page sends `consent_version`, the version of the wording it showed, with each presign that
  has `research_consent`. The server accepts only its current `billing.CONSENT_VERSION`
  (`"2026-10-25"`); any other value → 409 `consent_outdated` ("Please refresh the page."), nothing
  reserved. It stores `consent_version` and `consented_at = now()` per consented job (migration 006).
  A wording change means a new constant and the new text on the page together; old jobs keep the
  version they agreed to.
- The privacy page's `#research` section ("Sharing a video for research") states, in plain words:
  sharing is optional and changes nothing about the analysis; what is kept (the video, its file
  details, any retention curve and the ranges given with it); the purposes (study how what is on
  screen relates to how audiences watch, and build and test NeuroLens's own engagement models, which
  may later be offered as a paid product, with notice first); models are trained on videos and real
  audience data, never on NeuroLens's brain predictions; never published, sold or given outside the
  team, results only as statistics; stored with AWS in the United States; kept up to 3 years, or
  deleted 90 days after upload if no curve was added; "Stop sharing", Delete or deleting the account
  removes the copy at once (a model trained before is not retrained, but the data is not used again);
  the uploader confirms rights and that identifiable people agreed to appear, keeps all rights and
  gives a free, non-exclusive licence for these purposes; a valid curve makes that video's analysis
  free.

### 5b. What is stored where
- `dataset/<job_id>/`: the video (`video<ext>`) and retention snapshots
  (`retention/<snapshot_id>.json`, the file as uploaded, and `retention/<snapshot_id>.csv`, the parsed
  curve). The only place model training reads.
- `academic/<job_id>/`: NeuroLens's result (`result.json`) and brain map (`brain`), for the course
  study. The worker writes them here instead of `dataset/`.
- The worker computes the video's SHA-256 while copying it and records it with
  `billing.record_video_hash(db, job_id, sha256)` (`jobs.video_sha256`, migration 006) before the
  copy.
- S3 lifecycle: `dataset/` and `academic/` expire after 1095 days (3 years), replacing the 365-day
  rule.

### 5c. Clean-up and withdrawal
- `storage.delete_job_prefixes(s3, bucket, job_id)` deletes every object under `dataset/<job_id>/`
  and `academic/<job_id>/` (listed by prefix, paged). It refuses a `job_id` that is not a UUID, so it
  can never list or delete a wider prefix. It raises on any failed delete.
- **Mark first, then purge.** Removing a research copy (Stop sharing, the 90-day rule, job delete) first
  marks it in one guarded update under the job's row lock that re-checks it is still eligible
  (`dataset_removed_at` and its reason, or `deleted_at`), so no curve can be added after that moment;
  then `delete_job_prefixes`; on success `jobs.dataset_purged_at = now()` (migration 006). A failed
  delete leaves `dataset_purged_at` empty and every reaper run retries the purge for consented jobs
  that are marked (removed or deleted) and not purged. For a deleted job the purge also deletes the job's own files (upload, result, brain,
  thumbnail), so `dataset_purged_at` means all its files are gone; the reaper's retry covers every
  marked job, consented or not. A route that has marked answers success even if the purge failed (it
  logs an error; the reaper finishes), since the user can no longer see or retry it. A purge that did not delete the job's own files records nothing if the job was
  deleted meanwhile. Marking a job
  deleted also clears `dataset_purged_at`, so a copy purged earlier (Stop sharing, 90 days) still gets
  its own files purged. Account delete marks too (M3c §5).
- **Curve details go with the copy:** the purge deletes the job's `retention_snapshots` rows (after
  the files), and every reaper run deletes snapshot rows older than 1095 days, matching the S3 rule.
  The reward guard does not depend on them: it is `jobs.curve_rewarded_at` and the video hash.
- **Lock order:** any transaction that locks both takes the user's balance row first, then the job row
  (`add_curve`, account delete, `reserve`), so two of them can never deadlock.
- **Stop sharing:** `POST /api/jobs/<id>/stop-sharing` on a done, shared, not-removed job marks it
  (`dataset_removed_reason = 'stopped'`) and purges, as above. A failed purge still answers 200 (the
  copy is no longer used and the reaper finishes the delete) and logs an error. A queued
  or processing job → 409 `job_active`; not shared or already removed → 409 `not_shared`. The user's
  own result, brain and thumbnail are untouched. History's "⋯" menu shows "Stop sharing" on done,
  shared rows (`list_jobs` `sharing` requires status done), with a one-line confirm ("Stop sharing this video? Its research copy is deleted now.").
- **No curve after 90 days:** the reaper's clean-up (M3e §7) uses `config.json`
  `research.no_curve_days` (90), deletes both prefixes and sets reason `'no_curve'`. It counts from
  `created_at` and skips jobs with any snapshot. The old `retention.csv` check is removed (§5f).
- Job delete and account delete delete both prefixes (replacing the fixed dataset key list).
- A refused curve upload on a removed copy says why: "You stopped sharing this video." or "This
  video's research copy was deleted after 90 days without a curve."

### 5d. Adding a curve
- Offered on a done, shared, not-removed job: the quiet "Add audience retention" link on its History
  row and results page until its first snapshot, and always in its "⋯" menu (a newer snapshot). In the
  upload dialog, per row (§2a).
- The form (a small pop-up): the video name, the guide link, "Choose file", and three required choice
  rows: views ("Under 1k", "1k–10k", "10k–100k", "Over 100k"), published ("This week", "This month",
  "Earlier"), promoted with ads ("Yes", "No"). When a reward is possible (below) the form says "Add a
  valid curve and this analysis is free ($0.72 back)." The reward amount uses the heat orange
  (`--heat`, `#FF8A2A`). Save, then the saved or error message.
- A curve chosen in the upload dialog is sent once its job is done, while the tab is open. If that
  fails, or the tab closed first, the row's link still offers it; a failed send shows "Couldn't add
  the curve for <name>." on the row.
- `POST /api/jobs/<id>/retention` body: `report` (text, as now), `views` (`lt_1k`, `1k_10k`,
  `10k_100k`, `gt_100k`), `published` (`week`, `month`, `older`), `promoted` (bool). Bad values → 400.
  The job must be done, shared and not removed (409s as now, with the messages of §5c).
- Duplicates are checked before any S3 write. If `add_curve` refuses for any reason (including a
  unique-constraint race, which answers 409 `duplicate_curve`), the route deletes the two files it
  wrote.
- Order: parse the report (`results.parse_retention_report`, unchanged) → compute the quality flags
  (below) → write the raw `.json` and the `.csv` to S3 under a new `snapshot_id` → `billing.add_curve`.
  An S3 failure → 500, nothing recorded, and a file already written is deleted. A database failure after the writes leaves two orphan files
  under the job's prefix, removed with it.
- **Quality (`results.curve_quality(pairs)`, pure):** valid when there are at least 20 points, the
  watch ratio spread is at least 0.02 (not flat), the last value is not above the first (not rising
  overall) and no value is above 10. It returns the list of failed checks (empty = valid). The
  report holds no video length, so a curve cannot be matched to its video's length; the reward caps
  below limit that.
- **`billing.add_curve(db, user_id, job_id, snapshot_id, raw_key, csv_key, curve_sha256, points,
  quality_flags, views, published, promoted, reward_cap, reward_window_days)`**, one transaction with
  the job row locked:
  - the job must be the user's, done, shared, not removed and not deleted, else the matching refusal;
  - a curve with the same `curve_sha256` already stored (any job, any account) → `DuplicateCurve`
    (409 `duplicate_curve`, "This curve was already added.");
  - insert the `retention_snapshots` row; set `jobs.retention_added_at` if empty;
  - **reward** the job's `captured_cents` when all hold: the curve is valid; `captured_cents > 0`; the
    job has no `curve_rewarded_at`; no other job with the same non-null `video_sha256` has
    `curve_rewarded_at`; the user has received credit beyond the starter credit at least once (a
    ledger row of kind `grant` or `test_topup`, a payment in a real launch), so throwaway accounts
    cannot farm rewards; the user has fewer than `reward_cap` rewards in the last
    `reward_window_days` days. Then `_move(tx, user_id, job_id, "curve_reward", +captured, 0)`,
    `jobs.curve_rewarded_at = now()` and the snapshot's `rewarded_cents`;
  - returns `{"rewarded_cents": n, "quality": [...]}`.
  A partial unique index on the ledger (`job_id` where `kind = 'curve_reward'`) makes a second reward
  for one job impossible even if the code is wrong.
- Settings in `config.json` `research`: `curve_rewards_per_window` (5), `curve_reward_window_days`
  (30), `no_curve_days` (90). These are anti-abuse product limits, not budget limits.
- Replies on the form: "Thanks! This analysis is now free: $0.72 is back in your credit." when
  rewarded (the amount comes from the reply's `rewarded_cents`); "Thanks, saved. It doesn't look like a
  full retention curve, so it isn't free." when the curve is invalid; otherwise "Thanks, saved."
  A curve held in the upload dialog also says "Thanks, saved." The credit in the top bar refreshes
  after a server save.
- `list_jobs` returns, for shared jobs, `sharing` (done, shared and not removed), `curve_count` and
  `curve_reward_cents` (what a valid curve would give now, 0 if not possible). It applies every reward
  condition above except the curve's own validity; the per-user ones (funded, under the cap) come from
  one helper shared with `add_curve`, so the preview and the payment cannot drift apart. `needs_retention` is
  replaced by `curve_count = 0`.

### 5e. Migration 006 (`infra/migrations/006_m3f.sql`)
- `jobs`: `claimed_at`, `consent_version TEXT`, `consented_at`, `video_sha256 TEXT`,
  `curve_rewarded_at`, `dataset_purged_at`, `dataset_removed_reason TEXT` (`'no_curve'` or `'stopped'`; existing removed
  rows get `'no_curve'`).
- `retention_snapshots`: `snapshot_id UUID PRIMARY KEY`, `job_id UUID NOT NULL REFERENCES jobs`,
  `added_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `source TEXT NOT NULL DEFAULT 'youtube_api'`,
  `raw_key`, `csv_key`, `curve_sha256 TEXT NOT NULL UNIQUE`, `points INT`, `quality_flags TEXT[]`,
  `views`, `published`, `promoted BOOLEAN`, `rewarded_cents INT NOT NULL DEFAULT 0`; index on
  `job_id`.
- Ledger: the partial unique index of §5d. The ledger's `kind` check (if any) allows
  `curve_reward`.

### 5f. Old research data
The six jobs shared under the old wording are Josh's test data. In the real-run session, after the
deploy, Josh stops sharing each one in the page (which also removes any old `retention.csv` and
`result.json` under `dataset/`). Nothing in the code reads the old layout afterwards.

## 6. GPU start and sign-in
### 6a. GPU start from the dispatcher: not built
Starting the GPU from the dispatcher (`UpdateEndpointWeightsAndCapacities`) would save about 4 minutes
of a cold start, but the scale-in alarm is in ALARM whenever the GPU is off, and AWS keeps applying a
step policy while its alarm stays in ALARM. The two could fight over the capacity. Nothing is built and
the idle wait stays 7 minutes (the backlog metric arrives about 3 minutes late, so a shorter wait would
shut the GPU as a job arrives). Decided 25 Oct: revisit before M4 as one design, our own GPU on/off
control (the dispatcher starts the GPU; an off-switch reads the database only while the GPU is on; the
AWS alarm stays as a backstop of about 30 minutes), with full money-path tests.

### 6b. Sign-in sessions (`neurolens/web/auth.py`)
- **Migration 006** adds `sessions`: `session_hash TEXT PRIMARY KEY` (SHA-256 of the token),
  `user_id TEXT NOT NULL`, `created_at`, `last_seen_at TIMESTAMPTZ NOT NULL`; index on `user_id`.
- A verified Cognito sign-in creates a random 32-byte token (`secrets.token_urlsafe`), stores its hash
  and puts only the token in the signed session cookie (cookie lifetime 30 days). The user id and email
  are read from the database, never trusted from the cookie.
- A request is signed in while its token's row exists and `last_seen_at` is within 30 days
  (`SESSION_DAYS = 30`). `last_seen_at` is refreshed at most once an hour, and the cookie's lifetime
  with it, so an active user stays signed in and a request costs no extra write most of the time.
- Sign-out deletes the row. Delete account deletes all the user's rows. Expired rows are deleted by
  the reaper's run.
- The landing page's hint cookie follows the session's lifetime: `/app` re-sets it for a signed-in
  user, so it lasts as long as the sliding session.
- Dev mode is unchanged. The 12-hour rule (`SESSION_HOURS`) is removed.

## 7. Laptop (`app.py`, `infra/local_stack.py`)
- `python app.py` refuses to start unless `AWS_ENDPOINT_URL` is set to a `localhost` or `127.0.0.1`
  address, no `AWS_ENDPOINT_URL_<SERVICE>` points elsewhere, and `AWS_IGNORE_CONFIGURED_ENDPOINT_URLS`
  is not set (checked after `.env` is loaded), with the message "python app.py runs only against the local fake stack: python
  infra/local_stack.py. The real .env is for the admin scripts." Nothing else reads this rule.
- `python infra/local_stack.py` runs the whole product on the laptop with no real AWS: it requires a
  local PostgreSQL DSN (host `localhost` or `127.0.0.1`, no `host`/`hostaddr` query parameters) (`--dsn`, default `postgresql://postgres@localhost:55432/postgres`), starts
  moto's server on port 5005, removes every inherited `AWS_*` variable and uses throwaway test credentials,
  region and endpoint in its own environment (never the real profile), creates the bucket (with the page's CORS) and the jobs queue and
  notification, applies the schema, then runs the web app on 5003 (Postgres backend) and a loop that
  passes each queued upload through the real `dispatcher.dispatch` (with a stand-in SageMaker client
  that hands the job straight to the fake-mode worker's handler; a dispatch error leaves the message
  on the queue for a retry). Ctrl-C stops everything it started.
  `--grant <email> <dollars>` gives local credit.

## 8. Docs
- `docs/M3e_spec.md` §7–§8 point to this spec for the consent, curve, clean-up and page parts they
  described; their rules that changed are removed there.
- `docs/M3d_spec.md` §8: the 4-hour fix (§4.15).
- `AGENTS.md` Commands: the local stack line.

## 9. Known limitations (accepted)
- A curve cannot be proved to belong to its video (the report holds no video id or length). Limits:
  one reward per video hash across accounts, a cap per user, and the right to remove fake data.
- A curve upload that crashes between its S3 writes and its refusal can leave its two files under a
  removed job's prefix (needs a crash at that exact moment).
- Two accounts sharing the same video at the same moment could each be rewarded once (no lock across
  jobs). Needs deliberate abuse; the cap bounds it.
- No request rate limit. The costly paths are bounded already: a GPU run needs credit, and a caller
  keeping the database awake trips the 6-hour database alarm. A rate limit is added when abuse or real
  traffic appears.
- A curve chosen in the upload dialog is lost if the tab closes before its job is done; the row's
  link still offers it.

## 10. Tests (written first by a separate agent; PostgreSQL for billing)
Money and data paths get full treatment:
- `add_curve`: reward once per job; never twice under the unique index (second call, and a forced
  direct insert); not for an invalid curve, a $0 job, a video hash already rewarded on another
  account, or a user at the cap (and again after the window passes); duplicate curve refused with
  nothing stored; refusals for not done, not shared, removed, deleted, someone else's job; balance and
  ledger change by exactly the captured price.
- `curve_quality`: each check, at its edge.
- `delete_job_prefixes`: deletes everything under both prefixes across pages; refuses a non-UUID
  (including empty and `../`); raises on a failed delete.
- Stop sharing: marks then purges; a purge failure still answers 200 and the reaper finishes it; 409s
  (including a queued or failed job); another user's job → 404.
- Purge: a job stopped then deleted gets its own files purged (reaper retry included); snapshot rows go
  with the purge and after 1095 days; a stopped job's video cannot be rewarded again.
- Reward preview: `curve_reward_cents` is 0 for an account with only starter credit and at the cap,
  and equals what `add_curve` then pays.
- Reaper: 90 days from config; a job with a snapshot is kept; reason `'no_curve'`.
- Retention route: field validation; an S3 failure on either write records nothing and leaves no
  file; the removed-copy messages.
- Worker: result and brain go to `academic/`, the video to `dataset/`, the hash recorded; nothing of
  NeuroLens's output under `dataset/`.
- Consent: version and time stored only when consent is true.
- `claim` sets `claimed_at`; `list_jobs` leaves out cancelled jobs and returns the new fields.
- Sessions (full treatment): a token signs in only while its row exists and was seen within 30 days;
  a stale cookie, a forged token, a deleted row (sign-out, account delete) and a 31-day-old row are
  refused; `last_seen_at` moves at most once an hour; the cookie holds no user id.
- Consent version: an outdated or missing version with consent → 409, nothing reserved.
- Reward: refused for an account with only starter credit.
- Presign: 429 at `max_waiting_jobs` queued jobs, nothing reserved.
- CSV header: ASCII and non-ASCII names, missing name.
- `app.py` refuses without a local endpoint, including one set only in `.env`.
- Local stack: a dispatch error leaves the queue message for a retry.
Page work (light treatment): one walk on the local stack at 1440 px and 390 px covering §2–§4.

## 11. Real run (cost estimate first)
One GPU session right after deploy, on the GPU the deploy starts (estimate about $0.50–1: the deploy's
cold start plus a 5-file batch and its idle wait):
1. A 5-file batch: each job charged once, all done on one warm GPU, title count and rows correct,
   cancel one file within 15 s (refunded, not listed).
2. Add a curve to a shared result: reward appears in the credit and the ledger once; a second curve
   is stored without a reward; stop sharing removes both prefixes.
3. Stop sharing the six old jobs (§5f).

## 12. Acceptance criteria
- All tests pass; `ruff check .` clean; `terraform validate` and `plan` show only the expected changes.
- The page walk shows every item of §2–§4 at both widths.
- The real run's three checks pass, or a failure is recorded with its cause.
