# NeuroLens — M2 Implementation Spec
**Milestone:** GPU Image, Networking & Elastic Autoscaling (9 – 19 Oct)
**Builds on:** M1 (Terraform-managed S3 bucket and SQS queue in `us-east-1`, presigned-POST upload, `inference.py` with the `FAKE_INFERENCE` switch, `worker.py` running on a laptop, pytest + moto suite). M2 moves the worker onto autoscaled Spot GPU machines in a private network, and adds real job-status tracking so the frontend can poll something true.

**Ground rules for all of M2:** region `us-east-1` (the `g6e` GPU family is not offered in Singapore). Python 3.11+ (required by the official `tribev2` repo). All infrastructure is Terraform in `infra/terraform/`, tagged `Project=neurolens`, `Milestone=M2`. Run AWS commands with the `neurolens` CLI profile only.

## 1. Scope
### In scope
- GPU machine image (AMI) with software only; model weights stored once in S3 and copied to the machine's local NVMe disk at boot
- VPC with private subnets for workers, a NAT instance for outbound traffic, and a free S3 gateway endpoint
- Launch Template + Auto Scaling Group of Spot GPU workers, scaled on SQS backlog, standing at zero
- systemd unit running `worker.py` from a pre-baked virtualenv; UserData that pulls code and weights on every boot
- `deploy_code.sh` for code updates without an image rebuild
- Dead-letter queue after two receive attempts
- S3-based job status so Flask can answer real status queries
- Duplicate-safe result publication via a conditional S3 write
- SQS visibility heartbeat, fast failure release, and Spot-interruption handling
- Start/stop scripts that keep idle cost near zero
- Real client-side polling UI
- Experiment 1 instrumentation and Experiment 2 (Locust) script
- The real-model check deferred from M1

### Out of scope (M3)
- Aurora, user accounts, credits/billing, Google sign-in, the web tier's load balancer
- EKS/Kubernetes, containers, image registries

## 2. GPU image build (`infra/build_ami.sh`)
A script (AWS CLI + shell) that:
1. Launches a temporary **`g6e.xlarge` on-demand** instance from an official AWS Deep Learning AMI (Ubuntu, NVIDIA driver + CUDA pre-installed — confirm the CUDA version matches what `torch` needs), in the VPC's **public** subnet with a temporary public IP. It downloads ~20 GB from HuggingFace, which must not go through the small NAT instance.
2. Uses SSM Run Command (no key pair) to:
   - Install Python 3.11 and create a virtualenv at `/opt/neurolens/venv`
   - Pin `numpy` **before** installing `tribev2`, exactly as `README.md`'s setup steps specify — do not reorder
   - Install `requirements.txt`, then `tribev2[plotting]` via the documented `git+https://...` install, then `ffmpeg` via apt
   - Install the systemd unit (§3), disabled
3. **Uploads model weights to S3 once.** On the build instance, with `HF_TOKEN` read from the Parameter Store `SecureString` `/neurolens/hf_token`, set the same `HF_HOME`/`HF_HUB_CACHE`/etc. env vars `inference.py` sets, and `NILEARN_DATA`, all pointed inside one scratch directory (so the Destrieux atlas is synced too and never downloaded at boot); trigger `TribeModel.from_pretrained(...)`, the encoder downloads, and the Destrieux atlas fetch; then `aws s3 sync` that directory to `s3://<bucket>/models/`. The `models/` prefix is not covered by any expiry rule. Skip the download if `models/` is already populated (`--refresh-weights` forces it).
4. Runs a short inference smoke test on a tiny sample clip and **records peak system RAM during model load and inference** (e.g. sample `free -m` / `/proc/meminfo` every second). Print it with the AMI ID. This decides the worker instance size (§4).
5. Deletes the weights scratch directory and every file that held `HF_TOKEN`, so the image contains **software only**.
6. Stops the instance, `aws ec2 create-image`, tags it `neurolens-worker-v{n}`, terminates the instance, prints the AMI ID.

