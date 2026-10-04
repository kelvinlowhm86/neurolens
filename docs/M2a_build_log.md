# M2a build log

Measurements and choices from building the GPU worker image (spec §2). Identifiers and numbers
only: no account IDs, no secrets.

## Image `neurolens-worker-v1` (2026-10-03)

| | |
|---|---|
| AMI | `ami-02401490feb3d139f` (us-east-1) |
| Snapshot | `snap-02245e042fc8dc175`: 75 GB volume, 78.5 GB of data stored (about $3.90 a month) |
| Base image | Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04) 20260929 |
| Code | `code/latest.zip` from commit `203ecee` |
| Built on | `g6e.2xlarge`, us-east-1c, on-demand. `g6e.xlarge` was sold out in all four zones, and `g6e.2xlarge` in three. The image runs on either size. |
| GPU, driver | NVIDIA L40S (46,068 MiB); driver 595.91.07, CUDA 13.2. torch 2.6.0+cu124 sees the GPU. |
| Python | 3.12.15 (uv), venv `/opt/neurolens/venv` |
| Cost | about $1.00: 24 GPU-minutes for this build, plus a failed attempt (about 10 cents) |

## Pipeline on the smoke clip (Sintel trailer, 52 s, with speech)

| | First run (with download) | Offline proof (fresh process, no token) |
|---|---|---|
| Wall time, model load + both passes | 9 min 27 s, including the ~20 GB download | 7 min 17 s |
| Output shape (with audio, without) | (53, 20484), (53, 20484) | (53, 20484), (53, 20484) |
| Peak VRAM | 18.6 GB | 11.2 GB |
| Peak system RAM | 14.8 GB | not sampled |

- **Text features ran.** WhisperX transcribed the speech (no `whisperx failed` line in the with-audio
  pass), and the Llama 3.2 3B weights (6.0 GB) were downloaded.
- **Weights:** 17 GB of HuggingFace models in the cache, plus 184 KB of atlas data, synced to
  `s3://<bucket>/models/`. That sync followed HuggingFace's snapshot links, so S3 holds every model
  file twice (`snapshots/` and `blobs/`, 16.7 GB each). Workers skip `blobs/`. Delete it from S3 once
  a real job has worked without it (chunk 7).
- **Kept in the image, outside the cache:** `/root/.cache/uv` (7.3 GB; WhisperX runs through uv, so
  workers need it, and the worker unit sets `HOME=/root` for that reason), `/root/.cache/pip`
  (408 MB), and two small library caches.

## Wiring rehearsal (2026-10-03, `t3.large` Spot, fake model)

| Step | Result |
|---|---|
| First worker | Ran out of disk: the image's software uses 66 of its 75 GB root disk, and a CPU machine has no instance-store disk for the weights. The boot script's error trap self-terminated it, and the group went from 1 to 0 with no replacement. Fix: 100 GB worker root disk. |
| Boot (second worker) | Reached Session Manager through the NAT in about 20 s. Weight sync of about 18 GB onto the root disk took 538 s, then code `203ecee` pulled and settings verified. |
| Worker start | About 2 min from service start to "Worker ready" (library and atlas loading from a freshly restored disk) |
| Job | `uploads/` → S3 event → queue → worker in a private subnet → `results/` in S3 (`fake_inference: true`); message deleted |
| Deploy | `deploy_code.sh` then `restart_workers.sh`: the worker ran `ab6ce4d`. The self-terminate guard logged "worker is active again (a restart); not terminating". |
| Clean stop | `systemctl stop neurolens-worker` led to self-termination about 17 s later, and the group shrank from 1 to 0. |
| `stop_work.sh` | ALL STOPPED |

Not covered: a `start_work.sh --worker` after a full stop. Chunk 7's GPU run starts that way.
These timings are from a small CPU machine. Measure them again on the GPU worker (local NVMe
disk, faster network).

## Worker instance type: `g6e.xlarge`

Peak RAM was 14.8 GB. That leaves about 17 GB free on the `xlarge`'s 32 GB, well above the spec's
~4 GB rule (§4b). Peak VRAM was 18.6 GB of 46 GB.

## First real GPU jobs (2026-10-03, on-demand `g6e.xlarge`, code `ab6ce4d`, before §7a)

