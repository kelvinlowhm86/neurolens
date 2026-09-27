# NeuroLens — M2 Implementation Spec
**Milestone:** GPU AMI Provisioning & Elastic Autoscaling (9 – 19 Oct)
**Builds on:** M1 (S3 presign upload, SQS decoupling, local `worker.py` running `inference.py` synchronously on one machine, SQS queue provisioned with a 120s visibility timeout). M2 replaces the worker's execution environment with an autoscaled EC2 Spot fleet and adds real job-status tracking so the frontend can poll something true.

## 1. Scope
### In scope
- AMI build script: base Deep Learning AMI + heavy deps (PyTorch/CUDA, `tribev2`, `ffmpeg`, model weights) baked in
- A systemd unit that runs `worker.py` directly on the host, inside a pre-baked Python virtualenv, started automatically on boot
- Launch Template + Auto Scaling Group on `g6e.2xlarge` Spot, CloudWatch target-tracking on SQS backlog
- UserData script: pulls the two application files (`worker.py`, `inference.py`) from an S3 code bundle, places them where the systemd unit expects them, (re)starts the service
- A `deploy_code.sh` convenience script (zip + `aws s3 cp` in one command) for pushing code updates without an AMI rebuild
- SQS DLQ wiring after two total processing attempts (`maxReceiveCount=2`)
- S3-based job-status mechanism so Flask can answer real status queries
- Atomic per-job claim via S3 conditional writes, preventing duplicate GPU runs from SQS at-least-once delivery
- **SQS visibility heartbeating and fast failure release** (§8) — replaces a single passive wait of up to the full 120s visibility timeout with an active, short-interval renewal, so a genuinely dead worker's job becomes retryable within roughly that same ~120s window instead of only being detectable after it silently lapses. Given NeuroLens's 'seconds to minutes' turnaround as a product claim, minimizing this detection latency matters directly to user experience.
- Graceful Spot-interruption handling in `worker.py`, using the same proactive-release mechanism as general failure handling
- Real client-side polling UI replacing the legacy fake progress timer
- Per-stage latency instrumentation (Experiment 1) and a basic Locust script (Experiment 2)

### Out of scope (explicitly deferred to M3)
- Aurora, real user accounts, credit balances/billing, SSO
- EKS/Kubernetes, any container runtime, image registries

## 2. AMI build (`infra/build_ami.sh`)
A script (AWS CLI + shell) that:
1. Launches a temporary `g6e.2xlarge` on-demand instance from an official AWS Deep Learning AMI (Ubuntu-based, NVIDIA driver + CUDA pre-installed — confirm the CUDA version matches what `torch` needs)
2. Uses SSM Run Command (no key pair needed for a throwaway build instance) to:
   - Create a Python virtualenv at a fixed path (e.g. `/opt/neurolens/venv`)
   - Pin `numpy` **before** installing `tribev2`, exactly as `README.md`'s setup steps specify — do not reorder
   - Install `requirements.txt`, then `tribev2[plotting]` via the documented `git+https://...` install, then `ffmpeg` via apt
   - Set the same `HF_HOME`/`HF_HUB_CACHE`/etc. env vars `inference.py` already sets, pointed at an on-instance directory (e.g. `/opt/neurolens/models`)
   - Run a one-off Python invocation that imports enough of `inference.py` to trigger `TribeModel.from_pretrained(...)` and the Destrieux atlas download, so weights land on disk during the build, not at every boot. This step needs a valid `HF_TOKEN` — pass it via an SSM `SecureString` parameter read at build time; delete any file containing it before the AMI snapshot step
3. Verifies the venv can run a short inference smoke test against a tiny sample clip
4. Stops the instance, `aws ec2 create-image`, tags it with a version (`neurolens-worker-v{n}`)
5. Terminates the temporary instance
6. Prints the resulting AMI ID

## 3. systemd unit (`infra/neurolens-worker.service`, baked into the AMI)
```ini
[Unit]
Description=NeuroLens GPU worker
After=network-online.target

[Service]
WorkingDirectory=/opt/neurolens/app
ExecStart=/opt/neurolens/venv/bin/python worker.py
Restart=on-failure
RestartSec=5
EnvironmentFile=/opt/neurolens/env.conf

[Install]
WantedBy=multi-user.target
```
- Baked into the AMI as a **disabled** unit — do not enable it at build time. UserData (§4) writes both `config.json` and `env.conf`, then enables and starts the service only once both are confirmed present.
- `env.conf` is created fresh by UserData on every boot, not baked — it holds instance-specific values (bucket name, queue URL, region). Never bake AWS resource identifiers into the AMI itself; that would force a rebuild every time a resource is recreated

