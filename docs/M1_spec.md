# NeuroLens — M1 Implementation Spec (v3)
**Milestone:** Cloud Storage & Task Queue Decoupling (29 Sept – 8 Oct)
**Grounded in:** actual current `app.py` (300 lines), `config.sample.json`, `requirements.txt` from `kelvinlowhm86/neurolens` as of this spec's writing. If the repo has changed since, re-verify assumptions below before implementing.

**Changes since v2 (30 Sep 2026)** — from the architecture review; each item departs from the Preliminary report:
- **Region `us-east-1`, not `ap-southeast-1`.** The `g6e` GPU family the design depends on is not offered in Singapore.
- **Terraform replaces bash provisioning** (`infra/terraform/`), with shared remote state so teammates can work on the same infrastructure. `terraform destroy` is the teardown.
- **Fake-model switch (`FAKE_INFERENCE`) and automated tests.** All M1 plumbing is built and tested on a laptop without a GPU; see §1 for what this does and does not prove.
- **Results kept 30 days** (uploads still 48 h), so M3's job history keeps working.
- **No `claims/` prefix.** M2 no longer builds the S3 lease protocol; duplicate protection is the conditional result write, then a database lock in M3.
- **SQS visibility timeout 900 s** until M2 adds the heartbeat.
- **Budget:** an account-wide $40 budget with 50/80/100% alerts replaces the $10/$20 alerts.
- **Python 3.11+.** The official `facebookresearch/tribev2` repo requires it; README's "3.10+" is out of date.
- **Real-GPU checks deferred to M2's first GPU boot** (§9).

## 0. What exists today (do not re-derive this — read the actual file first)
`app.py` currently:
- Loads config from `config.json` (gitignored, copied from `config.sample.json`) — holds `hf_token`, model paths, `max_video_duration_seconds`
- Loads the TRIBE v2 model and builds Destrieux ROI masks **once at module import time**, as module-level globals (`model`, `roi_masks`, `ROI_LABEL_MAP`)
- Defines three standalone functions that do NOT depend on Flask request objects: `run_inference(video_path)`, `strip_audio(input_path, output_path)`, `extract_engagement(preds_full, preds_noaudio)`
- `POST /api/analyse` receives a raw multipart blob, saves to `/tmp`, calls those three functions in sequence, cleans up, returns JSON
- Real ffprobe-based duration check already exists in the route handler (`ffprobe` subprocess call) — **this is different from M1's client-side estimate**; keep both, they serve different purposes (see §4)

`app.py` also already serves `GET /`, `GET /api/samples` (reads pre-computed demo results from `data/samples.json`), and `GET /data/<filename>` (serves sample videos/thumbnails from the data directory). These predate this spec and are unchanged by M1.

## 1. Required refactor before adding cloud plumbing
The model-loading + inference functions currently live inside `app.py` as module-level code. Both the Flask process and the new `worker.py` process need this logic, but **should not both run a Flask app**. Extract into a new module:

**Create `inference.py`**, moving from `app.py`:
- All the HF env var setup (§ "Set HuggingFace env vars BEFORE any HF imports" block) — this ordering constraint must be preserved exactly
- Model loading (`TribeModel.from_pretrained(...)`)
- Atlas loading + `ROI_LABEL_MAP` + `roi_masks` construction
```python
# NOTE: regions["auditory"] is the 5th ROI engagement signal, normalized
# over the full T-length window, same basis as the other four ROIs.
# auditory_with_audio / auditory_without_audio are a SEPARATE dual-pass
# comparison from the same raw signal, normalized independently over the
# shorter min_len window shared between the full and no-audio passes.
# Because the normalization windows differ, "auditory" and
# "auditory_with_audio" can show different values at the same timestep
# even though both derive from the same signal. This is intentional —
# do not force them equal.
```
- `normalize_01`, `run_inference`, `strip_audio`, `extract_engagement`
```python
# MAX_DURATION reflects the target use case (short-form pre-roll / social ad
# creative) and bounds GPU job duration for predictable cost and to stay
# within the SQS visibility-timeout window (900 s in M1; M2 §8 adds a
# heartbeat). This is a hard
# product/cost constraint, independent of billing — it must be enforced
# regardless of credit balance (see M3 §4 Stage 2, which must check this
# BEFORE any credit-adjustment logic, not instead of it).
```
- Expose `MAX_DURATION` (read from config) as a module-level constant

