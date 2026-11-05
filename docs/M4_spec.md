# NeuroLens — M4 Spec
**Milestone:** the STePS booth, Experiments 1–3, the merge into `main`, and teardown (5 – 13 Nov;
STePS is on 11 Nov; the final report is due 13 Nov 23:59).
**Builds on:** M3: the complete product on demand (SageMaker async GPU, M3e; sign-in and minutes
of credit, M3b/M3c; early access by request, M3k), and the experiment scripts in `experiments/`
(`experiments/README.md`).

**A proposal:** the team running M4 may change any part of it; the report states what was
actually done.

**Accounts:** the live app runs in Brandon's AWS account (Josh's CLI profile for it:
`neurolens-brandon`). Josh's own account (profile `neurolens`) keeps a second, complete copy as a
backup until the course ends.

**Ground rules:** region `us-east-1`, the CLI profile of the account in use. **No new product features:**
problems the booth or the experiments find go in the report as future work. Every GPU session's
cost estimate is stated, and agreed by the team, before it starts; every session ends with
`infra/stop_work.sh` (`docs/session_checklist.md`).

## 1. Scope
**In scope:** the booth at STePS and its anonymous survey (§2); Experiments 1–3 (§3); what the report takes from them (§4); merging `aws-josh` into `main` (§5); teardown when the team
decides (§6).

**Out of scope:** in-app study code (consent screens, participant flags, timing); study scripts
beyond the survey form; new experiment tooling unless an experiment cannot be run without it.

## 2. STePS booth (11 Nov, about 3 hours)
A drop-in table at STePS: visitors try NeuroLens on a team laptop, then fill in a short anonymous survey.
- **Survey:** `docs/study/questionnaire.md` is the form's text and settings; `docs/study/make_form.gs`
  builds it in Google Forms (keep the two in step). It collects no email or name. It asks for
  consent (a required tick), what the visitor did, the 10 SUS items in the standard order,
  usefulness and trust, whether NeuroLens showed something new, willingness to pay, and two open
  questions. `docs/study/booth_survey_qr.svg` (and `.png`) is the form's QR code for the table card;
  the site's "Give feedback" link opens the same form.
- **Account:** one booth account, signed in on the booth laptop, given enough minutes beforehand with
  `infra/grant_credit.py`. Visitors use the samples or upload a short clip from the team's devices or
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
Experiments 1–3 as in the preliminary report (`docs/course/Preliminary.tex` §5.1): latency
breakdown, scaling under load, and cloud vs on-premise cost. The team decides the clips, run
counts and method. Ready-made, tested scripts for all three are in `experiments/`
(`experiments/README.md`); use, change or replace them. State each GPU session's cost estimate
before it starts.

## 4. What the report takes from this
What this milestone hands to the final report:
- The survey summary (§2) and the experiment outputs (§3), each with how many responses or runs
  it rests on. The GitHub repository stays reachable until marks are released.
- The AI-use declaration for the deployment work (add the report's own AI use):
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

## 6. Teardown (only when the team decides)
Nothing is deleted on a date. In order, each step checked before the next:
1. `infra/stop_work.sh` prints ALL STOPPED.
2. Copy anything the report still needs from `s3://<bucket>/experiments/` to the laptop, then
   empty the bucket.
3. Delete the images in the `neurolens-worker` ECR repository (Terraform cannot delete a
   repository that still holds images).
4. `terraform destroy` in `infra/terraform/`. This removes the database (no final snapshot), the
   sign-in user pool and its emails, the GPU endpoint, the Lambdas and the site.
5. Delete the Parameter Store entries made by hand (Terraform does not remove them):
   `/neurolens/terraform/google_client_secret`, `/neurolens/web/flask_secret_key`,
   `/neurolens/web/cognito_client_secret` and `/neurolens/web/youtube_api_key`; then check that
   nothing under `/neurolens/` is left, and delete the Terraform state bucket.
6. Outside AWS: delete the Google OAuth client, revoke the HuggingFace token, delete the YouTube
   API key (or its Google Cloud project), and delete the Google Form once the report is graded.
7. Check that nothing tagged `Project=neurolens` is left.
8. Last, as part of the same teardown: delete the deploy user's access keys (in a borrowed
   account, the account owner deletes the user and the five `neurolens-*` policies), and remove
   that account's profile from the laptop's `~/.aws/credentials` and `~/.aws/config`.
9. On every laptop that deployed: delete `.env` (it holds the revoked tokens), and
   `infra/terraform/backend.hcl`, `terraform.tfvars` and `.terraform/` (they name the deleted
   account and state bucket); remove the local test database container if one was made
   (`docker rm -f neurolens-test-pg`).

Run all nine steps in both accounts, Brandon's and Josh's backup, when the course ends.

## 7. Acceptance criteria
1. The STePS booth ran, and its survey responses are exported and summarised.
2. Experiments 1–3 each have at least one complete run.
3. No committed file holds an email, name, user ID, job ID or a visitor's free-text answer.
4. `aws-josh` is merged into `main` and CI passes there.
5. After each session, `stop_work.sh` printed ALL STOPPED.
