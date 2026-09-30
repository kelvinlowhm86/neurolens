# NeuroLens — M1 Implementation Spec
**Milestone:** Cloud Storage & Task Queue Decoupling (29 Sept – 8 Oct)
**Builds on:** M0 (the `neurolens/` package, tests-first suite, CI). Re-read `AGENTS.md` and the M0 layout before implementing.

**Ground rules for all of M1:** everything runs in AWS region `us-east-1` (the `g6e` GPU family used from M2a onward is not offered in Singapore, so do not "correct" the region). All M1 work runs without a GPU, in fake-model mode (§1a). Python 3.12, as in M0.

**Tests first, as in M0 §5:** a separate agent writes every test in §6a from this spec before the implementation exists; the implementer may not edit them and asks Josh if one looks wrong. The break-it check (M0 §8) is repeated for the worker's size and duration rejections.

## 0. What exists today (after M0)
- `neurolens/settings.py` (config, paths, env vars, `max_duration(cfg)`, host/port), `neurolens/engagement.py` (pure maths), `neurolens/inference.py` (`load_model()`, `run_inference`, `strip_audio`, `gpu_info`), `neurolens/web/app.py` (`create_app()`: `GET /`, `GET /api/samples`, `GET /data/<path>`, `POST /api/analyse`). The root `app.py` is a launcher.
- `POST /api/analyse` still runs the model inside the web process (the temporary M0 exception) and does a real ffprobe duration check. **That check is different from M1's client-side estimate**; keep both, they serve different purposes (§4).
- The golden test, import-hygiene test and CI (ruff + pytest, no `torch`) already exist and must stay green.

## 1. Worker entry point and shared S3 helpers
M0 already separated model loading from the web app. M1 adds:
- **`neurolens/worker.py`**: the SQS poll loop (§5). Root **`worker.py`** is a launcher: `from neurolens.worker import run; run()`. The worker calls `neurolens.inference.load_model()` **once at startup**, not per job.
- **`neurolens/storage.py`**: boto3 helpers used by both the web app and the worker. No `torch`, no `neurolens.inference` import.
- **`neurolens/pricing.py`**: the cost formula (§4), so web, worker and billing share one definition.
- `run_inference`, `strip_audio` and `extract_engagement` keep their M0 signatures.

Place this comment above `max_duration` in `neurolens/settings.py`:
```python
# The max duration reflects the target use case (short-form pre-roll / social ad
# creative) and bounds GPU job duration for predictable cost and to stay
# within the SQS visibility-timeout window (900 s in M1; M2b adds a
# heartbeat). This is a hard
# product/cost constraint, independent of billing — it must be enforced
# regardless of credit balance (see M3a §4a `verify`, which must check this
# BEFORE any credit-adjustment logic, not instead of it).
```

### Interfaces fixed by this spec (the §6a tests are written against exactly these)
- `neurolens.pricing.estimate_cost_cents(duration_seconds: float) -> int`: `90 * max(1, ceil((d - 0.5) / 30))`, i.e. $0.90 per started 30-second block, with a half-second allowance so a 30.02 s ad is one block. `estimate_cost_usd(d) -> float` is `estimate_cost_cents(d) / 100`, for display.
- `neurolens.storage`:
  - `presign_upload(s3, bucket, content_type, max_bytes, expires_in=300) -> dict` with keys `job_id`, `url`, `fields`, `object_key`, `expires_in`. Key is `uploads/placeholder-user/{job_id}{ext}`, where `ext` comes from `content_type` (`video/mp4` → `.mp4`, `video/quicktime` → `.mov`, `video/webm` → `.webm`), never from the user's filename.
  - `parse_s3_event(body: str) -> list[tuple[str, str]]`: `(bucket, key)` for every record, keys outside `uploads/` dropped. A body without `Records` (S3 sends `{"Event": "s3:TestEvent", ...}` once when the notification is created) returns `[]`.
  - `job_id_from_key(key: str) -> str`.
