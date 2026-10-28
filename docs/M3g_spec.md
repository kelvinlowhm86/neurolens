# NeuroLens — M3g Implementation Spec: the AI report

Status: current design. Builds on M3d (result, page), M3e (SageMaker worker) and M3f (deletion,
sharing). Where this spec and an older one disagree, this one wins, and the older section is
updated to match.

## Decisions (28 Oct, Josh)
- A written report per video, by Claude Sonnet 5.5 on Amazon Bedrock (us-east-1), from the
  brain numbers and the video's frames. Free, at most one finished report per video, stored and
  deleted with the job.
- Frames are cut on the GPU machine right after the analysis, 1 per second (the brain data is
  1 per second), long edge 640 px, JPEG. Cutting them never fails the paid job: on a failure the
  job finishes and that video has no report.
- The report is written in the background by its own Lambda (Bedrock alone takes ~30-90 s, longer
  than the web Lambda's 60 s). The page asks for it and checks back.
- Videos analysed before this ships have no frames, so no report. Accepted.
- Shipped in the same worker image rebuild as M3f §6a.
- The frames file also carries the spoken words with their times (§2b), so the report can say
  what was said when the speech signal moves.
- Written only when the user clicks "Write report", never automatically: no Bedrock cost for
  reports nobody reads. A second click cannot start a second report (the server claims the job
  once, §3; the page disables the button while waiting).
- The video price does not change: the report's cost fits inside it (§10).

## 1. Scope
### In scope
Frame capture in the worker (§2), migration 008 (§3), two web routes (§4), the report Lambda
(§5), the page's Report tab (§6), privacy page section (§7), Terraform and IAM (§8), laptop
fake (§9), tests (§11).
### Out of scope
A PDF download. Reports for old videos. Editing or regenerating a finished report. Reports in
other languages.

## 2. Frames (worker, GPU machine)
### 2a. Cutting
`inference.make_frames(path, seconds)` returns JPEG bytes, one per whole second `t = 0 ..
seconds - 1`, where `seconds` is `result["duration_seconds"]` (the length of the per-second
series, so frame `t` lines up with value `t`). One ffmpeg run:
`-vf fps=1,scale=<long edge 640, other edge even, never upscaled>`, `-q:v 5`, written to a
temporary folder that is always removed. Frames past `seconds` are dropped. Raises `FramesFailed`
on a non-zero exit, a 120 s timeout, or zero frames, like `make_thumbnail`. No heavy imports.

### 2b. Stored file
One object, `results/<job_id>.frames`: an uncompressed zip (JPEGs do not compress) holding
`0000.jpg`, `0001.jpg`, … and `words.json`: a list of
`{"start": seconds (2 dp), "text": word}` from the `Word` events `build_events` already made,
in time order; an empty list when there is no speech, and always an empty list in fake mode.
`storage.put_frames(s3, bucket, job_id, data)` and `storage.get_frames(...)` (returns the
frames in order and the words; raises on a malformed zip).

### 2c. Where in the worker
After `extract_response` and before the `saving_dataset` stage. In a `try` that catches
`FramesFailed` and any S3 error from `put_frames`: logs `FRAMES_FAILED <job_id>` and carries on.
Only when the put succeeded, `result["frames"] = <count>` is set; the result is written after, so
a result with `frames` always has its file. A retried attempt cuts and puts again (overwrite is
fine). The research dataset copy does not include frames.

## 3. Migration 008 (`infra/migrations/008_report.sql`)
`jobs` gains:
- `report_state TEXT NULL CHECK (report_state IN ('writing','ready','failed'))`
- `report_tries INT NOT NULL DEFAULT 0`
- `report_started_at TIMESTAMPTZ NULL`
- `report_error TEXT NULL` (a short code, never shown verbatim to the user)

`billing.claim_report(db, user_id, job_id, max_tries, stale_minutes)`: one UPDATE … RETURNING
that sets `writing`, `report_tries + 1`, `report_started_at = now()`, `report_error = NULL`, only
when the job is the user's, `status = 'done'`, `deleted_at IS NULL`, the user is not deleted,
`report_tries < max_tries`, and the state is NULL, `failed`, or `writing` started more than
`stale_minutes` ago (a crashed Lambda). Returns True/False. `billing.finish_report(db, job_id,
state, error=None)`: sets `ready` or `failed` only where the state is `writing` and the job is
not deleted; returns whether a row changed. `billing.release_report_try(db, job_id)`: sets
`failed` and gives the try back (`report_tries - 1`), used only when the Lambda could not be
started at all (§4).

## 4. Web routes (`neurolens/web/app.py`)
- `POST /api/jobs/<job_id>/report`: `owned_job`; 404 unless done; 409 `no_frames` when the result
  has no `frames`; 409 `report_ready` when ready; 409 `report_writing` when writing and not stale;
  429 `no_tries_left` when tries are used up. Otherwise `claim_report`, then start the report
  Lambda asynchronously (`InvocationType="Event"`, payload `{"job_id": ...}`). If starting it
  raises: `release_report_try`, log, 503. Answers 202 `{"report_state": "writing"}`.
- `GET /api/jobs/<job_id>/report`: `owned_job`; answers `{"report_state", "tries_left",
  "report"}` where `report` is the stored JSON when ready, else null. A `writing` state older than
  `stale_minutes` is answered as `failed` (the Lambda died; a new POST may claim it).
- `create_app` gains `report_starter=None`: a callable taking a job id. In Cognito mode, when not
  handed in, it is a boto3 Lambda `invoke` on `aws.report_function` (a required setting there).
  Without one, POST answers 500 `not_configured`. The web code still never imports
  `neurolens.inference` or the Anthropic SDK.

## 5. The report Lambda (`neurolens/lambdas/report.py`, `neurolens-report`)
### 5a. Flow
`handler(event, context)` → `write_report(job_id, db, s3, bucket, client, cfg)`:
1. Read the job row; stop (log, no writes) unless `report_state = 'writing'`.
2. Read `results/<job_id>.json` and the frames file. Missing either: `finish_report(failed,
   "missing_input")`.
3. Build the request (§5b) and call Bedrock (§5c).
4. Check the answer (§5d). Bad: `finish_report(failed, <code>)`.
5. Put `results/<job_id>.report.json`, then `finish_report(ready)`. If that changes no row (the
   job was deleted meanwhile), delete the object just written.
Any unexpected exception: log with the job id, `finish_report(failed, "error")`, return
normally (no Lambda retry). The Lambda's async retries are set to 0 and its timeout to 300 s;
`stale_minutes` (10) is above that, so a live run is never treated as stale.

### 5b. Request (`neurolens/report_prompt.py`, pure, numpy-free)
`build_request(result, frames, words, summary)` returns `(system, content)`:
- System prompt (fixed text, a module constant): what the seven signals and three groups mean
  (from M3d §2); values are signed means where 0 is typical film/TV viewing on the model's own
  scale, so they compare videos and moments, not absolute engagement; these are model
  predictions of an average viewer's brain, not measurements of real people; write for a video
  maker in plain words; cite times in whole seconds; never guess what was said beyond the
  words given.
- User content, in order: a JSON block with `duration_seconds`, the per-second values of
  `overall`, the three groups, the seven signals and `muted.overall` (2 dp), and the four-part
  `results.summarise` output; the words (if any); then for each second `t` a text block
  `"t = <t> s"` followed by its image block (base64 JPEG). At most `duration_seconds` images
  (120 at the 120 s limit; Claude accepts far more).

### 5c. Call
Anthropic Python SDK, `AnthropicBedrockMantle(aws_region=cfg aws.region)`, model from
`config.json` `report.model` (`anthropic.claude-sonnet-5-5`), `max_tokens` `report.max_tokens`
(16000), `output_config` with `effort: "medium"` and a JSON-schema `format` (§5d), adaptive
thinking left at its default, SDK timeout 240 s, `max_retries` 2. The SDK ships as a Lambda layer
built by `infra/build_report_layer.sh` (manylinux wheels, like the ffprobe layer), so the other
Lambdas' zip stays dependency-free. Before the build: verify on AWS that the model ID is served
in us-east-1 through the Mantle endpoint and which IAM action it needs; if Mantle does not serve
it, use the SDK's `AnthropicBedrock` client (InvokeModel) instead and note it here.

### 5d. Answer
Schema (also checked in code after parsing, since a wrong report must not be stored):
```
{ "headline": str (<= 120 chars),
  "summary": str (<= 900 chars),
  "moments": [ { "start": int, "end": int, "kind": "strong" | "weak",
                 "what_happens": str, "brain_response": str, "suggestion": str | null } ]  (1-8),
  "strengths": [str] (0-4), "improvements": [str] (0-4) }
```
`0 <= start <= end < duration_seconds`. Failure codes: `refused` (`stop_reason == "refusal"`),
`truncated` (`max_tokens`), `bad_answer` (not JSON or fails the checks), `bedrock_error`
(SDK error after its retries). The stored file adds `model`, `created_at` (UTC text) and the
`usage` token counts, for cost tracking.

## 6. Page (`static/app.html`)
A Report tab beside the charts of a finished job. States, few words:
- no `frames` in the result: "No report for this video."
- not yet asked: a "Write report" button, with "Free" beside it; disabled from the click until
  the POST answers.
- writing: "Writing your report…"; the page checks `GET …/report` every 5 s, stops after 10 min.
- ready: headline, summary, then the moments (each time a button that moves the time cursor,
  like the existing moment links), strengths, improvements; a one-line note that it is an AI
  reading of predicted brain activity.
- failed with tries left: "Couldn't write the report." and "Try again"; none left: the first
  line only.
All model text is inserted as text (`textContent`), never as HTML.

## 7. Privacy page (`static/privacy.html`)
A short section: when you ask for a report, the video's frames (1 per second), its words and its
brain numbers are sent to Claude on Amazon Bedrock in AWS us-east-1; AWS states Bedrock does not
store them or use them for training; the frames and report are deleted with the job, and expire
with the result after 30 days.

## 8. Terraform and IAM
- `aws_lambda_function.report`: the job Lambdas' zip plus `report.py`, `report_prompt.py` and
  `results.py` (standard library only), the report layer, 512 MB, timeout 300 s, async `maximum_retry_attempts = 0`, reserved concurrency 5
  (caps parallel Bedrock spend), env as the other job Lambdas.
- Its role: Data API on the cluster, `s3:GetObject` on `results/*`, `s3:PutObject` and
  `s3:DeleteObject` on `results/*.report.json`, the Bedrock invoke action on that one model,
  logs.
- Web role: `lambda:InvokeFunction` on `neurolens-report` only. `aws.report_function` added to
  the web Lambda's settings.
- `storage.job_keys` adds `results/<id>.frames` and `results/<id>.report.json`, so job delete,
  account delete and the reaper remove them. The `results/` 30-day expiry covers both.
- Josh's one-time console step: Bedrock → Model access → enable Claude Sonnet 5.5 in us-east-1
  (Anthropic asks a short use-case form the first time).

## 9. Laptop (`infra/local_stack.py`)
The local stack passes a `report_starter` that runs `write_report` in a thread with a fake
client: it returns a valid canned report built from `results.summarise` (no Bedrock call, no
network). Migration 008 runs on the local database like the others.

## 10. Cost (estimate, to measure on the real run)
Per 2-minute video: 120 frames at 640×360 ≈ 300 tokens each ≈ 36k, plus numbers and prompt
≈ 12k, so ≈ 48k input tokens; ≈ 3k output with thinking. At ~$2.20 / $11 per million
(us-east-1 Bedrock; aggregator figure, verify on the AWS pricing page) ≈ $0.14 per report,
less for shorter videos. At most `report.max_tries` (3) tries per video, so ≤ $0.45 against the
video's own price of $0.90-$3.60. Frame cutting adds an estimated 5-20 s of GPU time
(< $0.02). The report is free to the user; its cost is part of the video's price.

## 11. Tests (written first by a separate agent; PostgreSQL for billing)
Full treatment: it triggers paid Bedrock calls. Fake SDK client and fake S3; no network.
- Claim: once only; tries cap; stale `writing` reclaimable, fresh one not; not the owner, not
  done, deleted job, deleted user → no claim; release gives the try back.
- Routes: each 4xx/202 case in §4; start failure → 503 and the try returned; GET answers a stale
  `writing` as `failed`; no Anthropic or inference import in the web app.
- Lambda: success stores the report then sets ready; not `writing` → no Bedrock call; missing
  frames → `failed missing_input` and no call; refusal, truncation, bad JSON, out-of-range
  moment, too many moments → `failed` with the code and nothing stored; SDK error → `failed
  bedrock_error`; job deleted during the call → report object removed.
- Request: one image per frame in time order, each after its `t = <t> s` label; image count
  equals the frame count; the values are the result's.
- Worker: frames failure (ffmpeg or S3) still finishes and charges the job, result has no
  `frames`; success sets `frames` to the count and puts the file before the result.
- Storage: `job_keys` includes both new keys; `get_frames` round-trips `put_frames`.
Break-it checks (2): drop the tries cap from `claim_report`; drop the deleted-job check from
`finish_report`. Each must turn a test red.

## 12. Real run (ask Josh before; estimate ≤ $0.20)
After the deploy with M3f §6a: one fresh video through the real GPU, then one report. Check the
frames file, the report on the page, the stored `usage`, and the Bedrock line in Cost Explorer the
next day. Then delete the job and confirm both new objects are gone.

## 13. Known limitations (accepted)
- No report for videos analysed before this shipped, or after their result expires (30 days).
- The report reads predicted brain activity of an average viewer; it can be confidently wrong
  about why a moment works. The page says it is an AI reading.
- Bedrock may refuse a video it judges unsafe; that video then has no report.

## 14. Acceptance criteria
- A new job's result has `frames` and its frames file exists; a frames failure does not fail or
  refund the job.
- "Write report" produces a report within ~2 min on the real stack; reloading shows it; a second
  POST answers `report_ready`.
- Deleting the job removes the frames and the report.
- `pytest` and `ruff check .` pass; the two break-it checks go red.