**`app.py` becomes:** `from inference import run_inference, strip_audio, extract_engagement, MAX_DURATION` — the Flask route logic (request parsing, temp file handling, jsonify) stays in `app.py`, unchanged in behavior.

**`worker.py` (new):** `from inference import run_inference, strip_audio, extract_engagement, MAX_DURATION` — importing `inference.py` triggers model/atlas loading once at worker startup, same as it currently does for the Flask process. This is intentional: the worker needs the model loaded exactly once, at process start, not per-job.

Do not change any function signatures or behavior during this extraction — it's a pure move, not a rewrite.

### 1a. Fake-model switch (`FAKE_INFERENCE`)
`inference.py` reads `FAKE_INFERENCE` from the environment (default off; the env var wins over any config value). When it is on:
- Do **not** import `torch` or `tribev2`, and do not load TRIBE v2. Keep these imports inside the real-mode branch so fake mode runs on a laptop without them installed.
- **Do** still set the HF/nilearn environment variables and load the Destrieux atlas and `roi_masks` exactly as in real mode (CPU-only, ~200 MB atlas download).
- `run_inference(video_path)` measures the video with ffprobe and returns a seeded random `numpy` array of shape `(ceil(duration), 20484)`. Seed from the file's size so the same video gives the same output.
- `strip_audio`, `extract_engagement` and everything downstream run for real, unchanged.
- Log a clear `FAKE_INFERENCE is ON — results are not real model output` line at startup, and add `"fake_inference": true` to every result JSON produced in this mode.

### 1b. Proving the refactor is safe — three layers
Fake mode does **not** exercise model loading, so no single check proves the move. Use all three:
1. **Golden test (required, laptop).** *Before* moving any code, add a test that runs `extract_engagement` on seeded synthetic `preds_full`/`preds_noaudio` and synthetic ROI masks, and saves the output as `tests/fixtures/extract_engagement_golden.json`. The same test must pass unchanged after the move. This proves the calculation is identical.
2. **Real-import check (optional, laptop CPU).** With real `torch` and `tribev2` installed in the virtualenv, import `inference.py` in real mode far enough to set the HF environment variables and run `TribeModel.from_pretrained(...)` (~1 GB checkpoint), without calling `predict()`. This exercises the fragile "set env vars before any HF import" block that fake mode skips. `tribev2` on macOS/CPU is untested; if it will not install, skip this layer and rely on layer 3.
3. **Real run (required, deferred to M2).** On M2's first GPU boot, run one real inference through S3 → SQS → worker on a sample video and compare the result with that video's entry in `data/samples.json` (produced by the original pipeline), allowing for small floating-point differences. This is the final proof.

## 2. Config approach
Extend `config.json` / `config.sample.json` (same gitignored-secrets pattern already in use) with a new top-level `"aws"` block:
```json
{
  "hf_token": "...",
  "paths": { "...": "..." },
  "model": { "...": "..." },
  "aws": {
    "region": "us-east-1",
    "s3_bucket": "REPLACE_ME",
    "sqs_queue_url": "REPLACE_ME"
  },
  "hf_download_timeout": 300,
  "max_video_duration_seconds": 120,
  "max_upload_bytes": 300000000
}
```
**Do not put AWS access keys in this file.** boto3 must use the default credential chain (`~/.aws/credentials`, `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` env vars, or an instance role in later milestones) — only resource identifiers (bucket name, queue URL, region) go in config. Update `config.sample.json` with placeholder values so the schema is documented; actual values go only in the gitignored `config.json`, which I will fill in myself after the resources exist (copy them from `terraform output`).

The fake-model switch (§1a) is an environment variable, not a config field, so a real deployment can never be left in fake mode by a stale config file.

## 3. AWS resource provisioning (Terraform)
Create `infra/terraform/` — a Terraform configuration using the AWS provider in `us-east-1`, with `default_tags` `Project=neurolens` and `Milestone=M1` on every resource. Terraform tracks what exists, so running `terraform apply` twice is safe by design and `terraform destroy` removes everything it created.

**Budget guardrail.** An account-wide AWS Budget of $40/month with email alerts at 50%, 80% and 100% of actual spend already exists (created by hand, not by Terraform). Confirm it is still there before the first `terraform apply`.

