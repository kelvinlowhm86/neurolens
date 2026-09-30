# NeuroLens — M2b Implementation Spec
**Milestone:** Autoscaling, reliability, job status and Experiments 1–2 (second part of 9 – 19 Oct)
**Builds on:** M2a (software-only GPU image, weights in `s3://<bucket>/models/`, private network with NAT instance, Launch Template + ASG with no scaling policy, systemd worker that pulls code on every start and self-terminates when idle or broken, `deploy_code.sh`, results written to `results/{job_id}.json` with a conditional write, start/stop scripts, long-running GPU alarm, real-model check passed). M2b makes the worker fleet scale by itself, survive crashes and Spot interruptions, and report real status to the frontend.

**Ground rules for all of M2b:** region `us-east-1`. Python 3.12. All infrastructure is Terraform in `infra/terraform/`, tagged `Project=neurolens`, `Milestone=M2b`. Run AWS commands with the `neurolens` CLI profile only. **Tests first, as in M0 §5:** the §9 tests are written by a separate agent against §1a before the implementation, and are not edited by the implementer.

## 1. Scope
### In scope
- Autoscaling on the SQS queue, standing at zero; up to two workers for experiments
- Visibility timeout 120 s with a heartbeat, fast failure release, and Spot-interruption and shutdown handling
- Dead-letter queue after two receive attempts
- S3-based job status and Flask status/result endpoints
- Duplicate-safe processing of multi-record and repeated messages
- Real client-side polling UI
- Experiment 1 (latency) instrumentation and Experiment 2 (scaling, Locust), including the baked-weights cold-start comparison

### Out of scope (M3)
- Aurora, user accounts, credits/billing, Google sign-in, the web tier's load balancer
- EKS/Kubernetes, containers, image registries

### 1a. Interfaces fixed by this spec (the §9 tests are written against exactly these)
- `neurolens.storage`:
  - `result_exists(s3, bucket, job_id) -> bool`
  - `get_result(s3, bucket, job_id) -> dict | None`
  - `get_status(s3, bucket, job_id) -> dict | None`
  - `put_status(s3, bucket, job_id, status, *, stage=None, error=None, now=None) -> None`: reads the current object, sets `status`, `stage`, `error` and `updated_at`, appends `{"stage", "at"}` to `stages` when `stage` is given, and writes it back. It never changes an object whose status is already `done`. `now` (a `datetime`) is injectable for tests.
- `neurolens.worker`:
  - `Outcome` gains `SKIPPED` (a result already existed before any work). The final outcomes are now `DONE`, `REJECTED`, `DUPLICATE`, `SKIPPED`.
  - `Heartbeat(sqs, queue_url, receipt_handle, *, interval_seconds, visibility_seconds=120)`: a context manager that starts a daemon thread calling `change_message_visibility(..., VisibilityTimeout=visibility_seconds)` every `interval_seconds`, and stops it on exit.
  - `release(sqs, queue_url, receipt_handle) -> None`: `change_message_visibility(..., VisibilityTimeout=0)`.
  - `SpotWatcher(fetch_notice, *, poll_seconds=5)`: a daemon thread that calls `fetch_notice()` (default: an IMDSv2 read of `spot/instance-action`, returning `True` when a notice exists) and sets `SpotWatcher.interrupted` (a `threading.Event`). Injectable so tests need no IMDS.
  - Config `worker.heartbeat_seconds` (default 50), so tests can use a fraction of a second.
- `neurolens.web.app` routes (the web tier still never imports `neurolens.inference`):
  - `job_id` must be a UUID; anything else returns 400 `{"error": "bad_job_id"}`.
  - `GET /api/jobs/<job_id>/status`: 200 with `{"job_id", "status": "done"}` if a result exists (whatever the status object says); otherwise 200 with the status object; otherwise 404 `{"error": "not_found"}` (the frontend treats 404 as "still queued").
  - `GET /api/jobs/<job_id>/result`: 200 with the result JSON, or 404 `{"error": "not_found"}`.

## 2. Autoscaling (Terraform, on M2a's ASG)
Scale on the whole queue, including messages being worked on. Scaling only on *visible* messages would be wrong: a running job's message is invisible, so the queue would look empty mid-job and the ASG could terminate the worker doing it.
- **Scale out:** a CloudWatch alarm on `ApproximateNumberOfMessagesVisible` ≥ 1 for 1 minute triggers a step policy that adds 1 instance (cooldown 5 minutes, roughly one cold start), up to the ASG max.
- **Scale in:** a CloudWatch metric-math alarm on `ApproximateNumberOfMessagesVisible + ApproximateNumberOfMessagesNotVisible` = 0 for 15 consecutive minutes triggers a policy that sets capacity to 0. M2a's 30-minute idle self-termination stays as a backstop.
- SQS publishes these metrics about once a minute, and after a long idle period they can take several minutes to resume. That delay is part of the measured cold start (§8); for a live demo, pre-warm with `start_work.sh --worker`.
- Standing configuration stays **min 0 / max 1 / desired 0**. Max 2 only during Experiment 2, then back to 1.
- IAM additions to M2a's worker profile: `s3:GetObject` and `s3:PutObject` on `status/*`; `status/` added to the `s3:ListBucket` prefix condition (so a missing status object reads as 404, not 403); `s3:PutObject` on `experiments/*`; `sqs:ChangeMessageVisibility` on the job queue.

