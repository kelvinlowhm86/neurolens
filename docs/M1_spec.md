# NeuroLens — M1 Implementation Spec (v2)
**Milestone:** Cloud Storage & Task Queue Decoupling (29 Sept – 8 Oct)
**Grounded in:** actual current `app.py` (300 lines), `config.sample.json`, `requirements.txt` from `kelvinlowhm86/neurolens` as of this spec's writing. If the repo has changed since, re-verify assumptions below before implementing.

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
# within the SQS visibility-timeout window (see M2 §8). This is a hard
# product/cost constraint, independent of billing — it must be enforced
# regardless of credit balance (see M3 §4 Stage 2, which must check this
# BEFORE any credit-adjustment logic, not instead of it).
```
- Expose `MAX_DURATION` (read from config) as a module-level constant

**`app.py` becomes:** `from inference import run_inference, strip_audio, extract_engagement, MAX_DURATION` — the Flask route logic (request parsing, temp file handling, jsonify) stays in `app.py`, unchanged in behavior.

**`worker.py` (new):** `from inference import run_inference, strip_audio, extract_engagement, MAX_DURATION` — importing `inference.py` triggers model/atlas loading once at worker startup, same as it currently does for the Flask process. This is intentional: the worker needs the model loaded exactly once, at process start, not per-job.

Do not change any function signatures or behavior during this extraction — it's a pure move, not a rewrite. Verify by running the existing `/api/analyse` path after the refactor and confirming identical output to before.

## 2. Config approach
Extend `config.json` / `config.sample.json` (same gitignored-secrets pattern already in use) with a new top-level `"aws"` block:
```json
{
  "hf_token": "...",
  "paths": { "...": "..." },
  "model": { "...": "..." },
  "aws": {
    "region": "ap-southeast-1",
    "s3_bucket": "REPLACE_ME",
    "sqs_queue_url": "REPLACE_ME"
  },
  "hf_download_timeout": 300,
  "max_video_duration_seconds": 120,
  "max_upload_bytes": 300000000
}
```
**Do not put AWS access keys in this file.** boto3 must use the default credential chain (`~/.aws/credentials`, `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` env vars, or an instance role in later milestones) — only resource identifiers (bucket name, queue URL, region) go in config. Update `config.sample.json` with placeholder values so the schema is documented; actual values go only in the gitignored `config.json`, which I will fill in myself after the resources exist.

## 3. AWS resource provisioning
Create `infra/provision_m1.sh` — a plain AWS CLI shell script (not Terraform, per current scope), idempotent where reasonable (check-before-create), that:
Before running it, configure two AWS Budget alerts for this project/account: $10 and $20 monthly thresholds, each notifying an email address or SNS topic on breach. This one-time guardrail must exist before any billable M1 resource is created.
1. Creates the S3 bucket (name from an env var the script reads, e.g. `NEUROLENS_BUCKET_NAME`, region `ap-southeast-1`)
2. Immediately after creating the bucket, apply baseline security settings, before any lifecycle/CORS/notification configuration:
   - Enable S3 Block Public Access (all four settings) on the bucket.
   - Set Object Ownership to 'Bucket owner enforced' (disables ACLs entirely — the presigned-URL upload flow does not use or need ACLs).
   - Enable default server-side encryption (SSE-S3 is sufficient for this project; SSE-KMS is a reasonable upgrade but not required).
   - Attach a bucket policy statement that denies any request where `aws:SecureTransport` is `false`, ensuring all access — including the presigned POST URL and any GET — must use HTTPS.
   None of these settings change behavior for direct browser uploads via presigned URLs, since access is via a signed request rather than a public ACL or plaintext connection.
3. Sets prefix-scoped lifecycle rules, not one blanket bucket-wide rule:
   - `uploads/*`, `claims/*`, and `status/*` expire after 48h — these are transient per-job artifacts with no reason to persist once a job is long finished.
   - `results/*` also expires after 48h, matching the input-video window — but flag explicitly that this window is a product decision (how long a user's analysis result remains downloadable via `GET /api/jobs/{job_id}/result` and the CSV export, M3 §7), not incidental inheritance from the upload-cleanup rule. Revisit it if product requirements call for longer result retention.
   - `code/*` is explicitly excluded from expiration. M2's boot/scale-out flow depends on `code/latest.zip` persisting: every ASG instance launch pulls it via UserData (M2 §4). If it expired, a scale-out after inactivity would fail `aws s3 cp` with a 404 and, because UserData is fail-fast, never bring the worker up.
4. Sets a CORS policy allowing `POST` from one or more origins passed via `NEUROLENS_DEV_ORIGIN` (comma-separated, e.g. `http://localhost:5003`). Because AWS's `put-bucket-cors` call replaces the entire CORS configuration in one shot (there is no partial-update API), the script must first call `get-bucket-cors` to read whatever rules currently exist, merge in only the intended new origin(s) without dropping any existing rule, method, or header entry, and then write back the full merged document. On a brand-new bucket (as in a first run of this script), `get-bucket-cors` returns a `NoSuchCORSConfiguration` error rather than an empty rule set — the script must catch this specific error and treat it as an empty starting rule set, then write the first CORS rule from that empty base, rather than treating the error as a failure. Never write a CORS document containing only the new origin(s) when unrelated existing rules were present. S3 CORS rules apply per-origin at the bucket level, not per key-prefix; the `uploads/` restriction is enforced separately by the `key` condition inside the signed POST policy, not by CORS.
5. Creates the SQS queue (`NEUROLENS_QUEUE_NAME`), visibility timeout 120s
6. Wires S3 `ObjectCreated:*` → the SQS queue directly, filtered to the `uploads/` key prefix only (bucket notification configuration pointing at the queue ARN, with the corresponding SQS queue policy granting the bucket permission to send messages — S3→SQS needs an explicit queue access policy, this is a common gotcha, don't skip it). This prefix filter is required: M2 writes `status/`, `claims/`, `results/`, and `code/` objects to the same bucket, and an unfiltered notification would re-enqueue those writes as spurious jobs.
7. Prints the resulting bucket name and queue URL at the end so they can be copied into `config.json`

Script should **not** create IAM users/roles or attempt to configure credentials — it assumes valid AWS CLI credentials are already active in the shell it's run from (`aws sts get-caller-identity` should succeed before running it; have the script check this first and exit with a clear message if not).

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
2. **CORS.** The §3 CORS step permits `POST` for the configured origin(s), using its existing read-merge-write approach.
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
- On any exception: log full traceback, do **not** delete the message (let SQS redeliver / eventually it ages past visibility timeout — no DLQ wiring required this milestone, but leave `# TODO(M2): add DLQ redrive policy` at the queue provisioning script)
- Runs as a plain `python worker.py` long-running process, separate terminal/process from `python app.py` — no supervisor/systemd unit needed yet

## 6. Frontend (`static/`)
- Add duration read via the browser's `<video>` element `loadedmetadata` event (client-side only, not authoritative — see §4)
- Show estimated cost using the same `Math.ceil(duration/30)*0.90` formula as the backend, before calling `/api/uploads/presign`
- Reject client-side (UI message only) if duration > 120s, `file.size > max_upload_bytes`, or file type is not in the accepted set; send `file.size` as `client_declared_bytes` in the presign request body alongside `client_duration_seconds`.
- On confirm: `POST /api/uploads/presign` → construct a `FormData` object from the returned `url` and `fields`, appending every signed field before appending the file under the `file` key, then POST it to `url`.
- Show upload progress via `XMLHttpRequest.upload.onprogress` (fetch doesn't expose upload progress in most browsers)
- On POST success (S3 returns 204 with empty body): show "Uploaded — processing" static message, no polling (M2 adds job status polling)

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
├── inference.py         # NEW — model/atlas loading, run_inference, strip_audio, extract_engagement, MAX_DURATION
├── worker.py            # NEW — SQS poll loop, ffprobe validation, calls inference.py, writes outputs/
├── infra/
│   └── provision_m1.sh  # NEW — AWS CLI bucket+queue+event-notification setup
├── outputs/              # NEW — local worker output JSONs (gitignored)
├── config.json           # extended with "aws" block (gitignored, unchanged pattern)
├── config.sample.json    # extended with "aws" placeholder block
├── static/                # frontend: presign+direct-upload flow added
├── requirements.txt       # add: boto3
└── (unchanged) data/, batch_process.ipynb, explore.ipynb
```

## 9. Acceptance criteria
1. `inference.py` extraction is behavior-preserving: hitting `/api/analyse` post-refactor produces byte-identical JSON (modulo timing fields) to pre-refactor, on the same input video.
2. `bash infra/provision_m1.sh` run against a real AWS account creates the bucket and queue and prints their identifiers; a second run doesn't fail or duplicate resources.
3. `config.json`'s `aws` block, once filled with those identifiers, is all `app.py`/`worker.py` need to run — no other code changes required to point at the real resources.
4. Selecting a video in the browser shows duration + estimated cost; files >120s or wrong MIME are rejected before any network call.
5. `POST /api/uploads/presign` returns a working presigned POST (`url` + `fields`); a browser form POST using those fields lands the object in the real S3 bucket, visible via `aws s3 ls`.
6. That S3 upload produces a visible SQS message within seconds (`aws sqs receive-message` manually, or watch `worker.py`'s logs).
7. `python worker.py`, left running, picks up that message, runs ffprobe + both inference passes, and writes a result JSON to `outputs/` matching the schema `/api/analyse` already returns.
8. A video whose real (ffprobe-measured) duration exceeds 120s is rejected by the worker even if the client-side estimate was under 120s (proves the two checks are independent, per §4).
9. Objects under `code/` persist past the 48h window that applies to `uploads/`, `claims/`, `status/`, and `results/` — verify with `aws s3 ls` after 48+ hours, or inspect the per-prefix rules with `aws s3api get-bucket-lifecycle-configuration`.
10. Two AWS Budget alerts exist ($10 and $20 thresholds) and were confirmed configured before any of M1's provisioning steps ran.
11. A file exceeding `max_upload_bytes` is rejected before any network call on the client side; a POST attempting to upload a file larger than the signed `content-length-range` condition is rejected by S3 itself (with a 4xx response) before the object lands in the bucket; and, as a final backstop, a file that somehow bypasses both prior checks is rejected by the worker via `head_object`, before any `download_file` call, ffprobe run, or inference.

## 10. Explicitly not in this milestone
No auth, no billing/credit debiting, no GPU containerization/AMI, no ASG, no Aurora, no result persistence beyond local `outputs/`, no DLQ, no job-status polling UI. All of these are named in M2/M3 in the roadmap — don't let the agent pull them forward "for completeness."
