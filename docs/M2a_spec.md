# NeuroLens — M2a Implementation Spec
**Milestone:** GPU image, private network and first real GPU run (first part of 9 – 19 Oct)
**Builds on:** M1 (Terraform-managed S3 bucket and SQS queue in `us-east-1`, presigned-POST upload, `neurolens/worker.py` with the `FAKE_INFERENCE` switch running on a laptop, pytest + moto suite). M2a moves the worker onto an on-demand GPU machine in a private network and proves the real model runs end to end. M2b then adds autoscaling, reliability and job status.

**Ground rules for all of M2a:** region `us-east-1` (the `g6e` GPU family is not offered in Singapore). Python 3.12, as in M0. All infrastructure is Terraform in `infra/terraform/`, tagged `Project=neurolens`, `Milestone=M2a`. Run AWS commands with the `neurolens` CLI profile only. **Tests first, as in M0 §5:** the §8 tests are written by a separate agent before the implementation and are not edited by the implementer.

**Do first, on day one — GPU quotas.** New accounts often have 0 and increases can take days. In `us-east-1`, check and if needed request:
- "Running On-Demand G and VT instances": at least **8 vCPUs** (the image build and the workers are all on-demand: one `g6e.xlarge` worker now, two in M2b, or one `g6e.2xlarge` fallback).
Also run `aws ec2 describe-instance-type-offerings --location-type availability-zone --filters Name=instance-type,Values=g6e.xlarge` and use two of the listed zones for the private subnets (§4a).

