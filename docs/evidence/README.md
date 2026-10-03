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
