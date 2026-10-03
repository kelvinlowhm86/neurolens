# Evidence: Spot versus on-demand for the GPU workers

Raw data behind the choice of on-demand `g6e` workers (M2a spec §4b). Saved on 2026-10-03, because AWS keeps Spot price history for only 90 days and Auto Scaling activity for only 6 weeks.

## `spot_price_history_g6e_us-east-1.tsv`
Every Spot price change for `g6e.xlarge` and `g6e.2xlarge` (Linux) in us-east-1a-d, 2026-07-05 to 2026-10-03. The price is in USD per hour, and each row holds until the next row for the same type and zone.

```
aws ec2 describe-spot-price-history --instance-types g6e.xlarge g6e.2xlarge \
  --product-descriptions "Linux/UNIX" --start-time 2026-07-01T00:00:00Z --end-time 2026-10-03T06:30:00Z \
  --query 'SpotPriceHistory[].[Timestamp,InstanceType,AvailabilityZone,SpotPrice]' --output text | sort
```

On-demand prices for comparison (us-east-1, Linux): `g6e.xlarge` $1.861 an hour, `g6e.2xlarge` $2.242.

Time-weighted monthly averages, across the four zones:

| Month | `g6e.xlarge` | below on-demand | `g6e.2xlarge` | below on-demand |
|---|---|---|---|---|
| 2026-07 (from the 5th) | $1.803 | 3% | $2.156 | 4% |
| 2026-08 | $1.725 | 7% | $2.091 | 7% |
| 2026-09 | $1.702 | 9% | $2.175 | 3% |
| 2026-10 (1st-3rd) | $1.836 | 1% | $2.212 | 1% |

Lowest price seen: $1.349 (`xlarge`) and $1.786 (`2xlarge`), each for short spells only.

## `worker_launch_attempts_us-east-1.tsv`
Every launch the `neurolens-workers` Auto Scaling group recorded, with time (UTC), result, zone and AWS's message.
- The 4 successful launches on 2026-10-02 are the `t3.large` CPU wiring rehearsal (Spot).
- Every `g6e.xlarge` Spot launch failed for lack of capacity: 11 attempts on 2026-10-02 22:13-22:22 UTC and 15 on 2026-10-03 05:59-06:16 UTC (13:59-14:16 in Singapore, about 2 a.m. in us-east-1). Each round of attempts covered all four zones.

```
aws autoscaling describe-scaling-activities --auto-scaling-group-name neurolens-workers \
  --query 'Activities[].[StartTime,StatusCode,Details,StatusMessage]' --output json
```
The zone comes from the `Details` field. The group's ARN, which contains the account ID, was left out.

## Conclusion
Spot saved 1-9% a month and was often unavailable. Spot capacity is AWS's unused on-demand capacity, so for a GPU in this much demand there is little spare, which keeps both the discount and the availability low. On-demand is never taken back mid-job and costs about the same. If on-demand is sold out as well, Spot is too, so the group has no Spot fallback: it tries `g6e.xlarge`, then `g6e.2xlarge`, and keeps retrying.

# Evidence: choice of GPU

## Candidates
Every GPU instance type in us-east-1 (AWS price list and `aws ec2 describe-instance-types`, 2026-10-03), filtered by four requirements:

1. **GPU memory of at least about 22 GB usable.** The real pipeline peaked at 19.8 GB of GPU memory on a 119 s clip (first GPU run, 2026-10-03, `nvidia-smi` total including WhisperX).
2. **Runs our software:** x86 processor and a GPU that PyTorch below 2.7 supports (tribev2 requires `torch<2.7`).
3. **One GPU:** tribev2 runs on a single GPU. A multi-GPU machine could run several jobs side by side, but per GPU it costs more than the single-GPU types (for example 4 x L4 in `g6.12xlarge` is about $1.15 per GPU-hour against $0.98 for `g6.2xlarge`; 4 x A10G about $1.42 against $1.21).
4. **Smallest size with at least 32 GB RAM** (peak 13.7 GB). Larger sizes have the same GPU; the GPU was about 90% busy during video encoding, so extra CPU and RAM would not help. This is a judgment (keep about 4 GB free; running out of RAM kills the job), not a measurement: the 16 GB `xlarge` sizes would leave about 2 GB.

| Type | GPU | GPU memory usable | RAM | On-demand $/h | Result |
|---|---|---|---|---|---|
| g4dn.2xlarge | T4 | 16 GB | 32 GB | 0.752 | Out: GPU memory (1) |
| g6f.* | part of an L4 | up to 11 GB | | from 0.202 | Out: GPU memory (1) |
| g5g.* | T4G, ARM | 16 GB | | from 0.420 | Out: GPU memory (1), ARM (2) |
| g7e.2xlarge | RTX PRO 6000 (Blackwell) | 96 GB | 64 GB | 3.363 | Out: needs PyTorch 2.7 or newer (2) |
| p4d, p5 and multi-GPU g sizes | A100, H100, ... | | | 4.6 and up | Out: several GPUs (3) |
| g6.xlarge, g5.xlarge | L4, A10G | 22.9 GB | 16 GB | 0.805, 1.006 | Out: RAM (4) |
| gr6.4xlarge | L4 | 22.9 GB | 128 GB | 1.539 | Out: same GPU as g6.2xlarge, dearer (4) |
| **g6.2xlarge** | **L4** | 22.9 GB | 32 GB | **0.978** | **Tested** |
| **g5.2xlarge** | **A10G** | 22.9 GB | 32 GB | **1.212** | **Tested** |
| **g6e.xlarge** | **L40S** | 45.8 GB | 32 GB | **1.861** | **Tested (current choice)** |

Scope: AWS only; other GPU clouds were not considered.

