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

## Worker instance type: `g6e.xlarge`

Peak RAM was 14.8 GB. That leaves about 17 GB free on the `xlarge`'s 32 GB, well above the spec's
~4 GB rule (§4b). Peak VRAM was 18.6 GB of 46 GB.