**Comparison image (one-off).** `build_ami.sh --bake-weights` builds a second image that keeps the weights on the root disk. It is used once, for the cold-start comparison in §10, then deregistered with its snapshot. Reason: disks restored from a snapshot load lazily, so the first read of ~20 GB may be slow; the comparison measures whether copying from S3 is faster.

## 3. systemd unit (`infra/neurolens-worker.service`, baked into the image)
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
- Baked in **disabled**. UserData (§4c) writes `config.json` and `env.conf`, then enables and starts it.
- `env.conf` is written fresh on every boot (bucket name, queue URL, region, `HF_HOME` and `NILEARN_DATA` under `/opt/neurolens/models`, `HF_HUB_OFFLINE=1`). Never bake AWS resource identifiers into the image.

## 4. Networking, Launch Template and Auto Scaling (Terraform)

### 4a. Networking
- One VPC with **two private subnets** (in two availability zones, for Spot capacity) for workers, and **one public subnet** for the NAT instance and the image-build instance.
- **NAT instance:** a `t4g.nano` Amazon Linux 2023 instance in the public subnet, with an Elastic IP, IP forwarding and `iptables` masquerade set up in its user data, and source/destination check disabled. The private subnets' route table sends `0.0.0.0/0` to it. It carries only small API traffic (SQS, Parameter Store, CloudWatch). Tag it `Role=nat` so the start/stop scripts can find it.
- **S3 gateway endpoint** (free) on the private route table, so video, code and weight downloads go straight to S3, not through the NAT instance.
- **Worker security group:** no inbound rules; all outbound allowed. Workers have no public IP. Reason: servers with no public address can't be reached from the internet even if a firewall rule is later misconfigured.

### 4b. Launch Template and Auto Scaling Group
- Launch Template: the software-only AMI from §2; `instance_type` is a Terraform variable, default **`g6e.xlarge`**. Switch to `g6e.2xlarge` only if §2's peak-RAM measurement leaves less than ~4 GB free on the 32 GB `xlarge`; record the measurement and the choice in the build log. Spot market options (max price = on-demand price). No public IP; private subnets only.
- IAM instance profile, scoped per prefix:
  - `s3:GetObject` on `uploads/*`, `code/*`, `models/*`, `status/*`, `results/*`; `s3:ListBucket` limited to the `models/` prefix (for `aws s3 sync`)
  - `s3:PutObject` on `status/*`, `results/*`, `experiments/*`
  - `s3:DeleteObject` on `uploads/*` only (oversize rejection, M1 §4a)
  - `sqs:ReceiveMessage`, `DeleteMessage`, `ChangeMessageVisibility`, `GetQueueAttributes` on the job queue
  - `ssm:GetParameter` for `/neurolens/*` (plus `kms:Decrypt` if a customer-managed key is used)
  - The AWS-managed SSM core policy, so instances can be reached with Session Manager (no SSH, no key pair)
