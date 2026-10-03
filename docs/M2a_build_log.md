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
