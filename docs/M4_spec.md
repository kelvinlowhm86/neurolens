# NeuroLens — M4 Spec
**Milestone:** the booth study, Experiments 1–3, the final report, the merge into `main`, and
teardown (5 – 13 Nov; the booth is on 11 Nov; the final report and slides are due 13 Nov 23:59).
**Builds on:** M3: the complete product on demand (SageMaker async GPU, M3e; sign-in and minutes
of credit, M3b/M3c; early access by request, M3k), and the experiment scripts in `experiments/`
(`experiments/README.md`).

**Ground rules:** region `us-east-1`, `neurolens` CLI profile. **No new product features:**
problems the booth or the experiments find go in the report as future work. Every GPU session's
cost estimate is stated, and agreed by Josh, before it starts; every session ends with
`infra/stop_work.sh` (`docs/session_checklist.md`).

## 1. Scope
**In scope:** the drop-in booth and its anonymous survey (§2); Experiments 1–3 with the existing
scripts (§3); the report material (§4); merging `aws-josh` into `main` (§5); teardown when Josh
decides (§6).

**Out of scope:** in-app study code (consent screens, participant flags, timing); study scripts
beyond the survey form; new experiment tooling unless an experiment cannot be run without it.

## 2. Booth study (11 Nov, about 3 hours)
A drop-in table: visitors try NeuroLens on Josh's laptop, then fill in a short anonymous survey.
- **Survey:** `docs/study/questionnaire.md` is the form's text and settings; `docs/study/make_form.gs`
  builds it in Google Forms (keep the two in step). It collects no email or name. It asks for
  consent (a required tick), what the visitor did, the 10 SUS items in the standard order,
  usefulness and trust, whether NeuroLens showed something new, willingness to pay, and two open
  questions. `docs/study/booth_survey_qr.svg` (and `.png`) is the form's QR code for the table card;
  the site's "Give feedback" link opens the same form.
- **Account:** one booth account, signed in on Josh's laptop, given enough minutes beforehand with
  `infra/grant_credit.py`. Visitors use the samples or upload a short clip from Josh's devices or
  their own.
- **GPU:** `infra/start_work.sh --keep-warm --hours 4` at least 15 minutes before opening (a cold
  GPU takes about 10 minutes), then one short warm-up upload. `infra/stop_work.sh` at closing.
- **After the booth:** delete the booth account's jobs from History (or the account itself), so
  visitors' uploads do not stay. Download the form responses as CSV; keep it local (git-ignored,
  `*.local.*` name) and use only totals and short anonymous quotes in the report. SUS score per
  response: odd items `answer − 1`, even items `5 − answer`, sum × 2.5 (0–100).
- **Cost (estimate):** 4 GPU hours on `ml.g6e.xlarge` at about $2.61 an hour (SageMaker price) plus Aurora awake:
  about $11.

