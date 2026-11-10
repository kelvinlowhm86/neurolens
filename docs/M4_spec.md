# NeuroLens — M4 Spec
**Milestone:** the STePS booth, Experiments 1–3, the merge into `main`, and teardown (5 – 13 Nov;
STePS is on 11 Nov; the final report is due 13 Nov 23:59).
**Builds on:** M3: the complete product on demand (SageMaker async GPU, M3e; sign-in and minutes
of credit, M3b/M3c; early access by request, M3k), and the experiment scripts in `experiments/`
(`experiments/README.md`).

**A proposal:** the team running M4 may change any part of it; the report states what was
actually done.

**Ground rules:** region `us-east-1`, the CLI profile of the account in use. Every GPU session's
cost estimate is stated, and agreed by the team, before it starts; every session ends with
`infra/stop_work.sh` (or `0 (Auto-scale)` in the Booth QR admin modal; `docs/session_checklist.md`).

## 1. Scope
**In scope:** the booth at STePS (including the Booth QR visitor passes and admin modal) and its anonymous survey (§2); Experiments 1–3 (§3); what the report takes from them (§4); merging the working branch into `main` (§5); teardown when the team
decides (§6).

**Out of scope:** study scripts beyond the survey form; new experiment tooling unless an experiment cannot be run without it.

## 2. STePS booth (11 Nov, the whole event)
A drop-in table at STePS: visitors try NeuroLens on a team laptop or scan the live Booth QR code on their phone, then fill in a short anonymous survey.
- **Booth QR passes & Admin controls:** Booth admins (`BOOTH_ADMIN_HASHES` in `neurolens/web/app.py`) open the **Booth QR** modal in the web app to display an auto-rotating QR code (polled every 0.5 s). Scanning the QR code on a phone redeems a 2-minute (`400` cents) guest session (`guest-<pass_id>`, 1 pass per phone, `research_consent = False` forced on all guest uploads). Inside the same modal, admins can adjust the pass pool (`+5 passes`, `+10 passes`, `Stop passes`), control GPU warmth (`Keep 1 GPU`, `Keep 2 GPUs` for 6 hours, or `0 (Auto-scale)`), grant account credit by email (`+5 min`, `+10 min`), and view live + per-day (`Asia/Singapore`) funnel totals.
- **Survey:** `docs/study/questionnaire.md` is the form's text and settings; `docs/study/make_form.gs`
  builds it in Google Forms (keep the two in step). It collects no email or name. It asks for
  consent (a required tick), what the visitor did, the 10 SUS items in the standard order,
  usefulness and trust, whether NeuroLens showed something new, willingness to pay, and two open
  questions. `docs/study/booth_survey_qr.svg` (and `.png`) is the form's QR code for the table card;
  the site's "Give feedback" link opens the same form.
- **After the booth:** use only totals and short anonymous quotes from the survey in the report.
  SUS score per response: odd items `answer − 1`, even items `5 − answer`, sum × 2.5 (0–100).

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

## 5. Branch
Everyone works on `main`; CI runs `pytest` on every push. A conflict is resolved with the teammate
who changed the file.

## 6. Teardown (only when the team decides)
Nothing is deleted on a date. In order, each step checked before the next:
1. `infra/stop_work.sh` prints ALL STOPPED.
2. Delete every image in the `neurolens-worker` ECR repository, tagged and untagged
   (`aws ecr list-images --repository-name neurolens-worker --filter tagStatus=ANY`, then
   `aws ecr batch-delete-image`). Terraform cannot delete a repository that still holds images.
3. Copy anything the report still needs from `s3://<bucket>/experiments/` to the laptop. Then
   empty the bucket and abort incomplete multipart uploads (`aws s3api list-multipart-uploads` /
   `aws s3api abort-multipart-upload`). Do this just before `terraform destroy`: the site keeps
   writing until then.
4. `terraform destroy` in `infra/terraform/`. This removes the database (no final snapshot), the
   sign-in user pool and its emails, the GPU endpoint, the Lambdas and the site.
5. Delete the Parameter Store entries made by hand (Terraform does not remove them):
   `/neurolens/terraform/google_client_secret`, `/neurolens/web/flask_secret_key`,
   `/neurolens/web/cognito_client_secret` and `/neurolens/web/youtube_api_key`; then check that
   nothing under `/neurolens/` is left. The Terraform state bucket is versioned: delete every
   object version and delete marker first (`aws s3api list-object-versions` + `delete-objects`,
   repeat until empty), then delete the bucket. It holds old Terraform state (secrets included),
   so this matters.
6. Outside AWS: delete the Google OAuth client, revoke the HuggingFace token, delete the YouTube
   API key (or its Google Cloud project), and delete the Google Form once the report is graded.
   Also delete the form's linked response Sheet and any downloaded exports, and the Apps Script
   project that built the form (`docs/study/make_form.gs`).
7. Check that nothing tagged `Project=neurolens` is left
   (`aws resourcegroupstaggingapi get-resources --tag-filters Key=Project,Values=neurolens`). The
   tag check misses some resources, so also check explicitly: Secrets Manager has no
   `rds!cluster-*` secret left (RDS deletes its own secret with the cluster; delete one that
   remains with `--force-delete-without-recovery`); no SageMaker endpoint configurations or models
   named `neurolens*`; nothing in Parameter Store under `/neurolens/`; and no `neurolens-*` IAM
   roles.
8. Last, as part of the same teardown, the ACCOUNT OWNER (the deploy user cannot delete itself)
   deletes the deploy user and the five `neurolens-*` policies. Remove the operator's profile from every laptop that used it (`~/.aws/credentials` and `~/.aws/config`).
9. On every laptop that deployed: delete `.env` (it holds the revoked tokens), and
   `infra/terraform/backend.hcl`, `terraform.tfvars` and `.terraform/` (they name the deleted
   account and state bucket); remove the local test database container and its data with
   `docker rm -fv neurolens-test-pg`; and delete a stored HuggingFace login token if one exists
   (`~/.cache/huggingface/token`). The team's own sample videos and `data/output/` may be kept.

Run steps 1-5, 7 and 8 in each account (the live account, then the backup account), step 6 once, and step 9 on
every laptop that deployed.

## 7. Acceptance criteria
1. The STePS booth ran, and its survey responses are exported and summarised.
2. Experiments 1–3 each have at least one complete run.
3. No committed file holds an email, name, user ID, job ID or a visitor's free-text answer.
4. The working branch is merged into `main` and CI passes there.
5. After each session, `stop_work.sh` printed ALL STOPPED.