| | |
|---|---|
| Launch | `start_work.sh --worker` after a full stop (this covers the spec's "start after a full stop" check). Spot `g6e.xlarge` had been sold out in all four zones twice (`docs/evidence/`); after the switch to on-demand, three zones were sold out and the fourth (1c) launched within about 30 s. |
| Boot | Weight sync 77 s (local NVMe disk; 538 s on the CPU rehearsal); worker service started 2.5 min after launch; model load 3 min 52 s; "Worker ready" 6.5 min after launch. |
| tribev2 | Commit `af58661` (now pinned in `requirements/model.txt`), neuralset 0.0.2. |

| | Job 1: Sintel trailer, 52.2 s | Job 2: trailer looped, 119.01 s |
|---|---|---|
| Job time | 1003.5 s | 1039.1 s |
| First-job warm-up | about 8 min (WhisperX 6 min 16 s, then a 2 min gap); not repeated in job 2 | none (WhisperX 14 s) |
| Video encoding | 104 steps at about 2.07 s, done twice (with and without audio) | 238 steps at about 2.07 s, done twice |
| GPU busy while encoding | about 90% (limited by the GPU, not by video decoding) | similar |
| Peak GPU memory, all processes (`nvidia-smi`) | 13.5 GB (video encoding) | 19.8 GB (text model) |
| Peak system RAM | 5.5 GB | 13.7 GB |
| Rows returned | 53 (correct) | 239 (wrong: should be 119) |

- **Both jobs ran past the 900 s visibility timeout.** With one worker nothing was handed out twice (queue and dead-letter queue empty afterwards); with two it would have been. Timeout raised to 1800 s.
- **239 rows for a 119 s video:** tribev2's word step gives every audio chunk (chunks start every 60 s) a copy of the whole transcript shifted by `start + offset`, so every word reappears 120 s too late and the timeline stretches to 239 s. Same bug as upstream pull request #29 (open, unmerged); its `start - offset` change would still leave every word duplicated, and text features are summed. The training config extracts words before chunking, so training was unaffected. Fixed in our code (spec §7a), with a loud timeline check.
- **Video encoded twice:** the no-audio pass ran on a separate audio-free file, so tribev2's feature cache missed. Fixed in §7a: the no-audio pass reuses the same video events.
- **How tribev2 reads video:** 64 frames per half-second step (from the previous 4 s), read a few at a time, so memory does not grow with resolution. The video model resizes frames to 292 px and analyses the centre 256 x 256.
- Results kept in S3 for comparison after §7a: `results/154a2ebc-8db8-444d-b5ab-3d74cff22c4c.json` (job 1), `results/783d91f9-e262-445a-9e5e-d5b86275a800.json` (job 2).
- Session cost about $1.50 (GPU 07:47-08:33 UTC plus NAT).

## §7a verified on the GPU (2026-10-03, image `neurolens-worker-v1`, code `b1fb933`)

`g6e.xlarge` was sold out; the group launched a `g6e.2xlarge` (same L40S GPU) in us-east-1d within
10 s. Worker ready 6 min after launch (weight sync 55 s, model load 3 min 19 s).

| | Job 1: Sintel trailer, 52.2 s | Job 2: trailer looped, 119.01 s |
|---|---|---|
| Job time (before §7a) | 736 s (1003 s) | 485 s (1039 s) |
| WhisperX | 8 min 3 s (first job after boot) | 14 s |
| Video encoding, with-audio pass | 3 min 31 s | 7 min 33 s (two 60 s / 59 s video chunks) |
| No-audio pass | about 1 s: reuses the cached video features (encoded once) | about 1 s |
| Rows | 53 | 120 (one per second started; the spec's earlier "119" was a wrong expectation) |
| Peak GPU memory (`gpu` field) | 11.16 GB | 19.94 GB |
| Check | every value within 0.0000 of the first real run (`results/154a2ebc...`) | timeline check passed: 0..119 with no gap or repeat (before §7a: 239 rows) |

- The no-audio pass on `without_audio(events)` gives the same numbers as the old audio-free file, and
  costs about 1 s instead of a second full encoding.
- **First-job warm-up is the WhisperX step, not the GPU:** tribev2 runs WhisperX through `uvx`, which on
  the first call after boot rebuilt its tool environment (6,904 files, 119 MB written to the uv cache
  after boot, part of it fetched through the NAT instance). Later calls take 14 s. To look at before the
  demo; not changed here.
- Session cost about $1.30 (`g6e.2xlarge` 13:14-13:46 UTC plus NAT).

## Image `neurolens-worker-v2` (2026-10-03, plain Ubuntu)

| | |
|---|---|
| AMI | `ami-05dbbbe71905d75e8` (`neurolens-worker-v2`), snapshot `snap-053d98217186573ee` |
| Base | Canonical Ubuntu 22.04 (`ubuntu-jammy-22.04-amd64-server-20261001`), kernel 6.8.0-1066-aws |
| Driver | packages `*-570-server` installed; Ubuntu now maps them to driver **580.178.04** (nvidia-smi: CUDA 13.0), which runs torch 2.6's CUDA 12.4 |
| Size | 50 GB volume, **22.9 GB** stored (v1: 78.5 GB): about $1.15 a month instead of $3.90 |
| Build | `g6e.xlarge` in us-east-1b (1a sold out), 36 min, about $1.10; reboot 37 s; 4-hour shutdown re-armed; CPU rehearsal on `t3.large` passed first (6 min) |
| Software | python 3.12.15, torch 2.6.0+cu124, transformers 4.57.6; `/root/.cache/uv` 7.3 GB (WhisperX), pip cache 383 MB |

**The build's pipeline check proved less than intended this time:** 80 s and 0.77 GB peak GPU memory
(a worker run of the same clip: 12 min, 11 GB). The S3 `models/` folder also held the v1 build's
per-video feature caches (audio, text and video extractors), and the build reuses the same clip file
names, so the cached features stood in for the encoders. The v2 worker check (a fresh upload, new
file name) is therefore the real proof that the encoders run on this image. Fix: feature caches out
of `models/`, and a unique clip name per build.

**v2 verified on a worker** (2026-10-03, `g6e.xlarge` in us-east-1b, fresh uploads, code `b1fb933`):
ready 6.2 min after launch (weight sync 85 s). Both results match the v1 runs: 52 s trailer max
difference 0.0000 (53 rows, 585.8 s, peak GPU memory 11.16 GB), 119.01 s loop max difference 0.0001
(120 rows, 490.9 s, 19.94 GB). The video, audio and text encoders ran on the new image and driver
(13.5 GB in use during video encoding). First-job WhisperX: 5 min 41 s (v1 image: 8 min 3 s); second
job 14 s. Session about $0.90.

v1 (`ami-02401490feb3d139f`) deregistered and its snapshot deleted on 2026-10-03 after v2 passed; v2 is the only worker image.

Stale features removed from S3 (2026-10-04): the three `models/models/neuralset.extractors.*` folders
(22 objects, 72 MB) deleted after the build fix (`3a7d14a`); `models/` now holds weights only
(`hub/`, `torch/`, `data/nilearn/`): 56 objects, 16.72 GB.

## 4K check (2026-10-04, `g6e.xlarge` us-east-1b, image v2, code `b1fb933`)

Clip: Big Buck Bunny, 3840x2160 30 fps, 55 s, H.264 at 38 Mbit/s, 263 MB (`data/videos/SOURCES.md`).
The worker group had found no `g6e` in any zone for about 40 minutes first (17:32-18:11 UTC).

| | 4K, 55 s | 480p trailer, 52 s (same GPU) |
|---|---|---|
| Result | 55 rows, all checks passed | 53 rows |
| Video encoding | 1,000 s (18.2 s per second of video) | about 215 s (4.1 s per second of video) |
| Whole job | 1,427 s (about 1,080 s without the first-job warm-up) | about 259 s warm |
| GPU busy during video encoding | 15% on average (busy in 29 of 192 samples) | about 90% |
| CPU during video encoding | 54% of 4 cores on average | |
| Peak RAM / GPU memory | 14.6 GB of 32 / 19.07 GB | 13.7 GB (119 s clip) / 19.94 GB |

- **4K works and memory holds** (frames are read a few at a time, as expected from the code).
- **But 4K is about 4.4 times slower,** and the GPU mostly waits: the job becomes limited by decoding the
  4K frames on the CPU, not by the model. The model only looks at a 256 x 256 centre square of each
  frame, so a downscale before the model (for example to 720p with ffmpeg) would likely remove most of
  this cost; whether it changes the results measurably has to be checked first. Decision deferred
  (M2b, with the warm-up fix): a 1-minute 4K ad currently costs about $0.55 and takes about 20 minutes warm.
- Session cost about $1.10.

## Milestone review fixes (2026-10-04)

An independent review of M2a found that a slowly crashing worker was never shut down (systemd's
3-in-10-minutes limit resets before a ~4-minute crash cycle fills it), that self-termination tried
once with only an email behind it, and that `stop_work.sh` could stop nothing (no Terraform) or skip
a group still waiting for a GPU. Fixes in spec §3, §4b-§4g, §12; code `ad25cb3`, `c55e9d1`, `48e0425`,
`395387d`. A second review of the spec and a third of the code each found and fixed further gaps.

**Idle alarm, measured before relying on it** (read-only `get-metric-data`):
- In 5-minute periods where the queue published **no data points at all** (it stops after about
  6 quiet hours), `FILL(recv, 0)` still returned a full series and the alarm expression evaluated
  to 1 in all 24 periods of a 2-hour window: the alarm fires in that state.
- Empty long polls are not counted as messages received: in the 2026-10-03 session, half-hour periods
  with a worker running show `NumberOfMessagesReceived` 0 alongside `NumberOfEmptyReceives` 5. A worker
  that polls an empty queue reads as idle.
- After the apply the alarm moved to OK with all values 0 (no worker), and the email arrived.

**`stop_work.sh`, live:**
- Before the deploy policy had `cloudwatch:EnableAlarmActions`: NOT CONFIRMED with the AccessDenied
  reason (twice), exit 1. After the policy paste: ALL STOPPED.
- From a fresh `git worktree` (no Terraform setup): the `terraform init` command and "NOT CONFIRMED:
  nothing was checked or stopped", exit 1.
- `start_work.sh --worker && stop_work.sh` (group at desired 1, machine still launching): the first
  version zeroed the group but its wait saw no machine yet, so the late-appearing worker gave NOT
  CONFIRMED. Fix `395387d`: also wait until the group tracks no machine. Re-run: waited through
  "pending" and "shutting-down", then ALL STOPPED (criterion 21).

## Acceptance criteria (spec §10)

| # | Status | Evidence |
|---|---|---|
| 1 | Pass | 8 vCPU on-demand G quota in us-east-1; zones a-d offer `g6e.xlarge` (`zones` in `terraform.tfvars`) |
| 2 | Pass | v1 and v2 image sections above (AMI, snapshot size, peak RAM 14.8 GB / VRAM 18.6 GB, offline re-run); `g6e.xlarge` justified by RAM |
| 3 | Pass | wiring rehearsal above (job end to end on a CPU worker); NAT stop/start then a new job: every later session starts that way |
| 4 | Pass | §7a verified on the GPU (52 s trailer within 0.0000 of the first run) |
| 5 | Pass | boot log timestamps; weight sync 538 s (CPU), 55-85 s (GPU) |
| 6 | Pass | `deploy_code.sh` refused an uncommitted change (2026-10-04); after a deploy, `restart_workers.sh` showed `ab6ce4d` and REVISION matched (rehearsal) |
| 7 | Pass | every worker boot writes both files and starts the service with no manual step; both mode 600 (CPU test 2026-10-04) |
| 8 | Pass | UserData failure (disk-full rehearsal) and clean stop (rehearsal) self-terminated with the group to 0; three crashes: criterion 19. Idle exit: unit test that `run()` returns, which takes the same clean-stop path as the rehearsal's clean stop |
| 9 | Pass | private-subnet workers reached SQS via the NAT and S3 via the endpoint in every session |
| 10 | Pass | sessions above: `stop_work.sh` ALL STOPPED; `start_work.sh --worker` then a completed job |
| 11 | Pass | alarm emails received (subscription confirmed) |
| 12 | Pass | `terraform plan`: "No changes" (2026-10-04) |
| 13 | Pass | 345 tests pass locally and in CI; tests committed before the code (`c1f6bca` before `2d8a47a`, `382a001` before `6954972`) |
| 14 | Pass | redrive on the real queue (2026-10-04): a test message received twice (counts 1, 2) was not returned on the third receive and was in the dead-letter queue, then deleted; the worker never deletes a failed message (unit tests) |
| 15 | Pass | 120 timesteps for 119.01 s, one encoding per job, visibility 1800 s |
| 16 | Pass | v2 from plain Ubuntu, 22.9 GB vs 78.5 GB; v1 deregistered, snapshot deleted |
| 17 | Pass | region refactor: plan with no changes in us-east-1 |
| 18 | Pass | benchmark in `docs/evidence/README.md`; `g6e.xlarge` first |
| 19-23 | Pass | CPU test session below |

## CPU test session (2026-10-04, `t3.large`, fake model, code `48e0425`)

| Check | Result |
|---|---|
| 20, idle alarm | Worker started, NAT instance stopped at once, after more than 6 quiet hours on the queue (no SQS metrics). The worker could reach neither SQS nor Auto Scaling, so its own idle exit and self-termination could not work. At +88 min the alarm went to ALARM; Auto Scaling's log: "a monitor alarm neurolens-worker-idle in state ALARM triggered policy neurolens-workers-to-zero changing the desired capacity from 1 to 0"; worker gone 2 min later; ALARM email received. Cost of the stuck worker about $0.12. |
| 21, `stop_work.sh` | See "Milestone review fixes" above |
| 7 | `env.conf` and `config.json` mode 600; the worker's `config.json` has no `aws` block |
| 22 | On the worker, `aws ssm get-parameter --name /neurolens/hf_token`: AccessDeniedException, explicit deny |
| 23 | Local web app code with the region only in `.env`: presign, upload of the 27 s clip (HTTP 204), result with 28 rows. With `NEUROLENS_AWS_REGION` empty, the web app and the worker stop with an error naming it |
| 19, restarts | Three `restart_workers.sh` in a row (four starts within the hour with the boot start): worker still running; `reset-failed` clears the start counter on a running service |
| 19, crash loop | Start command replaced by `sleep 240; exit 1`: crashes at about 4, 8 and 12 min, the next start refused (`failed`), self-termination at 12.5 min, group 1 to 0 |

Also confirmed on the worker: start limit `1h` / burst 3 from the UserData drop-in, the
`/etc/profile.d` warning file, code `48e0425`. Session cost about $0.30.
