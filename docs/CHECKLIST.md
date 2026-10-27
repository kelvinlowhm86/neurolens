# NeuroLens — Build Checklist

Companion to `docs/PLAN.md` (section numbers below point there). Work top to bottom.
**[CODE]** = built in the repo. **[SETUP]** = something a person does in a console / terminal; step-by-step instructions are inline.

**House rules for this machine:** nothing is installed on the host (no dnf/apt, no CUDA/drivers/toolkits,
no global pip/npm, no `~/.local`). Tools go into project venvs or run as containers. Temp files and
caches stay under `/home` (the Makefile points `TMPDIR` and `PIP_CACHE_DIR` at `.dev/`).

**One switch, two worlds:** `APP_ENV=dev` runs the whole system on this machine (`make dev`);
`APP_ENV=aws` is the deployed system. See `.env.example`.

Deviation from PLAN §3: the notebooks and the old `app.py` stay where they are. Moving them would break their
relative paths, and nothing new depends on them.

---

## Part 0 — Accounts, quotas, tools [SETUP]  (do today; quota approval can take days)

### 0.1 AWS CLI + credentials (CLI lives in a project venv)
1. `python3.11 -m venv .venv-tools && .venv-tools/bin/pip install awscli` (AWS CLI v1 from PyPI — same commands as v2 for everything here).
   Use it as `.venv-tools/bin/aws`, or `source .venv-tools/bin/activate` for the session.
2. AWS console → IAM → Users → create user `neurolens-admin` → attach `AdministratorAccess` (course project) → Security credentials → Create access key (CLI).
3. `aws configure --profile neurolens` → paste keys, region `ap-southeast-1`, output `json`.
4. `export AWS_PROFILE=neurolens` for the session. Check: `aws sts get-caller-identity`.

### 0.2 GPU availability + quota
1. Check g6e is offered in Singapore:
   ```bash
   aws ec2 describe-instance-type-offerings --location-type availability-zone \
     --filters Name=instance-type,Values=g6e.2xlarge --region ap-southeast-1 --output table
   ```
   Empty table → tell the team; region choice must change (fallback: `ap-northeast-1` or `us-east-1`).
2. Check current quotas:
   ```bash
   aws service-quotas get-service-quota --service-code ec2 --quota-code L-3819A6DF   # G and VT Spot vCPUs
   aws service-quotas get-service-quota --service-code ec2 --quota-code L-DB2E81BA   # G and VT On-Demand vCPUs
   ```
3. If Spot value < 16, request an increase to 16:
   ```bash
   aws service-quotas request-service-quota-increase --service-code ec2 --quota-code L-3819A6DF --desired-value 16
   ```
   Do the same for On-Demand (`L-DB2E81BA`, value 8; needed to bake the AMI and as fallback).
   In the "use case" (console route: Service Quotas → EC2 → search "G and VT"), write: *"University cloud computing course project (NUS CS5224). Single-GPU inference workers that scale to zero; max 2 instances."*

### 0.3 Cost guardrail
Console → Billing → Budgets → Create budget → Cost budget → Monthly → amount **$100** → alerts at 50 / 80 / 100 % → your email. (CDK also creates one in Part 6; this one protects you before that.)

### 0.4 Hugging Face
1. Log in at huggingface.co with the account whose token is in `config.json`.
2. Confirm access is **granted** (not "pending") on https://huggingface.co/meta-llama/Llama-3.2-3B.
3. Create a **read** token named `neurolens-bake` (Settings → Access Tokens). Keep it for the AMI bake (Part 8).
   Locally it is not needed: the 16 GB of weights in `/home/kelvinlow/projects/neurolens/models` are reused.

### 0.5 Google OAuth client (optional for dev — dev sign-in works without it)
1. https://console.cloud.google.com → create project `neurolens`.
2. APIs & Services → OAuth consent screen → External → app name `NeuroLens`, your email as support + developer contact → scopes: `openid`, `email`, `profile` → add the 5 SUS participants + team as **test users** → save.
3. Credentials → Create credentials → OAuth client ID → Web application → name `neurolens-web`.
   Authorised redirect URIs: `http://localhost:8000/auth/callback` (the CloudFront one is added in Part 8).
