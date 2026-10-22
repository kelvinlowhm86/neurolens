# NeuroLens — M3e Implementation Spec: SageMaker Async GPU

**Builds on:** M1–M3d. The GPU work moves from our own EC2 worker group to a SageMaker Asynchronous Inference endpoint. SageMaker starts a GPU when a video is waiting and stops it when idle. It also replaces the machinery we built for that: the Auto Scaling group, its alarms, scale-in protection, the circuit breaker, the worker image build and the NAT Gateway. The website, sign-in, credit, consent and results stay as they are. The last custom-worker version is the tag `custom-gpu`.

**Full treatment** (tests first by a separate agent, break-it checks, Opus review): the dispatcher's checks and credit (§3), the container's charge and refund paths (§4c), every failure path (§5), the GPU guard (§6c) and the dataset clean-up (§7). They decide money, GPU spend and data deletion. Light treatment: Terraform, the image build, scripts, page wording.

## Decisions (22 Oct, Josh)
- **SageMaker Async is the design.** It needs no operator to switch processing on. The GPU starts on demand and stops after 7 idle minutes. The NAT Gateway is deleted, because nothing in the private network needs the internet any more: Lambdas and the container reach Aurora through the Data API and S3 through its public endpoint.
- **The container keeps the bookkeeping.** It claims the job, records progress stages, charges on success and refunds on failure. SageMaker only replaces how GPU machines start and stop.
- **Checks before the GPU.** A small Lambda (the dispatcher) checks the size, measures the duration with ffprobe and prices the job before anything is sent to SageMaker. Bad files and short credit never wake a GPU.
- **No automatic retry.** SageMaker does not retry a failed request (AWS docs, Async troubleshooting). A job that fails on the GPU is refunded at once and the user uploads again.
- **Automatic last line of defence.** A GPU guard (§6c) stops the GPU if it stays on with nothing to do. It reads our own database, not the scaling metrics it is guarding. It emails when it acts.
- **Hard time bounds.** A request waits at most 2 hours in SageMaker's queue and runs at most 60 minutes. So any job still open 4 hours after it was created is dead, and the reaper refunds it within the hour.
- **The image is built in AWS CodeBuild.** It starts from AWS's PyTorch inference image. The weights stay in S3 and SageMaker copies them in when an instance starts.
- **Research consent needs the retention curve** (agreed 22 Oct). A consented copy with no audience-retention curve 60 days after upload is deleted.

## 1. Scope
### In scope
The dispatcher Lambda; the container (`neurolens/worker.py` rewritten), its image and its build; the SageMaker model, endpoint, scaling and notifications in Terraform; the error-notification Lambda; the GPU guard; the reaper's new bounds and the dataset clean-up; migration 004; the web's pause check and History link; `start_work.sh`/`stop_work.sh` rewritten; the experiment scripts that read the worker group; deleting everything the custom workers needed; docs.

### Out of scope
- Running more than one GPU at a time (`max_instances` stays 1; quota is 2). Experiment 2's burst run is redesigned with M4.
- The teardown script and the account-switch kit (their own plan steps). `docs/M4_spec.md` §1 and §7 are updated to the new resources in the M4 rewrite (plan step 7), not here.
- Drawing the real retention curve next to the predicted one.

## 2. How a job flows
1. `POST /api/uploads/presign` reserves credit and creates the `queued` job, as now. It refuses with 503 `processing_paused` when the endpoint's scaling maximum is 0 (§8).
2. The browser uploads to S3. S3 notifies the jobs queue (SQS, unchanged), and the queue triggers the dispatcher (§3).
3. The dispatcher checks the file, prices the job and calls `InvokeEndpointAsync`. The job stays `queued`.
4. If no GPU is running, SageMaker's `HasBacklogWithoutCapacity` alarm starts one (§6a).
5. SageMaker downloads the upload and POSTs it to the container. The container claims the job, runs the analysis, writes the results and charges the job (§4).
6. If the container fails, it refunds the job itself. If it never answers (crash, timeout, lost machine) or the request expires in the queue, SageMaker's error notification reaches the error handler, which refunds (§5).
7. After 7 minutes with an empty queue the GPU stops (§6a). If that ever fails, the GPU guard stops it (§6c).

