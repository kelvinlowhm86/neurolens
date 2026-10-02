# NeuroLens — M4 Implementation Spec
**Milestone:** Usability study, results consolidation and teardown (31 Oct – 13 Nov; the final report and slides are due 13 Nov 23:59)
**Builds on:** M1–M3b: a complete, signed-in, credit-accounting product running **on demand** (M3b §3c), with the page and sample results served from S3 through CloudFront. Experiments 1 and 2 (M2b §8) and Experiment 3 (M3b §6) write artifacts to `experiments/<experiment>/<run_id>/` in S3 following **M2b §8's artifact contract**. M4 is mostly running a study and turning measurements into report material; the engineering is small: a pseudonymised export, a withdrawal script, a consolidation script, and a teardown script.

**Ground rules for all of M4:** region `us-east-1`; `neurolens` CLI profile. **Tests first, as in M0 §5,** for the §9 code. The study design (five participants, the task, SUS, the three open questions: report §5.2) is fixed input: don't redesign it. **No new product features:** usability problems the study finds are reported as future work.

## 1. Scope
### In scope
- Study materials: a consent form, a questionnaire, the moderator script, the task sheet
- Running five moderated sessions with the on-demand system
- A pseudonymised export of the participants' jobs, and a withdrawal script
- A consolidation script that turns Experiments 1–3, the SUS responses and the task sheet into report-ready charts and tables
- Removing the load balancer after the presentation
- A teardown script that deletes everything, run only when Josh decides

### Out of scope
- Any in-app study code (no consent screen, participant flags, event tables or study buttons). Consent and questionnaires live in Google Forms; timing is done by the moderator; the app's own job timings supply the automated part. Study UI would change the interface being measured.
- New features or UI fixes.

## 2. Study materials (`docs/study/`, committed)
### 2a. Consent form (`consent_form.md`), completed **before** the participant signs in
A Google Form with **email collection turned off**, containing:
1. **Participant code** (P1–P5, given by the moderator).
2. The consent text, in plain language:
   - We are testing a prototype cloud service, not you.
   - You will upload a short video ad: your own, or one we give you. Please don't upload footage showing private individuals who haven't agreed to it.
   - You sign in with your Google account. The app's database then stores your email address, the uploaded file's name, and your jobs (times, processing stages and results). The video itself is stored for up to 48 hours and the results for 30 days. The system runs on Amazon Web Services in the United States. Your email is also added to our Google sign-in test-user list.
   - The questionnaire stores your participant code and answers, not your name or email.
   - Only the NeuroLens team sees the raw data. The final report and code repository contain only pseudonymous, aggregated results (for example "P3 scored 82.5"); your written answers are summarised, not quoted with your code.
   - Taking part is voluntary. You can stop at any time, and you can withdraw your data until the final report is submitted on 13 November 2026 by telling the moderator.
   - When we shut the project's cloud system down after the course is graded, your account data is deleted from it, and we delete our local link between your code and your email.
3. A required **"I agree"** checkbox.

The moderator checks the response has arrived before the participant signs in. No agreement, no sign-in: their email is never stored.

### 2b. Questionnaire (`questionnaire.md`), completed **after** the task
A second Google Form, email collection off: participant code; the standard 10 SUS items (Brooke, 1996), unmodified wording, 1–5 scale, "the system" meaning NeuroLens, **in the standard order** (the scoring depends on it); then the three open questions (report §5.2): (1) dashboard clarity and playhead responsiveness; (2) how well they understood the brain-region labels (faces, bodies, scenes, social/STS, auditory); (3) whether $0.90 per 30-second ad seems good value, and why.