**Remote state.** Terraform's record of what exists (its *state*) must not live on one laptop, or teammates will clash. Store it in a separate, small S3 bucket (e.g. `neurolens-tfstate-<account-id>`) with versioning on, using the S3 backend with S3-native locking (`use_lockfile = true`). Create that one bucket once, by hand or with a tiny `infra/terraform/bootstrap/` configuration, and document the step in `infra/terraform/README.md`. The state bucket is never destroyed by `terraform destroy`.

**Variables:** `bucket_name`, `queue_name`, `allowed_origins` (list, e.g. `["http://localhost:5003"]`). Outputs: `bucket_name`, `queue_url`, `queue_arn`.

The configuration:
1. Creates the S3 bucket (`bucket_name`, region `us-east-1`)
2. Applies baseline security settings to the bucket:
   - Enable S3 Block Public Access (all four settings) on the bucket.
   - Set Object Ownership to 'Bucket owner enforced' (disables ACLs entirely — the presigned-URL upload flow does not use or need ACLs).
   - Enable default server-side encryption (SSE-S3 is sufficient for this project; SSE-KMS is a reasonable upgrade but not required).
   - Attach a bucket policy statement that denies any request where `aws:SecureTransport` is `false`, ensuring all access — including the presigned POST URL and any GET — must use HTTPS.
   None of these settings change behavior for direct browser uploads via presigned URLs, since access is via a signed request rather than a public ACL or plaintext connection.
3. Sets prefix-scoped lifecycle rules, not one blanket bucket-wide rule:
   - `uploads/*` and `status/*` expire after 48 h — transient per-job artifacts. Input videos are the large objects, so this keeps storage small.
   - `results/*` expires after **30 days**. This is a product decision: how long a user's analysis stays viewable in M3's job history and downloadable as CSV. Result files are a few KB, so the cost is negligible.
   - `code/*` and `experiments/*` never expire. M2's boot flow pulls `code/latest.zip` on every GPU launch (a 404 would stop the worker coming up), and M4 reads `experiments/*` for the final report.