## 3. Experiments
The run layout, file columns and manifests are fixed by the scripts themselves
(`neurolens/experiment_runs.py` and each script's docstring); `experiments/README.md` lists them.
Each run writes `experiments/<experiment>/<run_id>/` in the bucket with a `manifest.json`
(`experiments/finish_run.py`, given the live image's digest).
- **Clips:** Josh supplies them: one fixed set for all three experiments; open-licensed, listed in
  `data/videos/SOURCES.md`; with sound, some clear English speech, some without speech; H.264/AAC,
  720p or less, at most 120 s. Proposed lengths 15, 30 and 60 s, 3 clips each (120 s: open).
- **Experiment 1 (latency):** on a warm GPU (`start_work.sh --keep-warm`, then one warm-up job),
  `experiments/latency_run.py` runs each clip as a browser would and appends a row to
  `experiment-1/<run_id>/runs.csv`; 3 runs per clip. `experiments/latency_breakdown.py` gives the
  median, minimum and maximum time per stage and clip length. Client render time is read on 3 runs per length from
  `window.neurolensTimings.render_ms` in the browser and added by hand.
- **Experiment 2 (load bursts):** `experiments/locustfile.py` sends bursts of simultaneous jobs
  (proposed 1, 5, 10, 20; open: the GPU quota allows at most 2 machines, `max_instances = 2`
  for this session only, then back to 1). The first burst starts from zero GPUs so it includes a
  cold start, as the GPU now starts when a job is dispatched (M3f §6a), a start never timed yet.
  `experiments/export_cloudwatch.py` saves the one-minute backlog and instance series and
  `experiments/cold_start.py` the cold-start breakdown.
- **Experiment 3 (cloud vs on-premise cost, an estimate):** the on-premise leg runs
  `experiments/tco_benchmark.py` on the school's Slurm GPU (`tco_benchmark.sbatch`); the cloud leg
  reuses Experiment 1's runs. `experiments/tco_compare.py` turns both, with the prices in
  `experiments/tco_assumptions.json`, into cost per video and a break-even volume, labelled as
  estimates from measured run times and stated assumptions.
- **Cost (estimate):** Experiment 1 about $6, Experiment 2 about $5–8, Experiment 3 $0 (Slurm is
  free; the cloud leg reuses Experiment 1): about $12–20 in total. Flag each session before it
  starts.

## 4. Report handoff
- Every number, chart and table in the final report comes from §2's survey export or §3's
  outputs, and the report says how many runs and responses each rests on.
- The preliminary report in `docs/course/` is submitted and never edited; the final report and
  slides are added there.
- The report links the GitHub repository, which stays reachable until marks are released. It says
  the service ran on demand and was shut down after evaluation.
- The AI-use declaration covers the deployment work with this paragraph (add the report's own AI use):
  > AI coding assistants (Claude Code, with DeepSeek and others for first drafts) helped plan,
  > build, test and document the AWS deployment, and explained AWS concepts along the way. Every
  > change was checked by automated tests and reviewed; all design decisions, spending and
  > deployments were made by the team. The product itself uses Claude on Amazon Bedrock to write
  > each video's AI report.
- After the last experiment, set `log_retention_days = 14` in `terraform.tfvars` and apply
  (90 days are kept until then for the run timings).

## 5. Merge into `main`
Teammates work on `main` in parallel: merge `aws-josh` into `main` (not a rebase), resolve
conflicts with the teammate who changed the file, and check that `pytest` passes in CI on `main`.

## 6. Teardown (only when Josh decides)
Nothing is deleted on a date. In order, each step checked before the next:
1. `infra/stop_work.sh` prints ALL STOPPED.
2. Copy anything the report still needs from `s3://<bucket>/experiments/` to the laptop, then
   empty the bucket.
3. Delete the images in the `neurolens-worker` ECR repository (Terraform cannot delete a
   repository that still holds images).
4. `terraform destroy` in `infra/terraform/`. This removes the database (no final snapshot), the
   sign-in user pool and its emails, the GPU endpoint, the Lambdas and the site.
5. Delete the Parameter Store entry `/neurolens/terraform/google_client_secret` (made by hand, not
   by Terraform), then the Terraform state bucket.
6. Outside AWS: delete the Google OAuth client, revoke the HuggingFace token, and delete the
   Google Form once the report is graded.
7. Check that nothing tagged `Project=neurolens` is left.
8. Last, as part of the same teardown: delete the deploy user's access keys (in a borrowed
   account, the account owner deletes the user and the five `neurolens-*` policies), and remove
   that account's profile from the laptop's `~/.aws/credentials` and `~/.aws/config`.

Run all eight steps in every AWS account that holds a NeuroLens deployment.

## 7. Acceptance criteria
1. The booth ran, and its survey responses are exported and summarised in the report.
2. Experiments 1–3 each have at least one complete run with a manifest, and every report figure
   traces to one of them.
3. No committed file holds an email, name, user ID, job ID or a visitor's free-text answer.
4. `aws-josh` is merged into `main` and CI passes there.
5. After each session, `stop_work.sh` printed ALL STOPPED.
