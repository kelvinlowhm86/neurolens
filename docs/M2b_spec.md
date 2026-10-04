# NeuroLens — M2b Implementation Spec
**Milestone:** Autoscaling, reliability, job status, first-job warm-up, 4K downscale test, and Experiments 1–2 (second part of 9 – 19 Oct)
**Builds on:** M2a (software-only GPU image `neurolens-worker-v2`, weights in `s3://<bucket>/models/`, private network with NAT instance, Launch Template + ASG of on-demand `g6e` workers, systemd worker that pulls code on every start, the idle alarm that sets the group to 0 after 90 minutes without queue activity, the 3-hour email alarm, `deploy_code.sh`, results written to `results/{job_id}.json` with a conditional write, start/stop scripts, dead-letter queue). M2b makes the worker fleet scale by itself, survive crashes and lost machines, and report real status to the page.

**Ground rules for all of M2b:** the region is M2a's single Terraform setting (`us-east-1`). Python 3.12. All infrastructure is Terraform in `infra/terraform/`, tagged `Project=neurolens`, `Milestone=M2b`. Run AWS commands with the `neurolens` CLI profile only. **Tests first, as in M0 §5:** the §11 tests are written by a separate agent against §1a before the implementation, and are not edited by the implementer. Workers are **on-demand** (M2a §4b); nothing in M2b uses Spot.

**One owner for the number of machines:** from M2b on, only AWS decides how many workers run (scaling alarms, the circuit breaker, the warm hold, the start/stop scripts). The worker never ends its own machine. Reason: with automatic scale-out, a worker that ends itself while a job waits is replaced at once, in a loop; two deciders also needed extra rules to agree with each other.

**Where each check runs (cost):** behaviour checks (scaling, shutdown, crash, warm hold, breaker) run on the CPU rehearsal worker (`t3.large`, about $0.08 an hour) in fake mode with a slow fake job (§1a `FAKE_INFERENCE_SECONDS`). GPU sessions are used only for the warm-up fix, the 4K test, the experiments and one real-job stop check (§6). **Order:** §8 (warm-up fix) before any experiment, so first jobs are not polluted by the warm-up.

## 1. Scope
### In scope
- Autoscaling on the SQS queue, standing at zero; up to two workers for Experiment 2
- A circuit breaker that stops automatic launches after a failure; removal of the worker's self-termination
- A warm hold for demos and study sessions, ended by an AWS-side timer
- Visibility timeout 120 s with a heartbeat, fast failure release, retry-aware status, and shutdown handling
- S3-based job status and Flask status/result endpoints
- Duplicate-safe processing of multi-record and repeated messages
- Real client-side polling, with a link that resumes a job
- Removing the first-job WhisperX warm-up (§8)
- The 4K downscale test, and the downscale step only if the test passes (§9)
- Experiment 1 (latency) and Experiment 2 (scaling, Locust), including the cold-start breakdown

### Out of scope (M3)
- Aurora, user accounts, credits/billing, Google sign-in, email notification, the web tier's load balancer
- EKS/Kubernetes, containers, image registries

### 1a. Interfaces fixed by this spec (the §11 tests are written against exactly these)
- `neurolens.storage`:
  - `result_exists(s3, bucket, job_id) -> bool`
  - `get_result(s3, bucket, job_id) -> dict | None`
  - `get_status(s3, bucket, job_id) -> dict | None`
  - `put_status(s3, bucket, job_id, status, *, stage=None, error=None, now=None) -> None`: reads the current object (when there is none, starts from `{"job_id": job_id, "stages": []}`), sets `status`, `stage`, `error` and `updated_at`, appends `{"stage", "at"}` to `stages` when `stage` is given, and writes it back. It never changes an object whose status is already `done`. `now` (a `datetime`) is injectable for tests.