## 1. Scope
### In scope
- GPU machine image (AMI) with software only; model weights stored once in S3 and copied to the machine's local NVMe disk at boot
- VPC with private subnets for workers, a NAT instance for outbound traffic, and a free S3 gateway endpoint
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
2. Launches a temporary **`g6e.xlarge` on-demand** instance from the **AWS Deep Learning Base GPU AMI (Ubuntu 22.04)** (NVIDIA driver and CUDA, no preinstalled frameworks, so the image stays smaller), in the VPC's **public** subnet with a temporary public IP. GPU capacity can run out in one zone (`InsufficientInstanceCapacity`), so Terraform also makes free public subnets in us-east-1b/c/d (`build_extra_zones`, output `build_subnet_ids`) and the script tries each zone in turn on that error only, first for `g6e.xlarge`, then for `g6e.2xlarge` (same GPU; the image works on either). It downloads ~20 GB from HuggingFace, which must not go through the small NAT instance. `torch` 2.6's PyPI wheels bundle their own CUDA 12.4 libraries, so only the NVIDIA driver version matters: confirm it supports CUDA 12.4.
3. Uses SSM Run Command (no key pair) to:
   - Install Python 3.12 (`uv python install 3.12` if the image's system Python is not 3.12) and create a virtualenv at `/opt/neurolens/venv`
   - Download and unzip `code/latest.zip` to `/opt/neurolens/app` and `pip install -r requirements/model.txt`. Do **not** `pip install -e .`: the worker runs as `python worker.py` from `/opt/neurolens/app`, which puts that folder on Python's import path.
   - Install `ffmpeg` via apt
   - Install `infra/neurolens-worker.service`, `infra/neurolens-self-terminate.service`, `infra/pull_code.sh` and `infra/self_terminate.sh` (§3), with the worker service disabled
   - Mount the instance's NVMe disk at `/opt/neurolens/cache`, so the ~20 GB download never lands on the root disk that becomes the image
4. **Downloads everything through the worker's own code.** Write a build-time `/opt/neurolens/app/.env` with `HF_TOKEN` read from Parameter Store (`/neurolens/hf_token`, a `SecureString`), and a build-time `/opt/neurolens/app/config.json` with `paths.models = /opt/neurolens/cache/models` and `paths.data = /opt/neurolens/cache/data`. Then run the full pipeline once on `smoke/clip.mp4`: `load_model()`, `run_inference`, `strip_audio`, `run_inference` again, `extract_engagement`. Using the same code as the worker guarantees the same environment-variable order and cache layout, and both passes on a clip with speech trigger every lazily loaded encoder (video, audio, text including the gated Llama 3.2 model).
   The step fails if the with-audio pass logs `whisperx failed`: the clip has speech, so that would mean the text features (and the Llama download) were silently skipped. The token is passed as an environment variable of that one process, never written to a file.
5. **Proves the cache is complete:** run the same pipeline again in a fresh process with `HF_HUB_OFFLINE=1`, no token, on a re-muxed copy of the clip under a new name (so no per-video feature cache can stand in for the models). If anything is missing, this fails now instead of on the first real job.
6. **Records peak system RAM and peak GPU memory** during step 4 (sample `/proc/meminfo` every second; `gpu_info()` for VRAM). Print both with the AMI ID. The RAM figure decides the worker instance size (§4b).
7. `aws s3 sync /opt/neurolens/cache/ s3://<bucket>/models/ --exclude "models/xet/*"`. The xet folder is a download-deduplication cache that can be as large as the weights themselves and is not needed offline. The `models/` prefix is not covered by any expiry rule. If `models/` is already populated, skip steps 4 and 7 (`--refresh-weights` forces them) and instead sync `models/` down to the cache and run step 5.
8. Deletes `/opt/neurolens/app/` and every file that held `HF_TOKEN` (the cache is on the NVMe disk, which is not part of the image), so the image contains **software only**.
9. Stops the instance, `aws ec2 create-image`, tags it `neurolens-worker-v{n}` (n = highest existing + 1), terminates the instance, prints the AMI ID and the size of its snapshot (it is billed while it exists).

**Money guards:** the script terminates the build instance on any exit (including Ctrl-C, and an instance launched just before one); every remote step has a deadline; and the instance is launched with `shutdown -h +240` in its user data and shutdown behaviour `terminate`, so it ends itself after 4 hours even if the Mac sleeps. Step output goes to `/var/log/neurolens-build.log` on the instance; on failure the last 40 lines come back to the Mac.

**CPU rehearsal (`build_ami.sh --cpu-rehearsal`), run once before the first GPU build.** It launches a `t3.large` (on-demand, about $0.08 an hour) from the same base image in the public subnet with the `neurolens-build` profile, runs step 3 only (the NVMe mount falls back to a folder on the root disk, as in §4c step 2), prints success or the step that failed, and always terminates the instance. It reads no token, downloads no weights and creates no image. Reason: install and script mistakes are then fixed at CPU prices, not at the GPU's $1.86 an hour. If the base image will not boot on a `t3.large`, use the plain Ubuntu 22.04 image instead (the install steps are the same apart from the NVIDIA driver).

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
  - the worker crashes 3 times within 10 minutes (`OnFailure`), for example a broken model load;
  - UserData fails (an `ERR` trap in UserData calls `self_terminate.sh`).
  If the API call itself fails (for example the NAT instance is down), `self_terminate.sh` logs and does nothing more; the §4f alarm is the backstop.
  `systemctl restart` (used by `restart_workers.sh`) also stops the worker for a moment, which fires `OnSuccess`. So when the unit calls it (`--after-worker-stop`), `self_terminate.sh` first waits 15 s and stands down if the worker is active or activating again. To debug a worker by hand, `systemctl mask neurolens-self-terminate` first, or stopping the service ends the machine.

## 4. Networking, Launch Template and Auto Scaling Group (Terraform)

### 4a. Networking
- One VPC with **two private subnets** for workers, in two availability zones that offer `g6e.xlarge` (checked on day one; a Terraform variable), and **one public subnet** for the NAT instance and the image-build instance.
- **NAT instance:** a `t4g.micro` Amazon Linux 2023 instance in the public subnet (a `t4g.nano` has too little memory: `dnf` is killed while installing) with an **auto-assigned public IP, not an Elastic IP** (AWS charges for every public IPv4 address, and an Elastic IP keeps billing while the instance is stopped; an auto-assigned one is released on stop). Source/destination check disabled. Its user data runs once, so it must make the setup **survive stop/start**: install `iptables-services`, write IP forwarding to `/etc/sysctl.d/`, add the masquerade rule on the interface named by `ip route show default` (on AL2023 it is usually `ens5`, not `eth0`), `iptables-save > /etc/sysconfig/iptables`, and `systemctl enable iptables`. The private subnets' route table sends `0.0.0.0/0` to it. It carries only small API traffic (SQS, Parameter Store, Auto Scaling, CloudWatch). Tag it `Role=nat` so the start/stop scripts can find it.
- **S3 gateway endpoint** (free) on the private route table, so video, code and weight downloads go straight to S3, not through the NAT instance.
- **Worker security group:** no inbound rules; all outbound allowed. Workers have no public IP. Reason: servers with no public address can't be reached from the internet even if a firewall rule is later misconfigured.

### 4b. Launch Template and Auto Scaling Group
- Launch Template: the software-only AMI from §2; the group's instance types are a Terraform list, tried in order with a mixed instances policy (`prioritized`, 100% on-demand): default **`g6e.xlarge`, then `g6e.2xlarge`** when no `xlarge` is free in any zone. Put `g6e.2xlarge` first only if §2's peak-RAM measurement leaves less than ~4 GB free on the 32 GB `xlarge`; record the measurement and the choice in the build log. **On-demand, not Spot:** over the 90 days to 2026-10-03, Spot `g6e` averaged only 1–9% below on-demand and was sold out in all four zones on repeated attempts (data in `docs/evidence/`). If on-demand is sold out too, Spot is as well, so there is no Spot fallback; the group keeps retrying. Root disk 100 GB gp3 (the image's software fills 66 of its 75 GB, and a CPU rehearsal has no instance store for the weights). No public IP; private subnets only. UserData is rendered with Terraform `templatefile`, which fills in the region, bucket name and queue URL (identifiers, not secrets).
- Worker IAM instance profile, scoped per prefix (M2b adds to it):
  - `s3:GetObject` on `uploads/*`, `code/*`, `models/*`, `results/*`
  - `s3:ListBucket` on the bucket, with no `s3:prefix` condition. Without it, S3 answers a request for a *missing* object with 403 instead of 404, which the worker would treat as a failure instead of `GONE`; a prefix condition would bring the 403 back, because a HEAD or GET request carries no prefix.
  - `s3:PutObject` on `results/*`
  - `s3:DeleteObject` on `uploads/*` only (oversize and too-long rejection, M1)
  - `sqs:ReceiveMessage`, `DeleteMessage`, `GetQueueAttributes` on the job queue
  - `autoscaling:TerminateInstanceInAutoScalingGroup` on this ASG only (§3 self-termination)
  - The AWS-managed SSM core policy, so instances can be reached with Session Manager (no SSH, no key pair)
- Auto Scaling Group: **min 0 / max 1 / desired 0, no scaling policy.** Group metrics enabled (free; §4f uses them). Terraform `lifecycle { ignore_changes = [desired_capacity, min_size, max_size] }` so the start/stop scripts (§4d) and Terraform don't fight.

### 4c. UserData (bash, in the Launch Template, first boot, in order)
1. `set -euo pipefail`; an `ERR` trap that logs the failing line and calls `self_terminate.sh` (§3); log every step with a timestamp to `/var/log/neurolens-boot.log` (M2b's Experiment 2 reads these).
2. Format and mount the instance-store NVMe disk at `/opt/neurolens/cache`. If no instance-store disk exists (the CPU rehearsal instance, §4e), use a folder on the root disk instead.
3. `aws s3 sync s3://<bucket>/models/ /opt/neurolens/cache/`. Log its duration.
4. Write `/opt/neurolens/env.conf` (§3), with `NEUROLENS_S3_BUCKET`, `NEUROLENS_SQS_QUEUE_URL` and `NEUROLENS_AWS_REGION` from the templated values, and `/opt/neurolens/app/config.json` (the shared file's schema) with absolute paths: `paths.models = /opt/neurolens/cache/models`, `paths.data = /opt/neurolens/cache/data`, `paths.output = /opt/neurolens/output`; `worker.idle_exit_minutes = 30`. `chmod 600` both. No `HF_TOKEN` is needed: weights come from S3 and `HF_HUB_OFFLINE=1` is set.
5. Run `pull_code.sh` once, then verify: `test -s /opt/neurolens/env.conf` and `/opt/neurolens/venv/bin/python -c "from neurolens.settings import load_config; load_config()"` run from `/opt/neurolens/app` with `NEUROLENS_ROOT` set. A failure trips the `ERR` trap.
6. `systemctl enable --now neurolens-worker`.

### 4d. Start/stop scripts (budget discipline)
- `infra/start_work.sh`: start the NAT instance, wait until it is running, set the ASG to min 0 / max 1. With `--worker`, also set desired capacity to 1 (the only way a worker starts in M2a).
- `infra/stop_work.sh`: set the ASG to min 0 / max 0 / desired 0, wait for workers to terminate, stop the NAT instance, and confirm with a tag-filtered `aws ec2 describe-instances` that no GPU or NAT instance is running. Print a clear "all stopped" line.
- Run `stop_work.sh` at the end of every working session. Workers cannot reach SQS while the NAT instance is stopped, which is why the ASG max is 0 then.

### 4e. Wiring rehearsal (before the first GPU boot)
Set the worker instance types to a small CPU type (e.g. `["t3.large"]`), with `FAKE_INFERENCE=1` in `env.conf`, and a software image without CUDA if the GPU AMI won't boot on it. Use this to debug networking, UserData, the S3 syncs, the systemd unit, code pulls, self-termination and the result write for cents instead of dollars. **Include one full `stop_work.sh` / `start_work.sh --worker` cycle**, to prove the NAT instance's routing survives a stop/start. Switch back to the GPU type for real runs.

### 4f. Long-running GPU alarm (Terraform)
A CloudWatch alarm on the ASG's `GroupInServiceInstances` > 0 continuously for **3 hours** sends an email through an SNS topic to Josh's address (a variable; confirm the subscription email once). It catches the cases self-termination cannot, such as a worker stuck mid-job or an unreachable NAT instance. Cost: about $0.10 a month.

### 4g. Configuration additions (`config.json`)
`worker.idle_exit_minutes` (absent means never exit on idle; UserData sets 30 on AWS).

### 4h. Retry cap (dead-letter queue, Terraform on M1's queue)
Real inference costs money, and M1's queue would redeliver a permanently failing job every 900 s for days. Add a **dead-letter queue** (a side queue that parks a message after repeated failures) named `<queue_name>-dlq`, SQS-managed encryption on, message retention 14 days, and a **redrive policy** on the job queue with `maxReceiveCount = 2`: a message received twice without being deleted moves to the dead-letter queue and is never retried again. Remove M1's `# TODO(M2b)` comment on the queue. Set the job queue's **visibility timeout to 1800 s** (was M1's 900 s): the first real jobs on `g6e.xlarge` took 1003 s and 1039 s (52 s and 119 s clips, before §7a), longer than 900 s, so with more than one worker a job would have been handed out twice. A failing job is therefore given up on after about an hour (M2b replaces this with its 120 s heartbeat). SQS moves the message itself, so the worker's IAM needs no access to the dead-letter queue. To inspect a parked job: `aws sqs receive-message --queue-url <dlq url>`; to retry after a fix, use the console's "Start DLQ redrive". The name starts with `neurolens-`, so the deploy user's scoped policy already covers it.

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
`git archive` keeps the `neurolens/` folder structure (a flattening `zip -j` would break every `from neurolens...` import) and ships exactly one commit, whose ID later goes into experiment manifests. `infra/restart_workers.sh` runs `systemctl restart neurolens-worker` on every running worker through SSM Run Command, so a deploy reaches them without new instances.

## 6. Results in S3
From M2a on, the worker writes each result to `results/{job_id}.json` instead of M1's local output folder, on the laptop and on AWS alike.
- `neurolens.storage.put_result(s3, bucket, job_id, result) -> bool` writes with `put_object(..., IfNoneMatch="*")`: `True` if written, `False` if a result already existed (`PreconditionFailed`, HTTP 412). Any other error propagates, including the `ConditionalRequestConflict` (HTTP 409) S3 may return when two writes race; the record then fails and is retried.
- A completed result is therefore never replaced. M2b builds its duplicate handling and status on this.
- `neurolens.worker.Outcome` gains `DUPLICATE`: returned when `put_result` returns `False`. It is final, so the message is deleted.
- Results are read with `aws s3 cp` for now; M2b adds the web endpoints.

## 7. Real-model check (deferred from M1)
On the first GPU boot, upload one video with speech that we have the rights to (the open-licensed Sintel trailer, about 52 s; record its title, URL and licence in `data/videos/SOURCES.md`) through the web app's presign flow and let the worker process it. There are no stored reference numbers to compare against, so the check is that the real pipeline ran and its output is sane and repeatable:
- The job finishes: `results/{job_id}.json` exists, `fake_inference` is absent and `gpu` is present.
- `duration_seconds` and the number of `timesteps` give one row per second of the clip (a final partial second may add a row or not: the 52.2 s trailer gave 53 rows, the 119.01 s loop 119); every value is between 0 and 1; `engagement_overall` and the five region columns are not constant (maximum above minimum).
- The same clip run a second time agrees with the first run to within 0.001 on every value. A larger gap is not automatically a bug but must be explained in the build log before M2b starts.
- The worker log shows all three encoders loaded (video, audio, and text including the gated Llama 3.2 model), as in §2 step 4.
- Record the job's wall-clock time and the peak GPU memory from the `gpu` field, for the cost figures in M2b and M4.
- **After §7a:** the 119 s loop (`data/videos/_test_clips/`) gives exactly 119 timesteps with every transcribed word attached once, and each job encodes the video once. The 52 s trailer (no chunking, so the ghost-word fix does not touch it) agrees with the first real run's result within 0.001, which also checks that the no-audio pass on `without_audio` matches the old audio-free file.
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
  terraform/                        MODIFIED: VPC, NAT instance, S3 endpoint, Launch Template, ASG, IAM, alarm
  build_ami.sh                      NEW: software-only image; weights to S3; peak RAM/VRAM record; --cpu-rehearsal
  neurolens-worker.service          NEW: systemd unit, baked into the image
  neurolens-self-terminate.service  NEW
  pull_code.sh                      NEW: runs before every worker start
  self_terminate.sh                 NEW
  deploy_code.sh                    NEW
  restart_workers.sh                NEW
  start_work.sh                     NEW: start NAT instance, ASG max 1, optional worker
  stop_work.sh                      NEW: ASG to 0, stop NAT instance, confirm nothing running
neurolens/storage.py                MODIFIED: put_result
neurolens/inference.py              MODIFIED: build_events, without_audio, predict, attach_chunk_words, order_by_time (§7a)
requirements/model.txt              MODIFIED: tribev2 pinned to commit af58661
neurolens/worker.py                 MODIFIED: results to S3, Outcome.DUPLICATE, idle exit, §7a call order
tests/                              MODIFIED: §8 tests
docs/M2a_build_log.md               NEW: measurements and choices (§2)
```

## 10. Acceptance criteria
1. Both GPU quotas were checked (and raised if needed), and the private subnets are in zones that offer `g6e.xlarge`.
2. `build_ami.sh` produces a software-only image and populates `s3://<bucket>/models/` (without the xet cache); the offline re-run (§2 step 5) passed; it prints the AMI ID, snapshot size, and the peak RAM and VRAM. The chosen worker `instance_type` is justified by the RAM number.
3. The wiring rehearsal (§4e) processes a job end-to-end in fake mode on a CPU instance, including a NAT stop/start cycle after which a second job still completes.
4. **Real-model check:** one real job through S3 → SQS → GPU worker passes §7, or its difference is explained.
5. The boot log shows timestamps for every UserData step, including the S3 weight-sync duration.
6. `deploy_code.sh` refuses to run with uncommitted changes; after a deploy, `restart_workers.sh` makes a running worker run the new commit, and `/opt/neurolens/app/REVISION` shows its ID.
7. A fresh instance, with no `config.json` in the image, writes `config.json` and `env.conf` (mode 600) through UserData and starts `neurolens-worker` without manual steps; the service is disabled in the image and starts only after both files are verified.
8. Self-termination works in all three cases, observed on the rehearsal instance: idle timeout, three crashes (e.g. a deliberately broken config), and a failing UserData step. Each time the ASG's desired capacity drops to 0 and no replacement launches.
9. Workers have no public IP and no inbound rules, yet reach SQS and Auto Scaling through the NAT instance and S3 through the gateway endpoint.
10. `stop_work.sh` leaves zero GPU instances running and the NAT instance stopped; `start_work.sh --worker` brings the system back and a new job completes.
11. The §4f alarm exists and its email subscription is confirmed.
12. `terraform apply` run twice reports no changes the second time.
13. `FAKE_INFERENCE=1 pytest` passes locally and in CI, including the §8 tests, and the tests were committed before the implementation.
14. A job forced to fail twice lands in the dead-letter queue and is not retried again. Force it by starting the rehearsal worker with a `paths.output` that cannot be created: every job then fails before any work, so the test costs nothing. Fix the path afterwards and confirm a new job completes.
15. §7a: a 119 s clip returns 119 timesteps on the GPU, each job encodes the video once, and the job queue's visibility timeout is 1800 s.

## 11. Between sessions, idle cost and teardown
- End every working session with `stop_work.sh`.
- **Idle cost with everything stopped** (record the real numbers in the README once known): the image snapshot (billed per GB-month; §2 prints its size), the ~20 GB of weights in S3 (about $0.50/month), the NAT instance's small root disk, and the alarm. There is no Elastic IP charge, because §4a uses none.
- Keep the software-only image, `models/`, and `code/latest.zip`; M2b, M3 and M4 reuse them.
- Final teardown (`terraform destroy`, deregistering images, deleting snapshots) happens only after M4's consolidation.

## 12. Explicitly not in this milestone
Everything listed for M2b and M3 in §1. Don't pull autoscaling, status tracking or the heartbeat forward "for completeness".