### 2c. Moderator script and task sheet
- **`moderator_script.md`:** welcome; confirm the consent response; the participant signs in; the task wording: "Upload your ad (or this one we've given you), watch it process, then use the timeline to find one moment where predicted attention peaks and one where it drops off, and tell me what they are". **Start timing** when the participant first clicks the upload area. **Stop timing** when they have named both moments. **Success** means they named a plausible peak and drop-off without the moderator pointing to them. What the moderator may and may not say. Participants without their own ad **upload** one of 2-3 short provided clips, never the pre-computed sample browser. The provided clips are open-licensed (no third-party ads) and each is listed with its title, URL and licence in `data/videos/SOURCES.md`; pick ones under the maximum duration with people, scenes and sound, so every region has something to show, so every session exercises the upload and processing path the report describes.
- **`task_sheet_template.csv`:** `participant_code, time_on_task_s, success, used_own_ad` (`yes`/`no`). The moderator times with a stopwatch and records only the duration: no dates or clock times, which could identify people in a group of five.
- **Local only, never committed** (covered by the `*.local.*` pattern added to `.gitignore`): `mapping.local.csv` (`participant_code, email`), `notes.local.md` (moderator notes), and the questionnaire's full export `questionnaire.local.csv`.

## 3. Running the sessions
- **Timing:** hold all sessions by about **5–6 November**, batched back-to-back on one or two days so they share one start-up, leaving a week for export, consolidation and writing. The load balancer (`web_mode = "alb"`, M3b §3b) is switched on shortly before (about 2 November) and stays until the presentation: about $6–7 in total.
- Before the sessions: add each participant as a Google test user (M3b §2); set up both forms from §2.
- A batch that keeps a GPU warm for more than 3 hours will trigger M2a's long-running GPU alarm email. That is expected during a batch; after `stop_work.sh`, an alarm email means something was left running.
- Each session follows M3b's `docs/session_checklist.md`: `start_work.sh --study` about 15 minutes ahead (once per batch); consent form; the participant signs in; `grant_credit.py 500` (it asks for the email); the task; the questionnaire; `stop_work.sh` after the batch.