## 3. Queue changes (Terraform, on M1's queue)
- **Visibility timeout 120 s** (was 900 s). The §6 heartbeat keeps long jobs invisible; a dead worker's job is retried within about two minutes.
- **Dead-letter queue** with `maxReceiveCount = 2`. Remove M1's `# TODO(M2b)` comment.

## 4. Job status tracking
Status objects at `status/{job_id}.json` in the same bucket:
```json
{ "job_id": "...", "status": "processing|done|failed",
  "stage": "downloading|inference_full|stripping_audio|inference_noaudio|extracting_roi|null",
  "updated_at": "2026-10-14T03:22:10Z",
  "stages": [{"stage": "downloading", "at": "2026-10-14T03:22:10Z"}],
  "error": null }
```
- `job_id` is the UUID from the presign step. There is no `queued` status: before a worker takes the job, no status object exists and the endpoint returns 404.
- The worker writes `processing` as soon as it takes a record, before any real work, and updates `stage` at each pipeline transition (Experiment 1 reads `stages`).
- Write order: `put_result` first. Only if it returns `True`, write `status: "done"`. If it returns `False`, return `DUPLICATE` without touching status.

## 5. Duplicate deliveries
SQS may deliver a message more than once, and one S3 event message can hold several `Records`. `process_message` already handles each record independently and deletes the message only when every record has a final outcome (M1). M2b adds:
- If `result_exists` is true before any work, return `SKIPPED` without running inference.
- If `put_result` returns `False`, another worker finished first: `DUPLICATE`, as in M2a.

With at most two workers, a duplicate that wastes one GPU run is rare and accepted; results and status are never duplicated. M3 adds a database lock that prevents the duplicate run itself. Do not add S3 lease or claim objects.

## 6. Heartbeat, fast release, Spot interruption and shutdown
**Heartbeat:** every record's processing runs inside `Heartbeat(..., interval_seconds=cfg worker.heartbeat_seconds)`. A silently dead worker stops the heartbeat, and the message becomes visible again within about 120 s.

**Fast release on any failure** (download, ffprobe, inference, ROI extraction): `process_message` catches the exception, writes `status: "failed"` with the error only if no result exists, then calls `release(...)` so the message is immediately retryable. It does not delete it; after two receives it moves to the dead-letter queue.

**Spot interruption:** a `SpotWatcher` runs for the life of the worker. When `interrupted` is set, the worker stops receiving new messages and **always** releases the in-flight message with `release(...)` (no guess about whether it would finish in two minutes), then exits cleanly.

**Shutdown:** the worker handles `SIGTERM` (sent by `systemctl stop` and by ASG scale-in) the same way: release the in-flight message, then exit.

## 7. Frontend: real polling
Replace the `setInterval`-based fake progress text in `analyseVideo()` with:
- After the presigned S3 POST succeeds (204), poll `GET /api/jobs/{job_id}/status` every 5 s.
- Map `status`/`stage` to the existing spinner/detail-line UI — reuse the visual design, drive it from real values. 404 shows "Queued".
- On `done`: fetch `GET /api/jobs/{job_id}/result` and render.
- On `failed`: show the real `error` field instead of a generic `alert()`.
- After 10 minutes without `done`/`failed`, show "taking longer than expected" (a UI safeguard only).

## 8. Experiments instrumentation
- **Shared artifact contract:** Experiments 1–3 write versioned artifacts to `experiments/<experiment>/<run_id>/` in the bucket. Each run has a `manifest.json` (`experiment`, `run_id`, UTC timestamps, code revision from `/opt/neurolens/app/REVISION`, AMI ID, instance type, input clip identifiers, configuration, output-file names), with experiment-specific CSV/JSON beside it. `experiments/*` never expires and is the sole input for M4's consolidation script.
- **Experiment 1 (latency):** per-stage durations from the `stages` list in status objects, across 15 s / 30 s / 60 s test videos. Analysis script `experiments/latency_breakdown.py`.
- **Experiment 2 (scaling):** `locustfile.py` POSTs to `/api/uploads/presign`, uploads the fixed test video via the presigned POST (fields first, file last), then polls status to completion.
  - **Elasticity sub-test (1 and 2 concurrent):** ASG scaling from 0, cold start, per-worker throughput. Use the 15 s clip first.
  - **Saturation sub-test (5 and 10 concurrent):** exceeds the 2-worker ceiling on purpose, to show the queue absorbing backlog, no dropped or duplicated results, and a full drain afterwards.
  - Raise the ASG max to 2 only for these runs, then restore it to 1. Cross-reference CloudWatch ASG instance count over time.
  - **Spot interruption:** manually terminating an instance does **not** produce an interruption notice. Use AWS Fault Injection Service's `aws:ec2:send-spot-instance-interruptions` action on one worker mid-job (a one-off FIS experiment template in Terraform, with its own IAM role; FIS bills per action-minute, cents for one run). Confirm the worker releases the job and another attempt completes it within about 2 minutes.
  - **Cold-start breakdown:** SQS metric delay → scale-out alarm; instance launch → UserData start; S3 weight sync (from the boot log); model load into GPU memory; first job ready. No target number is asserted in advance.
