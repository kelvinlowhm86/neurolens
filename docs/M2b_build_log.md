# M2b build log

Measurements, choices and evidence for `docs/M2b_spec.md`. Newest decisions are folded into the
sections, not appended as a diary.

## CPU rehearsal plan (§13 criteria on `t3.large`, fake mode)

**Setup.** Josh pastes the three `infra/iam/*.json` policies, then `infra/deploy_code.sh`, then applies
with `worker_instance_types = ["t3.large"]`, `worker_fake_inference = true` and
`worker_fake_job_seconds = 240` in `terraform.tfvars` (each fake `predict` sleeps 4 minutes, so a job
takes about 8). Afterwards restore the three settings and apply again (criterion 13: a second apply
shows no changes).

**Cost.** `t3.large` $0.083 an hour plus the NAT instance and the 100 GB disk (about $0.02 an hour):
about 5 hours of machine time over two or three sittings, about $0.55. The new alarms add about
$0.50 a month; the breaker's runs are inside the Lambda free tier.

Job uploads use the page (`?pass=1`) with a short clip, so criterion 7 is checked on the way. Before
each step that stops or kills the worker, run `systemctl reset-failed neurolens-worker`: manual
starts count toward the crash limit (3 starts an hour), which would otherwise end the service mid-plan.

| Step | Criterion | What | Pass mark |
|---|---|---|---|
| R0 | 2b, 2f | First worker up: on it, `systemctl is-enabled neurolens-self-terminate` and `aws autoscaling set-desired-capacity` (any group) | `masked`; the Auto Scaling call is AccessDenied (the worker may only protect itself) |
| R1 | 1, 7 | `start_work.sh` (no `--worker`), upload one clip | Group 0 to 1 by the scale-out alarm (scaling activity names the alarm); the worker is protected while the job runs (`describe-auto-scaling-instances`) and unprotected after; the page shows changing step text then the result; reopening the `?job=` link after closing the tab shows the result; group back to 0 about 15 minutes after the queue empties, by the scale-in alarm. Record `get-metric-data` for the scale-in expression across that window |
| R1b | 1 | During a job: run the scale-in policy by hand (`aws autoscaling execute-policy --policy-name neurolens-workers-to-zero`) | Desired goes to 0 but the busy worker keeps running and finishes the job; then it is removed with no command |
| R2 | 2 | Re-send the finished job's S3 event body to the queue by hand | Exactly one result and one `done` status; the worker log says `Skipped` |
| R3 | 4 | During a job: `systemctl stop neurolens-worker` (SSM) | Message visible again within about 10 s (`get-queue-attributes`); machine still running; protection removed; after `systemctl start` the job completes |
| R4 | 3 | During a job: `kill -9` the worker process (SSM) | Message visible again within about 120 s (no heartbeat); systemd restarts the worker; a second attempt completes it |
| R5 | 8, 7 | Set `FAKE_INFERENCE_SECONDS=boom` in the worker's `env.conf`, restart it, upload a clip | Status `retrying` after the first attempt, `failed` with the error after the second; the page shows both; the message is in the dead-letter queue. Restore `env.conf` and restart |
| R6 | 1 | `start_work.sh --max 2`, upload two clips at once | Two workers launch together (second step); afterwards `start_work.sh` (max 1) is refused while two run |
| R7 | 5 | `start_work.sh --worker --hours 1`, no uploads | `--hours 5` refused; a plain `start_work.sh` during the hold leaves min 1; after 45 minutes `systemctl is-active` is active with recent poll lines; after the hold ends the worker is removed with no command (record the time from hold end to termination); then `stop_work.sh` leaves min 0 and no `neurolens-warm-hold-end` |
| R8 | 6 | `start_work.sh --worker --hours 1`; on the worker a drop-in replaces `ExecStart` with `sleep 240; exit 1` (as M2a criterion 19); upload a clip | Crash limit reached, the service stays failed and the machine stays up; the hold ends at 60 minutes; the idle alarm fires at about 90-100 minutes; within 5 minutes the breaker logs "set to 0/0/0"; the worker ends; watching 15 more minutes, no worker launches while the job waits; `start_work.sh` prints the breaker note and sets max 1 |
| R9 | 6 | During R7's hold: invoke the breaker by hand (`aws lambda invoke`) | Group unchanged; log says "warm hold" (or "not in ALARM") |
| R10 | 13 | `get-metric-data` on the quiet queue; `aws events describe-rule` for the schedule; the breaker's `Errors` metric; `terraform plan` after restoring the settings | Scale-in reads missing data as an empty queue; the schedule is ENABLED; no breaker errors over the rehearsal; the second apply has no changes |

Boot records: each rehearsal worker runs in fake mode, so it writes none (§1a); the first GPU worker
gives the real boot log that replaces `tests/fixtures/neurolens-boot-log.txt`.
