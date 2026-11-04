# NeuroLens — M2b Implementation Spec
Only the parts of this milestone still in use are described here; the rest is in later specs (see docs/README.md).
**Milestone:** Autoscaling, reliability, job status, first-job warm-up, 4K downscale test, and Experiments 1–2 (second part of 9 – 19 Oct)

**Ground rules for all of M2b:** the region is M2a's single Terraform setting (`us-east-1`). Python 3.12. All infrastructure is Terraform in `infra/terraform/`, tagged `Project=neurolens`. Run AWS commands with the `neurolens` CLI profile only. **Tests first, as in M0:** a separate agent writes the tests before the implementation, and they are not edited by the implementer.

## 2. Warm hold and demo routine

### 2d. Warm hold (demos and study sessions)
- `start_work.sh --keep-warm [--hours N]` (N from 1 to 4, default 3): **first** creates or replaces the one-off scheduled action `neurolens-keep-warm-end` at now + N hours, which sets the endpoint's scaling **minimum to 0** and its maximum to 1; then sets min 1, max 1. If creating the scheduled action fails, it stops with an error and starts nothing. Re-running it during a hold moves the end to N hours from now. The 4-hour cap bounds the cost of a forgotten hold (about $7.50), since a hold deliberately turns off every automatic stop.
- While the minimum is 1, AWS keeps the worker: scale-in cannot go below the minimum. When the hold ends, the minimum is 0 again and the normal rules apply: an idle worker is removed after 7 idle minutes. A job in progress is never cut.
- `start_work.sh` without a keep option sets min 0 / max 1 only; it never changes the keep-warm timer, so it cannot end a running hold.
- `start_work.sh` does not check alarms: the GPU guard is the backstop (M3e §6c).
- `stop_work.sh` deletes `neurolens-keep-warm-end` first (absent is fine; any other error is a failed check, NOT CONFIRMED), sets the scalable target to 0 / 0, sets the endpoint's desired instance count to 0 and waits until no instance remains. A running job dies and is refunded.
- A deploy is `infra/build_image.sh` then `terraform apply` with the new image digest (M3e §4d).
- **Manual work on a worker:** the container's logs are in CloudWatch (`/aws/sagemaker/Endpoints/neurolens`).
- The deploy user's Application Auto Scaling permissions cover scheduled actions.

### 2e. Demo routine (also goes into the README)
1. The day before (the week before for the TA demo): one full dry run of steps 2–5.
2. 45–60 minutes before: `start_work.sh --keep-warm --hours 3` (demo length + early start + margin).
3. Wait for the container to be ready (the CloudWatch log, or the result of step 4).
4. One warm-up job: upload a short sample clip, so the first-job costs are paid before the audience arrives and the whole chain is proven that day.
5. Demo. If it overruns the hold, run step 2 again.
6. Straight after: `stop_work.sh`, and wait for ALL STOPPED.
Cost: about 1.5–2 hours of GPU, roughly $3–4.

## 3. Queue changes (Terraform, on M1's queue)
- **Visibility timeout 1080 s**, six times the dispatcher's 180 s timeout: the dispatcher deletes the message once it has invoked the endpoint, so a slow dispatch is not redelivered while it is still invoking. A failed dispatch is retried after the timeout.
- The dead-letter queue (`maxReceiveCount = 2`, M3e §3) is unchanged; Terraform sets it.

## 4. Job status tracking
The job's state (`queued`, `processing`, `done`, `failed`), its current `stage` and its `stages` history live in the `jobs` row in Aurora (M3a §3c, §4a); there are no status objects in S3. A `stages` entry is `{"stage": "transcribing", "at": "2026-10-14T03:22:10Z"}`, and each attempt starts with an empty list.
- The stage names, in order: `transcribing` (`build_events`, where WhisperX runs), `inference_full`, `inference_noaudio`, `extracting_roi`, then the result-writing stages (M3c §3b, M3d §5). The worker sets each one with `billing.set_stage` as the step begins. Experiment 1 reads `stages`.
- Write order: `put_result` first, then settle the job (`done`, M3a §4a) whatever `put_result` returned. A duplicate is still settled, so the finished result is charged once. If `put_result` returned `False`, return `DUPLICATE` after settling: the other worker's result is left unchanged.
- **Failures:** a failure after the claim refunds the job at once (`processing_failed`) and deletes its files; there is no automatic GPU retry (M3e §4c, §5). A job whose container dies is refunded by the SageMaker error handler, with the reaper as backstop (M3e §5). A rejection is final: the job is refunded with a plain reason ("The video is longer than 120 seconds.", "The file is not a readable video.", "The file is larger than the upload limit."). The dead-letter handler refunds a job the dispatcher could not finish (M3a §7). Error text is short and user-facing (at most 200 characters, no traceback); the full traceback goes to the worker log.

## 9. 4K downscale test
A 55 s 4K clip took 4.4 times longer than a 480p clip of similar length (video encoding 18.2 s per second of video, GPU busy 15%, CPU 54% of 4 cores): decoding 4K frames on the CPU limits the job, while the model only looks at a 256 x 256 centre square of each frame (its processor resizes the short side to 292). The pass mark is fixed here, before any run.

**Step A (no code), in an Experiment 1 GPU session (M4 §3):**
- From the exact 4K test file (`bbb_4k_55s_38mbps.mp4`, `data/videos/SOURCES.md`), cut a 4K clip of about 30 s with stream copy (no re-encode). From that cut, make **lossless** H.264 copies at 720p and 480p (`-qp 0`, same aspect ratio) with the **audio stream copied unchanged**, so the resize is the only difference. Choose the length so the largest file is under `max_upload_bytes`, and check the sizes before uploading.
- Upload the 4K cut, the 720p copy and the 480p copy as normal jobs on the same worker, image and code, after the session's warm-up job (no first-job warm-up in any of them).
- **Pass:** every value of a copy's result within **0.001** of the 4K cut's result from the same session (measured noise: 0.0000 on the same GPU, at most 0.0003 across GPUs). Compare the `inference_full` stage times. The smallest resolution that passes is the candidate.
- Limits to state in the report: one animated clip. A live-action 4K clip (Tears of Steel, 6.7 GB source) is added only if Josh agrees to the extra cost.

## 10. Experiments instrumentation
The experiment method is in `docs/M4_spec.md` §3.