4. Sets a CORS configuration allowing `POST` from `allowed_origins`. Terraform owns the whole CORS document, so there is no read-merge-write step: to add an origin later (M3's HTTPS domain), add it to the variable and apply. S3 CORS applies per bucket, not per prefix; the `uploads/` restriction is enforced by the `key` condition in the signed POST policy.
5. Creates the SQS queue (`queue_name`), visibility timeout **900 s**. M1's worker has no heartbeat, and two inference passes can take several minutes; with a shorter timeout a long job's message reappears mid-processing. M2 §8 adds the heartbeat and may lower this. Leave `# TODO(M2): add DLQ redrive policy` next to the queue resource.
6. Wires S3 `ObjectCreated:*` → the SQS queue, filtered to the `uploads/` prefix only, with the SQS queue policy that lets the bucket send messages (S3→SQS needs an explicit queue access policy; this is a common gotcha). Add `depends_on` so the notification is created after the queue policy. The prefix filter is required: later milestones write `status/`, `results/`, `code/` and `experiments/` objects to the same bucket, and an unfiltered notification would enqueue them as spurious jobs.
7. Outputs the bucket name and queue URL for `config.json`.

Terraform does **not** create IAM users or configure credentials. Run it with the `neurolens` AWS CLI profile (`AWS_PROFILE=neurolens`); never with another project's profile. `infra/terraform/README.md` says to check `aws sts get-caller-identity` first.

**Teardown:** `terraform destroy`. S3 refuses to delete a non-empty bucket, so the README documents emptying it first (`aws s3 rm s3://<bucket> --recursive`) once results and experiment files are no longer needed.

## 4. Flask: presigned URL endpoint
Add to `app.py` (do not remove `/api/analyse` yet — see §7):
```
POST /api/uploads/presign
Body: { "filename": "ad_variant_1.mp4", "content_type": "video/mp4", "client_duration_seconds": 27.4, "client_declared_bytes": 52428800 }

Success 200: {
  "job_id": "<uuid>",
  "url": "<S3 POST endpoint>",
  "fields": { "key": "uploads/placeholder-user/<uuid>.mp4", "policy": "<signed policy>", "x-amz-signature": "..." },
  "object_key": "uploads/placeholder-user/<uuid>.mp4",
  "expires_in": 300,
  "estimated_cost_usd": 0.90
}

Error 400 (bad content_type): { "error": "unsupported_content_type", "message": "..." }
Error 400 (duration too long): { "error": "duration_exceeds_max_estimated", "message": "...", "max_seconds": 120 }
Error 400 (file too large): { "error": "file_too_large", "message": "...", "max_bytes": <max_upload_bytes> }
Error 500 (S3/boto3 failure): { "error": "presign_failed", "message": "..." }
```
`job_id` is the same UUID used in the object's filename, returned as an explicit top-level field so the frontend and M2 polling have a stable identifier without parsing the storage key. Use `job_id` for all subsequent status/result calls.

- Validate `content_type` ∈ `{video/mp4, video/quicktime, video/webm}`
- Validate `client_duration_seconds` ≤ `MAX_DURATION` (imported from `inference.py`) — **this is a UX check only, not a security boundary**; the real duration check remains ffprobe on the server/worker side (see below), since client-reported duration can be wrong or spoofed
- `file_too_large` (400) is returned from `/api/uploads/presign` when `client_declared_bytes > max_upload_bytes`. This mirrors the existing `duration_exceeds_max_estimated` pattern: it's a UX-only rejection based on client-reported data, separate from and in addition to S3's own `content-length-range` enforcement and the worker's `head_object` backstop. If `client_declared_bytes` is absent or clearly inconsistent with reality, the request may still proceed to the POST-policy and worker-side checks, which are the only checks that cannot be spoofed.
- Cost estimate: `math.ceil(client_duration_seconds / 30) * 0.90`, returned for display only — no debit, no reservation logic in M1
- Object key: `uploads/placeholder-user/{uuid4()}{suffix}` — the `placeholder-user` segment is a literal TODO marker; when M3 adds auth, this becomes the real user ID. Leave a `# TODO(M3): replace placeholder-user with authenticated user_id` comment at the exact line
- Use `boto3.client('s3', region_name=CFG['aws']['region']).generate_presigned_post(...)`.

## 4a. Upload size limit via presigned POST
`/api/uploads/presign` switches from generating a presigned PUT URL to generating a **presigned POST** (`s3_client.generate_presigned_post(...)`), because S3's hard, server-enforced byte-size cap (`content-length-range`) is only available as a condition embedded in a POST policy — there is no equivalent bucket-policy mechanism that caps the size of an arbitrary presigned PUT. Reference: AWS's presigned-POST policy documentation.

Add `max_upload_bytes` to `config.json`/`config.sample.json` as a top-level field alongside `max_video_duration_seconds` (e.g. 300000000 for ~300 MB, comfortably above the report's stated 50–250 MB creative range).

This subsection supersedes and replaces the PUT-based instructions elsewhere in this spec. The response schema in §4 uses `url` and `fields`, not `upload_url`; the presign implementation uses `generate_presigned_post(...)`; and the frontend uses the multipart form POST described below. Do not leave both the old PUT instructions and this section's POST instructions in the document — an implementer reading either in isolation must land on one consistent flow.

1. **Presign endpoint response shape.** Return `url` (the bucket endpoint to POST to) and a `fields` object containing the signed form fields — including `policy`, signature, and `key` — with the policy embedding the `content-length-range` condition that bounds the upload to `max_upload_bytes`.
2. **CORS.** The §3 CORS configuration permits `POST` from every origin in `allowed_origins`.
3. **Frontend upload.** After receiving `{ url, fields }` from presign, construct a `FormData` object, append every key from `fields` first (the file field must be appended last), append the file itself under the `file` key, and POST that `FormData` to `url`. Update the upload-progress wiring (`XMLHttpRequest.upload.onprogress`, per §6) to work against this POST request. Treat a `204` response as success.
4. **Worker-side backstop.** Before calling `s3.download_file`, call `s3.head_object(Bucket=..., Key=...)` and check its `ContentLength` against `max_upload_bytes`. If it exceeds the cap, reject immediately — log, delete the object, delete the SQS message — and never call `download_file`, ffprobe, or inference. This remains necessary as a backstop even with the POST policy's `content-length-range` enforcement, in case that condition is ever misconfigured or bypassed.
5. **Frontend pre-check.** Read `file.size` before calling `/api/uploads/presign` and reject client-side (UI message only) if it exceeds `max_upload_bytes`, using the existing duration/MIME-check pattern in §6.

## 5. worker.py
New file, same directory as `app.py`:
- Imports from `inference.py` (triggers model load at startup — log this clearly, it will take the same 2-5 min the notebook documents on first run)
- boto3 SQS client, long-polls `receive_message(QueueUrl=..., WaitTimeSeconds=20, MaxNumberOfMessages=1)`
- Parses the S3 event from the message body (note: SQS message body **is** the raw S3 event JSON when S3 publishes directly to SQS — don't assume an SNS wrapper unless one was added)
- Before downloading, call `s3.head_object(Bucket=..., Key=...)` and reject immediately if `ContentLength > max_upload_bytes`: log, delete the object, delete the SQS message, and never call `download_file`, ffprobe, or inference.
- Downloads the object via `s3.download_file(bucket, key, local_tmp_path)` only after that size check passes.
- Runs `ffprobe` on the downloaded file (reuse the same subprocess call pattern already in `app.py`'s `/api/analyse` — this is the real, authoritative duration check) and rejects (log + delete message, do not process) if it exceeds `MAX_DURATION`
- Calls `run_inference` → `strip_audio` → `run_inference` (no-audio pass) → `extract_engagement`, exactly mirroring the current `/api/analyse` sequence
- Writes result JSON to local `outputs/{object_key_uuid}.json` (a flat local folder is fine for M1 — S3 output storage and Aurora pointers are M3)
- On success: `delete_message`
- On any exception: log full traceback, do **not** delete the message (let SQS redeliver after the visibility timeout — no DLQ wiring required this milestone; the `# TODO(M2)` sits on the Terraform queue resource, §3)
- Runs as a plain `python worker.py` long-running process, separate terminal/process from `python app.py` — no supervisor/systemd unit needed yet
- In M1 the worker runs on a laptop with `FAKE_INFERENCE=1` (§1a), against the real S3 bucket and SQS queue. Every result it writes carries `"fake_inference": true`.

## 6. Frontend (`static/`)
- Add duration read via the browser's `<video>` element `loadedmetadata` event (client-side only, not authoritative — see §4)
- Show estimated cost using the same `Math.ceil(duration/30)*0.90` formula as the backend, before calling `/api/uploads/presign`
- Reject client-side (UI message only) if duration > 120s, `file.size > max_upload_bytes`, or file type is not in the accepted set; send `file.size` as `client_declared_bytes` in the presign request body alongside `client_duration_seconds`.
- On confirm: `POST /api/uploads/presign` → construct a `FormData` object from the returned `url` and `fields`, appending every signed field before appending the file under the `file` key, then POST it to `url`.
- Show upload progress via `XMLHttpRequest.upload.onprogress` (fetch doesn't expose upload progress in most browsers)
- On POST success (S3 returns 204 with empty body): show "Uploaded — processing" static message, no polling (M2 adds job status polling)

## 6a. Local setup and automated tests

**Local setup (safe for a laptop).**
- Work inside a project virtualenv: `python3.11 -m venv .venv && source .venv/bin/activate`. Add `.venv/` to `.gitignore`. Nothing installs into system Python, and deleting `.venv/` removes it all.
- Fake mode needs only the web/CPU dependencies plus `nilearn` and `boto3`; it never downloads the ~20 GB model weights. Install `ffmpeg` (includes `ffprobe`) with Homebrew.
- The Flask dev server binds to `127.0.0.1` only.

**Automated tests** in `tests/`, run with `FAKE_INFERENCE=1 pytest`. Add `requirements-dev.txt` with `pytest` and `moto[s3,sqs]`. `moto` fakes S3 and SQS in memory, so tests make no real AWS calls and cost nothing. Only the manual end-to-end check (§9) touches the real bucket and queue, using the `neurolens` profile.

Required tests:
- **Cost formula:** `math.ceil(d / 30) * 0.90` for 0.5 s, 30 s, 30.1 s, 120 s. Note in a comment that `static/` mirrors this formula and must be changed together.
- **Presign endpoint** (Flask test client + moto): rejects a bad content type, an over-long duration and an oversize file with the documented error codes; on success returns `job_id`, `url`, `fields`, and a key under `uploads/placeholder-user/` that contains the `job_id`.
- **S3 event parsing:** a message with several `Records` is handled record by record; records outside `uploads/` are ignored.
- **Worker handling** (moto): an object over `max_upload_bytes` is deleted along with its message, and `download_file` / inference are never called; a video whose ffprobe duration exceeds `MAX_DURATION` is rejected without inference; a valid video produces a result JSON matching the `/api/analyse` schema plus `"fake_inference": true`.
- **Golden test** for `extract_engagement` (§1b layer 1).

## 7. Migration of the old endpoint
Keep `/api/analyse` working through M1 (don't break the existing notebook-validated demo path) but mark it clearly as deprecated:
```python
# TODO(M1-cleanup): remove this endpoint once the presign+SQS+worker path
# is verified end-to-end (see M1 spec §9 acceptance criteria). Do not
# maintain both paths past this milestone.
```
Do not delete it until acceptance criteria in §9 all pass — you want a known-good fallback while debugging the new path.

## 8. File layout after M1
```
neurolens/
├── app.py              # Flask: /, /api/samples, /data/<file>, /api/uploads/presign, (deprecated) /api/analyse
├── inference.py         # NEW — model/atlas loading, FAKE_INFERENCE switch, run_inference, strip_audio, extract_engagement, MAX_DURATION
├── worker.py            # NEW — SQS poll loop, ffprobe validation, calls inference.py, writes outputs/
├── infra/
│   └── terraform/       # NEW — bucket, queue, notification, lifecycle, CORS; README with state bootstrap + teardown
├── tests/                # NEW — pytest suite (§6a), fixtures/extract_engagement_golden.json
├── outputs/              # NEW — local worker output JSONs (gitignored)
├── config.json           # extended with "aws" block (gitignored, unchanged pattern)
├── config.sample.json    # extended with "aws" placeholder block
├── static/                # frontend: presign+direct-upload flow added
├── requirements.txt       # add: boto3
├── requirements-dev.txt   # NEW — pytest, moto[s3,sqs]
└── (unchanged) data/, batch_process.ipynb, explore.ipynb
```

## 9. Acceptance criteria
1. `inference.py` extraction is behavior-preserving: the `extract_engagement` golden test (§1b layer 1) was created before the move and passes unchanged after it. (Model loading is checked by §1b layers 2–3.)
2. `terraform apply` against the real AWS account creates the bucket and queue and outputs their identifiers; running `terraform apply` again reports no changes.
3. `config.json`'s `aws` block, once filled with those identifiers, is all `app.py`/`worker.py` need to run — no other code changes required to point at the real resources.
4. Selecting a video in the browser shows duration + estimated cost; files >120s or wrong MIME are rejected before any network call.
5. `POST /api/uploads/presign` returns a working presigned POST (`url` + `fields`); a browser form POST using those fields lands the object in the real S3 bucket, visible via `aws s3 ls`.
6. That S3 upload produces a visible SQS message within seconds (`aws sqs receive-message` manually, or watch `worker.py`'s logs).
7. `FAKE_INFERENCE=1 python worker.py`, left running, picks up that message, runs ffprobe + both (fake) inference passes, and writes a result JSON to `outputs/` matching the schema `/api/analyse` already returns, plus `"fake_inference": true`.
8. A video whose real (ffprobe-measured) duration exceeds 120s is rejected by the worker even if the client-side estimate was under 120s (proves the two checks are independent, per §4).
9. Lifecycle rules match §3: `uploads/` and `status/` 48 h, `results/` 30 days, no expiry on `code/` or `experiments/` — inspect with `aws s3api get-bucket-lifecycle-configuration`.
10. The account-wide $40 budget with 50/80/100% alerts was confirmed to exist before the first `terraform apply`.
11. A file exceeding `max_upload_bytes` is rejected before any network call on the client side; a POST attempting to upload a file larger than the signed `content-length-range` condition is rejected by S3 itself (with a 4xx response) before the object lands in the bucket; and, as a final backstop, a file that somehow bypasses both prior checks is rejected by the worker via `head_object`, before any `download_file` call, ffprobe run, or inference.

12. `FAKE_INFERENCE=1 pytest` passes, covering every test listed in §6a.
13. `terraform destroy` (after emptying the bucket) removes every M1 resource; the Terraform state bucket remains.

**Deferred to M2 (not an M1 gate):** on M2's first GPU boot, one real-model job through S3 → SQS → worker on a sample video, compared against that video's entry in `data/samples.json` (§1b layer 3).

## 10. Explicitly not in this milestone
No auth, no billing/credit debiting, no GPU (all M1 work runs in fake mode), no GPU containerization/AMI, no ASG, no VPC or NAT, no Aurora, no result persistence beyond local `outputs/`, no DLQ, no job-status polling UI. All of these are named in M2/M3 in the roadmap — don't let the agent pull them forward "for completeness."