4. Put the client ID + secret into `.env` as `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`.

### 0.6 Stripe (test mode; optional for dev)
1. Create a Stripe account; stay in **Test mode** (toggle top-right).
2. Developers → API keys → copy the **Secret key** (`sk_test_...`) → `.env` `STRIPE_SECRET_KEY`.
3. Webhooks in dev: `make dev-stripe` runs the Stripe CLI **as a container** and forwards events to localhost.
   It prints a `whsec_...` secret on start → put it in `.env` as `STRIPE_WEBHOOK_SECRET`, then `make dev-down && make dev`.
   (Packs are priced in code via `CREDIT_PACKS`, so no Stripe products are needed for credit packs.)

### 0.7 Docker storage (only needed before building the big images in Part 7/8)
Docker keeps images in `/var/lib/docker` on the root partition (12 GB free); the GPU worker image is ~10 GB.
This is a configuration change, not an install — your call whether to do it, or to build images on AWS instead (Part 7 offers that).
```bash
sudo systemctl stop docker
sudo mkdir -p /home/docker-data && sudo rsync -aHAX /var/lib/docker/ /home/docker-data/
echo '{ "data-root": "/home/docker-data" }' | sudo tee /etc/docker/daemon.json
sudo systemctl start docker && docker info | grep "Docker Root Dir"     # → /home/docker-data
```
The local GPU worker does **not** need Docker or the NVIDIA Container Toolkit — it runs from `.venv-worker` (Part 5).

### 0.8 CDK + Packer (Part 6–7), without host installs
- CDK: `npm install --prefix infra aws-cdk` (project-local `infra/node_modules`), run as `npx --prefix infra cdk ...`.
  CDK's Python libraries go into `.venv-tools`. `cdk bootstrap aws://<account-id>/ap-southeast-1` once after 0.1.
- Packer: run the official container image (`docker run hashicorp/packer:<version> ...`); the Makefile target will wrap it.

- [ ] 0.1 AWS CLI (venv) + profile
- [ ] 0.2 g6e offered + quota requests filed
- [ ] 0.3 Budget alert
- [ ] 0.4 HF LLaMA access + token
- [ ] 0.5 Google OAuth client (optional for dev)
- [ ] 0.6 Stripe test key (optional for dev)
- [ ] 0.7 Docker storage on /home (before Part 7 image builds)
- [ ] 0.8 CDK + Packer runnable, CDK bootstrapped

---

## Part 1 — Shared core: database, billing ledger, job rules [CODE]  (PLAN §4, §5)
- [x] `packages/common/neurolens_common`: `config`, `db`, `migrate`, `jobs`, `ledger`, `pricing`, `s3keys`, `reaper_sql`
- [x] `migrations/001_init.sql` (tables), `002_ledger_functions.sql` (reserve / adjust / capture / release / grant / fail_job)
- [x] Tests against a real Postgres: ledger invariants, no overdraw under 20 concurrent reserves, double release is a no-op, exactly one of two concurrent claims wins, stale-heartbeat reclaim, reaper rules, S3 key + event parsing
- **Done when** `pytest packages/common` is green. ✅ 29 passed (2026-10-27)

## Part 2 — GPU worker [CODE]  (PLAN §7)
- [x] `roi.py` ported from `app.py` + parity test against the old function
- [x] Result schema v2 (`neurolens_common/schemas.py`)
- [x] Inference backends: `mock` and `tribe` (incl. warm-up clip, `SILENT_MODE=ffmpeg|events`)
- [x] `queue.py` (SQS receive / extend / delete / requeue), `pipeline.py` (stages + timestamps), `loop.py` (boot → warm → poll → idle exit), `lifecycle.py`, `spot.py`, `media.py`, `weights.py`
- **Done when** the worker processes a job end to end with the mock backend in tests. ✅ 40 passed incl. ROI parity (2026-10-27)
- Note: the TRIBE backend itself is exercised in Part 5 (needs the GPU + weights).