## 4. Study data
### 4a. Export (`experiments/export_study_jobs.py`)
`python experiments/export_study_jobs.py --mapping docs/study/mapping.local.csv` reads, through the Data API, the jobs of the users whose emails are in the mapping, and writes **`experiments/data/study/<run_id>/`** (gitignored) with:
- `jobs.csv`: `participant_code, job_label` (`J1`, `J2`, … per participant, in time order), `status, error_code, verified_duration_ms, created_to_first_stage_ms` (from presign to processing start, so it includes the upload and the queue wait), and one `<stage>_ms` column per stage (each stage's start to the next stage's start; the last stage ends at the job's `updated_at` when `done`, M3a §4a). A job with no stages (e.g. `upload_not_received`) has empty stage columns.
- `manifest.json` following M2b §8 (`experiment: "study"`, `environment` of the web tier, `files: ["jobs.csv"]`).
- No email, user ID, job ID, filename or clock time. Jobs of anyone not in the mapping are excluded. An email matching no user, or several, stops the export with an error.
It then uploads the run to `s3://<bucket>/experiments/study/<run_id>/`. For each participant, consolidation uses their first `done` job.

### 4b. Withdrawal (`infra/withdraw_participant.py <participant_code>`)
Until 13 November, a participant can withdraw. The script looks up their email in the mapping, shows what it will delete, asks for confirmation, then:
- in one transaction, deletes their `ledger`, `refunds`, `jobs`, `balances` and `users` rows, in that order (foreign keys);
- deletes their objects under `uploads/{user_id}/` and `results/{job_id}.json` for each of their jobs;
- removes their row from the mapping.
It then prints the manual steps: delete their questionnaire response in Google Forms, remove them as a Google test user, and re-run the export and consolidation. Their consent-form response is kept as the record of consent and withdrawal. If someone withdraws, the report says so and uses the remaining participants' data.

## 5. Consolidation
### 5a. Inputs
- `experiments/pull_artifacts.sh` syncs `s3://<bucket>/experiments/` to `experiments/data/` (gitignored), and prints the object count in S3 and locally. From then on consolidation runs **offline**, so it still works after teardown.
- `experiments/tco_assumptions.json` (committed): the Spot and on-demand hourly prices on the day measured, GPU TDP, electricity price, hardware purchase price and amortisation period, the report's $0.10-per-video model and the ~1,475 videos/month breakeven.
- `docs/study/sus.csv` (committed): `participant_code, q1 … q10`, made from the questionnaire export by `experiments/form_to_sus.py docs/study/questionnaire.local.csv`, which takes the code column and the ten SUS columns **by position** (Google Forms uses the question text as the column header) and checks there are exactly ten 1–5 answers.
- `docs/study/tasks.csv` (committed): the filled task sheet (§2c).

### 5b. `experiments/consolidate_results.py`
- For each experiment it combines **all runs of one series** (M2b §8), by default the most recent series, or the one named with `--series experiment-1=<name>`. A run whose manifest lacks a required field, or whose listed files are missing or have the wrong columns, is reported and skipped, never guessed at. A missing experiment is reported, and its outputs are skipped; the rest still run.
- Outputs in `docs/results/`, committed and regenerated by one command:
  - `exp1_latency.png`, `exp1_latency.csv`: mean per-stage time (upload, queue wait, each processing stage, result fetch, client render where recorded) for 15 s, 30 s and 60 s clips, and peak VRAM.
  - `exp2_throughput.png`: jobs completed over time per burst size (1, 5, 10, 20). `exp2_queue.png`: queue depth and running GPU workers over time. `exp2_reliability.csv`: results vs submissions, duplicates, dead-lettered jobs per burst, and the simulated Spot interruption's recovery. `exp2_cold_start.csv`: the cold-start breakdown.
  - `exp3_tco.csv`, `exp3_tco.md`: measured runtimes and the scenario cost estimates for cloud and on-premise, compared with the breakeven, labelled as estimates from measured runtimes and stated assumptions, never as observed fully-loaded costs.
  - `sus.csv`, `sus.png`: each participant's score and the mean, with the 80 ("Grade A") line.
  - `tasks.csv`: success rate and time-on-task per participant and mean, with each participant's automated processing time from the export alongside.
  - `README.md`: which series and run IDs were used, and the command that regenerates everything.
- **SUS scoring:** odd items `response − 1`, even items `5 − response`, sum × 2.5, giving 0–100. A response outside 1–5 or a missing item stops the script with the participant code named.

## 6. After the presentation
Set `web_mode = "instance"` and apply, which removes the load balancer (about $0.55 a day), then run `stop_work.sh`. Until teardown, the idle cost is the GPU image snapshot, the stopped servers' disks, the S3 weights and files, Aurora's small storage and the Secrets Manager secret ($0.40 a month). `infra/idle_cost.sh` prints these sizes and a monthly estimate.

## 7. Teardown (`infra/teardown.sh`): run only when Josh decides
Nothing deletes on a date. The consent form promises deletion after grading, so Josh runs this once marks are out. The script is the "button":
- **Safety checks first**, in every mode:
  - `aws sts get-caller-identity` must match the expected account ID in `infra/teardown.conf`;
  - `docs/results/README.md` must exist;
  - the object count under `experiments/` in S3 must equal the local copy's, and `experiments/data/study/` must exist.
  If any check fails, it explains what would be lost and stops unless `--force` is given.
- **`--dry-run`:** lists everything it would delete (bucket object count, `terraform plan -destroy`, images and snapshots, Parameter Store names, the state bucket) and changes nothing.
- **Real run:** asks Josh to type `delete neurolens`, then runs these steps in order. Each step checks whether it is already done and skips it, so a failed run can simply be re-run.
  1. `stop_work.sh`.
  2. Empty the bucket.
  3. Build the Lambda zips if missing (Terraform needs them to plan).
  4. `terraform destroy`, retried up to 3 times with 10-minute waits. The CloudFront VPC origin's AWS-created security group is removed asynchronously and can block the VPC's deletion for 15 minutes or more. Aurora is set to delete without a final snapshot and with its backups (M3a §3a), so participant emails are really gone.
  5. Deregister every `neurolens-worker-*` image and delete its snapshots.
  6. Delete Parameter Store entries under `/neurolens/`.
  7. **Only if `terraform destroy` succeeded**, and after a second typed confirmation, delete the Terraform state bucket, including all object versions and delete markers.
- **Final check:** it lists anything still tagged `Project=neurolens` and double-checks each with that service's own describe call, because the tagging API can briefly still list just-deleted resources.
- It then prints the **manual steps**:
  - delete the Google OAuth client and remove the test users in the Google Cloud console;
  - revoke the HuggingFace token;
  - delete both Google Forms after the report is graded (keep the exported CSVs' committed, pseudonymous parts);
  - delete `docs/study/*.local.*`;
  - optionally delete the budget alert (free to keep).

## 8. Report handoff
- Every figure and table in the final report comes from `docs/results/`, regenerated by one command.
- The report's code section links the GitHub repository, which must stay accessible until marks are released, per the project brief. The report says the service ran on demand and was shut down after evaluation.
- The AI-use declaration draws on `docs/ai_log.local.md`.

## 9. Tests (tests-first; `pytest`)
- **SUS scoring:** all 3s → 50; best possible answers → 100; worst → 0; one mixed set worked by hand in the test's comment, chosen so that swapping the odd- and even-item formulas gives a different score; out-of-range and missing answers raise with the participant code.
- **`form_to_sus.py`:** a fixture shaped like a Google Forms export (question text as headers) converts to `q1 … q10` by position; a missing or non-numeric answer raises.
- **Consolidation**, on fixture artifacts in `tests/fixtures/results/` that follow M2b §8 exactly:
  - every §5b output is produced;
  - two runs of one series are combined, and a second series is ignored unless named;
  - a manifest missing a field, or a file with a wrong column, is skipped and reported;
  - a missing experiment leaves the others' outputs intact.
- **Export** (moto + local PostgreSQL, as in M3a):
  - no email, user ID, job ID, filename or clock-time column;
  - non-participants absent;
  - stage durations correct from a known `stages` list, including the last stage ending at `updated_at`;
  - a job with no stages gives empty stage columns;
  - an unknown or duplicated email stops with an error;
  - it writes under `experiments/data/`.
- **Withdrawal** (moto + local PostgreSQL): all of one participant's rows and objects are gone, other users' are untouched, and M3a's ledger invariants still hold for everyone else.
- **Privacy scan:** every committed file under `docs/results/` and `docs/study/*.csv` contains no `@`, no UUID, no 21-digit number (Google user IDs), and no cell longer than 30 characters.
- The shell scripts are not unit-tested; the teardown dry run is an acceptance criterion.

## 10. Acceptance criteria
1. Five complete sessions (fewer only if someone withdrew, stated in the report), each with a consent response recorded **before** that participant's sign-in.
2. `consolidate_results.py` regenerates every file in `docs/results/` from local data in one command, and each report figure and table traces to one of them.
3. The privacy-scan test passes; no committed file holds an email, name, user ID, job ID, clock time or free-text answer.
4. After the presentation, the load balancer is gone and `stop_work.sh` reports nothing running.
5. Josh has reviewed `teardown.sh --dry-run`. When he later runs the real teardown, the final check lists nothing left (not a gate for submission).
6. `pytest` passes locally and in CI, with the tests committed before the implementation.

## 11. File layout additions
```
docs/study/consent_form.md, questionnaire.md, moderator_script.md, task_sheet_template.csv   NEW
docs/study/sus.csv, tasks.csv                  NEW (after the sessions)
docs/results/                                  NEW (generated)
experiments/export_study_jobs.py               NEW
experiments/form_to_sus.py                     NEW
experiments/pull_artifacts.sh                  NEW
experiments/consolidate_results.py             NEW
experiments/tco_assumptions.json               NEW
infra/withdraw_participant.py                  NEW
infra/idle_cost.sh                             NEW
infra/teardown.sh, teardown.conf               NEW
tests/fixtures/results/                        NEW
requirements/dev.txt                           (already includes experiments.txt from M2b, so CI has matplotlib for the consolidation tests)
.gitignore                                     MODIFIED: *.local.*, experiments/data/
```

## 12. Cost
- Study sessions, batched on one or two days: a few hours of started system (web, NAT, one warm GPU, Aurora at 0.5 ACU): roughly $3–6 in total on Spot.
- Load balancer: about $6–7 from about 2 November to the presentation.
- Idle until teardown: a few dollars a month (printed by `idle_cost.sh`).