## 3. Dispatcher (`neurolens/lambdas/dispatcher.py`)
- **Trigger:** SQS event source on the jobs queue, batch size 1. The queue keeps its dead-letter queue and `maxReceiveCount`, and a message that fails that often goes to `dlq_handler` (unchanged), which refunds the job.
- **Config:** Terraform environment variables: `NEUROLENS_S3_BUCKET`, the database trio, `NEUROLENS_GPU_ENDPOINT`, `NEUROLENS_MAX_UPLOAD_BYTES`, `NEUROLENS_MAX_DURATION_SECONDS`. Terraform reads the last two from `config.json` (`jsondecode(file(...))`), so they have one source.
- **Per S3 record**, in this order:
  1. `job_id_from_key`; `billing.job_state`. Unknown job → log and skip.
  2. Terminal: if `failed`, delete the upload (a refund deleted it already and this one landed afterwards, M3a §7b); skip.
  3. `processing` → skip (already running).
  4. `head_object`: missing → refund `upload_missing` (no delete). Larger than the limit → refund `file_too_large`, delete the upload.
  5. Measure the duration with `media.probe_duration(url)` on a 10-minute presigned GET URL (SigV4, as the web's `_presigned_get`). `UnreadableVideo` → refund `unreadable_video`, delete the upload.
  6. `billing.verify_queued(db, job_id, round(duration * 1000), max_duration_s)`:
     - `ok` → invoke;
     - `not_queued` (cancelled or claimed meanwhile) → skip;
     - otherwise it refunded (too long, or not enough credit) → delete the upload.
  7. `InvokeEndpointAsync(EndpointName, InputLocation="s3://<bucket>/<key>", InferenceId=job_id, CustomAttributes=job_id, ContentType="application/octet-stream", RequestTTLSeconds=7200, InvocationTimeoutSeconds=3600)`.
- **Refunds** use `issue_refund(..., queued_before_s=0)`, so a job claimed meanwhile is never refunded.
- **Errors:** any other exception (S3, Data API, SageMaker throttling) propagates, so SQS redelivers the message. A second dispatch of the same job is harmless: `verify_queued` is idempotent (the price difference is then 0), and the container skips a job it cannot claim.
- **ffprobe:** a static Linux x86-64 ffprobe in a Lambda layer, built by `infra/build_ffprobe_layer.sh` from a pinned release URL with a SHA-256 check. Lambda memory 1024 MB, timeout 120 s, and `probe_duration`'s ffprobe timeout is a parameter here (90 s).
- **`neurolens/media.py`** (new, standard library only): `probe_duration(path_or_url, timeout_s=30)` and `UnreadableVideo`, moved unchanged from `inference.py` apart from the timeout parameter. `inference.py` and the container import them from here.

### 3a. `billing.verify_queued(db, job_id, verified_duration_ms, max_duration_s)`
The same rules as the old `verify` (length limit first, then record the duration, then adjust the reservation or refund for short credit), but guarded by `status = 'queued'` under the job's row lock instead of a claim. It returns `"ok"`, `"not_queued"` (nothing changed), or the refund reason it applied. The attempt-based `verify` is deleted.

## 4. The GPU container
### 4a. Serving (`neurolens/worker.py`, run by `python worker.py serve`)
- SageMaker's contract, on port 8080 (stdlib `ThreadingHTTPServer`):
  - `GET /ping` → 200 once the model is loaded. The server starts listening only after loading, so SageMaker's start-up health check waits for it.
  - `POST /invocations` → one job.
- `/ping` must answer while a job runs (AWS: a container that misses pings during a long request is replaced as unhealthy), so each request runs on its own thread and the job holds no lock that `/ping` needs.
- **Start-up**, in order:
  1. `validate_config`;
  2. the Data API client;
  3. `inference.load_model`, then `roi_masks`;
  4. a warm-up: `inference.build_events` on the bundled 3-second clip with speech (`data/warmup.mp4`), so the first real job pays no WhisperX set-up;
  5. the boot record (§10);
  6. listen.
- **Concurrency:** `MaxConcurrentInvocationsPerInstance = 1`, so SageMaker sends one job at a time. The handler refuses a second concurrent request with 503 as a guard.

### 4b. `POST /invocations`
- **Request:** the body is the video (SageMaker streams it from the upload). The handler streams it to a temporary file, never holding it in memory. The job id comes from the `X-Amzn-SageMaker-Custom-Attributes` header. A missing or malformed id → 400, logged.
- **Response:** JSON `{"job_id", "outcome"}`. 200 for `done`, `duplicate`, `rejected` and `skipped`. 500 for `failed`, after the refund (§4c), so SageMaker counts the failure and the error handler sees it (a no-op on a refunded job).
- **`handle_job(job_id, video_path, *, s3, db, cfg, roi_masks)`** keeps `handle_record`'s logic, with these changes:
  - The object key comes from the job row: `job_state` also returns `object_key` and `verified_duration_ms`.
  - Unknown job → `skipped`.
  - A result already exists → `settle_success`, then `skipped`.
  - `claim` fails on a terminal job → `skipped` (delete the upload if `failed`). It fails on a busy job → `skipped`: the holder finishes it, or the error handler refunds it once stale.
  - A claimed job with `verified_duration_ms` NULL was never dispatched → refund `not_verified`, logged as an error.
  - The `downloading` stage is gone, since SageMaker downloads. `probe_duration` runs on the local file for the analysis length only (credit was settled by the dispatcher). `UnreadableVideo` → refund `unreadable_video`.
  - The stages from `transcribing` on, the dataset copy, the claim checks before every write, the thumbnail, brain file, result and `settle_success` are unchanged (M3c §3b, M3d §5, §5b).
- **Claim keep-alive:** `ClaimKeepAlive(on_beat, interval_seconds, max_seconds)`. It is the old `Heartbeat` minus SQS: it calls `billing.touch` every `worker.heartbeat_seconds` (50) and stops after `worker.max_job_minutes` (now **55**), logging an error. A hung job's claim therefore goes stale before SageMaker's 60-minute timeout, so the error handler can refund it at once.

### 4c. Failure inside the container
- Any exception after the claim: `issue_refund(db, job_id, "processing_failed", _error_text(err), attempt=attempt)`, then delete every file of the job (`storage.job_keys`). Answer 500.
  - A refund that finds the claim lost does nothing, and the handler answers 500 too.
  - A failed file delete is logged with `REFUND_DELETE_FAILED <job_id>` and never undoes the refund. It still answers 500.
- A SIGTERM (SageMaker removing the instance) is not handled. The process ends, the claim goes stale within 90 s, and §5 refunds.

### 4d. Image (`infra/worker_image/Dockerfile`, `infra/worker_image/buildspec.yml`, `infra/build_image.sh`)
- **Base:** AWS's PyTorch inference GPU image for torch 2.6.0, CUDA 12.4, Python 3.12, pinned by digest in the Dockerfile. Our own `ENTRYPOINT ["python", "/opt/neurolens/worker.py", "serve"]` replaces its server.
- **Contents:**
  - the `neurolens` package;
  - `requirements/worker.txt` and `requirements/model.txt`;
  - ffmpeg;
  - `data/warmup.mp4`;
  - the WhisperX tool environment, created at build time with the exact `uvx` command tribev2 runs (so `UV_OFFLINE=1` works at run time).
- **No weights, tokens or secrets** in the image.
- **Environment at run time:** `HF_HUB_OFFLINE=1`, `UV_OFFLINE=1`, `NEUROLENS_DEPLOYED=1`, and the deployment values from the SageMaker model (§9). The model and data paths point at `/opt/ml/model`.
- **Weights:** SageMaker copies the uncompressed S3 prefix `model-data/` to `/opt/ml/model` when an instance starts. `infra/make_model_data.sh` fills that prefix once, by a server-side copy of `models/` without `*/blobs/*` (the layout the EC2 boot sync produced, including `data/hcp_mmp/`). It checks the object count and total size against the source.
- **Build:**
  - `infra/build_image.sh` zips the repository at `HEAD` (refusing a dirty tree), uploads it to `s3://<bucket>/build/source.zip`, starts the CodeBuild project, waits and prints the image digest.
  - The image is tagged with the commit hash in the ECR repository `neurolens-worker`.
  - Terraform's `worker_image` variable takes the digest.
  - A deploy is: build, set the digest, `terraform apply`.

## 5. Failure paths
| What failed | Who ends the job | How |
|---|---|---|
| Bad file, too long, short credit | dispatcher | refund before any GPU (§3) |
| Dispatcher keeps failing | `dlq_handler` (unchanged) | refund after `maxReceiveCount` deliveries |
| Analysis raises | container | refund at once (§4c) |
| Container crash, SIGTERM, hang, 60-min timeout | error handler | SageMaker error notification (below) |
| Request waits 2 hours (no GPU capacity) | error handler | the expired request's error notification |
| A notification is lost | reaper | any open job 4 h after creation |

- **Error handler (`neurolens/lambdas/gpu_failed.py`):** subscribed to the SNS topic `neurolens-gpu-errors`, which is the endpoint's `ErrorTopic` (there is no success topic: the container settles itself).
  - It takes `inferenceId` (the job id) from the notification. It reuses `dlq_handler.settle_or_refund`, moved to `neurolens/lambdas/__init__.py` as `finish_job`: settle if a result exists, else refund `processing_failed` if queued or stale for 90 s, and delete the job's files.
  - While the claim is fresh it raises. Lambda's asynchronous retries (2, over about 3 minutes) then try again, and the reaper is the backstop after that.
  - An unparseable notification is logged and dropped.
- **Reaper:**
  - It runs **hourly** (was 3-hourly). `DEADLINE_S` becomes **4 hours** after `created_at` (was 24): 2 hours of queue TTL, plus 60 minutes of invocation timeout, plus 1 hour for the upload and dispatch.
  - `HEARTBEAT_STALE_S` stays 10 minutes.
  - The user-facing text that promised release "within about 4 hours" (M3c §4) is now true.
  - It also runs the dataset clean-up (§7).

## 6. Scaling, pause and the guard
### 6a. Scaling (Application Auto Scaling on `endpoint/neurolens/variant/main`)
- **Scalable target:** min 0, max `var.max_instances` (1).
- **Scale out:** the step policy from AWS's scale-from-zero pattern. Alarm `neurolens-gpu-scale-out`: `HasBacklogWithoutCapacity` ≥ 1 for 1 of 1 one-minute periods, missing data not breaching → `ChangeInCapacity +1`.
- **Scale in:** alarm `neurolens-gpu-scale-in`: `ApproximateBacklogSize` maximum ≤ 0 for 7 of 7 one-minute periods → `ExactCapacity 0`.
  - The backlog counts requests in progress too (AWS metric definition), so a running job holds the GPU.
  - **Missing data counts as breaching:** AWS may stop publishing the backlog when idle, and an alarm without data must not keep a GPU on.
- No target-tracking policy: with one instance it adds nothing, and AWS warns that several policies conflict.
- **Instance types:** an instance pool list, `ml.g6e.xlarge` first, then `ml.g6.2xlarge` (SageMaker tries the next pool when the first has no capacity; `VariantInstanceProvisionTimeoutInSeconds` 600).
  - If the Terraform provider cannot express instance pools, the build stops and reports. No hand-made endpoint config.
- **Creating or updating the endpoint** always starts one instance (`InitialInstanceCount` ≥ 1, AWS API), which scales in after 7 idle minutes. A deploy therefore costs one cold start plus about 7 minutes of GPU time (an estimate: $0.50–0.80 on `g6e`).

### 6b. Pause and keep-warm (`infra/stop_work.sh`, `infra/start_work.sh`)
- **`stop_work.sh`** (emergency pause):
  - sets the scalable target's max and min to 0;
  - deletes any keep-warm scheduled actions;
  - calls `UpdateEndpointWeightsAndCapacities(DesiredInstanceCount=0)`;
  - waits until `DescribeEndpoint` shows 0 instances and prints `ALL STOPPED`.

  A running job dies and is refunded (§5). Uploads are refused while paused (§8).
- **`start_work.sh`** sets max 1 (the normal state; also what Terraform applies).
  - `--keep-warm --hours N` (1–4): min 1 now, plus a one-time scheduled action that sets min back to 0 at the end. AWS ends a keep-warm without anyone remembering. Running it again moves the end.
  - `--keep-db` keeps its current meaning (Aurora minimum 0.5 ACU for the same hours).
- `debug_worker.sh` is deleted. Container logs are in CloudWatch (`/aws/sagemaker/Endpoints/neurolens`).

### 6c. GPU guard (`neurolens/lambdas/gpu_guard.py`, every 10 minutes)
- **Stops the GPU** (`UpdateEndpointWeightsAndCapacities(DesiredInstanceCount=0)`) when all of these hold:
  - `DescribeEndpoint` shows at least one instance and the status `InService`;
  - the scalable target's min is 0 (no keep-warm);
  - no job is `processing`;
  - no job's `updated_at` is newer than 30 minutes.

  It then logs `GPU_GUARD_STOPPED <instance count>`.
- **Reads our database, not the backlog metric** that the scale-in alarm uses, so one broken signal cannot blind both.
- A job created in the last 30 minutes blocks it, because `reserve` sets `updated_at`. So it never stops a GPU that a fresh upload is waiting for.
- **Any other status blocks it** (`Updating` while an instance is added or removed, `Creating`, `Failed`): a slow start-up can pass 30 minutes (provisioning up to 10, model loading up to 30) while a queued job waits for it, and stopping it then would only make SageMaker start it again. The guard acts only on an instance that is up and getting no work.
- **Alarms:**
  - a log metric filter on `GPU_GUARD_STOPPED` feeds the alarm `neurolens-gpu-guard-stopped`, which emails the alerts topic (the normal scale-in failed, so a person should look);
  - the guard's own failures use the existing per-Lambda error alarm;
  - a missing run (EventBridge not invoking) uses an alarm on its `Invocations` < 1 in 30 minutes, the pattern of `breaker_not_running`.
- **Permissions:** `sagemaker:DescribeEndpoint` and `sagemaker:UpdateEndpointWeightsAndCapacities` on our endpoint only, plus `application-autoscaling:DescribeScalableTargets`.

## 7. Research consent needs the retention curve
- **Migration `infra/migrations/004_retention_rule.sql`:** `jobs.retention_added_at TIMESTAMPTZ`, `jobs.dataset_removed_at TIMESTAMPTZ`.
- **Consent box** (one line, replaces M3c §3a's text): "Keep this video for research for up to 12 months and add its audience retention once it is published ([how](/static/retention_guide.html)); without retention, the copy is deleted after 60 days. I own the rights to this video."
- **Retention upload** (M3c §3c):
  - On success it sets `retention_added_at = now()`.
  - It is refused with 409 `dataset_removed` ("This video's research copy was deleted after 60 days without retention.") when `dataset_removed_at` is set.
- **History:** a done, consented, not-removed job without `retention_added_at` shows a small "Add retention" link in its row, no banner. The link opens the job with the retention upload in view. `list_jobs` returns `needs_retention` (bool) for this.
- **Clean-up** (in the reaper's hourly run): select every job that is `done`, has `research_consent`, has no `retention_added_at` and no `dataset_removed_at`, is not deleted, and has `created_at` older than 60 days. For each:
  1. delete `dataset/<id>/video<ext>`, `result.json` and `brain` with `storage.delete_keys`;
  2. only if every delete succeeded, set `dataset_removed_at`.

  A failure leaves the row for the next run and makes the run raise, so the Lambda error alarm emails. The user's own result, brain and thumbnail are untouched.
- **Privacy page:** a consented copy without retention is deleted 60 days after upload. The 12-month rule for the rest is unchanged.
- **Constant:** `reaper.DATASET_WITHOUT_RETENTION_DAYS = 60`.

## 8. Web
- **Pause check:** `_endpoint_paused(appscaling, endpoint)` reads `DescribeScalableTargets` for the variant. Max 0, or no target → paused. It replaces `_worker_group_paused`, with the same 503 and the same "could not read" → 500.
- **Settings:** `NEUROLENS_WORKER_GROUP`/`aws.worker_group` becomes `NEUROLENS_GPU_ENDPOINT`/`aws.gpu_endpoint`, required in Cognito mode.
- **Page:**
  - the waiting text for a queued job says the GPU may take up to about 10 minutes to start;
  - the `downloading` stage label is removed.
- **Web role:** `application-autoscaling:DescribeScalableTargets` (it cannot be scoped to one resource; read-only).

## 9. Terraform and IAM
**Added:**
- the ECR repository `neurolens-worker` (keep the last 3 images);
- the CodeBuild project `neurolens-worker-image` (Linux x86-64, privileged for Docker, 60-minute timeout) and its role, which may only read `build/source.zip`, push to the repository and write its log group;
- the SageMaker execution role:
  - read on `model-data/*` and `uploads/*`;
  - put on `results/*`, `dataset/*`, `experiments/boots/*` and `sagemaker-output/*`;
  - delete on the job prefixes (refund clean-up);
  - the Data API and the database secret;
  - ECR pull on our repository;
  - its logs;
  - `sns:Publish` on the error topic;
- `aws_sagemaker_model` `neurolens-<image digest prefix>`, with environment: bucket, region, database trio, `NEUROLENS_DEPLOYED=1` and `FAKE_INFERENCE` from `var.fake_inference`;
- `aws_sagemaker_endpoint_configuration`:
  - the instance pools of §6a;
  - `ContainerStartupHealthCheckTimeoutInSeconds` 1800 and `ModelDataDownloadTimeoutInSeconds` 1800;
  - the async config: `S3OutputPath s3://<bucket>/sagemaker-output/ok/`, `S3FailurePath …/failed/`, `ErrorTopic`, `MaxConcurrentInvocationsPerInstance` 1;
- `aws_sagemaker_endpoint` `neurolens`;
- the scaling of §6a;
- the topic `neurolens-gpu-errors` and its Lambda subscription;
- the Lambdas `dispatcher`, `gpu_failed` and `gpu_guard` (the one M3a zip; the dispatcher also gets the ffprobe layer), their schedules, alarms and permissions;
- the lifecycle rules: `sagemaker-output/` 1 day, `build/` 7 days;
- `var.fake_inference` (default false): when true, the pools are one `ml.m5.xlarge` (a CPU rehearsal at about $0.23/h, real price list) and `FAKE_INFERENCE=1`.

**Deleted:**
- the launch template, Auto Scaling group, scale-out/in alarms and policies, `workers_to_zero`, the idle and 3-hour alarms;
- the breaker Lambda, its schedule, role and `breaker_not_running`;
- the build and worker roles and instance profiles;
- the NAT Gateway, its Elastic IP and `time_sleep`, `var.nat_gateway`, the public and build subnets, the internet gateway, the public route table;
- the S3 gateway endpoint (nothing in the network uses S3 any more);
- `worker_ami_id`, the outputs that only served those resources, and the SQS permissions of the deleted worker role.

**Kept:** the VPC and private subnets (Aurora's subnet group needs them), the database security group, the jobs queue and DLQ (now consumed by the dispatcher).

**IAM paste by Josh** (one batch):
- `infra/iam/neurolens-deploy-*.json` gain SageMaker, ECR, CodeBuild and Application Auto Scaling actions scoped to `neurolens*`, and lose EC2/Auto Scaling launch rights;
- the role boundary gains the SageMaker execution role's and the guard's actions.

## 10. Scripts, experiments and records
- **Deleted:** `infra/build_ami.sh`, `infra/pull_code.sh`, `infra/deploy_code.sh`, `infra/neurolens-worker.service`, `infra/terraform/worker_userdata.sh.tftpl`, `infra/debug_worker.sh`, `infra/lambda/breaker.py`.
- **Boot record:** at start-up the container writes `experiments/boots/<host>-<start time>.json` with `container_start_utc`, `model_loaded_utc`, `warmup_done_utc` and `ready_utc`. Never fatal.
- **`experiments/cold_start.py`:** one row per scale-out, joining Application Auto Scaling's scaling activities (`describe_scaling_activities`), the boot records, and the first job's `transcribing` start.
- **`experiments/export_cloudwatch.py`:** the series become `ApproximateBacklogSize` and the instance count (`CPUUtilization` sample count, AWS's documented method).
- **`experiments/finish_run.py`:** `--ami-id` becomes `--image-digest`.
- **Unchanged:** `latency_run.py` and `latency_breakdown.py` (they use the web API and job stages).
- **Docs:**
  - `AGENTS.md` Layout (`worker.py` is the SageMaker container; the new Lambdas; `media.py`);
  - `README.md` deploy steps;
  - `infra/terraform/README.md`;
  - the architecture page master copy (`docs/architecture.local.html`), at the milestone close-out.

## 11. Tests (written first; PostgreSQL for billing)
- **Dispatcher:**
  - each refusal (missing, too large, unreadable, too long, short credit) refunds, deletes the upload where §3 says, and never invokes;
  - a cancelled or processing job is not invoked;
  - a good job is invoked once, with `InferenceId` = job id;
  - an exception before the invoke leaves the job queued with its reservation;
  - a second dispatch is harmless.
- **`verify_queued`:** the old `verify` cases, plus `not_queued` for processing and terminal jobs, and idempotence.
- **Container:**
  - `handle_job` keeps every `handle_record` test that still applies (claim, stages, dataset copy, claim checks before writes, thumbnail, brain, duplicate result, settle);
  - a failure refunds and deletes at once and answers 500;
  - a lost claim answers 500 without refunding;
  - `not_verified` is refunded;
  - the body is streamed to disk;
  - a missing job-id header → 400;
  - `/ping` answers 200 while `/invocations` is busy;
  - a second concurrent invocation → 503;
  - `ClaimKeepAlive` stops at `max_seconds`.
- **Error handler:** done result → settled; queued or stale → refunded and files deleted; fresh claim → raises; already terminal → no-op; garbage → logged and dropped.
- **Guard:**
  - stops only when all four conditions hold;
  - an endpoint that is not `InService` (an instance still starting) blocks it, even with an old queued job;
  - each condition alone blocks it;
  - it logs the marker;
  - a keep-warm (min ≥ 1) blocks it.
- **Reaper:** the 4-hour deadline; the clean-up deletes only the right jobs' dataset files, marks them only after the deletes succeed, and leaves the user's own result.
- **Retention:** sets `retention_added_at`; refused after removal; `list_jobs` `needs_retention`.
- **Web:** paused when max 0 or no target; a read failure → 500.
- **Break-it checks (3):**
  - the guard stops while a job is processing;
  - the clean-up deletes a job with a retention curve;
  - the container charges a job whose claim it lost.
- **Existing tests this spec retires or changes** (Josh approves by approving this spec):
  - **deleted with their subject:** `test_breaker.py`, `test_breaker_alarm.py`, `test_m2b_protection.py`, `test_m2b_shutdown.py`, `test_m2b_heartbeat.py`, `test_m3a_heartbeat.py`, and the SQS-polling parts of `test_worker.py`, `test_m2b_worker.py`, `test_m3a_worker.py`;
  - **rewritten to the new entry point with the same assertions:** the job-logic parts of those worker tests, `test_m3d_worker.py`, `test_m3d_thumbnail.py`, `test_m3c_consent.py`, `test_unreadable_video.py`, `test_cancel_and_refund_cleanup.py`;
  - **renamed setting:** `worker_group` → `gpu_endpoint` in `conftest.py`, `test_env_settings.py`, `test_m3b_auth.py`, `test_m3b_web.py`, `test_m2b_web.py`, `test_m3a_web.py`;
  - **reworked:** `test_work_scripts.py`, `test_experiments_scripts.py`, `test_m2b_boot_record.py`, and the `verify` tests in `test_m3a_billing.py` → `verify_queued`;
  - the reaper's deadline tests in `test_m3a_lambdas.py` (24 h → 4 h);
  - any other existing test that fails is reported to Josh, not edited.

## 12. AWS day B (cost estimate first)
Order, with no long gap between the steps:
1. Josh pastes the IAM changes.
2. `make_model_data.sh`.
3. Build the ffprobe layer and the image.
4. **CPU rehearsal:** `terraform apply` with `fake_inference = true`. Check that:
   - a fake job runs end to end through the dispatcher;
   - an unreadable file is refused without any instance starting;
   - a request with `RequestTTLSeconds` 60 against a paused endpoint produces an error notification and a refund;
   - the endpoint scales to 0 after 7 idle minutes;
   - the guard stops an instance kept up by hand (with min reset to 0 and no jobs) and emails.
5. Apply with the GPU. Run one real job, and per-video delete it.
6. A consented job: check the dataset copy, then delete it.
7. Measure:
   - cold start (upload to `transcribing`), split by the boot record;
   - the first job's `transcribing` against a later job's (the warm-up must make them alike);
   - cost per video.
8. Fallback: not forced. Record which type ran.
9. Finish: confirm `ALL STOPPED`. Nothing stays running: there is no NAT to switch off.

**Costs (estimates, from the AWS price list, us-east-1):**
- `ml.g6e.xlarge` $2.61/h; `ml.g6.2xlarge` $1.22/h; `ml.m5.xlarge` $0.23/h;
- CodeBuild about $0.40 a build;
- ECR storage about $1/month for 3 images;
- the day itself about $3–5.

Measured figures replace these in the build log.

## 13. Acceptance criteria
1. The full suite passes with PostgreSQL, and `ruff` is clean.
2. `git grep -i -E 'autoscaling_group|nat_gateway|breaker|sqs.receive_message|ScaleInProtection'` finds only specs, build logs and git-history notes.
3. `terraform plan` shows exactly the additions and deletions of §9.
4. Day B: every check in §12 passes.
   - A job's whole wait from upload to result is recorded.
   - The GPU returns to 0 by itself.
   - The guard's stop was seen once and emailed.
5. Day B: a bad file was refused in seconds without starting a GPU.
6. Day B: the dataset clean-up was run against a test row with `created_at` set back 61 days, and deleted exactly that copy.
7. The Opus review of the money, failure and deletion slices finds no HIGH.
