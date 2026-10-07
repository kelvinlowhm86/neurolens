# NeuroLens — M2a Implementation Spec
**Milestone:** GPU image, private network and first real GPU run (first part of 9 – 19 Oct)
**Builds on:** M1 (Terraform-managed S3 bucket and SQS queue in `us-east-1`, presigned-POST upload, `neurolens/worker.py` with the `FAKE_INFERENCE` switch running on a laptop, pytest + moto suite). M2a moves the worker onto an on-demand GPU machine in a private network and proves the real model runs end to end. M2b then adds autoscaling, reliability and job status.

**Ground rules for all of M2a:** region `us-east-1` (the `g6e` GPU family is not offered in Singapore), held in one Terraform setting (§4i). Python 3.12, as in M0. All infrastructure is Terraform in `infra/terraform/`, tagged `Project=neurolens`, `Milestone=M2a`. Run AWS commands with the `neurolens` CLI profile only. **Tests first, as in M0 §5:** the §8 tests are written by a separate agent before the implementation and are not edited by the implementer.

**Do first, on day one — GPU quotas.** New accounts often have 0 and increases can take days. In `us-east-1`, check and if needed request:
- "Running On-Demand G and VT instances": at least **8 vCPUs** (the image build and the workers are all on-demand: one `g6e.xlarge` worker now, two in M2b, or one `g6e.2xlarge` fallback).
Also run `aws ec2 describe-instance-type-offerings --location-type availability-zone --filters Name=instance-type,Values=g6e.xlarge` and use two of the listed zones for the private subnets (§4a).

## 1. Scope
### In scope
- GPU machine image (AMI) with software only; model weights stored once in S3 and copied to the machine's local NVMe disk at boot
- VPC with private subnets for workers, an optional NAT Gateway for outbound traffic (a Terraform switch, M3b §5), and a free S3 gateway endpoint
- Launch Template + Auto Scaling Group of on-demand GPU workers, standing at zero, with **no scaling policy yet** (a worker is started by hand, §4d)
- systemd unit that pulls the latest code on every start; UserData that pulls weights and writes config on first boot
- Cost guards: a worker that shuts its instance down when idle or broken, an email alarm for a long-running GPU, and a retry cap (dead-letter queue, §4h) so a job that keeps failing stops after two attempts instead of repeating real GPU inference
- `deploy_code.sh` for code updates without an image rebuild
- Results written to S3 with a conditional write, instead of M1's local output folder
- Start/stop scripts that keep idle cost near zero
- A fake-mode wiring rehearsal on a cheap CPU instance, then the real-model check deferred from M1
- Inference corrections found by that check (§7a): correct word timing for videos over 60 s and a single video encoding per job

### Out of scope
- **M2b:** autoscaling, two workers, 120 s visibility timeout with heartbeat, fast failure release, graceful shutdown when a worker is stopped or scaled in, job-status objects and endpoints, the polling UI, and Experiments 1–2. Until M2b, a crash simply means the message reappears after M1's 900 s visibility timeout.
- **M3:** Aurora, user accounts, credits/billing, Google sign-in, the web tier's load balancer.
- EKS/Kubernetes, containers, image registries.