## Part 3 — Web app + frontend [CODE]  (PLAN §6)
- [x] Flask app factory, Google OAuth, dev-login, load-test auth
- [x] `/api/me`, `/api/jobs*` (presigned POST, reserve, pre-warm), `/api/samples`, CSV export, metrics beacon
- [x] Stripe Checkout + webhook (idempotent)
- [x] Frontend: split `index.html`, login bar, upload with progress + GPU status chip, polling, history, buy credits
- **Done when** API tests pass (validation, 402, ownership, webhook replay). ✅ 60 passed total; signed-out page checked in headless Chrome (2026-10-27)
- Note: full signed-in upload flow is exercised by the Part 4 end-to-end test.

## Part 4 — Dev mode + containers [CODE]  (PLAN §8, §10)
- [x] `APP_ENV=dev|aws` switch; `.env.example`; every AWS piece has a dev stand-in
      (Postgres container ← Aurora, moto ← S3/SQS/DLQ, gunicorn ← Fargate/ALB/CloudFront, reaper loop ← both Lambdas, always-on worker ← Spot ASG)
- [x] `scripts/dev_stack.py` (`make dev`, `make dev-mock`, `make dev-down`, `make dev-status`, `make dev-logs NAME=…`), `scripts/dev_aws_init.py`
- [x] Venvs: `make venv-dev` (web, moto, mock worker, tests), `make venv-worker` (real GPU worker, `worker/requirements.lock` pinned from the venv that ran TRIBE v2)
- [x] Container specs: `web/Dockerfile`, `worker/Dockerfile` (targets `gpu`, `mock`), `.dockerignore`, `docker-compose.yml` (profiles `mock`, `gpu`)
- [x] `scripts/e2e.py` (`make test-e2e`), `make test` against the dev Postgres
- **Done when** `make test-e2e` passes on the mock stack. ✅ e2e PASS + 60 tests passed on `make dev-mock` (2026-10-27)
- Changes from plan: moto replaces LocalStack (smaller, no auth token; S3 POST → SQS notification verified);
  GPU image uses `python:3.11-slim` + the same cu126 wheels as the venv (identical packages dev ↔ AWS).

## Part 5 — Real model on this machine [SETUP]  (PLAN §7.6, §8.3, §15)
All commands from `/home/kelvinlow/projects/MCOMP/cloudcomputing`. Nothing goes on the host; everything lands in `.venv-worker` and `.dev/`.
1. **Build the GPU worker venv** (~8 GB download, 10–20 min):
   `make venv-worker` → last line must print `cuda True 2 GPU(s)`.
2. **Point at the existing weights:** `.env` already has `MODELS_DIR` / `HF_HOME` = `/home/kelvinlow/projects/neurolens/models` and `HF_HUB_OFFLINE=1`.
3. **Start everything with the real model:** `make dev-down` (if the mock stack is running), then `make dev`.
   Watch `make dev-logs NAME=worker` until `Worker … ready (tribe backend …)` (~1–2 min).
   - If it fails offline (a missing encoder), set `HF_HUB_OFFLINE=0` and `HF_TOKEN=<token>` in `.env` once, restart, then set it back to 1.
4. **Real end-to-end run** on a sample ad:
   `make test-e2e VIDEO="data/videos/7 Apr_CHOPS-Luckin.mp4"` → all PASS. Note `full=…s silent=…s` (per-pass GPU time).
5. **Compare with the prototype's numbers:** open http://localhost:8000, dev sign-in, upload the same ad, and compare its charts with the "Luckin Coffee — CHOPS" sample. They should match (same model, same ROI code).
6. **Silent-pass speed-up check:** `make dev-down` (frees the GPU), then
   `make silent-check VIDEO="data/videos/7 Apr_CHOPS-Luckin.mp4"` → if `"pass": true`, set `SILENT_MODE=events` in `.env` (and later in CDK). Record `silent_events_s` vs `silent_ffmpeg_s`.