- **Cold-start comparison image (one-off).** `build_ami.sh --bake-weights` builds a second image that keeps the weights on the root disk. Boot it once and measure the first full read of the weights from the snapshot-restored disk, then deregister it and delete its snapshot. Reason: disks restored from a snapshot load lazily, so the first read of ~20 GB may be slow; the comparison measures whether M2a's copy from S3 is faster. Report both.

## 9. Tests (`FAKE_INFERENCE=1 pytest`, moto)
Add to the suite, written first against §1a:
- `Heartbeat` with `interval_seconds=0.05` calls `change_message_visibility` with 120 repeatedly while the block runs, and not after it exits.
- A record that raises leads to status `failed` (when no result exists) and `release` with `VisibilityTimeout=0`, and the message is not deleted.
- A record whose result already exists returns `SKIPPED` with no inference and no status change.
- A lost conditional write returns `DUPLICATE` and leaves the existing result and `done` status untouched; `put_status` never changes a `done` object.
- `put_status` appends to `stages` in order, with the injected `now`.
- A multi-record message is deleted only after all its records have final outcomes.
- `SpotWatcher` with a stub `fetch_notice` that returns `True` on its second call sets `interrupted`; the worker then releases the in-flight message and receives no more messages.
- Status endpoint: `done` when a result exists even if the status object says `processing`; the status object otherwise; 404 when neither exists; 400 for a non-UUID `job_id`. Result endpoint: 200 with the stored JSON, 404 when missing.
- Import hygiene still holds: the web app with the new endpoints never loads `neurolens.inference` or `torch`.

## 10. File layout additions
```
infra/terraform/          MODIFIED: scale-out/in alarms and policies, DLQ, 120 s visibility, IAM additions, FIS template
infra/build_ami.sh        MODIFIED: --bake-weights
neurolens/worker.py       MODIFIED: status writes, SKIPPED, Heartbeat, release, SpotWatcher, SIGTERM
neurolens/storage.py      MODIFIED: result_exists, get_result, get_status, put_status
neurolens/web/app.py      MODIFIED: /api/jobs/<id>/status and /api/jobs/<id>/result
locustfile.py             NEW
experiments/latency_breakdown.py   NEW: Experiment 1 analysis
tests/                    MODIFIED: §9 tests
static/                   MODIFIED: real polling
```

## 11. Acceptance criteria
1. The ASG scales 0 → 1 (and 0 → 2 during Experiment 2) as the queue fills, never terminates a worker mid-job, and returns to 0 after the queue has been fully empty for 15 minutes, visible in CloudWatch.
2. Two identical SQS messages for one `job_id` (manual re-send) produce exactly one result object and one `done` status; the second delivery returns `SKIPPED` or `DUPLICATE`.
3. Killing the worker process mid-job (`kill -9`) makes the job visible for redelivery within about 120 s, and a second attempt completes it.
4. An FIS Spot interruption of a worker mid-job makes it release the job immediately (visibility set to 0), and another attempt completes it.
5. `systemctl stop neurolens-worker` mid-job releases the job immediately.
6. The frontend shows real, changing status text and the real error message on failure.
7. A job forced to fail twice lands in the dead-letter queue.
8. A crash after the result write but before the `done` status still reports `done` from `GET /api/jobs/{job_id}/status`, and a duplicate delivery does not reprocess.
9. Experiments 1 and 2 have manifests and data under `experiments/`, including the cold-start comparison, and the comparison image and its snapshot are deleted afterwards.
10. `terraform apply` run twice reports no changes the second time.
11. `FAKE_INFERENCE=1 pytest` passes locally and in CI, including the §9 tests, and the tests were committed before the implementation.

## 12. Between sessions and teardown
- End every working session with `stop_work.sh`, and restore the ASG max to 1 after Experiment 2.
- Keep the software-only image, `models/`, and `code/latest.zip`; M3 and M4 reuse them.
- Final teardown (`terraform destroy`, deregistering images, deleting snapshots) happens only after M4's consolidation.

## 13. Explicitly not in this milestone
No Aurora, no user accounts or sign-in, no credits, billing or refunds, no web-tier load balancer, no containers.
