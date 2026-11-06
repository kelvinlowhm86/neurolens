# NeuroLens — M3l Implementation Spec: honest GPU wait, one curve rule, real viewers, compare with samples

## Decisions (6 Nov)
- A video waiting for a GPU says so honestly once the wait is clearly not a normal start (normal
  cold start 9 to 13 minutes, M3f §6a): after 20 minutes. It replaces "slower than usual".
- When the GPU request's wait runs out, the job gets its own reason, shown as
  "No GPU free · not charged", not a generic failure. No email.
- One curve-credit rule: 1,000 views (YouTube checked, Facebook as reported). The one-week wait is
  removed everywhere.
- A stored retention curve is shown as a "Real viewers" strip under the signal strips.
- Compare is offered for every result: a signed-in person's video can be compared with their other
  videos and with the samples.
- Page copy: few words (M3k, "UI copy").

## 1. Scope
### In scope
§2 GPU wait, §3 curve rule, §4 Real viewers strip, §5 Compare with samples, §6 brain panel layout.
### Out of scope
A written (AI) comparison between a person's video and a sample: comparing with a sample shows the
charts only; the written comparison stays for two of the person's own videos (M3h §2). Email.

## 2. Honest GPU wait
### 2a. Migration 014 (`infra/migrations/014_gpu_wait_until.sql`)
`ALTER TABLE jobs ADD COLUMN gpu_wait_until timestamptz` (null until dispatched).

### 2b. Dispatcher (`neurolens/lambdas/dispatcher.py`)
Right after `invoke_endpoint_async` succeeds, the dispatcher records
`gpu_wait_until = now() + RequestTTLSeconds` through a new `billing.record_gpu_wait(db, job_id,
until)` (only while the job is queued). A failure to record is logged and does not undo the
dispatch (the job then behaves as before this spec).

### 2c. Giving up: reason `no_gpu_free`
New refund reason `no_gpu_free` for a job that is still `queued`, never claimed (`claimed_at` null)
and past `gpu_wait_until + GPU_WAIT_GRACE_S` (`GPU_WAIT_GRACE_S = 300`, in `billing`, so a request
SageMaker hands over at the last second is not refunded under the worker). One helper,
`billing.refund_if_gpu_wait_over(db, job_id) -> bool`, does the check and the refund atomically
(`issue_refund(..., queued=True)`, so a worker claim and this refund cannot both win). Callers:
- the web routes that read a person's jobs (job list and job status): for each such job of the
  signed-in person, before answering, so the person sees the honest reason as soon as they look.
  The web does not delete files; the reaper's existing retry of recently failed jobs' file deletes
  (reaper docstring) removes the upload. The worker must confirm that retry covers this reason and
  extend it if not.
- the reaper: such a job is refunded `no_gpu_free` instead of `deadline_passed`.
- `lambdas.finish_job` (the SageMaker error handler): such a job is refunded `no_gpu_free` instead
  of `processing_failed`.
A job with `gpu_wait_until` null keeps today's paths and reasons.

### 2d. Status data
The job status and job list answers carry `gpu_wait_until` (UTC text, or null).

### 2e. Page (`static/app.html`)
- `QUEUED_SLOW_MS` becomes 20 minutes; "slower than usual" is removed.
- Your videos row of the waiting job, from 20 minutes: `GPUs busy · <n> min · not charged`
  (before 20 minutes it stays `Waiting for the GPU · <n> min`; "In line" unchanged).
- Loading screen note while queued, from 20 minutes: `GPUs are busy. We'll keep trying until
  <time>. You won't be charged if none frees up.` `<time>` is `gpu_wait_until` in the person's
  local time (short, e.g. 4:05 pm). Without `gpu_wait_until` the sentence drops "until <time>".
- `FAILURE_TEXTS.no_gpu_free = 'No GPU free'` (the row then reads `… · No GPU free · not charged`).
- The privacy page and guides that list failure reasons, if any, gain this one.