## Not tested: a faster GPU
The only faster single GPU on AWS that runs PyTorch below 2.7 is the H100 (`p5.4xlarge`, $6.88/h on demand in us-east-1), 3.7 times the price of `g6e.xlarge`, so it would have to finish a job 3.7 times faster to cost the same per job. The pipeline is expected to run at full 32-bit precision: tribev2 loads the video and text models without a reduced-precision setting, and we deliberately add none, to keep the features the brain model was trained on (32-bit: see Model precision). NVIDIA's published figures:

| | L40S (`g6e`) | H100 SXM (`p5`, assumed to be the SXM version) | H100 / L40S |
|---|---|---|---|
| FP32 compute | 91.6 TFLOPS | 67 TFLOPS | 0.73 |
| TF32 tensor compute (dense) | 183 TFLOPS | about 495 TFLOPS (half the published 989 "with sparsity") | 2.7 |
| BF16 tensor compute (dense) | 362 TFLOPS | 989 TFLOPS | 2.7 |
| Memory bandwidth | 864 GB/s | 3,350 GB/s | 3.9 |

At 32-bit the H100 has less raw compute; only memory bandwidth is far higher, and that helps only the parts of a job limited by moving data. A 3.7-fold overall speed-up is therefore very unlikely, so it was not tested: this exclusion is an argument from published specifications, not a measurement. Sources: NVIDIA L40S and H100 datasheets (for example the vendor copies at supermicro.com/datasheet/datasheet_NVIDIA_L40S_Systems.pdf and cisco.com's nvidia-h100-80-gpu.pdf).

## Test
On each tested type, after the timeline and double-encoding fixes: the 52 s Sintel trailer first (it carries the cold start), then the 119 s loop (warm rate and worst-case memory, since 120 s is the upload limit). Recorded per type: GPU memory peak, job times, cost per second of video, and wait for a 120 s video. Rule: a cheaper type wins only if it fits in memory at 119 s and its wait stays acceptable. One run per clip per type; if the two best types are within 20% of each other on cost per second of video, the close ones are re-run before deciding, so run-to-run variation cannot decide the choice. Also recorded: the `transformers` version on the worker (see Model precision).

## Model precision
All three feature models run at 32-bit precision, read from the pinned code rather than measured. TRIBE v2's `config.yaml` (HuggingFace `facebook/tribev2`) sets `device: cuda` for the text (`meta-llama/Llama-3.2-3B`), audio (`facebook/w2v-bert-2.0`) and video (`facebook/vjepa2-vitg-fpc64-256`) extractors. In neuralset 0.0.2, half precision (`torch_dtype=float16`) is used only when `device` is `accelerate` (text) or for LLaVA video models; the audio extractor asks for `float32` explicitly; the others load with `transformers`' default, which is 32-bit in the 4.x versions `requirements/model.txt` allows (`<5`). Consistent with the first real jobs: the peak of 19.8 GB of GPU memory came with the text model, whose ~3.2 billion parameters take about 13 GB at 32 bits (about 6.4 GB at 16). Reduced precision was not tried: the brain model was trained on features made this way.

## Results (2026-10-03, image `neurolens-worker-v2`, code `b1fb933`)
One run per clip per type, in us-east-1. "Warm" leaves out the one-time WhisperX environment set-up of a worker's first job (5.7-6 min on every type; see `docs/M2a_build_log.md`), which was measured directly where the job ran first and subtracted otherwise.

| | `g6e.xlarge` (L40S) | `g5.2xlarge` (A10G) | `g6.2xlarge` (L4) |
|---|---|---|---|
| On-demand $/h | 1.861 | 1.212 | 0.978 |
| 52 s trailer, warm job | about 259 s, **$0.134** | 521 s, **$0.175** | 655 s, **$0.178** |
| 119 s loop, warm job | 491 s, **$0.254** | about 1,184 s, **$0.399** | about 1,540 s, **$0.418** |
| Video encoding, 52 s / 119 s | 211 s / 453 s | 492 s / 1,121 s (2.3x / 2.5x) | 636 s / 1,457 s (3.0x / 3.2x) |
| Cost per second of video (119 s) | $0.0021 | $0.0034 | $0.0035 |
| Peak GPU memory (`gpu` field) | 19.94 GB of 45.8 | 19.94 of 22.9 | 19.94 of 22.9 |
| Results vs `g6e` | reference | within 0.0003 | within 0.0001 |
| `g6e.2xlarge` (fallback, same L40S, $2.24/h) | 119 s loop 485 s (code `b1fb933`, image v1): same speed, about $0.30 a job | | |
| First job of a worker (119 s) | | 1,528 s | 1,880 s (over the 1,800 s queue timeout) |

**Decision: `g6e.xlarge` stays** (then `g6e.2xlarge` when sold out). The two cheaper GPUs cost 1.3-1.6 times more per video and make people wait 2.3-3.2 times longer. The best cheaper type (A10G) is 31-57% dearer per video than `g6e`, outside the 20% re-run band, so no re-run was needed.

**What limits the job:** video encoding on the A10G (same 32-bit compute as the L4, twice its memory bandwidth) was 2.4 times slower than on the L40S, close to the compute ratio (2.9) and far from the bandwidth ratio (1.4), so encoding is mainly limited by compute. The L4 alone could not show this: it trails the L40S by about 3 times on both.

**As fallbacks when `g6e` is sold out:** both fit and give the same results, but they are not used (Josh, 2026-10-04): slower and dearer per video; a region move is the fallback instead (M4 spec §1). The L4's first 119 s job also took longer than the queue's 1,800 s visibility timeout. The fallback stays `g6e.2xlarge`: the same GPU, the same speed, about 20% more per job.