- `neurolens.inference`:
  - `probe_duration(path) -> float`: the ffprobe call currently inside `/api/analyse`, moved here so web and worker share it.
  - `roi_masks() -> dict[str, ndarray]`: the masks loaded by `load_model()`.
  - `fake_mode() -> bool`: reads `FAKE_INFERENCE` from the environment **at call time** (so tests can set it with `monkeypatch`). `"1"`, `"true"`, `"yes"` mean on.
- `neurolens.worker`:
  - `Outcome`: an enum of final record outcomes, `DONE` and `REJECTED` (oversize or too long; the object is deleted). M2a and M2b add values. Any failure is an exception, not an outcome.
  - `handle_record(bucket, key, *, s3, cfg, roi_masks) -> Outcome`: never touches SQS.
  - `process_message(message, *, s3, sqs, cfg, roi_masks) -> None`: calls `handle_record` for every record from `parse_s3_event`; deletes the message only if every record returned an `Outcome` (a message with zero records, such as the test event, is deleted). If any record raised, it logs the traceback and leaves the message for redelivery.
  - `run() -> None`: loads config and model, builds clients, polls forever.
  - AWS clients are passed in as parameters so tests can hand in `moto` clients.
- **Result JSON** (M1 writes it locally, M2a to S3): `duration_seconds` and `timesteps` exactly as `extract_engagement` returns them, plus `job_id`, `processing_time_seconds`, `fake_inference` (present and `true` only in fake mode) and `gpu` (present only when `gpu_info()` returns a value). There is no `filename` field; the browser already knows it.
- `neurolens.web.app.create_app(..., cfg: dict | None = None)`: when `cfg` is given it is used instead of reading `config.json`, so tests need no config file. The S3 client (and the presign route's ability to succeed) exists only when the config has an `aws` block; without one, `/api/uploads/presign` returns 500 `presign_failed`, and the M0 web tests keep working unchanged.

### 1a. Fake-model switch (`FAKE_INFERENCE`)
`neurolens.inference` checks `fake_mode()` (an environment variable only; there is no config field, so a stale config file can never leave a real deployment in fake mode). When it is on:
- `load_model()` does **not** import `torch` or `tribev2` and does not load TRIBE v2. Those imports are already inside functions (M0), so fake mode runs on a laptop without them installed.
- `load_model()` **does** still set the HF/nilearn environment variables and load the Destrieux atlas and ROI masks exactly as in real mode (CPU-only, small download).
- `run_inference(video_path)` measures the video with `probe_duration` and returns a seeded random `numpy` array of shape `(ceil(duration), 20484)`, seeded from the file's size so the same video gives the same output. It works even if `load_model()` was never called, so tests need no atlas download.
- `strip_audio`, `extract_engagement` and everything downstream run for real, unchanged.
- Log a clear `FAKE_INFERENCE is ON — results are not real model output` line at startup, and add `"fake_inference": true` to every result JSON produced in this mode.

### 1b. Proving the code is safe to run for real — three layers
Fake mode does **not** exercise model loading, so no single check proves it. Use all three:
1. **Golden test (done in M0).** It must keep passing unchanged.
2. **Real-import check (optional, laptop CPU).** In a separate venv with `requirements/model.txt` installed, call `neurolens.inference.load_model()` in real mode far enough to set the HF environment variables and run `TribeModel.from_pretrained(...)` (~1 GB checkpoint), without calling `predict()`. This exercises the fragile "set env vars before any HF import" ordering and the dependency pins M0 chose. `tribev2` on macOS/CPU is untested; if it will not install, skip this layer and rely on layer 3.
3. **Real run (required, deferred to M2a).** On M2a's first GPU boot, run one real inference through S3 → SQS → worker on a sample video and compare the result with that video's entry in `data/samples.json` (produced by the original pipeline), allowing for small floating-point differences. This is the final proof.

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
   - `code/*` and `experiments/*` never expire. M2a's boot flow pulls `code/latest.zip` on every GPU launch (a 404 would stop the worker coming up), and M4 reads `experiments/*` for the final report.
4. Sets a CORS configuration allowing `POST` from `allowed_origins`. Terraform owns the whole CORS document: to add an origin later (M3's HTTPS domain), add it to the variable and apply. S3 CORS applies per bucket, not per prefix; the `uploads/` restriction is enforced by the `key` condition in the signed POST policy.
5. Creates the SQS queue (`queue_name`), visibility timeout **900 s**. M1's worker has no heartbeat, and two inference passes can take several minutes; with a shorter timeout a long job's message reappears mid-processing. M2b adds the heartbeat and lowers this. Leave `# TODO(M2b): add DLQ redrive policy` next to the queue resource.
6. Wires S3 `ObjectCreated:*` → the SQS queue, filtered to the `uploads/` prefix only, with the SQS queue policy that lets the bucket send messages (S3→SQS needs an explicit queue access policy; this is a common gotcha). Add `depends_on` so the notification is created after the queue policy. The prefix filter is required: later milestones write `status/`, `results/`, `code/` and `experiments/` objects to the same bucket, and an unfiltered notification would enqueue them as spurious jobs.
7. Outputs the bucket name and queue URL for `config.json`.

Terraform does **not** create IAM users or configure credentials. Run it with the `neurolens` AWS CLI profile (`AWS_PROFILE=neurolens`); never with another project's profile. `infra/terraform/README.md` says to check `aws sts get-caller-identity` first.

**Teardown:** `terraform destroy`. S3 refuses to delete a non-empty bucket, so the README documents emptying it first (`aws s3 rm s3://<bucket> --recursive`) once results and experiment files are no longer needed.

## 4. Flask: presigned URL endpoint
Add to `neurolens/web/app.py`, using `neurolens.storage.presign_upload` (do not remove `/api/analyse` yet — see §7):
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
- Validate `client_duration_seconds` ≤ `max_duration(cfg)` — **this is a UX check only, not a security boundary**; the real duration check remains ffprobe on the server/worker side (see below), since client-reported duration can be wrong or spoofed
- `file_too_large` (400) is returned from `/api/uploads/presign` when `client_declared_bytes > max_upload_bytes`. This mirrors the existing `duration_exceeds_max_estimated` pattern: it's a UX-only rejection based on client-reported data, separate from and in addition to S3's own `content-length-range` enforcement and the worker's `head_object` backstop. If `client_declared_bytes` is absent or clearly inconsistent with reality, the request may still proceed to the POST-policy and worker-side checks, which are the only checks that cannot be spoofed.
- Cost estimate: `neurolens.pricing.estimate_cost_usd(client_duration_seconds)`, returned for display only — no debit, no reservation logic in M1
- Object key: `uploads/placeholder-user/{uuid4()}{ext}` (extension from `content_type`, §1) — the `placeholder-user` segment is a literal TODO marker; when M3 adds auth, this becomes the real user ID. Leave a `# TODO(M3): replace placeholder-user with authenticated user_id` comment at the exact line
- `create_app` builds one `boto3.client('s3', region_name=cfg['aws']['region'])` and `presign_upload` calls `generate_presigned_post(...)` on it.

## 4a. Upload size limit via presigned POST
`/api/uploads/presign` generates a **presigned POST** (`s3_client.generate_presigned_post(...)`), never a presigned PUT: S3's server-enforced byte-size cap (`content-length-range`) exists only as a condition in a POST policy. Reference: AWS's presigned-POST policy documentation.

Add `max_upload_bytes` to `config.json`/`config.sample.json` as a top-level field alongside `max_video_duration_seconds` (e.g. 300000000 for ~300 MB, comfortably above the report's stated 50–250 MB creative range).

1. **Presign endpoint response shape.** Return `url` (the bucket endpoint to POST to) and a `fields` object containing the signed form fields — including `policy`, signature, and `key` — with the policy embedding the `content-length-range` condition that bounds the upload to `max_upload_bytes`.
2. **CORS.** The §3 CORS configuration permits `POST` from every origin in `allowed_origins`.
3. **Frontend upload.** After receiving `{ url, fields }` from presign, construct a `FormData` object, append every key from `fields` first (the file field must be appended last), append the file itself under the `file` key, and POST that `FormData` to `url`. Update the upload-progress wiring (`XMLHttpRequest.upload.onprogress`, per §6) to work against this POST request. Treat a `204` response as success.
4. **Worker-side backstop.** Before calling `s3.download_file`, call `s3.head_object(Bucket=..., Key=...)` and check its `ContentLength` against `max_upload_bytes`. If it exceeds the cap, reject immediately — log, delete the object, return `Outcome.REJECTED` (so `process_message` deletes the message) — and never call `download_file`, ffprobe, or inference. This remains necessary as a backstop even with the POST policy's `content-length-range` enforcement, in case that condition is ever misconfigured or bypassed.
5. **Frontend pre-check.** Read `file.size` before calling `/api/uploads/presign` and reject client-side (UI message only) if it exceeds `max_upload_bytes`, using the existing duration/MIME-check pattern in §6.

## 5. Worker (`neurolens/worker.py`, launched by root `worker.py`)
- Calls `neurolens.inference.load_model()` at startup (log this clearly; in real mode it takes the same 2–5 min the notebook documents on first run)
- boto3 SQS client, long-polls `receive_message(QueueUrl=..., WaitTimeSeconds=20, MaxNumberOfMessages=1)`
- Parses the S3 event from the message body with `parse_s3_event` (the SQS message body **is** the raw S3 event JSON when S3 publishes directly to SQS — don't assume an SNS wrapper unless one was added)
- Before downloading, call `s3.head_object(Bucket=..., Key=...)` and reject immediately if `ContentLength > max_upload_bytes`: log, delete the object, return `Outcome.REJECTED`, and never call `download_file`, ffprobe, or inference.
- Downloads the object via `s3.download_file(bucket, key, local_tmp_path)` only after that size check passes.
- Runs `neurolens.inference.probe_duration` on the downloaded file (the real, authoritative duration check) and returns `Outcome.REJECTED` (log, delete the object, no inference) if it exceeds `max_duration(cfg)`
- Calls `run_inference` → `strip_audio` → `run_inference` (no-audio pass) → `extract_engagement`, exactly mirroring the current `/api/analyse` sequence
- Writes the result JSON (keys in §1) to `<output dir>/{job_id}.json`, where the output dir is `paths.output` from config (already gitignored), and returns `Outcome.DONE`. A local folder is fine for M1; M2a moves results to S3
- `process_message` deletes the message once every record has an outcome
- On any exception: log full traceback, do **not** delete the message (let SQS redeliver after the visibility timeout — no DLQ wiring required this milestone; the `# TODO(M2b)` sits on the Terraform queue resource, §3)
- Runs as a plain `python worker.py` long-running process, separate terminal/process from `python app.py` — no supervisor/systemd unit needed yet
- In M1 the worker runs on a laptop with `FAKE_INFERENCE=1` (§1a), against the real S3 bucket and SQS queue. Every result it writes carries `"fake_inference": true`.

## 6. Frontend (`static/`)
- Add duration read via the browser's `<video>` element `loadedmetadata` event (client-side only, not authoritative — see §4)
- Show estimated cost using the same formula as `estimate_cost_cents` (90 cents × `max(1, ceil((duration − 0.5) / 30))`), before calling `/api/uploads/presign`
- Reject client-side (UI message only) if duration > 120s, `file.size > max_upload_bytes`, or file type is not in the accepted set; send `file.size` as `client_declared_bytes` in the presign request body alongside `client_duration_seconds`.
- On confirm: `POST /api/uploads/presign` → construct a `FormData` object from the returned `url` and `fields`, appending every signed field before appending the file under the `file` key, then POST it to `url`.
- Show upload progress via `XMLHttpRequest.upload.onprogress` (fetch doesn't expose upload progress in most browsers)
- On POST success (S3 returns 204 with empty body): show "Uploaded — processing" static message, no polling (M2 adds job status polling)

## 6a. Local setup and automated tests

**Local setup (safe for a laptop).**
- Use the M0 venv (Python 3.12, `pip install -e . -r requirements/dev.txt`). Fake mode needs nothing more: `dev.txt` includes `worker.txt` (nilearn, boto3) but not `model.txt`, so the ~20 GB weights are never downloaded. Install `ffmpeg` (includes `ffprobe`) with Homebrew.
- Change the Flask dev server's host in `neurolens/settings.py` from `0.0.0.0` to `127.0.0.1`.
- Add `boto3` to `requirements/web.txt` and `requirements/worker.txt`, and `moto[s3,sqs]` to `requirements/dev.txt`.

**Automated tests** in `tests/`, run with `FAKE_INFERENCE=1 pytest` (CI sets the variable). `moto` fakes S3 and SQS in memory, so tests make no real AWS calls and cost nothing. Only the manual end-to-end check (§9) touches the real bucket and queue, using the `neurolens` profile. Tests that need a video generate a few-second clip with `ffmpeg -f lavfi` in a fixture (no binary files in git), so CI gains an `apt-get install ffmpeg` step. Worker tests pass tiny hand-made ROI masks to `handle_record`; nothing downloads the atlas.

Required tests:
- **Cost formula:** `estimate_cost_cents` for 0.2 s, 1 s, 30 s, 30.4 s (all 90), 30.6 s, 60 s (180) and 120 s (360). Note in a comment that `static/` mirrors this formula and must be changed together.
- **Presign endpoint** (Flask test client + moto): rejects a bad content type, an over-long duration and an oversize file with the documented error codes; on success returns `job_id`, `url`, `fields`, and a key under `uploads/placeholder-user/` that contains the `job_id`.
- **S3 event parsing:** a message with several `Records` is handled record by record; records outside `uploads/` are ignored; the `s3:TestEvent` body returns `[]` and its message is deleted.
- **Presign key:** the object key's extension follows `content_type`, even when the filename says otherwise.
- **Worker handling** (moto): an object over `max_upload_bytes` returns `REJECTED`, is deleted along with its message, and `download_file` / inference are never called; a video whose ffprobe duration exceeds `max_duration(cfg)` returns `REJECTED` without inference; a valid video returns `DONE` and produces a result JSON with exactly the §1 keys, including `"fake_inference": true`; a record that raises leaves the message undeleted.
- **Fake switch:** `fake_mode()` follows `monkeypatch.setenv`/`delenv` within one process.
- **Import hygiene** (extends M0's test): `neurolens.storage`, `neurolens.pricing` and `neurolens.worker` import without `torch`; `neurolens.storage` and `neurolens.pricing` also without `neurolens.inference`.
- **Golden test** for `extract_engagement` (M0) still passes unchanged.

## 7. Migration of the old endpoint
Keep `/api/analyse` working through M1 (don't break the existing notebook-validated demo path) but mark it clearly as deprecated:
```python
# TODO(M1-cleanup): remove this endpoint once the presign+SQS+worker path
# is verified end-to-end (see M1 spec §9 acceptance criteria). Do not
# maintain both paths past this milestone.
```
Do not delete it until acceptance criteria in §9 all pass — you want a known-good fallback while debugging the new path.

**Final M1 commit, after §9 passes:** remove `/api/analyse`, the `load_model` parameter of `create_app`, and the lazy `neurolens.inference` import inside it. From then on the web tier never imports `neurolens.inference` (the M0 temporary exception ends). In the same commit, the test-writing agent removes the tests for `/api/analyse`, drops the `load_model=False` argument from every `create_app` call in the suite, and extends the import-hygiene test to assert that importing and calling `create_app` never loads `neurolens.inference`. This is a spec'd test change, not the implementer editing tests.

## 8. File layout after M1 (additions to M0)
```
neurolens/
  storage.py        NEW: presign, S3 event parsing, job IDs
  pricing.py        NEW: estimate_cost_cents, estimate_cost_usd
  worker.py         NEW: SQS poll loop, size + ffprobe checks, writes <output>/{job_id}.json
  inference.py      MODIFIED: FAKE_INFERENCE, probe_duration, roi_masks()
  settings.py       MODIFIED: host 127.0.0.1, max-duration comment
  web/app.py        MODIFIED: /api/uploads/presign; /api/analyse removed at the end of M1
worker.py           NEW launcher
infra/terraform/    NEW: bucket, queue, notification, lifecycle, CORS; README with state bootstrap + teardown
tests/              MODIFIED: §6a tests
config.sample.json  MODIFIED: "aws" block, max_upload_bytes
static/             MODIFIED: presign + direct-upload flow
requirements/       MODIFIED: boto3 (web, worker), moto (dev)
```

## 9. Acceptance criteria
1. The M0 golden test and import-hygiene test still pass. (Model loading is checked by §1b layers 2–3.)
2. `terraform apply` against the real AWS account creates the bucket and queue and outputs their identifiers; running `terraform apply` again reports no changes.
3. `config.json`'s `aws` block, once filled with those identifiers, is all `app.py`/`worker.py` need to run — no other code changes required to point at the real resources.
4. Selecting a video in the browser shows duration + estimated cost; files >120s or wrong MIME are rejected before any network call.
5. `POST /api/uploads/presign` returns a working presigned POST (`url` + `fields`); a browser form POST using those fields lands the object in the real S3 bucket, visible via `aws s3 ls`.
6. That S3 upload produces a visible SQS message within seconds (`aws sqs receive-message` manually, or watch `worker.py`'s logs).
7. `FAKE_INFERENCE=1 python worker.py`, left running, picks up that message, runs ffprobe + both (fake) inference passes, and writes a result JSON to the output folder matching the schema `/api/analyse` already returns, plus `"fake_inference": true`.
8. A video whose real (ffprobe-measured) duration exceeds 120s is rejected by the worker even if the client-side estimate was under 120s (proves the two checks are independent, per §4).
9. Lifecycle rules match §3: `uploads/` and `status/` 48 h, `results/` 30 days, no expiry on `code/` or `experiments/` — inspect with `aws s3api get-bucket-lifecycle-configuration`.
10. The account-wide $40 budget with 50/80/100% alerts was confirmed to exist before the first `terraform apply`.
11. A file exceeding `max_upload_bytes` is rejected before any network call on the client side; a POST attempting to upload a file larger than the signed `content-length-range` condition is rejected by S3 itself (with a 4xx response) before the object lands in the bucket; and, as a final backstop, a file that somehow bypasses both prior checks is rejected by the worker via `head_object`, before any `download_file` call, ffprobe run, or inference.

12. `FAKE_INFERENCE=1 pytest` passes locally and in CI, covering every test listed in §6a, and the tests were committed before the implementation.
13. `/api/analyse` is gone and the web tier no longer imports `neurolens.inference` (§7).
14. `terraform destroy` (after emptying the bucket) removes every M1 resource; the Terraform state bucket remains.

**Deferred to M2a (not an M1 gate):** on M2a's first GPU boot, one real-model job through S3 → SQS → worker on a sample video, compared against that video's entry in `data/samples.json` (§1b layer 3).

## 10. Explicitly not in this milestone
No auth, no billing/credit debiting, no GPU (all M1 work runs in fake mode), no GPU containerization/AMI, no ASG, no VPC or NAT, no Aurora, no result persistence beyond the local output folder, no DLQ, no job-status polling UI. All of these are named in M2a/M2b/M3 in the roadmap — don't let the agent pull them forward "for completeness."

**Security note for M3:** `/api/uploads/presign` has no sign-in and `CORS(app)` allows any origin. That is acceptable only while Flask binds to `127.0.0.1`. Before any public web tier, M3 must require sign-in on presign and restrict CORS, or anyone could start GPU jobs.