## 3. One curve rule (replaces M3k §7's date part and M3j §5's `too_early`)
- A curve pays when every other rule passes and the video has at least `curve_min_views` views
  (YouTube from the checked count; Facebook from the person's report). A hidden or unknown count
  skips the views check, as now.
- Removed everywhere: `research.curve_reward_min_days` (config.json), `DEFAULT_REWARD_MIN_DAYS`,
  the `reward_min_days` keyword, `reward_from` (billing returns, route answers, page), the
  `too_early` reason, its page copy, and the week in specs, privacy page, retention guide, READMEs.
  The checked publish date stays only where something other than the week still uses it; the worker
  lists what it kept and why.
- `billing.curve_offer` / `_curve_reward` return `(cents, reason)`.
- Page copy (exact):
  - Upload box: `Add its retention curve once it's reached 1,000 views: see real viewers beside the prediction, and get its credit back.`
  - Retention card after sharing: `Thanks for sharing. Once it reaches 1,000 views, add its retention curve: see real viewers beside the prediction and get <reward> back.`
  - `too_few_views` message keeps its current meaning, without any date.
- Tests that pinned `too_early` or `reward_from` are changed because this behaviour was
  deliberately removed (say so in the commit).

## 4. Real viewers strip
- New route `GET /api/jobs/<id>/retention` (owner only, like the other job routes): the newest
  stored, quality-checked curve of the job as `{"points": [[seconds, percent], ...],
  "duration_s": ...}` read from its `retention/<snapshot_id>.csv`; 404 when there is none.
- The page shows it only when `abs(duration_s - video duration) <= research.curve_length_tolerance_s`
  (2 s); otherwise nothing changes.
- Placement: a strip directly under the signal strips in the Charts view, same width, same time
  axis, same playhead and hover behaviour as a signal strip. Label `Real viewers`, values as
  `% still watching`. Same style as the signal strips (no new colours beyond the existing accent).
- Fetched after a curve is added too, so the strip appears without a reload.
- Samples: shown only if `data/samples.json` already carries a curve for the sample; no new sample
  data is made.

## 5. Compare with samples
- For a signed-in person's video, the Compare choices are their other finished videos, then the
  samples (two labelled groups in the select: `Your videos`, `Samples`). The Compare tab therefore
  shows for every result.
- Choosing a sample loads it the way a sample-vs-sample comparison does today (charts, videos,
  differences). No written comparison is offered for a video-vs-sample pair (§1).
- Samples keep today's behaviour.

## 6. Brain panel layout
On a narrow window (seen at about 420 × 750 CSS px) the brain legend ("typical → higher") and
"Drag to rotate" are drawn over the Seeing / Hearing / Understanding values. Find the cause, fix it
in CSS (no element may overlap another at any width), and check with Playwright (installed Chrome,
`channel="chrome"`) at 390×844, 420×750, 860×700, 1280×800 and 1440×900: no overlap, nothing cut.

## 7. Tests (written first by a separate agent; PostgreSQL for billing)
Full treatment for §2 and §3 (they move credit):
- `refund_if_gpu_wait_over`: refunds a queued, unclaimed job past `gpu_wait_until + grace` with
  `no_gpu_free` and releases its reservation; leaves a job inside the grace, a claimed or
  processing job, a done job, and a job with null `gpu_wait_until`; calling twice refunds once; a
  claim and the refund racing leave exactly one outcome.
- Reaper and `finish_job` use `no_gpu_free` for such a job and keep today's reasons otherwise.
- Dispatcher records `gpu_wait_until` equal to now + the TTL it sent.
- Job list/status routes refund such a job of the signed-in person and never another person's.
- Curve rule: a curve with ≥ 1,000 views pays at once regardless of age; < 1,000 pays nothing and
  says `too_few_views`; hidden count pays; no answer carries `reward_from`; the setting is gone.
- Route `GET /api/jobs/<id>/retention`: owner gets points; other person 404; no curve 404.
Break-it checks (after commit): drop the `claimed_at` condition; drop the grace.

## 8. Rollout
Migration 014 and `infra/deploy_web.sh` and `terraform apply` (Lambdas) in BOTH accounts; the
worker image is unchanged.

## 9. Known limitations (accepted)
- A person who never looks is refunded by the reaper (every 3 hours) or the error handler.
- A video-vs-sample comparison has no written report (§1).