## 2. GPU image build (`infra/build_ami.sh`)
**Order of first-time setup:** `terraform apply` (network, IAM, everything except the Launch Template's AMI) → `deploy_code.sh` (§5, so `code/latest.zip` exists) → `build_ami.sh` → set the AMI ID variable → `terraform apply` again.

**Build instance permissions:** its own IAM instance profile, `neurolens-build`: `ssm:GetParameter` on `/neurolens/hf_token`; `s3:GetObject` on `code/*` and `smoke/*`; `s3:PutObject` and `s3:GetObject` on `models/*`; `s3:ListBucket` limited to `models/`; the AWS-managed SSM core policy.

The script (AWS CLI + shell), given the path of one local video that contains speech (§7: the open-licensed Sintel trailer):
1. Uploads that clip to `s3://<bucket>/smoke/clip.mp4` (never expires; a few MB).
2. Launches a temporary **`g6e.xlarge` on-demand** instance from the **plain Ubuntu 22.04 image** (Canonical's, from its public Parameter Store path) with a **50 GB gp3 root disk** set explicitly (the image's default is 8 GB; the disk size becomes the image's size and the workers' minimum root disk), in the VPC's **public** subnet with a temporary public IP. GPU capacity can run out in one zone (`InsufficientInstanceCapacity`), so Terraform also makes free public subnets in us-east-1b/c/d (`build_extra_zones`, output `build_subnet_ids`) and the script tries each zone in turn on that error only, first for `g6e.xlarge`, then for `g6e.2xlarge` (same GPU; the image works on either). It downloads ~20 GB from HuggingFace, which must not go through the NAT Gateway. `torch` 2.6's PyPI wheels bundle their own CUDA 12.4 libraries, so only the NVIDIA driver is needed from the system, not the CUDA toolkit. Reason for plain Ubuntu: the AWS Deep Learning Base GPU image (used for `neurolens-worker-v1`) carries CUDA toolkits nothing uses; v1's software filled 66 GB, billed every month as a snapshot.
3. **Installs the NVIDIA driver** (SSM Run Command, no key pair): Ubuntu's prebuilt, signed kernel modules and user-space tools of the NVIDIA **server** driver branch (`linux-modules-nvidia-<branch>-server-aws` and `nvidia-utils-<branch>-server`; branch 550 or newer, which supports CUDA 12.4; no DKMS build), and disables the `apt-daily`, `apt-daily-upgrade` and `unattended-upgrades` timers (otherwise every worker would start package updates at boot, through the NAT Gateway, holding the package lock). The script then **reboots** the instance (`aws ec2 reboot-instances`), waits for it to be back online in Session Manager, and checks `nvidia-smi` (skipped on the CPU rehearsal, which installs the driver but has no GPU). Driver branch and version go in the build log. `--dlami` builds from the Deep Learning Base GPU image instead and skips this step: a fallback if WhisperX turns out to need that image's system CUDA libraries (the step 5 checks fail loudly if so).
4. Uses SSM Run Command to:
   - Install Python 3.12 (`uv python install 3.12` if the image's system Python is not 3.12) and create a virtualenv at `/opt/neurolens/venv`
   - Download and unzip `code/latest.zip` to `/opt/neurolens/app` and `pip install -r requirements/model.txt`. Do **not** `pip install -e .`: the worker runs as `python worker.py` from `/opt/neurolens/app`, which puts that folder on Python's import path.
   - Install `ffmpeg` via apt
   - Install `infra/neurolens-worker.service`, `infra/neurolens-self-terminate.service`, `infra/pull_code.sh` and `infra/self_terminate.sh` (§3), with the worker service disabled
   - Mount the instance's NVMe disk at `/opt/neurolens/cache`, so the ~20 GB download never lands on the root disk that becomes the image
5. **Downloads everything through the worker's own code.** Write a build-time `/opt/neurolens/app/.env` with `HF_TOKEN` read from Parameter Store (`/neurolens/hf_token`, a `SecureString`), and a build-time `/opt/neurolens/app/config.json` with `paths.models = /opt/neurolens/cache/models` and `paths.data = /opt/neurolens/cache/data`. Then run the full pipeline once on `smoke/clip.mp4`: `load_model()`, `build_events`, `predict`, `predict` on `without_audio`, `extract_engagement` (the worker's own calls, §7a). Using the same code as the worker guarantees the same environment-variable order and cache layout, and both passes on a clip with speech trigger every lazily loaded encoder (video, audio, text including the gated Llama 3.2 model). tribev2 caches per-video features in the cache folder (`neuralset.extractors.*`), so the clip gets a file name unique to the build, and each run must show that the video, text and audio encoders each wrote new features during it; otherwise cached features could stand in for the models and the check would prove nothing.
   The step fails if the with-audio pass logs `whisperx failed`: the clip has speech, so that would mean the text features (and the Llama download) were silently skipped. The token is passed as an environment variable of that one process, never written to a file.
6. **Proves the cache is complete:** run the same pipeline again in a fresh process with `HF_HUB_OFFLINE=1`, no token, on a re-muxed copy of the clip under a new name (so no per-video feature cache can stand in for the models). If anything is missing, this fails now instead of on the first real job.
7. **Records peak system RAM and peak GPU memory** during step 5 (sample `/proc/meminfo` every second; `gpu_info()` for VRAM). Print both with the AMI ID. The RAM figure decides the worker instance size (§4b).
8. `aws s3 sync /opt/neurolens/cache/ s3://<bucket>/models/ --exclude "models/xet/*" --exclude "models/neuralset.extractors.*"`. The xet folder is a download-deduplication cache that can be as large as the weights themselves and is not needed offline; the features are per-video data, not weights. The `models/` prefix is not covered by any expiry rule. If `models/` is already populated, skip steps 5 and 8 (`--refresh-weights` forces them) and instead sync `models/` down to the cache and run step 6.
9. Deletes `/opt/neurolens/app/` and every file that held `HF_TOKEN` (the cache is on the NVMe disk, which is not part of the image), so the image contains **software only**.
10. Stops the instance, `aws ec2 create-image`, tags it `neurolens-worker-v{n}` (n = highest existing + 1), terminates the instance, prints the AMI ID and the size of its snapshot (it is billed while it exists).

**Money guards:** the script terminates the build instance on any exit (including Ctrl-C, and an instance launched just before one); every remote step has a deadline; and the instance is launched with `shutdown -h +240` in its user data and shutdown behaviour `terminate`, so it ends itself after 4 hours even if the Mac sleeps. A reboot cancels a scheduled shutdown, so the user data also records the deadline (launch + 4 hours) in a file and installs a small boot-time unit, `neurolens-build-deadline.service`, that re-arms the shutdown for the time left after the step 3 reboot. Step 9 removes both, and fails if either is still there: in a worker image they would shut every worker down. Step output goes to `/var/log/neurolens-build.log` on the instance; on failure the last 40 lines come back to the Mac.

**CPU rehearsal (`build_ami.sh --cpu-rehearsal`), run once before the first GPU build.** It launches a `t3.large` (on-demand, about $0.08 an hour) from the same base image in the public subnet with the `neurolens-build` profile, runs steps 3 and 4 only (driver install with its reboot, then the software; the NVMe mount falls back to a folder on the root disk, as in §4c step 2), prints success or the step that failed, and always terminates the instance. It reads no token, downloads no weights and creates no image. Reason: install and script mistakes are then fixed at CPU prices, not at the GPU's $1.86 an hour.

**Build log:** the measurements and choices this milestone asks to record (AMI IDs, snapshot size, peak RAM and VRAM, the chosen instance type, the §7 numbers) go in `docs/M2a_build_log.md`. It holds identifiers and numbers only, never account IDs or secrets.

## 3. Worker service on the machine (baked into the image)
`infra/neurolens-worker.service`:
```ini
[Unit]
Description=NeuroLens GPU worker
After=network-online.target
StartLimitIntervalSec=600
StartLimitBurst=3
OnFailure=neurolens-self-terminate.service
OnSuccess=neurolens-self-terminate.service

[Service]
WorkingDirectory=/opt/neurolens/app
EnvironmentFile=/opt/neurolens/env.conf
Environment=HOME=/root
ExecStartPre=/opt/neurolens/bin/pull_code.sh
ExecStart=/opt/neurolens/venv/bin/python worker.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```
- Baked in **disabled**. UserData (§4c) writes `config.json` and `env.conf`, then enables and starts it.
- **`pull_code.sh`** runs before every start (boot, crash restart, or `systemctl restart`): downloads `code/latest.zip` and `code/latest.revision`, removes `/opt/neurolens/app/neurolens/`, unzips over `/opt/neurolens/app` (keeping `config.json`), and writes `/opt/neurolens/app/REVISION`. It also copies the bundle's `infra/pull_code.sh` and `infra/self_terminate.sh` to `/opt/neurolens/bin/`, so script changes reach machines with the next start too (changes to the two systemd unit files still need an image rebuild, since they are read before this script runs). This is why a deploy reaches running machines with a plain restart; EC2 runs UserData only on an instance's first boot.
- **`env.conf`** is written on first boot and holds only `NEUROLENS_ROOT=/opt/neurolens/app`, the deployment identifiers `NEUROLENS_S3_BUCKET`, `NEUROLENS_SQS_QUEUE_URL` and `NEUROLENS_AWS_REGION` (the names `settings.load_settings` reads; `pull_code.sh` uses the same bucket variable), `NEUROLENS_DEPLOYED=1` (M3b's guard against development settings on AWS), `HF_HUB_OFFLINE=1`, and `FAKE_INFERENCE=1` during the wiring rehearsal only. The HF/nilearn cache variables are **not** set here: `neurolens.settings.configure_env` sets them from `config.json` paths, so there is one source of truth. Never bake AWS resource identifiers into the image.
- **Self-termination** (cost guard). `neurolens-self-terminate.service` is a oneshot unit running `self_terminate.sh`, which calls `aws autoscaling terminate-instance-in-auto-scaling-group --instance-id <own id> --should-decrement-desired-capacity`. Decrementing matters: a plain `shutdown` would make the ASG launch a replacement, which fails the same way, in a billing loop. It runs when:
  - the worker exits cleanly after `worker.idle_exit_minutes` (config, 30 on AWS) with no messages received (`OnSuccess`);
  - the worker crashes 3 times within an hour (`OnFailure`), for example a broken model load or a failure just after it. The window must be longer than a crash cycle: each restart reloads the model (about 3.5 minutes), so with a 10-minute window crashes ~4 minutes apart never reach the limit and the machine restarts forever. systemd counts in fixed windows, not sliding ones. The limits (`StartLimitIntervalSec=3600`, `StartLimitBurst=3`) live in a drop-in, `/etc/systemd/system/neurolens-worker.service.d/limits.conf`, written by UserData (§4c), so they reach new workers without an image rebuild; the baked unit's own `600`/`3` lines are removed at the next rebuild (§12). `deploy_code.sh` runs `systemctl reset-failed neurolens-worker` before each restart, so deploys never count toward the limit;
  - UserData fails (an `ERR` trap in UserData calls `self_terminate.sh`).
  If the API call itself fails (for example the NAT Gateway is missing), `self_terminate.sh` logs and does nothing more; the §4f idle alarm is the backstop.
  `systemctl restart` (used by `deploy_code.sh`) also stops the worker for a moment, which fires `OnSuccess`. So when the unit calls it (`--after-worker-stop`), `self_terminate.sh` first waits 15 s and stands down if the worker is active or activating again. To debug a worker by hand, `systemctl mask neurolens-self-terminate` first, or stopping the service ends the machine.

## 4. Networking, Launch Template and Auto Scaling Group (Terraform)

### 4a. Networking
- One VPC with **two private subnets** for workers, in two availability zones that offer `g6e.xlarge` (checked on day one; a Terraform variable), and **one public subnet** for the NAT Gateway and the image-build instance.
- **NAT Gateway (a switch):** the Terraform variable `nat_gateway` (bool, default `false`, M3b §5). When `true`, an Elastic IP and a managed NAT Gateway in the public subnet, and the private subnets' route table sends `0.0.0.0/0` to it; when `false`, neither exists and the private subnets reach only S3. It carries only small API traffic (SQS, Parameter Store, Auto Scaling, CloudWatch, the Data API) and the first-boot downloads. It costs about $1.20 a day while it exists, so it is on only for days with GPU work. Terraform refuses to switch it off while the worker group's max is above 0 (a precondition reading the group).
- **S3 gateway endpoint** (free) on the private route table, so video, code and weight downloads go straight to S3, not through the NAT Gateway.
- **Worker security group:** no inbound rules; all outbound allowed. Workers have no public IP. Reason: servers with no public address can't be reached from the internet even if a firewall rule is later misconfigured.

### 4b. Launch Template and Auto Scaling Group
- Launch Template: the software-only AMI from §2; the group's instance types are a Terraform list, tried in order with a mixed instances policy (`prioritized`, 100% on-demand): default **`g6e.xlarge`, then `g6e.2xlarge`** when no `xlarge` is free in any zone. Put `g6e.2xlarge` first only if §2's peak-RAM measurement leaves less than ~4 GB free on the 32 GB `xlarge`; record the measurement and the choice in the build log. **On-demand, not Spot:** over the 90 days to 2026-10-03, Spot `g6e` averaged only 1–9% below on-demand and was sold out in all four zones on repeated attempts (data in `docs/evidence/`). If on-demand is sold out too, Spot is as well, so there is no Spot fallback; the group keeps retrying. Root disk 100 GB gp3: at least the image's 50 GB, plus room for the ~18 GB of weights on a CPU rehearsal, which has no instance store (a root disk bills only while a worker runs, about one cent an hour). No public IP; private subnets only. UserData is rendered with Terraform `templatefile`, which fills in the region, bucket name and queue URL (identifiers, not secrets).
- Worker IAM instance profile, scoped per prefix (M2b adds to it):
  - `s3:GetObject` on `uploads/*`, `code/*`, `models/*`, `results/*`
  - `s3:ListBucket` on the bucket, with no `s3:prefix` condition. Without it, S3 answers a request for a *missing* object with 403 instead of 404, which the worker would treat as a failure instead of `GONE`; a prefix condition would bring the 403 back, because a HEAD or GET request carries no prefix.
  - `s3:PutObject` on `results/*`
  - `s3:DeleteObject` on `uploads/*` only (oversize and too-long rejection, M1)
  - `sqs:ReceiveMessage`, `DeleteMessage`, `GetQueueAttributes` on the job queue
  - `autoscaling:TerminateInstanceInAutoScalingGroup` on this ASG only (§3 self-termination)
  - The AWS-managed SSM core policy, so instances can be reached with Session Manager (no SSH, no key pair)
  - An explicit **Deny** of `ssm:GetParameter`, `GetParameters`, `GetParametersByPath` and `GetParameterHistory` on `parameter/neurolens/*`. The SSM core policy allows parameter reads on everything and the role boundary allows `/neurolens/*`, so without it a worker could read the HuggingFace token, which only the image build needs. A Deny always wins over an Allow.
- Auto Scaling Group: **min 0 / max 1 / desired 0.** Its scaling is the queue-driven policies of M2b; the idle alarm only emails. Group metrics enabled (free; §4f uses them). Terraform `lifecycle { ignore_changes = [desired_capacity, min_size, max_size] }` so the start/stop scripts (§4d) and Terraform don't fight.

### 4c. UserData (bash, in the Launch Template, first boot, in order)
1. `set -euo pipefail`; an `ERR` trap that logs the failing line and calls `self_terminate.sh` (§3); log every step with a timestamp to `/var/log/neurolens-boot.log` (M2b's Experiment 2 reads these).
2. Format and mount the instance-store NVMe disk at `/opt/neurolens/cache`. If no instance-store disk exists (the CPU rehearsal instance, §4e), use a folder on the root disk instead.
3. `aws s3 sync s3://<bucket>/models/ /opt/neurolens/cache/`. Log its duration.
4. Write `/opt/neurolens/env.conf` (§3), with `NEUROLENS_S3_BUCKET`, `NEUROLENS_SQS_QUEUE_URL` and `NEUROLENS_AWS_REGION` from the templated values, and `/opt/neurolens/app/config.json` (the shared file's schema) with absolute paths: `paths.models = /opt/neurolens/cache/models`, `paths.data = /opt/neurolens/cache/data`. `chmod 600` both. No `HF_TOKEN` is needed: weights come from S3 and `HF_HUB_OFFLINE=1` is set.
5. Run `pull_code.sh` once, then verify: `test -s /opt/neurolens/env.conf` and `/opt/neurolens/venv/bin/python -c "from neurolens.settings import load_config; load_config()"` run from `/opt/neurolens/app` with `NEUROLENS_ROOT` set. A failure trips the `ERR` trap.
6. Write the §3 start-limit drop-in, `systemctl daemon-reload`, then `systemctl enable --now neurolens-worker`.

### 4d. Start/stop scripts (budget discipline)
- `infra/start_work.sh`: re-enable the idle alarm's actions (§4f) and refuse to start anything unless the alarm exists with its actions on (a clear message for `AccessDenied` or a missing alarm), refuse unless a NAT Gateway is available, set the ASG to min 0 / max 1. With `--keep-worker`, also set desired capacity to 1 (the only way a worker starts in M2a).
- `infra/stop_work.sh`: the money-safety script, so it never claims success it has not proven.
  1. Get the region through `aws_env.sh` (§4i). If Terraform cannot answer, print **NOT CONFIRMED** with the command to fix it (`terraform -chdir=infra/terraform init -backend-config=backend.hcl`) and exit non-zero; it suggests no region by hand (a guessed region after a move would find nothing and wrongly report success). The §4f idle alarm caps what a worker left running meanwhile can cost.
  2. Re-enable the idle alarm's actions (§4f). Read the `neurolens-workers` group; a missing group is a failed check ("is this the right region?"), since it exists once the image is set. If it exists and any of its min / max / desired is above 0, set it to 0 / 0 / 0, **whether or not it has machines yet** (a group still waiting for GPU capacity has desired 1 and no machine; left alone, it would launch a worker later with no route to SQS). Wait up to 10 minutes for its workers to go (not if setting it to 0 failed). Then report a NAT Gateway or Elastic IP that still exists as NOT CONFIRMED (about $1.20 a day: set `nat_gateway = false` and apply). Other live machines tagged `Project=neurolens` (an image build) are listed, not stopped, with the exact `aws ec2 terminate-instances --instance-ids <id>` command to end one: a build has its own guards and may be running on purpose.
  3. Check every AWS call's exit status (never test `$(aws ...)` output inside `[ ]`, where a failed call reads as "nothing found"). An `AccessDenied` is a failed check.
  4. Print **ALL STOPPED** only if every check succeeded, the group reads 0 / 0 / 0 and no neurolens machine is pending, running, stopping or shutting down; stopped machines (disk only) are listed for information. Otherwise print **NOT CONFIRMED**, with each failed call, non-zero group or live machine, and exit non-zero.
  Every call is filtered on the `Project=neurolens` tag or the group name `neurolens-workers`, so it cannot touch other projects; the deploy user already has every permission it needs in the deployment region.
- Run `stop_work.sh` at the end of every working session. Workers cannot reach SQS without the NAT Gateway, which is why the ASG max must be 0 whenever it is off.

### 4e. Wiring rehearsal (before the first GPU boot)
Set the worker instance types to a small CPU type (e.g. `["t3.large"]`), with `FAKE_INFERENCE=1` in `env.conf`, and a software image without CUDA if the GPU AMI won't boot on it. Use this to debug networking, UserData, the S3 syncs, the systemd unit, code pulls, self-termination and the result write for cents instead of dollars. **Include one full `stop_work.sh` / `start_work.sh --keep-worker` cycle, with the NAT Gateway switched off and on again**, to prove the network comes back each time. Switch back to the GPU type for real runs.

### 4f. GPU alarms (Terraform)
Two CloudWatch alarms, both emailing Josh's address through one SNS topic (a variable; confirm the subscription email once):
- **Idle worker (acts):** fires when the group has a worker in service and the job queue has seen **no message received and none deleted for 90 minutes** (metric math `IF(insvc > 0 AND FILL(recv, 0) + FILL(del, 0) == 0, 1, 0)`, where `insvc` is the group's `GroupInServiceInstances` (Maximum) and `recv` / `del` are the job queue's `NumberOfMessagesReceived` / `NumberOfMessagesDeleted` (Sum); threshold ≥ 1 for 18 of 18 five-minute periods; missing data not breaching; `insvc` is not filled, so no group data never fires it). Empty long-poll receives are counted separately (`NumberOfEmptyReceives`), so a polling but idle worker reads as idle; redeliveries count as received. Its action only emails (M3b §6b); the circuit breaker (M2b §2c), on its 5-minute schedule, then stops the group unless a warm hold is on, with no laptop or working worker involved. A healthy worker always picks up or finishes a job within 90 minutes: the longest job (a 120 s 4K video plus the first-job warm-up) takes about 55 minutes, and an idle worker ends itself after 30. So the alarm never ends a worker that is doing work or that would not have ended itself, and it catches: a failed self-termination, a crash loop (with or without a waiting job), a worker frozen mid-job, and a worker cut off from SQS because the NAT Gateway is missing. Missing SQS data (AWS stops publishing a queue's metrics after about 6 hours without activity) counts as no activity (the `FILL`). The alarm returns to OK one period after the worker ends, so the next `start_work.sh --keep-worker` gets a fresh 90 minutes. A leak costs at most about 1.5–2 hours of GPU time (about $3–4). Raise the window if a single job can ever take longer than about 75 minutes (M2b's 4K downscaling only shortens jobs). The alarm cannot see work done outside the queue (manual runs or debugging through Session Manager, or a worker kept warm with its self-termination masked), so:
  - **Warning where it matters:** `infra/debug_worker.sh` (finds the running worker, then runs `aws ssm start-session`) is the way to open a shell on a worker, and it refuses unless a warm hold is on ("run infra/start_work.sh --keep-worker first"), so AWS cannot end the worker mid-session. UserData also writes a reminder to `/etc/profile.d/` for login shells (`bash -l`, `sudo -i`). The alarm's description, which its email carries, says why the worker was ended.
  - **Manual work:** a warm hold (M2b §2d), which replaced M2a's pause switch: with M2b's scale-in, a paused alarm no longer kept a worker alive. `start_work.sh` and `stop_work.sh` still run `enable-alarm-actions` (and `start_work.sh` refuses unless the actions are on), so an alarm switched off by hand is back on by the next session.
- **Long-running (email only):** `GroupInServiceInstances` > 0 continuously for **3 hours**. A warning while there is time to react, and the backstop if the idle alarm itself is misconfigured. It does not act, because a busy demo or study session can legitimately keep a worker up for hours.
Cost: about $0.40 a month (four alarm metrics at $0.10), possibly inside AWS's always-free 10 alarm metrics. The deploy user already has `cloudwatch:PutMetricAlarm` and `autoscaling:*` on neurolens resources.

### 4g. Configuration additions (`config.json`, `.env`)
- `worker.idle_exit_minutes` (absent means never exit on idle; UserData sets 30 on AWS).
- **The region is a deployment value, not a behaviour setting** (AGENTS.md), so it lives with the bucket and queue: `config.json` has no `aws` block, and `NEUROLENS_AWS_REGION` is required in `.env` on a laptop (copied from `terraform output region` in the same step as the bucket and queue URL; `.env.example` lists it uncommented) and in `env.conf` on AWS (filled in by Terraform, §4c). `settings.apply_env` maps it to `aws.region` as before. Anything that builds an AWS client from the settings (the worker's `run()`, the web app's S3 client) fails before doing any work with an error naming `NEUROLENS_AWS_REGION` when it is missing. Tests build their own settings and do not read `config.json`.

### 4h. Retry cap (dead-letter queue, Terraform on M1's queue)
Real inference costs money, and M1's queue would redeliver a permanently failing job every 900 s for days. Add a **dead-letter queue** (a side queue that parks a message after repeated failures) named `<queue_name>-dlq`, SQS-managed encryption on, message retention 14 days, and a **redrive policy** on the job queue with `maxReceiveCount = 2`: a message received twice without being deleted moves to the dead-letter queue and is never retried again. Remove M1's `# TODO(M2b)` comment on the queue. Set the job queue's **visibility timeout to 1800 s** (was M1's 900 s): the first real jobs on `g6e.xlarge` took 1003 s and 1039 s (52 s and 119 s clips, before §7a), longer than 900 s, so with more than one worker a job would have been handed out twice. A failing job is therefore given up on after about an hour (M2b replaces this with its 120 s heartbeat). A 120 s 4K video takes about 40–47 minutes, longer than 1800 s, so its message becomes visible again mid-job: harmless with one worker (nobody else takes it; the delete still works), but the group's max stays 1 until M2b's heartbeat exists. SQS moves the message itself, so the worker's IAM needs no access to the dead-letter queue. To inspect a parked job: `aws sqs receive-message --queue-url <dlq url>`; to retry after a fix, use the console's "Start DLQ redrive". The name starts with `neurolens-`, so the deploy user's scoped policy already covers it.

### 4i. Region (one setting)
The region is one Terraform variable, `region` (default `us-east-1`). Everything region-specific derives from it: the provider, the S3 gateway endpoint's service name, the ARNs in the role policies, UserData's region value, and an output `region` that every script in `infra/` reads (through `infra/aws_env.sh`, from Terraform only; the shell's `NEUROLENS_AWS_REGION`, the Python app's copy from `.env`, is ignored, so a stale copy can never point a script at the wrong region) instead of hard-coding one. The scripts ask Terraform, not a copy, because Terraform records where the resources actually are. When Terraform cannot answer, `aws_env.sh` stops the script before it does anything and says to check that Terraform is initialised (`terraform -chdir=infra/terraform init -backend-config=backend.hcl`) and the AWS profile works; it suggests no region by hand (`stop_work.sh` reports this as NOT CONFIRMED, §4d). The Python app reads the region from `NEUROLENS_AWS_REGION` (§4g), never from `config.json`. The zone lists (`zones`, `build_extra_zones`) are set in `terraform.tfvars`. IAM names are global, so a deployment outside `us-east-1` adds its region to its role and instance-profile names; `us-east-1` keeps the original names. The Terraform state bucket stays in `us-east-1`. This makes a later move to another region a small change; whether a second region is needed for the demo is decided in M4 (M4 §1).

## 5. Code deployment (`infra/deploy_code.sh`)
```bash
#!/usr/bin/env bash
set -euo pipefail
if [ -n "$(git status --porcelain)" ]; then
  echo "Uncommitted changes: commit first. Deploy ships the last commit only." >&2; exit 1
fi
git archive --format=zip -o /tmp/code.zip HEAD worker.py neurolens pyproject.toml requirements \
  infra/pull_code.sh infra/self_terminate.sh infra/neurolens-worker.service infra/neurolens-self-terminate.service
git rev-parse --short HEAD > /tmp/code.revision
aws s3 cp /tmp/code.zip      s3://<bucket>/code/latest.zip
aws s3 cp /tmp/code.revision s3://<bucket>/code/latest.revision
echo "Deployed $(cat /tmp/code.revision). Running workers pick it up on their next restart; new ones on start."
```
`git archive` keeps the `neurolens/` folder structure (a flattening `zip -j` would break every `from neurolens...` import) and ships exactly one commit, whose ID later goes into experiment manifests. `deploy_code.sh` then runs `systemctl restart neurolens-worker` through SSM Run Command on every running worker that has no job, so a deploy reaches them without new instances.

## 6. Results in S3
From M2a on, the worker writes each result to `results/{job_id}.json` instead of M1's local output folder, on the laptop and on AWS alike; the worker has no local output folder and config has no `paths.output`.
- `neurolens.storage.put_result(s3, bucket, job_id, result) -> bool` writes with `put_object(..., IfNoneMatch="*")`: `True` if written, `False` if a result already existed (`PreconditionFailed`, HTTP 412). Any other error propagates, including the `ConditionalRequestConflict` (HTTP 409) S3 may return when two writes race; the record then fails and is retried.
- A completed result is therefore never replaced. M2b builds its duplicate handling and status on this.
- `neurolens.worker.Outcome` gains `DUPLICATE`: returned when `put_result` returns `False`. It is final, so the message is deleted.
- Results are read with `aws s3 cp` for now; M2b adds the web endpoints.

## 7. Real-model check (deferred from M1)
On the first GPU boot, upload one video with speech that we have the rights to (the open-licensed Sintel trailer, about 52 s; record its title, URL and licence in `data/videos/SOURCES.md`) through the web app's presign flow and let the worker process it. There are no stored reference numbers to compare against, so the check is that the real pipeline ran and its output is sane and repeatable:
- The job finishes: `results/{job_id}.json` exists, `fake_inference` is absent and `gpu` is present.
- `duration_seconds` and the number of `timesteps` give one row per second started (the 52.2 s trailer gives 53 rows, the 119.01 s loop 120); every value is between 0 and 1; `engagement_overall` and the five region columns are not constant (maximum above minimum).
- The same clip run a second time agrees with the first run to within 0.001 on every value. A larger gap is not automatically a bug but must be explained in the build log before M2b starts.
- The worker log shows all three encoders loaded (video, audio, and text including the gated Llama 3.2 model), as in §2 step 5.
- Record the job's wall-clock time and the peak GPU memory from the `gpu` field, for the cost figures in M2b and M4.
- **After §7a:** the 119 s loop (`data/videos/_test_clips/`) gives exactly 120 timesteps (one per second started) with every transcribed word attached once, and each job encodes the video once. The 52 s trailer (no chunking, so the ghost-word fix does not touch it) agrees with the first real run's result within 0.001, which also checks that the no-audio pass on `without_audio` matches the old audio-free file.
- **On the plain-Ubuntu image** (§2), in the same session: the `transformers` version in the build log (model precision depends on it: see `docs/evidence/README.md`, "Model precision"; Meta's default is kept); the first job's warm-up time compared with v1's 8 minutes; one open-licensed 4K clip (peak RAM, job time); and the GPU benchmark of `g6e.xlarge`, `g6.2xlarge` and `g5.2xlarge`, run and decided by the rule in `docs/evidence/README.md` (section "Test"). The winner becomes the first entry of `worker_instance_types`. Only after the new image has passed the checks above: deregister `neurolens-worker-v1` and delete its snapshot.
These are read by hand from the result files (a few lines of Python pasted into the build log is fine); no script is added to the repo.

## 7a. Inference corrections (`neurolens/inference.py`, `neurolens/worker.py`)
Two problems found on the first real GPU run, in tribev2 commit `af58661` with neuralset 0.0.2 (evidence: `docs/M2a_build_log.md`):
- **Ghost words after 60 s.** tribev2's demo path splits audio longer than 60 s into chunks that share one audio file, transcribes the whole file once, then gives *every* chunk a copy of the *whole* transcript shifted by `start + offset` (120 s for the second chunk). Every word reappears 120 s too late, the timeline stretches to about twice the video, and a 119 s video returns 239 rows. Rows up to 99 s are right, rows 100–118 share a 100 s model window with the ghost words, and rows from 119 s on are garbage. Videos of 60 s or less are unaffected. (Upstream pull request #29 proposes `start - offset`, which still leaves every word duplicated; text features are summed, so that would double the text input.)
- **The video is encoded twice.** The no-audio pass runs on a separate audio-free copy of the file, so tribev2's per-file feature cache misses and the identical video is encoded again: about half of each job.

`run_inference` and `strip_audio` are replaced by three functions, so a job keeps three stages (with-audio pass, no-audio pass, ROI extraction):
- `build_events(video_path) -> events`: builds the events the same way as tribev2's `demo_utils.get_audio_and_text_events` (the same preparation steps, parameters and order, copied into our code with a comment naming the pinned commit), except that the word step is our corrected `ExtractWordsFromAudio` subclass. The subclass reuses tribev2's transcription and its `.tsv` transcript cache unchanged and replaces only how words are attached to audio chunks, through `attach_chunk_words`. Correct for any video length; there is no length condition in the fix. tribev2 is pinned to `af58661` in `requirements/model.txt`, so the copied steps only change when we change the pin on purpose.
- `without_audio(events) -> events`: keeps only the `Video` rows. It is the same video file, so the no-audio pass reuses the cached video features; the video model does not use the soundtrack.
- `predict(events, duration) -> numpy array (n, 20484)`: tribev2's `predict`, then `order_by_time(preds, [segment start times], duration, tr)` with `tr` read from the model (1 s).

Pure helpers, testable without tribev2:
- `attach_chunk_words(transcript, chunks)`: `transcript` holds one audio file's words with times measured from the start of the file (as WhisperX writes them); `chunks` are that file's audio chunks (`start` on the video timeline, `offset` into the file, `duration`). Each word goes to exactly one chunk, the one with `offset <= word start < offset + duration`, and its time becomes `chunk start + (word start - offset)`. Words outside every chunk are dropped and counted in the log. Each output word carries the same chunk fields tribev2 copies (everything except `frequency`, `filepath`, `type`, `start`, `duration`, `offset`), its own `duration`, and `type = "Word"`. The subclass then adds the `language` column, as tribev2 does.
- `order_by_time(preds, starts, duration, tr=1.0)`: returns the rows sorted by start time. Raises `TimelineError` (a `RuntimeError` subclass) unless the starts, rounded to 1 ms, are exactly `0, tr, 2·tr, ...` with no gap or repeat, the last start is below `duration` and reaches the end (at least `duration - 2·tr`, so a timeline cut short also fails), and there is one start per row (at least one row). A misaligned timeline must fail loudly, never reach a result. It is a safety net, not the fix.

Fake mode (`FAKE_INFERENCE=1`): `build_events` and `without_audio` return a small stand-in recording the path and whether audio is kept; `predict` returns seeded random numbers of shape `(ceil(duration), 20484)`, seeded from the file's size (plus 1 for the no-audio pass), so both passes differ and repeat exactly.

The worker calls `events = build_events(local)`, `preds_full = predict(events, duration)`, `preds_noaudio = predict(without_audio(events), duration)`, then `extract_engagement` (unchanged). No `.noaudio` file is written. A `TimelineError` is an ordinary failure (the message is retried, then dead-lettered).

The result's `gpu.peak_vram_gb` is the peak of that job alone: the worker calls `inference.reset_gpu_peak()` (imports `torch` inside the function; does nothing without a GPU or in fake mode) at the start of each job. Without the reset, a light job after a heavy one would report the heavy job's peak.

## 8. Tests (`FAKE_INFERENCE=1 pytest`, moto)
Add to M1's suite, written first:
- `put_result` writes `results/{job_id}.json` and returns `True`; a second call for the same job returns `False` and leaves the first result unchanged. If `moto` does not implement `IfNoneMatch`, the test simulates the 412 error on the client instead, and says so in a comment.
- The worker publishes through `put_result`, never to the local output folder, returns `DUPLICATE` and deletes the message when `put_result` returns `False`. M1's "valid video" test is changed by the test-writing agent to read `results/` in moto instead of the local folder (a spec'd test change).
- `run()` returns after `worker.idle_exit_minutes` with no messages (use a tiny value and a stubbed clock or short poll wait in the test), and never returns on idle when the setting is absent.
- `resolve_paths` keeps absolute config paths (as UserData writes them) unchanged instead of joining them to the root.
- §7a, on small `pandas` tables and arrays (no tribev2): `attach_chunk_words` with words at 1, 61 and 110 s of a 119 s file split into chunks (start 0, offset 0, 60 s) and (start 60, offset 60, 59 s) returns each word exactly once at 1, 61 and 110 s; a word exactly on a chunk boundary goes only to the later chunk; three chunks of a 150 s file put every word once at its true time; a chunk whose `start` differs from its `offset` maps times by `start + (word - offset)`; an empty transcript gives no words; a word past the last chunk is dropped. `order_by_time` sorts shuffled rows and raises `TimelineError` for a repeated start, a gap, a start at or after `duration`, a timeline ending more than `2·tr` before `duration`, a starts/rows count mismatch and no rows. Fake mode: `predict` shapes and repeatability, and the two passes differ.
- The worker runs `build_events`, `predict`, `without_audio`, `predict`, `extract_engagement` in that order, writes no `.noaudio` file, and treats a `TimelineError` as a failure. These replace M1's tests of `run_inference`, `strip_audio` and the strip-then-rerun order (a spec'd test change, made by the test-writing agent).

## 9. File layout additions
```
infra/
  terraform/                        MODIFIED: VPC, NAT Gateway switch, S3 endpoint, Launch Template, ASG, IAM, alarm
  build_ami.sh                      NEW: software-only image; weights to S3; peak RAM/VRAM record; --cpu-rehearsal
  neurolens-worker.service          NEW: systemd unit, baked into the image
  neurolens-self-terminate.service  NEW
  pull_code.sh                      NEW: runs before every worker start
  self_terminate.sh                 NEW
  deploy_code.sh                    NEW
  aws_env.sh                        NEW: sourced by the scripts: profile, and the region from Terraform (§4i)
  start_work.sh                     NEW: check the NAT Gateway, ASG max 1, optional warm hold
  stop_work.sh                      NEW: ASG to 0 / 0 / 0, report a leftover NAT Gateway; ALL STOPPED only when proven
  debug_worker.sh                   NEW: opens Session Manager on the worker; needs a warm hold
neurolens/storage.py                MODIFIED: put_result
neurolens/inference.py              MODIFIED: build_events, without_audio, predict, attach_chunk_words, order_by_time (§7a)
requirements/model.txt              MODIFIED: tribev2 pinned to commit af58661
neurolens/worker.py                 MODIFIED: results to S3, Outcome.DUPLICATE, idle exit, §7a call order
tests/                              MODIFIED: §8 tests
docs/M2a_build_log.md               NEW: measurements and choices (§2)
config.json                         MODIFIED: no aws block (the region comes from NEUROLENS_AWS_REGION, §4g)
.env.example                        MODIFIED: NEUROLENS_AWS_REGION required
```

## 10. Acceptance criteria
1. Both GPU quotas were checked (and raised if needed), and the private subnets are in zones that offer `g6e.xlarge`.
2. `build_ami.sh` produces a software-only image and populates `s3://<bucket>/models/` (without the xet cache); the offline re-run (§2 step 6) passed; it prints the AMI ID, snapshot size, and the peak RAM and VRAM. The chosen worker `instance_type` is justified by the RAM number.
3. The wiring rehearsal (§4e) processes a job end-to-end in fake mode on a CPU instance, including a NAT stop/start cycle after which a second job still completes.
4. **Real-model check:** one real job through S3 → SQS → GPU worker passes §7, or its difference is explained.
5. The boot log shows timestamps for every UserData step, including the S3 weight-sync duration.
6. `deploy_code.sh` refuses to run with uncommitted changes; after a deploy, `deploy_code.sh` restarts an idle running worker so it runs run the new commit, and `/opt/neurolens/app/REVISION` shows its ID.
7. A fresh instance, with no `config.json` in the image, writes `config.json` and `env.conf` (mode 600) through UserData and starts `neurolens-worker` without manual steps; the service is disabled in the image and starts only after both files are verified.
8. Self-termination works in all three cases, observed on the rehearsal instance: idle timeout, three crashes (e.g. a deliberately broken config), and a failing UserData step. Each time the ASG's desired capacity drops to 0 and no replacement launches.
9. Workers have no public IP and no inbound rules, yet reach SQS and Auto Scaling through the NAT Gateway and S3 through the gateway endpoint.
10. `stop_work.sh` leaves zero GPU instances running (and reports a NAT Gateway that still exists); `start_work.sh --keep-worker` brings the system back and a new job completes.
11. The §4f alarm exists and its email subscription is confirmed.
12. `terraform apply` run twice reports no changes the second time.
13. `FAKE_INFERENCE=1 pytest` passes locally and in CI, including the §8 tests, and the tests were committed before the implementation.
14. A job forced to fail twice lands in the dead-letter queue and is not retried again. Force it by restarting the worker twice while a fake job runs (`deploy_code.sh --now`, twice): each restart hands the job back, and the second receive is the last. Then confirm a new job completes.
15. §7a: the 119.01 s clip returns 120 timesteps on the GPU, each job encodes the video once, and the job queue's visibility timeout is 1800 s.
16. The worker image is built from plain Ubuntu (§2), its snapshot size is in the build log next to v1's, and v1 is deregistered with its snapshot deleted.
17. Region (§4i): `terraform plan` in `us-east-1` reports no changes after the refactor.
18. The GPU benchmark (§7) is recorded in the build log and the chosen type is first in `worker_instance_types`.
19. **Crash loop (rehearsal):** first, three `deploy_code.sh --now` runs within the hour after the worker started leave it running (the boot start counts too, so without `reset-failed` the third restart would end the machine). Then, on that worker only, a temporary drop-in replaces its start command with a slow failure (`systemctl edit neurolens-worker`: `[Service]`, `ExecStart=`, `ExecStart=/bin/sh -c 'sleep 240; exit 1'`), then `systemctl reset-failed neurolens-worker` and a restart: starts at about 0, 245 and 490 s, the next is refused, and the machine terminates itself with the group's desired capacity at 0 (about 13 minutes). Under the old 10-minute window this would loop forever.
20. **Idle alarm (rehearsal):** with the alarm email subscription confirmed, no other queue traffic, the worker's self-termination masked (`systemctl mask neurolens-self-terminate`) and no jobs, the idle alarm fires within about 90–100 minutes and an email arrives; the circuit breaker then stops the group within 5 minutes. Also once at the start of a session after more than 6 quiet hours (when the queue publishes no metrics): switch the NAT Gateway off right after `start_work.sh --keep-worker` (by hand in the console, since Terraform refuses while the group may run), so the worker can never reach SQS; the alarm and breaker still end it within about 100 minutes. `terraform plan` shows both alarms and the scaling policy. `debug_worker.sh` opens a session on the worker only during a warm hold.
21. **`stop_work.sh`:** run while the group has desired 1 and no machine yet (right after `start_work.sh --keep-worker`): the group ends at 0 / 0 / 0 and a leftover NAT Gateway is reported. Run from a fresh `git worktree` (no Terraform setup): it prints NOT CONFIRMED with the `terraform init` command and exits non-zero. Run with a failing AWS call (e.g. a wrong profile): NOT CONFIRMED, exit non-zero.
22. A worker's role cannot read `/neurolens/hf_token` (`aws ssm get-parameter` through Session Manager is denied). The build role is untouched by construction (the Deny is only on the worker and NAT roles); the next image build, whose weights step reads the token, confirms it.
23. With no `aws` block in `config.json` and `NEUROLENS_AWS_REGION` set in `.env`, the local web app presigns an upload and the job completes on AWS; without the variable, the web app and the worker stop with an error naming it.

## 11. Between sessions, idle cost and teardown
- End every working session with `stop_work.sh`.
- **Idle cost with everything stopped** (record the real numbers in the README once known): the image snapshot (billed per GB-month; §2 prints its size), the ~20 GB of weights in S3 (about $0.50/month), and the alarms. The Elastic IP and the NAT Gateway exist, and bill, only while `nat_gateway` is true.
- Keep the software-only image, `models/`, and `code/latest.zip`; M2b, M3 and M4 reuse them.
- Final teardown (`terraform destroy`, deregistering images, deleting snapshots) happens only after M4's consolidation.

## 12. Explicitly not in this milestone
Everything listed for M2b and M3 in §1. Don't pull autoscaling, status tracking or the heartbeat forward "for completeness".

**At the next image rebuild** (not before; each needs `build_ami.sh` edits or a new image):
- An outside guard for the build machine: a CloudWatch alarm with the EC2 terminate action, created right after launch and deleted in cleanup, so a machine that hangs during the driver reboot ends even if the Mac is asleep. Until then, do not leave a build unattended.
- Every remote step's deadline adds up to less than the 4-hour self-shutdown (today about 4.2 hours), and the first remote step checks the shutdown is scheduled on every path, including `--dlami`.
- A real (non-rehearsal) build fails if `/opt/neurolens/cache` is not a separate mounted disk, so the weights can never be baked into the image.
- Remove the `StartLimitIntervalSec`/`StartLimitBurst` lines from the baked unit (the §3 drop-in holds them).
- Self-termination (removed in M2b §2b): drop `OnSuccess=`/`OnFailure=` from the baked unit, delete `neurolens-self-terminate.service` and `infra/self_terminate.sh` (now a stub), and their lines in `pull_code.sh` and `build_ami.sh`.

**Known limitations** (accepted; they need a second failure, a mistake or an attacker):
- `self_terminate.sh` tries once; a failed call is caught by the idle alarm within about 90 minutes.
- `stop_work.sh` needs Terraform to know the region; if Terraform cannot answer, it stops nothing and says NOT CONFIRMED with the fix (the idle alarm caps the cost meanwhile).
- If the idle alarm itself fails, only the 3-hour email remains.
- The `ec2:InstanceType` allow-list can be bypassed through a Launch Template (Auto Scaling launches with its own role) or by changing a stopped instance's type; the real cap is the 8-vCPU GPU quota.
- `RunInstances` accepts any subnet or security group, including another project's (only a typo could cause that).
- The worker's `s3:ListBucket` covers the whole bucket (§4b explains why).
- A job parked in the dead-letter queue for more than 2 days comes back `GONE` when redriven: its upload has expired.
- A redelivered job after a lost delete reruns the GPU work before finding the existing result (`DUPLICATE`); M2b's check before any work removes this.