- `neurolens.worker`:
  - `Outcome` gains `SKIPPED` (a result already existed before any work). The final outcomes are now `DONE`, `REJECTED`, `GONE`, `DUPLICATE`, `SKIPPED`.
  - `Heartbeat(sqs, queue_url, receipt_handle, *, interval_seconds, visibility_seconds=120, max_seconds=None)`: a context manager that starts a daemon thread calling `change_message_visibility(..., VisibilityTimeout=visibility_seconds)` every `interval_seconds` (first call after one interval). A failed call is logged and beating continues. After `max_seconds` from entry it stops extending and logs an error (a hung job then becomes visible again and is retried). On exit it sets its stop flag first, then waits for the thread; the thread checks the flag under a lock immediately before each call, so **no call can start after exit has begun**.
  - `release(sqs, queue_url, receipt_handle) -> None`: `change_message_visibility(..., VisibilityTimeout=0)`. Never raises: an error (expired receipt handle, NAT down) is logged, and the message becomes visible on its own within 120 s.
  - `ShutdownRequested`: subclass of **`BaseException`** (like `KeyboardInterrupt`), so a library's `except Exception:` cannot swallow it.
  - `ShutdownSignal`: `install()` registers `handle` for `SIGTERM` and keeps the previous handler; `uninstall()` restores it; `requested() -> bool`; `job()` is a context manager marking "record work in progress"; `handle(signum, frame)` records the request and, **if inside `job()` and it has not raised before, raises `ShutdownRequested` at once** (at most once per `ShutdownSignal`). Reason: a pipeline stage lasts minutes (video encoding 3–7 minutes) and the machine's shutdown does not wait that long, so waiting for the next stage boundary would almost never release the job.
  - `handle_record(bucket, key, *, s3, cfg, roi_masks, heartbeat) -> Outcome` (replaces M2a's signature): `heartbeat` is a zero-argument callable returning a `Heartbeat` for the current message (built by `process_message`); the record's work after the result-exists check runs inside it. It still never touches SQS itself. A rejection (oversize, too long, unreadable) writes status `failed` with a plain reason (§4) before returning `REJECTED`.
  - `process_message(message, *, s3, sqs, cfg, roi_masks, shutdown, max_receives) -> None` (replaces M2a's signature; the queue URL comes from `cfg`). The attempt is final when the message's `ApproximateReceiveCount` (an integer string) is at least `max_receives`.
    - If `shutdown.requested()` already: release the message untouched and raise `ShutdownRequested`.
    - Otherwise run each record's `handle_record` inside `shutdown.job()`; **only the record work is inside `job()`**: the status writes, `release` and `delete_message` below run outside it, so a signal cannot interrupt them.
    - Delete the message only when every record returned a final outcome.
    - On `ShutdownRequested`, or any exception while `shutdown.requested()` is true: release the message; on the final attempt, if no result exists, write `failed` with "The job was interrupted. Please upload it again." (otherwise no status change); then raise `ShutdownRequested`.
    - On any other exception: the §4 failure status (only if no result exists), then release; returns normally.
  - `run() -> None`: installs a `ShutdownSignal`; reads `max_receives` once from the job queue's `RedrivePolicy` (`maxReceiveCount`, parsed as an integer; one source of truth in Terraform) and refuses to start, naming it, if the queue has none; receives with `MessageSystemAttributeNames=["ApproximateReceiveCount"]`; on `ShutdownRequested` or a shutdown request between messages it stops polling and **returns** (exit code 0, so systemd does not restart it).
  - The idle exit is removed: `worker.idle_exit_minutes`, its handling in `run()` and M2a's idle-exit test go (a spec'd test change by the test-writing agent). Scale-in (§2a) ends idle workers.
  - `boot_record(boot_log_text, ready_utc, instance_id, instance_type) -> dict`: pure; parses the boot log's timestamped steps (M2a §4c) into the fields of §10's `cold_start.csv` that the machine knows. `run()` writes it to `experiments/boots/<instance-id>.json` once the model is loaded ("Worker ready"), and after the worker's first job writes `experiments/boots/<instance-id>-first-job.json` with that job's `transcribing` seconds. Both writes log and continue on error. Skipped in fake mode and when not on AWS (no `NEUROLENS_DEPLOYED`).
  - Config (`config.json`): `worker.heartbeat_seconds` (50) and `worker.max_job_minutes` (75; the heartbeat's `max_seconds`). The longest job measured is a 120 s 4K video with the first-job warm-up, about 55 minutes.
- `neurolens.inference` fake mode: `FAKE_INFERENCE_SECONDS` (environment, default 0, ignored unless `FAKE_INFERENCE=1`) makes each fake `predict` call sleep that long, so rehearsals have a job long enough to interrupt. Terraform variable `worker_fake_job_seconds` writes it into `env.conf` on the rehearsal worker only.
- `infra/lambda/breaker.py`, `handler(event, context)`: the circuit breaker (§2c). Reads the `neurolens-workers` group; if its minimum is 1 or more (a warm hold), logs and changes nothing; otherwise sets min 0 / max 0 / desired 0 and logs. Group name from the environment variable `WORKER_GROUP`.
- `neurolens.web.app` routes (the web tier still never imports `neurolens.inference`):
  - `job_id` must be a UUID; anything else returns 400 `{"error": "bad_job_id"}`.
  - `GET /api/jobs/<job_id>/status`: 200 with `{"job_id", "status": "done"}` if a result exists (whatever the status object says); otherwise 200 with the status object; otherwise 404 `{"error": "not_found"}` (the page treats 404 as "queued").
  - `GET /api/jobs/<job_id>/result`: 200 with the result JSON, or 404 `{"error": "not_found"}`.

## 2. Machine count: scaling, circuit breaker, warm hold

### 2a. Scaling on the queue
Scale on the whole queue, including messages being worked on. Scaling only on *visible* messages would be wrong: a running job's message is invisible, so the queue would look empty mid-job and the ASG could terminate the worker doing it.
- **Scale out:** a CloudWatch alarm on `ApproximateNumberOfMessagesVisible` ≥ 1 for 1 minute triggers a step scaling policy that adds 1 instance, with an estimated instance warm-up from the Terraform variable `scale_out_warmup_seconds` (default **900 s**: a GPU boot is about 6.5 minutes and the SQS metric lags 1–3 minutes, so one waiting job never launches a second worker while the first boots). While the alarm stays in alarm, AWS repeats an Auto Scaling action every minute, so a backlog keeps adding workers up to the ASG max.
- **Scale in:** a metric-math alarm on `FILL(visible, 0) + FILL(not_visible, 0)` = 0 for 15 consecutive minutes (`ApproximateNumberOfMessagesVisible` + `ApproximateNumberOfMessagesNotVisible`), `treat_missing_data = "breaching"`, uses M2a's existing "set to 0" policy. A queue that publishes no metrics (quiet for hours) counts as empty. Verify that behaviour once with `get-metric-data` on the quiet queue, as in M2a.
- SQS publishes these metrics about once a minute, and after a long quiet period they can take up to about 15 minutes to resume. That delay is part of the measured cold start (§10).
- Outside a session the group's max is 0 (`stop_work.sh`), so an upload waits in the queue and starts a worker in the next session. `start_work.sh` sets max 1, after which an upload starts a worker by itself.
- Standing configuration stays **min 0 / max 1 / desired 0**. Max 2 only during Experiment 2 (`start_work.sh --max 2`), then back to 1.

### 2b. Worker self-termination removed
- UserData's existing drop-in gains `OnSuccess=` and `OnFailure=` (empty, which clears the baked unit's lists) under `[Unit]`, and `TimeoutStopSec=110` under `[Service]`. The baked crash limit (3 starts per hour, M2a §3) stays: after it, the service stays failed and the machine waits for the breaker.
- UserData's `ERR` trap only logs; the machine waits for the breaker.
- `infra/self_terminate.sh` becomes a stub that logs "self-termination removed (M2b)" and exits 0. It must stay in the code bundle: the `pull_code.sh` copy baked into `neurolens-worker-v2` installs it on every start and fails if it is missing. `neurolens-self-terminate.service`, the stub and its line in `pull_code.sh` and `build_ami.sh` are removed at the next image rebuild (added to M2a §12's list).
- Removed from the worker's IAM role and the role boundary: `autoscaling:TerminateInstanceInAutoScalingGroup`. The worker has no Auto Scaling permission at all.
- A manual `systemctl stop neurolens-worker` no longer ends the machine (useful for debugging).

### 2c. Circuit breaker
M2a's idle alarm (a worker in service and no message received or deleted for 90 minutes) now has three actions: the "set to 0" policy (as in M2a; repeats every minute while in alarm), the email, and an **EventBridge rule** on its change to `ALARM` that invokes `infra/lambda/breaker.py` (§1a), which sets the group to max 0. With max 0, nothing launches again until `start_work.sh` (which sets max 1). This is what stops a failure from looping: broken code, a crash loop, a hung job or a down NAT instance with a job waiting costs at most about 1.5–2 hours of GPU (about $3–4), then nothing more.
- During a warm hold the breaker changes nothing (a hold is deliberately idle). After the hold ends, the idle alarm's repeating "set to 0" action and the scale-in alarm remove the worker; if a broken worker with a waiting job is replaced, the replacement trips the breaker within 90 minutes.
- Terraform: the Lambda (Python 3.12, packaged with `archive_file`), its role `neurolens-breaker` (with the role boundary; `autoscaling:DescribeAutoScalingGroups`, `autoscaling:UpdateAutoScalingGroup` on this group, and its own log group), the EventBridge rule and target, and the invoke permission. Cost: effectively $0.
- The alarm's description and email text: "AWS has stopped all NeuroLens workers (max 0) after 90 minutes without queue activity, unless a warm hold is active. Run infra/start_work.sh to start again."
- `stop_work.sh` and `start_work.sh` print a line when they find the group at max 0 with the NAT instance running (the breaker tripped during the session).
- IAM policy text for Josh to paste (prepared alongside the code): the deploy user gets `lambda:*` on `function:neurolens-*`, `events:*` on `rule/neurolens-*`, `logs:*` on `log-group:/aws/lambda/neurolens-*`, `iam:PassRole` for `neurolens-*` roles to `lambda.amazonaws.com`, and `cloudwatch:DescribeAlarmHistory` (§10); the role boundary allows the breaker's Auto Scaling and log actions on `neurolens-*` resources. M3a's Lambdas reuse these.

### 2d. Warm hold (demos and study sessions)
- `start_work.sh --worker [--hours N]` (N from 1 to 4, default 3): **first** creates or replaces the one-off scheduled action `neurolens-warm-hold-end` at now + N hours, which sets the group's **minimum to 0** (max and desired untouched); then sets min 1, max 1, desired 1. If creating the scheduled action fails, it stops with an error and starts nothing. Re-running it during a hold moves the end to N hours from now. The 4-hour cap bounds the cost of a forgotten hold (about $7.50), since a hold deliberately turns off every automatic stop.
- While the minimum is 1, AWS keeps the worker: scale-in and the idle alarm's policy cannot go below the minimum, and the breaker skips. When the hold ends, the minimum is 0 again and the normal rules apply: an idle worker is removed within minutes. A job in progress is never cut: these rules only act when the queue is empty or nothing has moved for 90 minutes.
- `start_work.sh` without `--worker` sets max 1 (or `--max 2`) only; it never changes the minimum or the hold timer, so it cannot end a running hold.
- `stop_work.sh` also deletes `neurolens-warm-hold-end` (absent is fine; any other error is a failed check, NOT CONFIRMED) and sets the group to 0 / 0 / 0 as before.
- The deploy user's existing `autoscaling:*` on `neurolens-*` groups covers scheduled actions.

### 2e. Demo routine (also goes into the README)
1. The day before (the week before for the TA demo): one full dry run of steps 2–5.
2. 45–60 minutes before: `start_work.sh --worker --hours 3` (demo length + early start + margin). Starting early leaves time to fall back to the no-AWS backup (M4) if no GPU is free.
3. Wait for "Worker ready" (`connect_worker.sh`, or the result of step 4).
4. One warm-up job: upload a short sample clip, so the first-job costs are paid before the audience arrives and the whole chain is proven that day.
5. Demo. If it overruns the hold, run step 2 again.
6. Straight after: `stop_work.sh`, and wait for ALL STOPPED.
Cost: about 1.5–2 hours of GPU, roughly $3–4.

### 2f. IAM additions to M2a's worker profile
- `s3:GetObject` and `s3:PutObject` on `status/*`; `s3:PutObject` on `experiments/*`. (M2a's `s3:ListBucket` has no prefix condition, so a missing status object reads as 404. The boundary's `s3:*`/`sqs:*` on `neurolens-*` already covers these.)
- `sqs:ChangeMessageVisibility` on the job queue.

## 3. Queue changes (Terraform, on M1's queue)
- **Visibility timeout 120 s.** The §6 heartbeat keeps long jobs invisible; a dead worker's job is retried within about two minutes.
- The dead-letter queue (`maxReceiveCount = 2`, M2a §4h) is unchanged. Every receive counts, including one that ends in a shutdown release; §1a's final-attempt status covers that case. The worker reads `maxReceiveCount` at start (§1a); Experiment 2 reads the dead-letter count for its reliability numbers.

## 4. Job status tracking
Status objects at `status/{job_id}.json` in the same bucket:
```json
{ "job_id": "...", "status": "processing|done|failed",
  "stage": "downloading|transcribing|inference_full|inference_noaudio|extracting_roi|retrying|null",
  "updated_at": "2026-10-14T03:22:10Z",
  "stages": [{"stage": "downloading", "at": "2026-10-14T03:22:10Z"}],
  "error": null }
```
- `job_id` is the UUID from the presign step. There is no `queued` status: before a worker takes the job, no status object exists and the endpoint returns 404.
- The worker writes `processing` as soon as it takes a record (after the result-exists check, before any real work), and updates `stage` at each pipeline step: `downloading`, `transcribing` (`build_events`, where WhisperX runs), `inference_full`, `inference_noaudio`, `extracting_roi`. Experiment 1 reads `stages`.
- Write order: `put_result` first. Only if it returns `True`, write `status: "done"`. If it returns `False`, return `DUPLICATE` without touching status.
- **Failures:** on a non-final attempt, `status: "processing"`, `stage: "retrying"`, `error` set (the job will run again). On the final attempt (§1a), `status: "failed"` with `error`. A shutdown on the final attempt writes `failed` with "The job was interrupted. Please upload it again." (§1a). A rejection is always final: `failed` with a plain reason ("The video is longer than 120 seconds.", "The file is not a readable video.", "The file is larger than the upload limit."). `error` is a short user-facing text (at most 200 characters, no traceback); the full traceback goes to the worker log.

## 5. Duplicate deliveries
SQS may deliver a message more than once, and one S3 event message can hold several `Records`. `process_message` already handles each record independently and deletes the message only when every record has a final outcome (M1). M2b adds:
- If `result_exists` is true before any work, return `SKIPPED` without running inference or changing status.
- If `put_result` returns `False`, another worker finished first: `DUPLICATE`, as in M2a.

With at most two workers, a duplicate that wastes one GPU run is rare and accepted; results and status are never duplicated. M3 adds a database lock that prevents the duplicate run itself. Do not add S3 lease or claim objects.

## 6. Heartbeat, fast release and shutdown
**Heartbeat:** every record's processing runs inside `Heartbeat(..., interval_seconds=worker.heartbeat_seconds, max_seconds=60 * worker.max_job_minutes)`. A silently dead worker stops the heartbeat, and the message becomes visible again within about 120 s. `release` always happens after the heartbeat block has exited (the record's exception passes out of it first), and §1a's flag-under-lock rule means no beat can hide the message again.

**Fast release on any failure** (download, ffprobe, inference, ROI extraction): `process_message` writes the §4 failure status (only if no result exists), then calls `release(...)` so the message is immediately retryable. It does not delete it; after two receives it moves to the dead-letter queue.

**Shutdown** (AWS ends the machine, the group scales in, `stop_work.sh`, or `systemctl stop`/`restart`): systemd sends the worker `SIGTERM`. Inside a job, `ShutdownSignal` raises `ShutdownRequested` at once and `process_message` handles it as §1a says. Any exception caught while `shutdown.requested()` is true counts as a shutdown, not a failure: systemd stops the whole service, including WhisperX's or ffmpeg's subprocess, which may fail first. Between jobs, `run()` stops after the current long poll (at most 20 s). If the process is killed anyway (`TimeoutStopSec=110`, §2b), the heartbeat dies with it and the message becomes visible again within about 120 s.

**One real-job check** (GPU, in the Experiment 1 session, about $0.10): `systemctl stop neurolens-worker` during a real job's video encoding; the message must be visible again within about 10 s. A fake job only sleeps, which reacts to the signal faster than real compiled code does, so the CPU rehearsal alone does not prove this.

## 7. Frontend: real polling
After the presigned S3 POST succeeds (204), the page currently shows "Uploaded — processing (job …)" and stops. Replace that with:
- Put the job in the page's address (`history.replaceState` to `?job=<job_id>`). On load, a `?job=` parameter starts polling directly, so the user can close the tab and come back with the link (results are kept 30 days).
- Poll `GET /api/jobs/{job_id}/status` every 5 s and show the state in the existing upload panel's message line and progress area (reuse the visual design):
  - 404: "Queued. If no GPU worker is running, starting one can take up to about 20 minutes. You can close this page and come back with this link."
  - `downloading` / `transcribing` / `inference_full` / `inference_noaudio` / `extracting_roi`: a plain description of each step.
  - `retrying`: "Retrying after an error: <error>".
  - `failed`: the real `error` text, no `alert()`.
  - `done`: fetch `GET /api/jobs/{job_id}/result` and render it through the same code path as a sample result.
- After 30 minutes without `done` or `failed`, add "This is taking longer than expected" (a constant at the top of the script). Polling continues.
- Record the time from receiving the result to the chart being drawn in `window.neurolensTimings.render_ms` (Experiment 1).

## 8. First-job warm-up (WhisperX)
A fresh worker's first job spends about 6 minutes in WhisperX (5 min 41 s on `neurolens-worker-v2`); later jobs take about 14 s. tribev2 runs WhisperX through `uvx`, which on its first call after boot rebuilt its tool environment (6,904 files, 119 MB, partly fetched through the NAT instance).
1. **Diagnose on the CPU rehearsal worker** (the environment rebuild is not GPU work): on a fresh worker from the current image, run the exact `uvx` command tribev2 runs (read it from the pinned tribev2 source) twice on a few seconds of audio, timing each and noting network use. Record the cause in the build log.
2. **Fix, in this order of preference:** (a) an environment setting in `env.conf` that lets `uvx` reuse the environment baked into the image (if the rebuild comes from a refresh check); (b) otherwise a boot-time warm-up: UserData writes and starts a one-shot unit that runs the same command on one second of silence while the model loads (about 3.5 minutes), so it is done before the first job; (c) an image change, only if (a) and (b) fail.
3. If the fix needs an image rebuild, the same rebuild carries out M2a §12's deferred items and §2b's removals.
4. Pass: on a fresh GPU worker, the first job's `transcribing` stage takes within 1 minute of a later job's.

## 9. 4K downscale test
A 55 s 4K clip took 4.4 times longer than a 480p clip of similar length (video encoding 18.2 s per second of video, GPU busy 15%, CPU 54% of 4 cores): decoding 4K frames on the CPU limits the job, while the model only looks at a 256 x 256 centre square of each frame (its processor resizes the short side to 292). Shrinking the video first would likely remove most of this, but only if it does not change the results. The pass mark is fixed here, before any run.

**Step A (no code), in the Experiment 1 GPU session:**
- From the exact 4K test file (`bbb_4k_55s_38mbps.mp4`, `data/videos/SOURCES.md`), cut a 4K clip of about 30 s with stream copy (no re-encode). From that cut, make **lossless** H.264 copies at 720p and 480p (`-qp 0`, same aspect ratio) with the **audio stream copied unchanged**, so the resize is the only difference. Choose the length so the largest file is under `max_upload_bytes`, and check the sizes before uploading.
- Upload the 4K cut, the 720p copy and the 480p copy as normal jobs on the same worker, image and code, after the session's warm-up job (no first-job warm-up in any of them).
- **Pass:** every value of a copy's result within **0.001** of the 4K cut's result from the same session (measured noise: 0.0000 on the same GPU, at most 0.0003 across GPUs). Compare the `inference_full` stage times. The smallest resolution that passes is the candidate.
- Limits to state in the report: one animated clip. A live-action 4K clip (Tears of Steel, 6.7 GB source) is added only if Josh agrees to the extra cost.

**Step B (only if Step A passes; tests first like the rest):**
- Config `video.downscale_height` (`null` = off, the default until the check below passes). When set and the video's height is above it, the worker makes a lossless copy at that height on the local disk with ffmpeg (audio copied) before `build_events`, and uses it for both passes. A pure function `inference.downscale_command(src, dst, height) -> list[str]` builds the command (tested); `inference.probe_height(path) -> int` reads the height.
- Check: upload the original 55 s 4K file with the setting off and on in one session. Pass: every value within 0.001, and the job time including ffmpeg is shorter. Only then set the default in `config.json`.

## 10. Experiments instrumentation
- **Shared artifact contract** (binding for Experiments 1–3 and M4's study export; M4's consolidation script and its tests are written against exactly this). Each run writes `experiments/<experiment>/<run_id>/` in the bucket (`experiment` is `experiment-1`, `experiment-2`, `experiment-3` or `study`). `experiments/*` never expires and is the sole input for M4's consolidation.
  - **`manifest.json`, every run:** `experiment`, `run_id`, `series` (a name grouping runs that belong together, e.g. `exp1-final`; consolidation combines all runs of one series), `started_utc`, `finished_utc`, `code_revision` (the git short hash of the code used: `/opt/neurolens/app/REVISION` on AWS, `git rev-parse --short HEAD` elsewhere), `environment` (`{"type": "aws", "instance_type", "ami_id"}` or `{"type": "onprem", "host", "gpu"}`), `files` (the file names below, each of which must exist).
  - **`experiment-1`**: `runs.csv`, one row per job: `clip_seconds, job_label, upload_ms, queue_wait_ms, downloading_ms, transcribing_ms, inference_full_ms, inference_noaudio_ms, extracting_roi_ms, result_fetch_ms, render_ms, peak_vram_gb`. `render_ms` may be empty (filled by hand for 3 runs per clip length); `peak_vram_gb` comes from the result's `gpu` field. A stage's duration is the next stage's start minus its own; the last stage ends at the job's `done` time.
  - **`experiment-2`**:
    - `jobs.csv`: `burst_size, job_label, submitted_utc, done_utc, status`.
    - `cloudwatch.csv`: `minute_utc, sqs_visible, sqs_in_flight, asg_in_service`.
    - `locust_stats.csv`: Locust's own `--csv` stats file, copied as is.
    - `reliability.csv`, one row per burst: `burst_size, submitted, results, duplicate_results, dead_lettered`.
    - `instance_loss.csv`, the instance-loss test (a worker terminated mid-job): `injected_utc, released_utc, completed_utc, recovered`.
    - `cold_start.csv`, one row per GPU boot: `boot_utc, trigger, instance_type, metric_delay_s, capacity_wait_s, launch_to_userdata_s, weight_sync_s, model_load_s, ready_s, first_job_transcribing_s`. `trigger` is `alarm` (scale-out) or `manual` (`start_work.sh --worker`); `metric_delay_s` (upload to scale-out alarm) is empty for `manual`; `capacity_wait_s` is from the group's desired capacity rising to the instance launch (long when AWS has no GPU free); `ready_s` is from launch to "Worker ready"; `first_job_transcribing_s` is empty if the worker ran no job.
  - **`experiment-3`**: `runs.csv`: `environment` (`cloud` or `onprem`), `gpu, clip_seconds, run_index, wall_ms`. Cloud rows may be copied from an Experiment 1 series, named in the manifest's `source_series`.
  - **`study`**: see M4 §4.
  - `job_label` is a short label (`J1`, `J2`, …) unique within the run, never a job ID.
- **Every GPU boot is a cold-start sample.** Each worker writes its boot record and first-job record to `experiments/boots/` (§1a `boot_record`), because the machine's own disk disappears with it. `experiments/cold_start.py --since <date> --run-id <id>` combines them with the group's scaling activity history (`describe-scaling-activities`) and the scale-out alarm's history (`describe-alarm-history`) into `cold_start.csv` for an `experiment-2` run. Boots from every session (experiments, 4K test, M3 sessions, demo dry runs) count, so the report gives ranges with their number of boots instead of one figure from two boots. `experiments/boots/` holds raw input only; consolidation reads runs.
- **Experiment 1 (latency)** covers the stages the report promises: upload → queue wait → processing stages → result fetch → client render, for **15 s, 30 s, 60 s and 120 s** test videos (3 runs each), cut with ffmpeg from open-licensed videos and listed in `data/videos/SOURCES.md`.
  - Run on a **warm** worker (`start_work.sh --worker`, then one warm-up job), so queue wait measures the queue, not a cold start (Experiment 2 measures that).
  - `experiments/latency_run.py <clip>` does one run like a browser would: presign, upload via the presigned POST (timed), poll status every 2 s until `done`, fetch the result (timed). It saves the upload and fetch times and the job's `stages` list.
  - Queue wait = first stage time − upload end. Processing stages come from `stages`.
  - Client render: read `window.neurolensTimings.render_ms` in the browser console on 3 runs per clip length and add it to the run's CSV by hand.
  - Rows follow the contract's `experiment-1/runs.csv`. Everything goes into `experiments/experiment-1/<run_id>/` straight away (`status/` objects expire after 48 hours, and M3a retires them). Analysis script `experiments/latency_breakdown.py`.
  - The same session also runs §9 Step A and the §6 real-job stop check. Estimate about $3–3.60 (about 1.6–1.9 GPU hours: 12 warm jobs about 50 minutes, boot and warm-up job, §9 Step A about 15 minutes, the stop check, manual timings); flag it before starting.
- **Experiment 2 (scaling):** `locustfile.py` POSTs to `/api/uploads/presign`, uploads the fixed 15 s test video via the presigned POST (fields first, file last), then polls status to completion. Burst sizes match the report: **1, 5, 10 and 20** simultaneous jobs, `start_work.sh --max 2`.
  - **One session, bursts back to back** (each after the previous has drained, without waiting for scale-in): burst 1 starts from zero workers (scale-out from 0, ideally after a quiet day, so the SQS metric delay is included); burst 5 grows the pool from 1 to 2; bursts 10 and 20 go beyond the 2-worker ceiling on purpose, to show the queue absorbing the backlog, no dropped or duplicated results, and a full drain. After burst 20, the group returns to 0 by the scale-in alarm (measured once).
  - For this session set `worker_instance_types = ["g6e.xlarge"]` (Terraform), since the 8-vCPU GPU quota fits two `xlarge` but only one `2xlarge`; restore it afterwards. A terminating machine may hold quota for a minute or two, which can delay the instance-loss replacement; record it if seen.
  - **Instance loss (simulated):** during burst 10, terminate one busy worker (`aws ec2 terminate-instances`). The machine's shutdown sends the worker `SIGTERM`, so it releases its job; the other worker or the group's replacement completes it. Record the times in `instance_loss.csv`. The report describes this as a simulated instance loss: the same shutdown a Spot reclaim or hardware failure would cause.
  - Each run saves the contract's `experiment-2` files: Locust's CSV output, per-job times, the reliability counts (results vs submissions, duplicates, dead-lettered jobs), and `experiments/export_cloudwatch.py <run_id> --start --end` for the one-minute SQS and ASG series.
  - Estimate about $5–6 (about 2.5–3 GPU hours); flag it before starting.
- **Cold-start breakdown** (from `cold_start.csv`): SQS metric delay → scale-out alarm; GPU capacity wait; launch → UserData start; S3 weight sync; model load; ready; first job's transcribing. No target number is asserted in advance. Report the machine-side parts as ranges with their sample count, and the AWS-side waits (capacity, metric delay) as observed events.

## 11. Tests (`FAKE_INFERENCE=1 pytest`, moto)
Add to the suite, written first against §1a. `requirements/dev.txt` changes `moto[s3,sqs]` to `moto[s3,sqs,autoscaling]`. If the pinned moto ignores `MessageSystemAttributeNames`, the test sets the receive count through moto's own behaviour (receive the message twice) and says so in a comment.
- `Heartbeat` with `interval_seconds=0.05` calls `change_message_visibility` with 120 repeatedly while the block runs and not after it exits; no call before the first interval; a failing call does not stop later beats; with a small `max_seconds` the beats stop after it while the block is still running.
- `release` logs and does not raise when the call fails.
- A record that raises on a non-final attempt leads to status `processing` / `retrying` with the error, and on the final attempt (`ApproximateReceiveCount` ≥ `max_receives`) to `failed`; both when no result exists; then `release` with `VisibilityTimeout=0`, after the last heartbeat call; the message is not deleted.
- A rejected record (oversize, too long, unreadable) writes `failed` with its plain reason.
- A record whose result already exists returns `SKIPPED` with no inference and no status change.
- A lost conditional write returns `DUPLICATE` and leaves the existing result and `done` status untouched; `put_status` never changes a `done` object; `put_status` with no existing object creates one with `job_id` and `stages`.
- `put_status` appends to `stages` in order, with the injected `now`; the worker's stages appear in §4's order, including `transcribing`.
- A multi-record message is deleted only after all its records have final outcomes.
- Shutdown (each test installs and then uninstalls its `ShutdownSignal`):
  - `ShutdownSignal.handle` called from inside a fake pipeline stage, and once through a real `os.kill(os.getpid(), signal.SIGTERM)`, raises `ShutdownRequested` at once; a second signal does not raise again; outside `job()` it only records the request.
  - `process_message` then releases the message (visibility 0), does not delete it, leaves status unchanged on a non-final attempt, writes the "interrupted" `failed` status on the final attempt when no result exists, and re-raises.
  - An ordinary exception raised after a shutdown request is treated the same way.
  - A message received after a request is released untouched.
  - `run()` then receives no more messages and returns normally.
  - `ShutdownRequested` is not caught by `except Exception`.
- `run()` reads `max_receives` from the queue's `RedrivePolicy` (as an integer) and refuses to start when the queue has none; `run()` has no idle exit (with no messages it keeps polling until a shutdown request).
- `boot_record` on a sample boot log (fixture copied from a real worker's `/var/log/neurolens-boot.log`) gives the expected step durations.
- Fake mode: `FAKE_INFERENCE_SECONDS` makes `predict` take that long; ignored without `FAKE_INFERENCE=1`.
- Breaker (moto Auto Scaling): with min 0 the group ends at 0 / 0 / 0; with min 1 it is unchanged.
- Status endpoint: `done` when a result exists even if the status object says `processing`; the status object otherwise; 404 when neither exists; 400 for a non-UUID `job_id`. Result endpoint: 200 with the stored JSON, 404 when missing.
- Import hygiene still holds: the web app with the new endpoints never loads `neurolens.inference` or `torch`.
- §9 Step B only, if it goes ahead: `downscale_command` keeps the aspect ratio, is lossless and copies the audio; the worker downscales only when the setting is on and the video is taller than it, and uses the copy for both passes.
- Experiment scripts: `cold_start.py` and `latency_breakdown.py` on small fixture files produce the contract's columns.

## 12. File layout additions
```
infra/terraform/          MODIFIED: scale-out/in alarms and policies, breaker Lambda + EventBridge rule + role,
                                    120 s visibility, IAM changes, UserData drop-in (OnSuccess=/OnFailure=,
                                    TimeoutStopSec), ERR trap logs only, no weight sync in fake mode,
                                    scale_out_warmup_seconds, worker_fake_job_seconds
infra/lambda/breaker.py   NEW: circuit breaker (§2c)
infra/iam/*.json          MODIFIED: deploy user (Lambda, EventBridge, logs, PassRole to Lambda,
                                    DescribeAlarmHistory); boundary (breaker's actions; no TerminateInstance)
infra/start_work.sh       MODIFIED: --worker --hours N warm hold with its scheduled end; --max 2; plain start sets max only
infra/stop_work.sh        MODIFIED: deletes the hold's scheduled action; reports a tripped breaker
infra/self_terminate.sh   MODIFIED: stub until the next image rebuild (§2b)
infra/terraform/README.md MODIFIED: §2e demo routine, breaker, no self-termination
neurolens/worker.py       MODIFIED: status writes, SKIPPED, Heartbeat, release, ShutdownSignal, boot_record; idle exit removed
neurolens/storage.py      MODIFIED: result_exists, get_result, get_status, put_status
neurolens/inference.py    MODIFIED: FAKE_INFERENCE_SECONDS (and §9 Step B, only if it goes ahead)
neurolens/web/app.py      MODIFIED: /api/jobs/<id>/status and /api/jobs/<id>/result
config.json               MODIFIED: worker.heartbeat_seconds, worker.max_job_minutes
locustfile.py             NEW
requirements/experiments.txt  NEW: locust, matplotlib (experiment scripts; M4's consolidation also uses matplotlib)
requirements/dev.txt      MODIFIED: adds -r experiments.txt; moto[s3,sqs,autoscaling]
experiments/latency_run.py         NEW: Experiment 1 single run
experiments/latency_breakdown.py   NEW: Experiment 1 analysis
experiments/export_cloudwatch.py   NEW: Experiment 2 CloudWatch series
experiments/cold_start.py          NEW: cold_start.csv from boot records and scaling history
tests/                    MODIFIED: §11 tests; M2a's idle-exit test removed
static/                   MODIFIED: real polling, ?job= link, render timing
docs/M2b_build_log.md     NEW: §8 diagnosis, §9 results, measurements and choices
```

## 13. Acceptance criteria
CPU = rehearsal worker in fake mode with a slow fake job; GPU = real worker.
1. **Scaling (CPU):** with `start_work.sh` (no `--worker`), one upload scales the group 0 → 1 by the alarm, the job completes, and the group returns to 0 about 15 minutes after the queue empties, visible in CloudWatch; no worker is terminated mid-job. With `--max 2`, one upload launches only one worker. (GPU, Experiment 2: 0 → 1 → 2 and back to 0.)
2. **Duplicates (CPU):** two identical SQS messages for one `job_id` (manual re-send) produce exactly one result object and one `done` status; the second delivery returns `SKIPPED` or `DUPLICATE`.
3. **Crash (CPU):** `kill -9` of the worker process mid-job makes the job visible again within about 120 s, and a second attempt completes it.
4. **Instance loss (GPU, Experiment 2):** terminating a worker mid-job releases its job (visible again within seconds, recorded), another attempt completes it, and there is exactly one result.
5. **Stop (CPU, then once on GPU):** `systemctl stop neurolens-worker` mid-job makes the job visible again within about 10 s, and the machine keeps running; on GPU during real video encoding (§6).
6. **Warm hold (CPU):** after `start_work.sh --worker --hours 1`, the worker **process** is still running and polling after 45 minutes with an empty queue (`systemctl is-active` and recent log lines). After the hold ends, the worker is removed without any command. `stop_work.sh` then leaves min 0 and no `neurolens-warm-hold-end` action. A plain `start_work.sh` during a hold leaves the minimum at 1. `--hours 5` is refused.
7. **Breaker (CPU):** with a job waiting and the worker's code broken on purpose (as in M2a criterion 19), the group launches one worker, the idle alarm fires within about 90–100 minutes, the breaker sets max 0, the worker ends and **no further worker launches** while the job still waits (watch 15 minutes). During a warm hold the breaker changes nothing. `start_work.sh` sets max 1 again.
8. **Page:** shows real, changing status text; "retrying" after a first failure and the real error after the final one; a non-video file renamed `.mp4` shows "not a readable video"; reopening the `?job=` link after closing the tab resumes and shows the result.
9. **Dead-letter (CPU):** a job forced to fail twice lands in the dead-letter queue, with status `retrying` after the first failure and `failed` after the second.
10. A crash after the result write but before the `done` status still reports `done` from `GET /api/jobs/{job_id}/status`, and a duplicate delivery does not reprocess (§11 tests).
11. **Warm-up (GPU):** §8's cause recorded; on a fresh worker, the first job's `transcribing` stage is within 1 minute of a later job's.
12. **4K (GPU):** §9 Step A results recorded against the fixed pass mark; Step B done and checked only if Step A passed.
13. **Experiments:** Experiments 1 and 2 have manifests and data under `experiments/`, including the cold-start table with every M2b GPU boot and the instance-loss test.
14. `terraform apply` run twice reports no changes the second time; the scale-in alarm reads a quiet queue as empty (`get-metric-data`).
15. `FAKE_INFERENCE=1 pytest` passes locally and in CI, including the §11 tests, and the tests were committed before the implementation.

## 14. Between sessions, cost and teardown
- End every working session with `stop_work.sh`; restore `worker_instance_types` after Experiment 2.
- **M2b GPU spend estimate:** Experiment 1 session with §9 Step A and the stop check about $3–3.60; Experiment 2 about $5–6; warm-up fix check and §9 Step B about $1 each; CPU rehearsals (including the 2-hour breaker test) about $1. About $11–13 in total. Flag each session's estimate before starting it.
- Keep the software-only image, `models/`, `code/latest.zip` and `experiments/`; M3 and M4 reuse them. Keep the 4K baseline result until §9 is decided.
- Final teardown (`terraform destroy`, deregistering images, deleting snapshots) happens only after M4's consolidation.

## 15. Explicitly not in this milestone
No Aurora, no user accounts or sign-in, no email notification, no credits, billing or refunds, no web-tier load balancer, no containers, no Auto Scaling warm pool (stopped pre-initialised machines; mentioned in the report as the funded-product option for shorter cold starts).

**Known limitations** (accepted):
- A broken worker runs up to about 90 minutes (about $3) before the breaker stops it; a worker that ended itself would have cost about 13 minutes, but caused relaunch loops with scale-out (§2, "one owner").
- A job dead-lettered after two hard crashes (`kill -9`, power loss) never gets a `failed` status; the page shows its last stage and, after 30 minutes, "taking longer than expected". M3a's dead-letter handling fixes this.
- With two workers, the idle alarm does not see one stuck worker while the other is active; the heartbeat's `max_job_minutes` releases a hung job, and two workers run only during Experiment 2.
- A warm hold with no jobs for 90 minutes makes the idle alarm send its email, although the hold keeps the worker (the email text says so).
- A `?job=` link whose result has expired (30 days) shows "Queued".
- The status and result endpoints answer anyone who knows a job's UUID (unguessable); M3a adds ownership checks.
