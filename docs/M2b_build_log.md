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

## CPU rehearsal results (2026-10-05, `t3.large`, fake job 2 x 240 s)

**Found before any step passed (fixed):**
- The web app had no AWS credentials when started as `python app.py` (the README's command): the
  page said "Could not prepare the upload". Fix `b1c7132`: `AWS_PROFILE=neurolens` in `.env`
  (and `.env.example`), and the web app now refuses to start without credentials, naming the fix.
- The first worker's boot stopped at `systemctl mask neurolens-self-terminate.service`: the image
  installed that unit file in `/etc/systemd/system`, exactly where `mask` puts its `/dev/null`
  link, so `mask` refused. UserData's error trap only logged, as designed, and the job waited.
  Fix `77e5822` (launch template updated): remove the file, then mask (tried on that worker first). The
  broken worker was replaced with `terminate-instance-in-auto-scaling-group
  --no-should-decrement-desired-capacity`; the waiting job then ran on the replacement.

| Step | Result |
|---|---|
| R0 | Pass. Boot 27 s (no weight sync in fake mode); `neurolens-self-terminate` masked; the worker's `autoscaling:SetDesiredCapacity` is AccessDenied; the worker read max receives 2 from the redrive policy |
| R1 (scale out, protection, page) | Upload at about 16:50 UTC after a quiet night; scale-out alarm OK to ALARM at 16:52:47, desired 1 at 16:52:55 (8 s). Replacement worker launched 17:05:52, took the job 17:08:04 and was protected from scale-in from then until 17:17:43, 3 s after the `done` status (17:17:40). Status had all five stages in order. Page (Josh): step text changed, charts drawn, reopening the `?job=` link after closing the tab showed the result. Scale-in: alarm to ALARM at 17:34:25 (16 min 45 s after the job ended: 15 empty minutes plus the metric delay), desired 0 at 17:34:28, group empty 17:35:46; no worker ended mid-job |
| R1b (forced scale-in during a job) | Pass. Worker took the job 17:42:07 and was protected; the scale-in policy run by hand at 17:42:00 set desired 0 and AWS reported "group reached equilibrium" without ending the protected worker. The job ran once to the end (568.7 s, no hand-back; a one-reading blip of the queue counts at 17:42:15 was SQS's approximate count, the log shows a single receive) and protection came off at 17:51:47. Side effect: at 17:42:53 the scale-out alarm, still in ALARM because SQS metrics lag, set desired back to 1 (no new machine: the worker existed), so this worker was later removed by the normal scale-in. Harmless; the scale-out alarm repeating while it is in ALARM is expected AWS behaviour |
| R2 (duplicate) | Pass. During a 2-hour warm hold, the first job's S3 event re-sent by hand at 18:08: worker logged `Skipped ...: a result already exists`, message deleted, exactly one result object (same LastModified and ETag as before), status still `done` from 17:17:40 |
| R3 (stop mid-job) | Pass. `systemctl stop` at 18:11:35.709 during the first fake predict; the worker logged "releasing its message" 6 ms later and "stopped polling" at 0.13 s; the queue showed the job waiting at the next reading; the machine kept running with protection removed; the masked self-terminate unit was not started ("Unit ... is masked"). After `systemctl start` the job ran again (second, final attempt) from 18:13:00 and finished at 18:21:09 (`done`, one result). The status keeps both attempts' stages. Python's own exit took 16 s after "stopped polling" (process deactivated 18:11:51.6): harmless (job already released; TimeoutStopSec 110), noted for review B |
| R4 (kill -9 mid-job) | Pass. Job started 18:22:14; `kill -9` at 18:23:39 (`code=killed, status=9/KILL`); systemd restarted the worker (restart scheduled 18:23:45, ready 18:23:50); the message became visible when its 120 s timeout ran out and the restarted worker took it at 18:25:04 (85 s after the kill; the queue never showed it waiting because it was taken at once); done 18:33:13, one result |
| R5 (dead-letter) | Pass. `FAKE_INFERENCE_SECONDS=boom` in `env.conf`: attempt 1 of 2 failed at 18:35:39.49 (status `processing`/`retrying` with the error), the message was released and taken again 0.5 s later, attempt 2 of 2 failed at 18:35:40.13 (status `failed`, error "could not convert string to float: 'boom'"), then SQS moved it to the dead-letter queue (job queue 0, DLQ 1); no result. The `retrying` state lasted about 0.6 s, too short for the page's 5 s polling to show (its write is unit-tested). Setting restored |

**Observation for §8 (first-job warm-up):** on the fresh worker, 86 s passed between "Analysing" and
the `transcribing` stage, for a 0.3 MB file. Most likely the first `import torch` (in
`reset_gpu_peak`) reading a cold disk restored from the image snapshot (blocks load from S3 on first
read). It is counted in the `downloading` stage. Experiment 1 runs on a worker that has done a
warm-up job, so its numbers are not affected; the stage attribution is noted for review B, and the
cold-disk cost goes into the §8 diagnosis.