7. **Experiment 3 baseline (on-prem):** `make bench-local VIDEOS="data/videos/a.mp4 data/videos/b.mp4 …"` → `experiments/out/bench-local.csv`.
- [x] 5.1 venv-worker built, CUDA visible (`torch 2.6.0+cu126 cuda True 2 GPU(s)`, 8.9 GB)
- [x] 5.2–5.4 real e2e PASS (Colgate 11 s: full 51 s, silent 33 s; Luckin 28 s: full 148 s, silent 140 s)
- [x] 5.5 results match the prototype's samples.json (corr 1.000 every ROI, max diff ≤ 0.002)
- [x] 5.6 silent-mode decision: **keep `SILENT_MODE=ffmpeg`** — `events` is 50–200× faster but differs on ads with speech (Colgate corr 0.945, 11 vs 10 timesteps); identical on music-only ads
- [x] 5.7 local benchmark → `experiments/out/bench-local.csv` (4 ads; ≈ 5–10 GPU-s per video-second; peak VRAM 10.9 GB)
- Fixes found while running (all in code now): worker lock reproduced with `--no-deps` (+ CUDA wheels, torchmetrics, psycopg-binary);
  tribev2's unpinned `uvx whisperx` routed to the pinned WhisperX via `worker/neurolens_worker/bin/uvx` + an allow-list for torch 2.6 safe loading;
  no-speech ads give an empty transcript instead of crashing; transient failures hand the job back for an immediate retry;
  feature cache wiped only at worker start; gunicorn keep-alive 75 s (> ALB 60 s); benchmark uses fresh copies (no cached transcripts).

## Part 5b — AI video report (step after TRIBE) [CODE]  (added 2026-10-27)
- [x] Key moments → frames (ffmpeg) + charts (matplotlib) → LLM (system instruction in `worker/neurolens_worker/report/prompt.py`) → PDF (ReportLab)
- [x] `REPORT_LLM` switch: `openai` (local, key in `.env`), `bedrock` (AWS, Claude Opus 5.5 via IAM role), `mock`, `off`
- [x] Charts | AI report tabs; PDF shown in-page, open in new tab, download; report failure never fails the job
- [x] OpenAI connectivity verified (`gpt-5.6-terra`); 69 tests pass
- [ ] VERIFY in Part 8: Claude Opus 5.5 on Bedrock in the chosen region (`bedrock_region` in `infra/cdk.json`) and the IAM actions the Bedrock Messages endpoint needs

## Part 6 — AWS infrastructure as code [CODE]  (PLAN §9)
- [x] CDK app (`infra/`): Network, Data, Compute, Ops stacks; settings in `infra/cdk.json`, override with `-c key=value`
- [x] Lambdas `lambdas/dlq_refund.py`, `lambdas/sla_reaper.py` (Data API, shared SQL rules; skips the DB when idle) + tests
- [x] `scripts/put_secrets.py`, `scripts/seed_samples.py`, `scripts/quota_check.sh`; Makefile AWS targets; `.venv-tools` (CDK libs, CDK CLI from PyPI, AWS CLI)
- **Done when** `cdk synth` succeeds. ✅ `make cdk-synth`: 4 stacks, 87 resources; 63 tests pass (2026-10-27)

## Part 7 — Worker AMI [CODE]  (PLAN §8.4)
- [x] `ami/worker.pkr.hcl`, `ami/provision.sh`, `ami/files/{neurolens-boot.sh, neurolens-scratch.sh, neurolens-worker.service}`; `make ami`, `make ami-validate` (Packer as a container)
- [x] Shell syntax checked. `packer validate` needs AWS credentials (AMI lookup) → runs in Part 8.

## Part 8 — Deploy to AWS [SETUP]
Instructions written when Parts 6–7 land: secrets, push images, bake AMI, `cdk deploy`, add CloudFront redirect URI to Google, Stripe webhook endpoint, seed samples, smoke test, **measure L40S inference time and tell the team**.

## Part 9 — Experiment tooling [CODE]  (PLAN §12)
- [ ] Locust file, FIS spot script, metrics export, TCO calculator, clip trimming, `experiments/README.md`

## Part 10 — Run experiments, usability study, teardown [SETUP]
Instructions written when Part 9 lands.
