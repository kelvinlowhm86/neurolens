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
- Experiment 1 (latency) instrumentation and Experiment 2 (scaling, Locust), including the cold-start breakdown

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
  - `ShutdownRequested`: an exception raised when the worker has been asked to stop.
  - `handle_record(bucket, key, *, s3, cfg, roi_masks, heartbeat, should_stop) -> Outcome` (replaces M1's signature): `heartbeat` is a zero-argument callable returning a `Heartbeat` for the current message (built by `process_message`); the record's work after the result-exists check runs inside it. `should_stop()` is checked between pipeline stages; when it returns `True`, `handle_record` raises `ShutdownRequested`. It still never touches SQS itself.
  - `process_message(message, *, s3, sqs, cfg, roi_masks, should_stop) -> None` (replaces M1's signature; the queue URL comes from `cfg`): runs every record, deletes the message only when every record returned a final outcome, and on any exception (including `ShutdownRequested`) handles it as §6 says, then re-raises `ShutdownRequested` so `run()` exits.
  - `run() -> None`: installs a `SIGTERM` handler that sets a `threading.Event`, whose `is_set` is passed as `should_stop`, and stops polling once it is set.
  - Config `worker.heartbeat_seconds` (default 50), so tests can use a fraction of a second; added to `config.sample.json`.
- `neurolens.web.app` routes (the web tier still never imports `neurolens.inference`):
  - `job_id` must be a UUID; anything else returns 400 `{"error": "bad_job_id"}`.
  - `GET /api/jobs/<job_id>/status`: 200 with `{"job_id", "status": "done"}` if a result exists (whatever the status object says); otherwise 200 with the status object; otherwise 404 `{"error": "not_found"}` (the frontend treats 404 as "still queued").
  - `GET /api/jobs/<job_id>/result`: 200 with the result JSON, or 404 `{"error": "not_found"}`.

## 2. Autoscaling (Terraform, on M2a's ASG)
Scale on the whole queue, including messages being worked on. Scaling only on *visible* messages would be wrong: a running job's message is invisible, so the queue would look empty mid-job and the ASG could terminate the worker doing it.
- **Scale out:** a CloudWatch alarm on `ApproximateNumberOfMessagesVisible` ≥ 1 for 1 minute triggers a step policy that adds 1 instance (cooldown 5 minutes, roughly one cold start), up to the ASG max.
- **Scale in:** a CloudWatch metric-math alarm on `ApproximateNumberOfMessagesVisible + ApproximateNumberOfMessagesNotVisible` = 0 for 15 consecutive minutes triggers a policy that sets capacity to 0. M2a's 30-minute idle self-termination stays as a backstop.
- SQS publishes these metrics about once a minute, and after a long idle period they can take several minutes to resume. That delay is part of the measured cold start (§8); for a live demo or study session, hold a worker warm (below).
- Standing configuration stays **min 0 / max 1 / desired 0**. Max 2 only during Experiment 2, then back to 1.
- **Warm hold.** `start_work.sh --worker` now sets the ASG's **minimum** to 1 (and desired to 1), so neither the scale-in alarm nor M2a's idle self-termination can remove the warm worker before the demo. `stop_work.sh` sets the minimum back to 0 along with everything else. A worker that could reach the ASG minimum can't also decrement below it, so `self_terminate.sh` changes:
  - **Idle exit** (`OnSuccess`): if the ASG minimum is 1 or more, it restarts the worker service instead of terminating (the hold is deliberate).
  - **Repeated crashes** (`OnFailure`) or a UserData failure: it first sets the ASG minimum to 0 and then terminates with a decrement, so a broken warm worker is not replaced in a billing loop.
  - IAM additions to the worker profile: `autoscaling:DescribeAutoScalingGroups` (not resource-scoped by AWS) and `autoscaling:UpdateAutoScalingGroup` on this ASG only.
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

## 6. Heartbeat, fast release and shutdown
**Heartbeat:** every record's processing runs inside `Heartbeat(..., interval_seconds=cfg worker.heartbeat_seconds)`. A silently dead worker stops the heartbeat, and the message becomes visible again within about 120 s.

**Fast release on any failure** (download, ffprobe, inference, ROI extraction): `process_message` catches the exception, writes `status: "failed"` with the error only if no result exists, then calls `release(...)` so the message is immediately retryable. It does not delete it; after two receives it moves to the dead-letter queue.

**Shutdown, including Spot interruption:** when AWS reclaims a Spot worker, scales one in, or `systemctl stop` runs, the machine or service sends the worker `SIGTERM`. The worker stops taking messages; at the next stage boundary `handle_record` raises `ShutdownRequested`, and `process_message` releases the in-flight message with `release(...)` (no status change), so another worker picks it up at once. If the current stage runs longer than the shutdown allows (the service sets `TimeoutStopSec=110`), the process is killed; the heartbeat stops with it and the message becomes visible again within about 120 s anyway. There is no separate watcher for AWS's two-minute Spot warning: the shutdown path covers the same case with a little less head start.

## 7. Frontend: real polling
Replace the `setInterval`-based fake progress text in `analyseVideo()` with:
- After the presigned S3 POST succeeds (204), poll `GET /api/jobs/{job_id}/status` every 5 s.
- Map `status`/`stage` to the existing spinner/detail-line UI — reuse the visual design, drive it from real values. 404 shows "Queued".
- On `done`: fetch `GET /api/jobs/{job_id}/result` and render.
- On `failed`: show the real `error` field instead of a generic `alert()`.
- After 10 minutes without `done`/`failed`, show "taking longer than expected" (a UI safeguard only).

## 8. Experiments instrumentation
- **Shared artifact contract** (binding for Experiments 1–3 and M4's study export; M4's consolidation script and its tests are written against exactly this). Each run writes `experiments/<experiment>/<run_id>/` in the bucket (`experiment` is `experiment-1`, `experiment-2`, `experiment-3` or `study`). `experiments/*` never expires and is the sole input for M4's consolidation.
  - **`manifest.json`, every run:** `experiment`, `run_id`, `series` (a name grouping runs that belong together, e.g. `exp1-final`; consolidation combines all runs of one series), `started_utc`, `finished_utc`, `code_revision` (the git short hash of the code used: `/opt/neurolens/app/REVISION` on AWS, `git rev-parse --short HEAD` elsewhere), `environment` (`{"type": "aws", "instance_type", "ami_id"}` or `{"type": "onprem", "host", "gpu"}`), `files` (the file names below, each of which must exist).
  - **`experiment-1`**: `runs.csv`, one row per job: `clip_seconds, job_label, upload_ms, queue_wait_ms, downloading_ms, inference_full_ms, stripping_audio_ms, inference_noaudio_ms, extracting_roi_ms, result_fetch_ms, render_ms, peak_vram_gb`. `render_ms` may be empty (filled by hand for 3 runs per clip length); `peak_vram_gb` comes from the result's `gpu` field. A stage's duration is the next stage's start minus its own; the last stage ends at the job's `done` time.
  - **`experiment-2`**:
    - `jobs.csv`: `burst_size, job_label, submitted_utc, done_utc, status`.
    - `cloudwatch.csv`: `minute_utc, sqs_visible, sqs_in_flight, asg_in_service`.
    - `locust_stats.csv`: Locust's own `--csv` stats file, copied as is.
    - `reliability.csv`, one row per burst: `burst_size, submitted, results, duplicate_results, dead_lettered`.
    - `spot_interruption.csv`, the interruption test (a worker terminated mid-job): `injected_utc, released_utc, completed_utc, recovered`.
    - `cold_start.csv`, one row per measured boot: `alarm_to_launch_s, launch_to_userdata_s, weight_sync_s, model_load_s, first_job_ready_s`.
  - **`experiment-3`**: `runs.csv`: `environment` (`cloud` or `onprem`), `gpu, clip_seconds, run_index, wall_ms`. Cloud rows may be copied from an Experiment 1 series, named in the manifest's `source_series`.
  - **`study`**: see M4 §4.
  - `job_label` is a short label (`J1`, `J2`, …) unique within the run, never a job ID.
- **Experiment 1 (latency)** covers the stages the report promises: upload → queue wait → processing stages → result fetch → client render, for 15 s, 30 s and 60 s test videos (at least 3 runs each).
  - `experiments/latency_run.py <clip>` does one run like a browser would: presign, upload via the presigned POST (timed), poll status every 2 s until `done`, fetch the result (timed). It saves the upload and fetch times and the job's `stages` list.
  - Queue wait = first stage time − upload end. Processing stages come from `stages`.
  - Client render: the frontend records the time from receiving the result to the chart being drawn in `window.neurolensTimings.render_ms`; read it in the browser console on 3 runs per clip length and add it to the run's CSV by hand.
  - Rows follow the contract's `experiment-1/runs.csv`. Everything goes into `experiments/experiment-1/<run_id>/` straight away (`status/` objects expire after 48 hours, and M3a retires them). Analysis script `experiments/latency_breakdown.py`.
- **Experiment 2 (scaling):** `locustfile.py` POSTs to `/api/uploads/presign`, uploads the fixed 15 s test video via the presigned POST (fields first, file last), then polls status to completion. Burst sizes match the report: **1, 5, 10 and 20** simultaneous jobs, with the ASG max raised to 2 for these runs.
  - **Lower bursts (1 and 5):** scaling from 0, cold start, and per-worker throughput as the pool grows to its 2-worker ceiling.
  - **Higher bursts (10 and 20):** beyond the ceiling on purpose, to show the queue absorbing the backlog, no dropped or duplicated results, and a full drain afterwards.
  - About $1–2 of Spot GPU time in total; flag the estimate before running.
  - Each run saves the contract's `experiment-2` files: Locust's CSV output, per-job times, the reliability counts (results vs submissions, duplicates, dead-lettered jobs), and `experiments/export_cloudwatch.py <run_id> --start --end` for the one-minute SQS and ASG series. The Spot interruption test and the cold-start boots write their own files in the same format.
  - Restore the ASG max to 1 afterwards.
  - **Spot interruption (simulated):** terminate one worker mid-job (`aws ec2 terminate-instances`), which sends it the same shutdown a Spot reclaim does. Confirm the worker releases the job and another attempt completes it within about 2 minutes. The report describes this as a simulated interruption.
  - **Cold-start breakdown:** SQS metric delay → scale-out alarm; instance launch → UserData start; S3 weight sync (from the boot log); model load into GPU memory; first job ready. No target number is asserted in advance.
- **Fallback, only if cold start is too slow** (the S3 weight sync alone taking more than about 5 minutes): build a second image that keeps the weights on its disk and compare. Not planned; decide with Josh first.

## 9. Tests (`FAKE_INFERENCE=1 pytest`, moto)
Add to the suite, written first against §1a:
- `Heartbeat` with `interval_seconds=0.05` calls `change_message_visibility` with 120 repeatedly while the block runs, and not after it exits.
- A record that raises leads to status `failed` (when no result exists) and `release` with `VisibilityTimeout=0`, and the message is not deleted.
- A record whose result already exists returns `SKIPPED` with no inference and no status change.
- A lost conditional write returns `DUPLICATE` and leaves the existing result and `done` status untouched; `put_status` never changes a `done` object.
- `put_status` appends to `stages` in order, with the injected `now`.
- A multi-record message is deleted only after all its records have final outcomes.
- `should_stop` turning `True` mid-record makes `handle_record` raise `ShutdownRequested` at the next stage boundary; `process_message` releases the message (visibility 0), leaves status unchanged, does not delete it, and re-raises; `run()` then receives no more messages.
- Status endpoint: `done` when a result exists even if the status object says `processing`; the status object otherwise; 404 when neither exists; 400 for a non-UUID `job_id`. Result endpoint: 200 with the stored JSON, 404 when missing.
- Import hygiene still holds: the web app with the new endpoints never loads `neurolens.inference` or `torch`.

## 10. File layout additions
```
infra/terraform/          MODIFIED: scale-out/in alarms and policies, DLQ, 120 s visibility, IAM additions
neurolens/worker.py       MODIFIED: status writes, SKIPPED, Heartbeat, release, SIGTERM shutdown
neurolens/storage.py      MODIFIED: result_exists, get_result, get_status, put_status
neurolens/web/app.py      MODIFIED: /api/jobs/<id>/status and /api/jobs/<id>/result
locustfile.py             NEW
requirements/experiments.txt  NEW: locust, matplotlib (experiment scripts; M4's consolidation also uses matplotlib)
requirements/dev.txt      MODIFIED: adds -r experiments.txt, so CI can run the experiment and consolidation tests
experiments/latency_run.py         NEW: Experiment 1 single run
experiments/latency_breakdown.py   NEW: Experiment 1 analysis
experiments/export_cloudwatch.py   NEW: Experiment 2 CloudWatch series
tests/                    MODIFIED: §9 tests
static/                   MODIFIED: real polling
```

## 11. Acceptance criteria
1. The ASG scales 0 → 1 (and 0 → 2 during Experiment 2) as the queue fills, never terminates a worker mid-job, and returns to 0 after the queue has been fully empty for 15 minutes, visible in CloudWatch.
2. Two identical SQS messages for one `job_id` (manual re-send) produce exactly one result object and one `done` status; the second delivery returns `SKIPPED` or `DUPLICATE`.
3. Killing the worker process mid-job (`kill -9`) makes the job visible for redelivery within about 120 s, and a second attempt completes it.
4. Terminating a worker mid-job (the simulated Spot interruption) makes it release the job (visibility set to 0), and another attempt completes it within about 2 minutes.
5. `systemctl stop neurolens-worker` mid-job releases the job immediately.
5a. After `start_work.sh --worker`, the warm worker survives 45 minutes with an empty queue (no scale-in, no idle termination); `stop_work.sh` then removes it and leaves the ASG minimum at 0.
6. The frontend shows real, changing status text and the real error message on failure.
7. A job forced to fail twice lands in the dead-letter queue.
8. A crash after the result write but before the `done` status still reports `done` from `GET /api/jobs/{job_id}/status`, and a duplicate delivery does not reprocess.
9. Experiments 1 and 2 have manifests and data under `experiments/`, including the cold-start breakdown and the simulated interruption.
10. `terraform apply` run twice reports no changes the second time.
11. `FAKE_INFERENCE=1 pytest` passes locally and in CI, including the §9 tests, and the tests were committed before the implementation.

## 12. Between sessions and teardown
- End every working session with `stop_work.sh`, and restore the ASG max to 1 after Experiment 2.
- Keep the software-only image, `models/`, and `code/latest.zip`; M3 and M4 reuse them.
- Final teardown (`terraform destroy`, deregistering images, deleting snapshots) happens only after M4's consolidation.

## 13. Explicitly not in this milestone
No Aurora, no user accounts or sign-in, no credits, billing or refunds, no web-tier load balancer, no containers.
