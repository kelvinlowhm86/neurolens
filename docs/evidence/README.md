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
3. **One GPU:** tribev2 runs on a single GPU, so more GPUs cost more without speeding a job up.
4. **Smallest size with at least 32 GB RAM** (peak 13.7 GB). Larger sizes have the same GPU; the GPU was about 90% busy during video encoding, so extra CPU and RAM would not help.

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

## Test
On each tested type, after the timeline and double-encoding fixes: the 52 s Sintel trailer first (it carries the cold start), then the 119 s loop (warm rate and worst-case memory, since 120 s is the upload limit). Recorded per type: GPU memory peak, job times, cost per second of video, and wait for a 120 s video. Rule: a cheaper type wins only if it fits in memory at 119 s and its wait stays acceptable.

## Results
(to be added after the run)
