# Architecture

NeuroLens runs on Amazon Web Services (AWS). Each part of the system maps to one AWS service:

| Part | AWS service | Job |
| --- | --- | --- |
| Page and API | Amazon CloudFront (AWS's content delivery network) | Serves the page and forwards API calls to a Flask app in AWS Lambda (functions that run on demand) |
| Sign-in | Amazon Cognito (AWS's sign-in service) | Handles Google or email sign-in |
| Storage | Amazon S3 (object storage) | Holds videos, results and model files |
| Upload queue | Amazon Simple Queue Service (SQS) | Holds one message per upload |
| Model runtime | An asynchronous Amazon SageMaker endpoint (a managed machine-learning service with a queue in front of the GPU) | Runs the model on a graphics processing unit (GPU) |
| Database | Amazon Aurora (a managed PostgreSQL-compatible database) | Holds users, credit and job state through the Amazon RDS Data API (SQL over HTTPS) |
| AI report | Amazon Bedrock (managed access to foundation models) | Writes the optional AI report |
| Infrastructure | Terraform (infrastructure as code) | Creates the AWS resources |

![Architecture](images/architecture.svg)

In the code: `infra/terraform/`

## The path of one video

A video goes through these steps from sign-in to the page that shows the result.

1. Sign-in. The browser signs in on Cognito's hosted page with Google or an email and password. The callback accepts only a verified email, maps the sign-in's issuer and subject to a `users` row, and starts a server-side session. The browser cookie holds a random token; the server stores its hash.
2. Credit check and reserve. `POST /api/uploads/presign` checks that GPU work is not paused, that the claimed length and size fit the limits, that the account has fewer than ten queued jobs, and that research consent is current. The app then moves the estimated price from available to reserved credit and creates a `queued` job.
3. Upload to S3. The web function returns a presigned POST: a time-limited signed form that lets the browser write one object without AWS credentials. It expires after 300 seconds, and S3 enforces one key, one content type and the size cap. The object lands at `uploads/{user_id}/{job_id}{ext}`.
4. Queue. S3 sends an object-created event to the `neurolens-jobs` SQS queue; SQS delivers it after `queue.cancel_seconds` (15 seconds), so a user can still cancel. A message that keeps failing goes to the dead-letter queue, a queue for messages that cannot be processed.
5. Dispatcher Lambda. `neurolens-dispatcher` consumes one message at a time. It checks the object size, runs `ffprobe` from a Lambda layer (a zip of extra files loaded beside the function), re-prices the job by the measured duration, checks the job deadline and whether processing is paused, then calls SageMaker's asynchronous `InvokeEndpointAsync`. It also asks the endpoint for one instance, so the cold start does not wait for the scaling alarm.
6. SageMaker asynchronous endpoint and worker container. SageMaker starts an instance from the first available pool. The container receives the video on `/invocations`, claims the job, runs TRIBE v2 with audio and again with the sound removed, and extracts the seven brain signals. A heartbeat runs while the job works, and a job that reaches 55 minutes ends the process so SageMaker replaces the container.
7. Results to S3. The worker writes `results/<job_id>.json` with the signal series, `results/<job_id>.brain` with the raw predictions, and the thumbnail, frames and phone-playback copy beside them. A consented job also gets `dataset/<job_id>/video.*`.
8. Charge or refund. On success the app moves the reservation to captured. The job's failure paths return the reservation to available credit. A unique database index allows at most one capture or one refund per job.
9. AI report through Bedrock. After a successful result, `POST /api/jobs/<job_id>/report` claims one free attempt and invokes the `neurolens-report` Lambda asynchronously. That function reads the result and frames, calls Bedrock's Claude Sonnet 4.6, checks the answer's shape and time spans, and stores `results/<job_id>.report.json`.
10. The page shows the result. The page polls `/api/jobs/<job_id>/status` and reads `/api/jobs/<job_id>/result`, which adds the sound-share summary at serve time. Charts use the result endpoint, and the 3D brain reads the `.brain` file through a five-minute presigned GET link. CSV and PDF downloads come from the web Lambda.

In the code: `neurolens/web/auth.py`, `neurolens/web/app.py`, `neurolens/billing.py`, `neurolens/pricing.py`, `neurolens/storage.py`, `infra/terraform/main.tf`, `neurolens/lambdas/dispatcher.py`, `neurolens/worker.py`, `config.json`, `infra/migrations/004_one_ending_per_job.sql`, `neurolens/lambdas/report.py`, `neurolens/results.py`

## Brain signals and result files

The model outputs one row per second of video over 20,484 points of the cortex (the fsaverage5 surface: indices 0-10,241 left hemisphere, 10,242-20,483 right). TRIBE v2 was trained on fMRI responses standardised per point, so 0 means typical film/TV viewing. Values are signed and unscaled, so two videos can be compared directly.

The brain-area map comes from the Glasser HCP-MMP1 parcellation (360 areas; Glasser et al. 2016, *Nature*). The two map files live in the weights prefix (`model-data/data/hcp_mmp/`, uploaded once by `infra/upload_model_data.py`); the container checks their SHA-256, so it never runs on a changed map. The map is Human Connectome Project data under the WU-Minn HCP Open Access Data Use Terms (see the README's License).

Result format 2 (`results/<job_id>.json`):

```
{"format": 2, "duration_seconds": T,
 "signals": {"faces": [T numbers], ..., "overall": [T numbers]},
 "muted":   {"faces": [M numbers], ..., "overall": [M numbers]},
 "job_id": ..., "processing_time_seconds": ..., "gpu"?: ..., "fake_inference"?: true}
```

Each signal is the mean over its area's points; each group is the mean of its signals, and overall is the mean of the three groups. The worker also runs the video with its sound removed and computes the same 11 series from that pass (muted), cut to the shared length of the two passes.

Series order: faces, bodies, scenes, visual, sound, speech, social, see, hear, understand, overall. Values are rounded to 3 decimals. The web app adds a `summary` (`audio_dependence`) when it serves a result; it is never stored. Results without `"format": 2` are answered with 410 ("upload the video again").

The raw brain file (`results/<job_id>.brain`) holds the with-audio predictions as float16, shape (T, 20484), with no header. The 3D map reads it, and later changes to the signals need no GPU rerun.

In the code: `neurolens/response.py`, `neurolens/inference.py`, `neurolens/worker.py`

## Data

State lives in the database tables, the S3 key prefixes and the rules that delete old data.

### Aurora tables

Migrations apply each file once.

| Table | What it holds |
| --- | --- |
| `users` | One row per account, with the email and a deletion mark. |
| `balances` | Available and reserved credit in cents, both constrained to zero or more. |
| `identities` | The sign-in issuer and subject linked to one user. |
| `jobs` | The upload key, file name, status and stage list, client and measured durations, reserved and captured cents, research consent and version, video hash, claim time, retention and reward fields, report state, and the deletion mark. |
| `ledger` | One row for every balance change, with its kind and the available and reserved deltas. |
| `retention_snapshots` | One stored audience curve, its raw and CSV keys, its hash, quality flags, views, publish data, reward, linked video, cut answer and source. |
| `comparisons` | One row per pair of videos, with the comparison report's state and tries. |
| `sessions` | The hash of each sign-in token, its user and its last use. |
| `access_requests` | One screened request per account, with the channel link, platform, note and status. |
| `schema_migrations` | The migration files already applied. |

In the code: `infra/migrations/`, `infra/apply_schema.py`

### S3 prefixes

Files in S3 are grouped under key prefixes (the start of a key, like a folder). The bucket is private, encrypted at rest, and refuses anything except HTTPS.

| Prefix | Holds | Expires |
| --- | --- | --- |
| `uploads/` | The input videos | After `upload_retention_days` (30 days) |
| `results/` | Result JSON, brain file, thumbnail, frames, playback copy and reports | After `result_retention_days` (30 days) |
| `dataset/` | Consented shared videos and retention snapshots | After `research.dataset_retention_days` (1,095 days) |
| `sagemaker-output/` | SageMaker's asynchronous success and failure answers | After 1 day |
| `build/` | The source zip for an image build | After 7 days |
| `model-data/` | The model weights and brain maps copied into the GPU container at start | No expiry rule |
| `deploy/` | The web Lambda zip | No expiry rule |
| `site/` | The public page and samples served by CloudFront | No expiry rule |

In the code: `infra/terraform/main.tf`

### Retention and deletion

These rules decide when data is deleted, either by an S3 lifecycle rule or by the reaper.

| Rule | What happens |
| --- | --- |
| S3 lifecycle | S3 deletes `uploads/` after `upload_retention_days` (30 days) and `results/` after `result_retention_days` (30 days), `sagemaker-output/` after 1 day, and `build/` after 7 days. `dataset/` lasts `research.dataset_retention_days` (1,095 days). |
| Results | The page marks a result available only for `result_retention_days` (30 days), and a missing result answers `result_expired`. |
| Shared copies | A shared copy with no retention curve is removed after `research.no_curve_days` (90 days), and the reaper applies it. |
| User deletion | A user can stop sharing a copy or delete a job immediately. The job is marked first and the files are deleted; the reaper finishes anything a failed delete leaves. |
| Account deletion | Account deletion marks the account, removes its sign-ins, deletes its files and then its rows. Deleting an account is refused while one of its jobs is queued or processing. |
| Sessions and reports | Sign-in sessions expire after `auth.session_days` (30 days) from last use. A report that stays `writing` for `report.stale_minutes` (16 minutes) is treated as stale. |
| Logs | Amazon CloudWatch (AWS's monitoring and log service) keeps the Lambda, web and GPU logs for `log_retention_days` (14 days) and the image-build (CodeBuild) logs for `codebuild_log_retention_days` (30 days), both in `infra/terraform/variables.tf`. |

In the code: `infra/terraform/main.tf`, `neurolens/web/app.py`, `config.json`, `neurolens/lambdas/reaper.py`, `neurolens/billing.py`, `infra/terraform/variables.tf`

## Safety nets

Scheduled Lambdas, alarms and deadlines catch stuck work and runaway spend: they refund or flag stuck work and catch a GPU that fails to stop.

| Safety net | What it does |
| --- | --- |
| GPU guard | A Lambda runs every 10 minutes and stops the endpoint when an instance is running or wanted, no keep-warm holds it, and no live claim, dispatched request or recent job activity says work is coming. It reads the database, not the backlog metric. |
| Reaper | A Lambda runs every `reaper_schedule_hours` (3 hours). A job still open `queue.job_deadline_hours` (four hours) after creation is settled if its result exists, otherwise refunded `deadline_passed`. It also retries recent failed file deletes, removes shared copies with no curve after `research.no_curve_days` (90 days), finishes marked purges, deletes marked accounts, removes snapshots older than `research.dataset_retention_days` (1,095 days) and deletes expired sessions. |
| Dead-letter handler | A message that keeps failing on the jobs queue goes to the dead-letter queue. Its Lambda settles the job if a result exists, otherwise refunds it `processing_failed` and deletes its files. If a worker still holds a fresh claim, the handler raises so the message is tried again. |
| SageMaker error handler | The endpoint's error topic invokes a Lambda for each failed asynchronous request. It settles or refunds the job and ignores SageMaker's own start/stop test notification. A fresh claim makes it retry later; the reaper is the final backstop. |
| Alarms | CloudWatch emails one Amazon Simple Notification Service (SNS, AWS's notification service) topic when the GPU guard stops a GPU, when the guard asks twice in 30 minutes, when the guard does not run for 30 minutes, when Aurora stays awake for much of six hours, and on any Lambda error. A failed Cognito login deletion has its own log-based alarm. |
| Budget | Terraform creates no budget. The setup guide recommends an optional manual AWS Budgets alert at an amount the account owner chooses (AWS Budgets is AWS's cost-alert service). |
| Queue and request deadlines | The dispatcher gives each request a queue time to live that ends before the reaper's four-hour bound. SageMaker's invocation timeout is one hour, and the worker's own job cap is 55 minutes. |

In the code: `neurolens/lambdas/gpu_guard.py`, `infra/terraform/lambdas.tf`, `neurolens/lambdas/reaper.py`, `neurolens/lambdas/dlq_handler.py`, `neurolens/lambdas/__init__.py`, `neurolens/lambdas/gpu_failed.py`, `infra/terraform/alarm.tf`, `infra/terraform/database.tf`, `infra/terraform/README.md`, `neurolens/lambdas/dispatcher.py`, `config.json`

## Credit and billing

NeuroLens charges for GPU work in blocks sized by `neurolens.pricing` (30 seconds). The price is one dollar per started block, with a half-second allowance and a minimum of one block.

| Step | What happens |
| --- | --- |
| Reserve | At upload, the web function estimates the price from the browser's duration and moves it from available to reserved. |
| Verify | The dispatcher measures the real duration and re-prices the job. A longer video needs more available credit; if that is missing, the job is refunded before a GPU starts. A video above the `max_video_duration_seconds` limit (120 seconds) is refunded first. |
| Charge | A result settles the job once: reserved credit becomes captured credit. |
| Refund | A failed, cancelled or expired job returns its reservation to available credit. A refund also deletes the job's files. |

The ledger invariants are:

| Invariant | What it means |
| --- | --- |
| Same transaction | Every balance change writes its ledger row in the same transaction. |
| Lock order | The balance row is locked before the job row, so money transactions share one lock order. |
| One ending | A job ends at most once. A partial unique index allows one `capture` or one `refund` row per job. |
| No negative balances | Balances can never go negative. |
| Ledger sum | Available plus reserved equals the sum of the ledger deltas for that user. |

A stored audience curve can return the job's captured price once. The checks run before anything is written:

| Check | Condition |
| --- | --- |
| Curve validity | At least 20 points, a watch-ratio spread of at least 0.02, a falling or flat ending, and no value above 10. |
| Job state | The job must be done, shared, charged, not deleted, and not already rewarded. |
| Account history | The account must have had credit beyond the starter grant, and must have fewer than five rewards in the last 30 days (`research.curve_rewards_per_window` and `curve_reward_window_days`). |
| Length match | The video's measured length must match the analysed length within two seconds; the analysed length is the server's own measurement (`research.curve_length_tolerance_s`). |
| View count | When a view count is known, it must be at least 1,000 (`research.curve_min_views`). |
| Once per video and link | The same video hash and the same published link can each be rewarded only once. The link is stored on the job, so deleting the shared copy cannot forget the reward. |
| One curve | One exact curve (by hash) is stored once across all jobs and accounts. A published link cannot be stored by a second account, and one analysis cannot switch to a different link. |

In the code: `neurolens/pricing.py`, `neurolens/billing.py`, `infra/migrations/`, `neurolens/results.py`

## Sign-in and admin

Sign-in runs in one of two modes, and an admin panel handles operations.

| Mode | How it works |
| --- | --- |
| Dev mode | Dev mode sets `auth.mode` to `dev` in the config. The app uses one fixed development user on `127.0.0.1` only, creates that user with the starter credit on first use, and refuses to start in dev mode on AWS or on any other host. |
| Cognito mode | On AWS, Cognito hosts the sign-in page; Google and email/password are the providers. The app stores its own session token, not the Cognito token, and reads the user's email from the database. A changed email needs verification before it can update the account. Terraform holds the configuration, and the terraform README has the first-time steps. |

The `Admin` button appears when the signed-in email is in the admin list: Terraform's `admin_emails`, or `NEUROLENS_ADMIN_EMAILS` on a laptop. An empty list means nobody can open it. The panel lives in the web page, and its routes are `/api/visitor/pass`, `/api/visitor/stats` and `/api/visitor/passes`.

| Area | What it does |
| --- | --- |
| Delegated admins | Admin rights come from the email list. To delegate, add the person's email to `admin_emails` in Terraform and apply, or to `NEUROLENS_ADMIN_EMAILS` on a laptop and restart. The panel does not add or remove admins. |
| Visitor passes | The panel makes a signed QR code for a two-minute guest pass. The pass pool starts at ten passes of 400 cents each; `+5 passes`, `+10 passes` and `Stop passes` change it. Each scan creates or joins a `guest-` account. A copied link joins the same guest session without spending a second pass; two different phones scanning the same code get separate guest accounts. Guest accounts never receive research consent. |
| GPU warmth and credit | The panel sets the endpoint to auto-scale (`0`), or keeps up to `max_instances` GPUs warm for `visitor.gpu_warm_hours` (six hours). It grants or resets credit for an email in `visitor.grant_minutes` (`0`, `2`, `5`, `10`) steps; `+5 min` is the invite credit. |
| Access requests | A signed-in user asks for free minutes with a YouTube, Facebook, TikTok or Instagram channel link. The request row is the list; an SNS email notifies the operator. `infra/access_requests.py list --backend data_api` shows waiting requests, and `decline` turns one down. Granting credit in the panel marks the request granted. |

In the code: `neurolens/web/auth.py`, `neurolens/settings.py`, `infra/terraform/web.tf`, `infra/terraform/README.md`, `static/app.html`, `neurolens/web/app.py`, `neurolens/billing.py`, `config.json`, `neurolens/access.py`, `neurolens/notify.py`

## Design decisions

The main technical choices, why each was made, and what each one costs.

### Scale-to-zero SageMaker asynchronous endpoint instead of an EC2 GPU group

The model runs on one SageMaker asynchronous endpoint with instance pools, minimum zero and maximum one by default.

Why: It fits the current scale. The site pays only while a GPU works, has no machine to patch or drain, and AWS queues requests when no instance is free. Fallback instance pools keep the service working when the first GPU type has no capacity.

The cost: Per GPU-hour, SageMaker costs more than a self-managed group at high volume, and a cold start or a capacity shortage makes people wait. The self-managed Amazon Elastic Compute Cloud (EC2, virtual servers) version is kept at git tag `custom-gpu`.

In the code: `infra/terraform/sagemaker.tf`

### Aurora Serverless v2 pausing to zero

The database is Aurora PostgreSQL Serverless v2 with 0 to 1 Aurora Capacity Unit (ACU), a 300-second auto-pause, and access only through the RDS Data API.

Why: An idle database costs no compute, and no workload opens a database port. The database sits in a virtual private cloud (VPC, an isolated network) with no internet route and no NAT gateway (a managed internet gateway for private subnets).

The cost: The first call after a pause waits about 15 seconds for the database to wake. The Data API runs one statement per call, and the client retries only calls the database never ran (while it wakes), so the billing code is written to make a caller's retry safe.

In the code: `infra/terraform/database.tf`, `infra/terraform/network.tf`, `neurolens/db.py`

### A Lambda web tier

The Flask app runs as one arm64 Lambda behind CloudFront, with 1,769 MB of memory and a 60-second timeout.

Why: There is no server to run, the tier scales with requests, and the site pays per request. CloudFront signs its requests to the function URL, so the URL is not public.

The cost: A cold start adds seconds to the first request, and the 60-second limit rules out long work. Reports run in a separate Lambda with a 900-second timeout, and analysis runs in the GPU container.

In the code: `infra/terraform/web.tf`, `infra/terraform/lambdas.tf`

### The g6e/L40S GPU choice

The endpoint tries `ml.g6e.xlarge` (NVIDIA L40S) first, then `ml.g6.2xlarge` (L4), `ml.g5.2xlarge` (A10G) and `ml.g6e.2xlarge` as fallbacks. All run on demand, not Spot (AWS's spare-capacity pricing).

Why: The pipeline peaks at about 20 GB of GPU memory, and each type leaves at least 32 GB of system memory. The L40S finished the tested clips fastest per dollar. Spot saved little and often had no capacity.

The cost: L40S capacity is scarce. A fallback GPU keeps the site working but takes longer and costs more per video. SageMaker refuses a CUDA image on a CPU instance, so there is no cheap CPU rehearsal of the image.

In the code: `infra/terraform/sagemaker.tf`, `docs/evidence/README.md`

### Signed brain signals with no rescaling

The page shows signed means, with zero meaning typical film or TV viewing. There is no absolute value and no rescaling, and the muted series are cut to the shared length of the two passes.

Why: TRIBE v2's training data are fMRI responses standardised per cortical point, so zero is the model's built-in reference and two videos can be compared directly.

The cost: The numbers are relative to typical viewing, not absolute brain activity, and they are not a forecast of views, retention or sales.

In the code: `AGENTS.md`, `neurolens/response.py`

## Earlier versions

Two designs that were built, run on real GPUs and later replaced are kept as git tags. Each tag holds the full working code and the specs that describe it, so either can be used as a blueprint to bring the feature back. The product has changed a lot since, so bringing one back means rebuilding that piece in today's code, not checking the tag out.

| Tag | What it has | Why it was replaced | Where it is described |
|---|---|---|---|
| `custom-gpu-stripe` | Paid credit top-ups through Stripe in test mode, on the self-managed EC2 GPU workers. | Credit became a free allowance that an admin grants. The TRIBE v2 licence is non-commercial, so credit cannot be sold. | `docs/M3b_spec.md` (top-ups) and `docs/M3b_build_log.md` in the tag |
| `custom-gpu` | The self-managed EC2 GPU workers (an Auto Scaling group of on-demand `g6e` machines that scale on the job queue), with everything built up to that point except Stripe. | Replaced by the SageMaker endpoint (see Design decisions). | `docs/M2a_spec.md`, `docs/M2b_spec.md` and their build logs in the tag |

## Known limitations

What NeuroLens cannot do, or cannot yet check.

| Limitation | Detail |
| --- | --- |
| Audience-curve rewards rely on manual checks. | A reward needs an account that has had credit beyond the starter, so the invite step limits who can earn one, and the operator screens requests by hand. YouTube's public length and view count can be gamed, and the site does not verify that the signed-in account owns the channel. Facebook gets no automatic length or view check at all: the user pastes a retention report, and only full Facebook page addresses are accepted (no Reels or short links). Closing this gap needs owner sign-in through YouTube Analytics or Facebook Page Insights, which requires app review by Google or Meta. |
| The AI report cannot hear the soundtrack. | It reads frames and spoken words plus the predicted signals; the prompt says so. |
| The model predicts perception, not persuasion. | TRIBE v2 predicts how an average viewer's brain responds to what is seen and heard, on the brain's surface. It is not a real brain scan, and it does not measure emotion, reward or intent to buy, so NeuroLens reports attention to sights, sounds and speech, not whether an ad will sell. |
| Non-commercial use only. | The TRIBE v2 model is licensed CC BY-NC, so NeuroLens cannot be sold as is. |

In the code: `neurolens/youtube.py`, `neurolens/access.py`, `neurolens/results.py`, `neurolens/report_prompt.py`