## 4. Launch Template + ASG (`infra/provision_m2.sh`, extends M1's provisioning script)
- Launch Template referencing the AMI from §2, instance type `g6e.2xlarge`, Spot market options (max price = on-demand price as a safe default)
- IAM instance profile scoped explicitly per-prefix, not just per-bucket:
  - `s3:GetObject` on `uploads/*`, `code/*`, `claims/*`, `status/*`, and `results/*` (worker downloads videos, reads claims for staleness checks, and must check completed results before a duplicate claim; M3 also uses result reads for settlement reconciliation)
  - `s3:PutObject` on `claims/*`, `status/*`, `results/*`, and `experiments/*` (worker writes claims, status updates, final conditional results, and versioned experiment artifacts)
  - `s3:DeleteObject` on `claims/*` only (conditional release of the worker's own claim, §7/§8 — do not omit this)
  - `sqs:ReceiveMessage`/`DeleteMessage`/`ChangeMessageVisibility` on the job queue
  - `ssm:GetParameter` (plus `kms:Decrypt` on the relevant key, if the SecureString uses a customer-managed key) for `HF_TOKEN` and config retrieval
- UserData script (bash, embedded in the Launch Template), run on every boot, in this order:
  1. `set -euo pipefail`
  2. `aws s3 cp s3://<bucket>/code/latest.zip /tmp/code.zip && unzip -o /tmp/code.zip -d /opt/neurolens/app`
  3. Write `/opt/neurolens/env.conf` from instance tags or `aws ssm get-parameter` calls (bucket name, queue URL, region).
  4. Construct `/opt/neurolens/app/config.json` (matching `config.sample.json`'s schema — paths, `model.repo_id`, `max_video_duration_seconds`, `hf_download_timeout`), populated from instance tags/SSM the same way `env.conf` is. Fetch `HF_TOKEN` via `aws ssm get-parameter --name <param> --with-decryption` and inject it into `config.json`'s `hf_token` field. Immediately run `chmod 600 /opt/neurolens/app/config.json`. Do not log `HF_TOKEN` or write it to any file other than `config.json`.
  5. Verify `env.conf` and `config.json` before starting the worker: `test -s /opt/neurolens/env.conf` and `python3 -c "import json; json.load(open('/opt/neurolens/app/config.json'))"`. Exit non-zero with a clear log message if either check fails.
  6. `systemctl enable --now neurolens-worker`. A later code-only redeploy via `deploy_code.sh` uses `systemctl restart` because the unit is already enabled.
- ASG default configuration: min=0, max=1, desired=0. This is the standing configuration for normal development, correctness testing, and acceptance criteria 1, 4, 6, and 9; none require more than one concurrent GPU instance.

  Raise max to 5 only for the scheduled Experiment 2 saturation sub-test (§10), then reset max to 1 immediately afterward. Target tracking applies regardless of the current max.

- **Ownership boundaries and convergent provisioning:** `provision_m1.sh` owns the S3 bucket, upload/task SQS queue, `uploads/` S3 notification, and its base queue policy. `provision_m2.sh` owns the DLQ/redrive policy, Launch Template, ASG/scaling policy, and worker IAM role/instance profile; it must never create or modify M1's bucket or the `uploads/` S3 notification configuration. The one exception is M1's upload queue's `RedrivePolicy` attribute: `provision_m2.sh` may set this (via `aws sqs set-queue-attributes`) to point at the new DLQ, but must first read the queue's current attributes with `get-queue-attributes`. If a `RedrivePolicy` already exists and points at a different DLQ ARN, the script must fail with a clear error rather than silently overwriting it — `SetQueueAttributes` replaces the entire `RedrivePolicy` value, it does not merge fields within it. No other attribute of M1's queue (e.g. `VisibilityTimeout`) may be touched.

  `provision_m2.sh` must validate M1 resources by configured bucket name and queue URL/ARN, not tags: confirm the bucket and queue exist, and confirm `get-bucket-notification-configuration` contains exactly the `uploads/` prefix routed to the configured queue ARN. If not, fail with a clear M1-provisioning error; never call `put-bucket-notification-configuration` from M2, since that configuration is a single replaceable document.

  `provision_m2.sh` must be convergent for M2-owned resources. It must work both from no M2 resources and after `teardown_m2.sh`: detect existing Launch Template, ASG, scaling policy, DLQ/redrive policy, and worker IAM role/instance profile by stable names/tags; reuse them without duplication; and update the ASG to min=0, desired=0, max=1 on each run.

  Tag every M2-created Launch Template, ASG, IAM role/instance profile, CloudWatch alarm, and DLQ with `Project=neurolens` and `Milestone=M2` at creation time.
- Confirm empirically that target tracking actually holds at 0 instances when the queue is empty; some configurations need a scheduled minimum nudge — note if this happens

## 5. Code deployment (`infra/deploy_code.sh`)
```bash
#!/usr/bin/env bash
set -euo pipefail
zip -j /tmp/code.zip worker.py inference.py
aws s3 cp /tmp/code.zip s3://<bucket>/code/latest.zip
echo "Deployed. Running instances pick this up on their next restart."
```
A single-command way to push `worker.py`/`inference.py` updates without rebuilding the AMI. An optional `infra/rolling_restart.sh` that SSM-restarts the service on all currently-running instances is a nice-to-have, not required for acceptance.

## 6. Job status tracking
Status objects at `status/{job_id}.json` in the same S3 bucket:
```json
{ "job_id": "...", "status": "queued|claimed|processing|done|failed",
  "stage": "downloading|inference_full|stripping_audio|inference_noaudio|extracting_roi|null",
  "updated_at": "2026-10-14T03:22:10Z",
  "error": null }
```
- `job_id` reuses the UUID from the presign step
- The worker writes `queued`→`claimed` immediately on receiving the SQS message, before starting any real work
- Worker updates `stage` at each pipeline transition; also log these transitions with timestamps (CloudWatch or a local log) for Experiment 1's latency analysis
- Results are written to `results/{job_id}.json` using a conditional `put_object` with `IfNoneMatch="*"`. This, not the `claims/` lease, is the canonical terminal-completion gate: a completed result is immutable, so a delayed duplicate delivery cannot publish a replacement.
- Write order matters: attempt the conditional results write first. Only if it succeeds, write `status: "done"` to `status/{job_id}.json`. If the results write fails because the result already exists, abandon publication without error and do not overwrite status. A non-owner or retrying worker must never overwrite terminal `done` status.
- New Flask endpoint: `GET /api/jobs/{job_id}/status` — first checks whether `results/{job_id}.json` exists. If it does, respond with `status: "done"` regardless of the status object. Only if no result exists does the endpoint read `status/{job_id}.json`; return 404 if neither exists (frontend treats 404 as "still queued," not an error). This makes the result object authoritative across a crash after result creation but before the status update.

## 7. Preventing duplicate GPU runs
An S3 event notification delivered via SQS can contain multiple `Records` in one message. Parse every record independently: ignore records outside the `uploads/` prefix; for uploads, check for an existing `results/{job_id}.json` before claiming. If it exists, mark that record already handled and continue to the next record. Only delete the SQS message after every record is ignored, already complete, successfully processed, or has lost a claim race — never delete a whole message after inspecting only one record.

For each new upload record, atomically claim the job with a lease:
```python
s3.put_object(Bucket=..., Key=f"claims/{job_id}.json",
              Body=json.dumps({"worker_id": ..., "claimed_at": ..., "lease_expires_at": ...}),
              IfNoneMatch="*")
```
The ETag returned by a successful conditional write is the fencing token. Store it in memory as `claim_version`; timestamps and `worker_id` are informational only.

Set `lease_expires_at` to now + 90 seconds, deliberately shorter than the queue's 120-second visibility timeout. On a precondition failure, read the existing claim and ETag:

- If the lease is still live, another worker owns the job. Mark this record as a lost race and continue processing the remaining records.
- If the lease is stale, attempt takeover with `put_object(..., IfMatch=<ETag just read>)`. Treat a conflict as a lost race. On success, replace the in-memory `claim_version` with the new ETag.

Before publishing a result, re-read the claim and confirm its ETag still matches `claim_version`. If it does not, abandon publication. The conditional results write in §6 is the final authority: this protocol guarantees one committed result, not necessarily that only one GPU inference ever starts under SQS at-least-once delivery.

## 8. SQS visibility heartbeat and fast failure release
The queue's base visibility timeout is 120s — comfortably longer than a single pipeline stage, but short enough that a genuinely dead worker's job becomes retryable quickly rather than sitting invisible for the better part of your entire target end-to-end latency.

**Heartbeat, while a job is legitimately still processing:**
- On a background thread or between pipeline stages, every 45–60 seconds: (a) call `sqs.change_message_visibility(QueueUrl=..., ReceiptHandle=..., VisibilityTimeout=120)`; and (b) renew the claim with `put_object(..., IfMatch=<current claim_version>)` and `lease_expires_at = now + 90s`. Update `claim_version` with the new returned ETag. Both renewals must occur together.
- A silently dead worker stops both renewals; its lease expires within 90 seconds and the SQS message becomes visible within roughly 120 seconds.

**Fast release on any handled failure — do this instead of letting the timeout lapse passively:**
- In the worker's top-level exception handler (any failure during download, ffprobe, inference, or ROI extraction): first conditionally delete its own claim with `delete_object(..., IfMatch=<current claim_version>)`, then call `sqs.change_message_visibility(QueueUrl=..., ReceiptHandle=..., VisibilityTimeout=0)`. Log a claim-delete conflict but still release visibility; do not delete the SQS message.
- **Spot interruption** (background thread polling IMDSv2 for the interruption notice every ~5s): on notice, stop pulling new messages. If an in-flight job cannot plausibly finish in the two-minute warning, perform the same conditional claim delete followed by `VisibilityTimeout=0` release. This is the proactive-release behavior Experiment 2 must observe.
- In both cases (general failure and Spot interruption bail-out), releasing rather than deleting is what makes the message eligible for immediate redelivery to a healthy worker, and eventually to the DLQ after 2 total receive attempts if the failure keeps recurring

## 9. Frontend: real polling
Replace the `setInterval`-based fake progress text in `analyseVideo()` with:
- After the presigned S3 POST succeeds (204 response), poll `GET /api/jobs/{job_id}/status` every 5s
- Map `status`/`stage` to the existing spinner/detail-line UI — reuse the visual design, drive it from real values
- On `status: "done"`: fetch results via a new `GET /api/jobs/{job_id}/result` endpoint reading from S3 (workers are remote, ephemeral instances, so results must land in S3, not local disk)
- On `status: "failed"`: show the real `error` field instead of a generic `alert()`
- Client-side polling timeout (e.g. 10 minutes with no `done`/`failed`) shows a "taking longer than expected" message — a UI safeguard only, not an authoritative backend timeout. With §8's faster recovery, this should rarely if ever actually trigger under normal failure conditions

## 10. Experiments instrumentation
- **Shared artifact contract:** Experiments 1–3 write durable, versioned artifacts to `experiments/<experiment>/<run_id>/` in the project S3 bucket. Each run includes a `manifest.json` with `experiment`, `run_id`, UTC timestamps, code/AMI revision, input clip identifiers, configuration, and output-file names. Experiment-specific CSV/JSON files live beside that manifest. This prefix is outside M1's transient 48-hour lifecycle rules and is the sole input location for M4's consolidation script; do not rely on CloudWatch discovery or an unspecified local path.
- **Experiment 1:** append (not overwrite) stage transitions with timestamps to the status object or a parallel log, so a script can compute per-stage durations across 15s/30s/60s test videos
- **Experiment 2:** `locustfile.py` that POSTs to `/api/uploads/presign`, then submits the fixed test video as a multipart form POST (using the returned `url` and `fields`, file field last) to match M1 §4a's presigned-POST upload mechanism, then polls status to completion, run as two distinct sub-tests using §4's temporary max=5 raise for this test only; the standing default is max=1 and is restored afterward.
  - **Elasticity sub-test (1, 5 concurrent):** confirms ASG scaling, cold-start, and per-worker throughput. Timebox each run and use the 15-second clip for the first pass at each concurrency level before longer clips.
  - **Saturation sub-test (10, 20 concurrent):** deliberately exceeds the temporarily raised five-instance ceiling to verify graceful queue backpressure, no dropped/duplicated jobs, and full drain after load subsides. This is not a scaling test.

  Cross-reference both sub-tests against CloudWatch ASG instance-count-over-time. The manual Spot-interruption injection must confirm §8's proactive release: redelivery within roughly 120 seconds, not a passive multi-minute lapse.

## 11. File layout additions
```
neurolens/
├── infra/
│   ├── provision_m1.sh          # (from M1)
│   ├── build_ami.sh              # NEW
│   ├── provision_m2.sh           # NEW — launch template, ASG, scaling policy, DLQ redrive
│   ├── neurolens-worker.service   # NEW — systemd unit, baked into AMI
│   ├── deploy_code.sh             # NEW
│   └── teardown_m2.sh             # NEW — scales GPU ASG to 0 and preserves AMI/code bundle
├── worker.py                      # MODIFIED — claim logic, status writes, visibility heartbeat + fast release (§8), S3 result output
├── app.py                         # MODIFIED — new /api/jobs/{id}/status and /api/jobs/{id}/result endpoints; continues as M1's local/dev process with s3:GetObject scoped to status/* and results/*
├── locustfile.py                   # NEW
├── experiments/
│   └── latency_breakdown.py        # NEW — Experiment 1 analysis script
└── static/                         # MODIFIED — real polling replaces fake progress timer
```

## 12. Acceptance criteria
1. `build_ami.sh` produces an AMI where, on boot with UserData supplying real code, `systemctl status neurolens-worker` shows the service running and successfully claiming/processing a real queued job — identical inference output to M1's local worker.
2. Boot-to-processing-ready time is measured and broken down into its components (instance launch, UserData execution, model-load-to-VRAM), reported in Experiment 2's results — well under the 5–8 minute cold-dependency-pull baseline a from-scratch container/weights pull would require. No specific target number is asserted in advance.
3. `deploy_code.sh` followed by a new instance launch (or `systemctl restart` on an existing one) picks up new code without any AMI rebuild.
4. Submitting 2 identical SQS messages for the same job_id (simulate via manual re-send) results in exactly one committed result for that job — verify via the claim fencing token (claim_version/ETag) and final status/result objects, not by assuming GPU inference itself only ran once.
5. ASG scales 0→N as queue depth increases and back to 0 when drained, observable in CloudWatch/console.
6. **Killing a worker process mid-job (simulate a crash, e.g. `kill -9` the process) results in the job becoming visible for redelivery within roughly 120s, not 600s** — confirm via a second worker (or a manual `receive-message` call) picking it up shortly after the kill, not after a long wait. Confirm its takeover used a conditional `IfMatch` overwrite of the stale claim and that its claim_version contains the new ETag; if two workers race to take over, at most one succeeds.
7. Manually terminating a Spot instance mid-job results in the job being picked up by another instance rather than lost, and — per §8 — this happens via a proactive release, not a passive timeout lapse; confirm the message's visibility was explicitly reset to 0 rather than just expiring naturally.
8. Frontend shows real, changing status text driven by actual job progress, and the real error message on failure instead of a generic alert.
9. DLQ receives a message after 2 failed processing attempts on the same job (force a worker exception on a test job to verify) — and confirm this now happens noticeably faster than it would have under the old fixed 600s timeout, since failures release proactively instead of waiting out the window.
10. A freshly launched instance, with no `config.json` baked into the AMI, writes `config.json` through UserData, retrieves `HF_TOKEN` from SSM, and starts `neurolens-worker` without manual intervention; verify config mode 600.
11. `neurolens-worker` is disabled by default in the AMI and becomes active only after UserData completes both config writes and `systemctl enable --now`; verify no premature start or crash loop.
12. A delayed duplicate SQS delivery after successful completion is either rejected by the result-exists check or loses the conditional results write; it must not overwrite the result or status.
13. Simulate a crash after the conditional results write succeeds but before status is marked done. `GET /api/jobs/{job_id}/status` must still return done from result existence, and a duplicate delivery must not reprocess the job.
14. With the SQS queue empty (no visible or in-flight messages), run `provision_m2.sh`, then `teardown_m2.sh`, then `provision_m2.sh` again. The second provisioning run must validate—not recreate—M1's bucket, queue, and notification; restore the ASG to min=0, desired=0, max=1; create no duplicate DLQ, redrive-policy, or IAM resources; and then process one newly submitted test job end-to-end.

## 13. Teardown

Create `infra/teardown_m2.sh`:
- Set the GPU ASG desired capacity and max to 0; do not delete the ASG, Launch Template, AMI, or `code/latest.zip`.
- Delete only explicitly tagged disposable test artifacts, never a resource tagged `Milestone=M2`.
- Confirm, with tag-filtered `aws ec2 describe-instances`, that zero GPU instances remain running.

## 14. Explicitly not in this milestone
No Aurora, no real user accounts/auth, no credit balances or billing/reservation logic, no refund handling, no SSO.
