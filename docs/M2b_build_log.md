# M2b build log

Measurements, choices and evidence for `docs/M2b_spec.md`. Newest decisions are folded into the
sections, not appended as a diary.

## CPU rehearsal plan (§13 criteria on `t3.large`, fake mode)

**Setup.** Josh pastes the three `infra/iam/*.json` policies, then `infra/deploy_code.sh`, then applies
with `worker_instance_types = ["t3.large"]`, `worker_fake_inference = true` and
`worker_fake_job_seconds = 240` in `terraform.tfvars` (each fake `predict` sleeps 4 minutes, so a job
takes about 8). Afterwards restore the three settings and apply again (criterion 14: a second apply
shows no changes).

**Cost.** `t3.large` $0.083 an hour plus the NAT instance and the 100 GB disk (about $0.02 an hour):
about 4.5 hours of machine time over two sittings, about $0.50. The three new alarms add about
$0.40 a month; the Lambda and EventBridge rule are free at this volume.

Job uploads use the page (`?pass=1`) with a short clip, so criterion 8 is checked on the way.

| Step | Criterion | What | Pass mark |
|---|---|---|---|
| R1 | 1, 8 | `start_work.sh` (no `--worker`), upload one clip | Group 0 to 1 by the scale-out alarm (scaling activity names the alarm); the page shows changing step text then the result; reopening the `?job=` link after closing the tab shows the result; group back to 0 about 15 minutes after the queue empties, by the scale-in alarm; the worker was never ended mid-job |
| R2 | 2 | Re-send the finished job's S3 event body to the queue by hand | Exactly one result and one `done` status; the worker log says `Skipped` |
| R3 | 5 | During a job: `systemctl stop neurolens-worker` (SSM) | Message visible again within about 10 s (`get-queue-attributes`); machine still running; after `systemctl start` the job completes |
| R4 | 3 | During a job: `kill -9` the worker process (SSM) | Message visible again within about 120 s (no heartbeat); systemd restarts the worker; a second attempt completes it |
| R5 | 9, 8 | Set `FAKE_INFERENCE_SECONDS=boom` in the worker's `env.conf`, restart it, upload a clip | Status `retrying` after the first attempt, `failed` with the error after the second; the page shows both; the message is in the dead-letter queue. Restore `env.conf` and restart |
| R6 | 1 | `start_work.sh --max 2`, upload one clip | Only one worker launches (the 900 s warm-up holds the second) |
| R7 | 6 | `start_work.sh --worker --hours 1`, no uploads | `--hours 5` refused; a plain `start_work.sh` during the hold leaves min 1; after 45 minutes `systemctl is-active` is active with recent poll lines; after the hold ends the worker is removed with no command; then `stop_work.sh` leaves min 0 and no `neurolens-warm-hold-end` |
| R8 | 7 | `start_work.sh`; on the worker a drop-in replaces `ExecStart` with `sleep 240; exit 1` (as M2a criterion 19); upload a clip | Crash limit reached, the service stays failed and the machine stays up; the idle alarm fires within about 90-100 minutes; the breaker Lambda logs "set to 0/0/0"; the worker ends; watching 15 more minutes, no worker launches while the job waits (scale-out alarm in ALARM); `start_work.sh` prints the breaker note and sets max 1 |
| R9 | 7 | Invoke the breaker by hand (`aws lambda invoke`) during a warm hold | Group unchanged; log says "warm hold" |
| R10 | 14 | `get-metric-data` on the quiet queue; `terraform plan` after restoring the settings | Scale-in reads missing data as an empty queue; the second apply has no changes |

Boot records: each rehearsal worker runs in fake mode, so it writes none (§1a); the first GPU worker
gives the real boot log that replaces `tests/fixtures/neurolens-boot-log.txt`.