- Auto Scaling Group: standing configuration **min 0 / max 1 / desired 0**. Target tracking on SQS backlog (visible messages). Terraform `lifecycle { ignore_changes = [desired_capacity, min_size, max_size] }` so the start/stop scripts (§4d) and Terraform don't fight.
- Confirm empirically that target tracking holds at 0 when the queue is empty; note it if a scheduled nudge is needed.
- Queue changes (Terraform, on M1's queue): **visibility timeout 120 s** (the §8 heartbeat keeps long jobs invisible), and a **dead-letter queue** with `maxReceiveCount = 2`.
- **Quota:** 2 × `g6e.xlarge` = 8 Spot vCPUs; 2 × `g6e.2xlarge` needs 16. Check the approved "All G and VT Spot Instance Requests" quota before raising max to 2.

### 4c. UserData (bash, in the Launch Template, every boot, in order)
1. `set -euo pipefail`; log every step with a timestamp to `/var/log/neurolens-boot.log` (Experiment 2 reads these).
2. Format and mount the instance-store NVMe disk at `/opt/neurolens/models`.
3. `aws s3 sync s3://<bucket>/models/ /opt/neurolens/models/` — log its duration.
4. `aws s3 cp s3://<bucket>/code/latest.zip /tmp/code.zip && unzip -o /tmp/code.zip -d /opt/neurolens/app`
5. Write `/opt/neurolens/env.conf` (§3) and `/opt/neurolens/app/config.json` (matching `config.sample.json`'s schema) from instance tags / Parameter Store. `chmod 600` both. No `HF_TOKEN` is needed at boot: weights come from S3 and `HF_HUB_OFFLINE=1` is set.
6. Verify both files: `test -s /opt/neurolens/env.conf` and `python3 -c "import json; json.load(open('/opt/neurolens/app/config.json'))"`. Exit non-zero with a clear message if either fails.
7. `systemctl enable --now neurolens-worker`.

### 4d. Start/stop scripts (budget discipline)
- `infra/start_work.sh`: start the NAT instance, wait until it is running, set the ASG to min 0 / max 1.
- `infra/stop_work.sh`: set the ASG to min 0 / max 0 / desired 0, wait for workers to terminate, stop the NAT instance, and confirm with a tag-filtered `aws ec2 describe-instances` that no GPU or NAT instance is running. Print a clear "all stopped" line.
- Run `stop_work.sh` at the end of every working session. Workers cannot reach SQS while the NAT instance is stopped, which is why the ASG max is 0 then.

### 4e. Wiring rehearsal (before the first GPU boot)
Set the Launch Template's `instance_type` to a small CPU type (e.g. `t3.large`, on-demand), with `FAKE_INFERENCE=1` in `env.conf`, and a software image without CUDA if the Deep Learning AMI won't boot on it. Use this to debug networking, UserData, the S3 syncs, the systemd unit and scaling for cents instead of dollars. Switch back to the GPU type for real runs.

## 5. Code deployment (`infra/deploy_code.sh`)
```bash
#!/usr/bin/env bash
set -euo pipefail
zip -j /tmp/code.zip worker.py inference.py
aws s3 cp /tmp/code.zip s3://<bucket>/code/latest.zip
echo "Deployed. Running instances pick this up on their next restart."
```
Pushes `worker.py`/`inference.py` updates without rebuilding the image. An optional `infra/rolling_restart.sh` that SSM-restarts the service on running instances is a nice-to-have.

## 6. Job status tracking
Status objects at `status/{job_id}.json` in the same bucket:
```json
{ "job_id": "...", "status": "queued|processing|done|failed",
  "stage": "downloading|inference_full|stripping_audio|inference_noaudio|extracting_roi|null",
  "updated_at": "2026-10-14T03:22:10Z",
  "stages": [{"stage": "downloading", "at": "2026-10-14T03:22:10Z"}],
  "error": null }
```
- `job_id` is the UUID from the presign step.
- The worker writes `processing` as soon as it takes a message, before any real work, and updates `stage` at each pipeline transition, appending to `stages` (Experiment 1 reads it).
- Results go to `results/{job_id}.json` with a conditional `put_object(..., IfNoneMatch="*")`. This is the completion gate: a completed result is immutable, so a duplicate delivery cannot replace it.
- Write order: attempt the conditional result write first. Only if it succeeds, write `status: "done"`. If it fails because the result already exists, stop without error and do not touch status. Never overwrite a terminal `done`.
- New Flask endpoint `GET /api/jobs/{job_id}/status`: if `results/{job_id}.json` exists, return `done` regardless of the status object; otherwise return `status/{job_id}.json`; return 404 if neither exists (the frontend treats 404 as "still queued").
- New Flask endpoint `GET /api/jobs/{job_id}/result`: returns the result JSON from S3.

## 7. Duplicate deliveries
SQS may deliver a message more than once, and one S3 event message can hold several `Records`. For each message:
- Handle every record independently. Ignore records outside `uploads/`.
- If `results/{job_id}.json` already exists, the record is done: skip it.
- Otherwise process it and publish with the conditional write (§6). If that write loses, another worker finished first: discard this output silently.
- Delete the SQS message only after every record in it is ignored, skipped, or published (or lost the conditional write). Never delete a whole message after handling only one record.

With at most two workers, a duplicate that wastes one GPU run is rare and accepted; results and status are never duplicated. M3 adds a database lock that prevents the duplicate run itself. Do not add S3 lease or claim objects.

## 8. SQS visibility heartbeat and fast failure release
The queue's visibility timeout is 120 s: long enough to cover one heartbeat gap, short enough that a dead worker's job is retried within about two minutes.

**Heartbeat while a job is legitimately processing:** a background thread calls `sqs.change_message_visibility(QueueUrl=..., ReceiptHandle=..., VisibilityTimeout=120)` every 45–60 s. A silently dead worker stops the heartbeat, and the message becomes visible again within about 120 s.

**Fast release on any handled failure** (download, ffprobe, inference, ROI extraction): in the top-level exception handler, write `status: "failed"` with the error only if no result exists, then call `change_message_visibility(..., VisibilityTimeout=0)` so the message is immediately retryable. Do not delete it; after two receives it moves to the dead-letter queue.

**Spot interruption:** a background thread polls IMDSv2 for the interruption notice every ~5 s. On notice, stop taking new messages. If the in-flight job cannot plausibly finish within the two-minute warning, release it the same way (`VisibilityTimeout=0`). Experiment 2 must observe this proactive release.

## 9. Frontend: real polling
Replace the `setInterval`-based fake progress text in `analyseVideo()` with:
- After the presigned S3 POST succeeds (204), poll `GET /api/jobs/{job_id}/status` every 5 s.
- Map `status`/`stage` to the existing spinner/detail-line UI — reuse the visual design, drive it from real values.
- On `done`: fetch `GET /api/jobs/{job_id}/result` and render.
- On `failed`: show the real `error` field instead of a generic `alert()`.
- After 10 minutes without `done`/`failed`, show "taking longer than expected" (a UI safeguard only).

## 10. Experiments instrumentation
- **Shared artifact contract:** Experiments 1–3 write versioned artifacts to `experiments/<experiment>/<run_id>/` in the bucket. Each run has a `manifest.json` (`experiment`, `run_id`, UTC timestamps, code/AMI revision, instance type, input clip identifiers, configuration, output-file names), with experiment-specific CSV/JSON beside it. `experiments/*` never expires and is the sole input for M4's consolidation script.
- **Experiment 1 (latency):** per-stage durations from the `stages` list in status objects, across 15 s / 30 s / 60 s test videos. Analysis script `experiments/latency_breakdown.py`.
- **Experiment 2 (scaling):** `locustfile.py` POSTs to `/api/uploads/presign`, uploads the fixed test video via the presigned POST (fields first, file last), then polls status to completion.
  - **Elasticity sub-test (1 and 2 concurrent):** ASG scaling from 0, cold start, per-worker throughput. Use the 15 s clip first.
  - **Saturation sub-test (5 and 10 concurrent):** exceeds the 2-worker ceiling on purpose, to show the queue absorbing backlog, no dropped or duplicated results, and a full drain afterwards.
  - Raise the ASG max to 2 only for these runs, then restore it to 1. Cross-reference CloudWatch ASG instance count over time. Inject one manual Spot interruption and confirm redelivery within about 120 s via proactive release.
  - **Cold-start breakdown:** instance launch → UserData start; S3 weight sync (from the boot log); model load into GPU memory; first job ready. Plus one comparison boot from the `--bake-weights` image, measuring the first full read of the weights from the snapshot-restored disk. Report both; no target number is asserted in advance.

## 10a. Tests (`FAKE_INFERENCE=1 pytest`, moto)
Add to M1's suite:
- The heartbeat thread calls `change_message_visibility` with 120 while a job runs, and stops when it ends.
- A handled failure sets status `failed` and releases the message with `VisibilityTimeout=0` without deleting it.
- A record whose result already exists is skipped with no inference.
- A lost conditional result write leaves the existing result and `done` status untouched.
- The status endpoint returns `done` when a result exists even if the status object says otherwise, and 404 when neither exists.
- A multi-record message is deleted only after all its records are handled.

## 11. File layout additions
```
neurolens/
├── infra/
│   ├── terraform/                  # MODIFIED — VPC, NAT instance, S3 endpoint, DLQ, queue timeout, Launch Template, ASG, IAM
│   ├── build_ami.sh                # NEW — software-only image; weights to S3; peak-RAM record; --bake-weights
│   ├── neurolens-worker.service    # NEW — systemd unit, baked into the image
│   ├── deploy_code.sh              # NEW
│   ├── start_work.sh               # NEW — start NAT instance, ASG max 1
│   └── stop_work.sh                # NEW — ASG to 0, stop NAT instance, confirm nothing running
├── worker.py                       # MODIFIED — status writes, conditional result write, heartbeat + fast release, Spot handling, S3 results
├── app.py                          # MODIFIED — /api/jobs/{id}/status and /api/jobs/{id}/result
├── locustfile.py                   # NEW
├── experiments/
│   └── latency_breakdown.py        # NEW — Experiment 1 analysis
├── tests/                          # MODIFIED — §10a tests
└── static/                         # MODIFIED — real polling
```

## 12. Acceptance criteria
1. `build_ami.sh` produces a software-only image and populates `s3://<bucket>/models/`; it prints the AMI ID and the peak RAM measured during the smoke test. The chosen worker `instance_type` is justified by that number.
2. The wiring rehearsal (§4e) processes a job end-to-end in fake mode on a CPU instance: boot, S3 syncs, service start, status updates, result in `results/`.
3. **Real-model check (deferred from M1):** on the first GPU boot, one real job through S3 → SQS → worker on a sample video produces a result that matches that video's entry in `data/samples.json`, within small floating-point differences.
4. The boot log shows timestamps for every UserData step, including the S3 weight-sync duration.
5. `deploy_code.sh` followed by a new launch (or `systemctl restart`) picks up new code without an image rebuild.
6. Two identical SQS messages for one `job_id` (manual re-send) produce exactly one result object and one `done` status; the second delivery either skips (result exists) or loses the conditional write.
7. The ASG scales 0 → 1 (and 0 → 2 during Experiment 2) as the queue fills, and back to 0 when drained, visible in CloudWatch.
8. Killing the worker process mid-job (`kill -9`) makes the job visible for redelivery within about 120 s, and a second attempt completes it.
9. Terminating a Spot instance mid-job gets the job picked up by another instance via proactive release (visibility explicitly set to 0), not a passive timeout.
10. The frontend shows real, changing status text and the real error message on failure.
11. A job forced to fail twice lands in the dead-letter queue.
12. A fresh instance, with no `config.json` in the image, writes `config.json` and `env.conf` (mode 600) through UserData and starts `neurolens-worker` without manual steps; the service is disabled in the image and starts only after both files are verified.
13. A crash after the result write but before the `done` status still reports `done` from `GET /api/jobs/{job_id}/status`, and a duplicate delivery does not reprocess.
14. Workers have no public IP and no inbound rules, yet reach SQS and Parameter Store through the NAT instance and S3 through the gateway endpoint.
15. `stop_work.sh` leaves zero GPU instances running and the NAT instance stopped; `start_work.sh` brings the system back and a new job completes.
16. `terraform apply` run twice reports no changes the second time.
17. `FAKE_INFERENCE=1 pytest` passes, including the §10a tests.
18. The cold-start comparison (S3 copy vs baked-in weights) is recorded under `experiments/experiment-2/`, and the comparison image and its snapshot are deleted afterwards.

## 13. Between sessions and teardown
- End every working session with `stop_work.sh`.
- Keep the software-only image, `models/`, and `code/latest.zip`; M3 and M4 reuse them.
- Final teardown (`terraform destroy`, deregistering images, deleting snapshots) happens only after M4's consolidation.

## 14. Explicitly not in this milestone
No Aurora, no user accounts or sign-in, no credits, billing or refunds, no web-tier load balancer, no containers.
