# Operating NeuroLens

This is the operator guide for NeuroLens on Amazon Web Services (AWS). The detailed first-time commands live in `infra/terraform/README.md` and `infra/README.md`; this page gives the order, the day-to-day commands and what to do when an alarm fires.

## What you need

- Terraform (infrastructure as code) 1.10 or newer, the AWS command-line tool, Git, and Python 3.12.
- A named AWS profile in `~/.aws`, for example `aws configure --profile neurolens`.
- The repo's virtual environment, from the repo root:

  ```bash
  python3.12 -m venv .venv && source .venv/bin/activate && pip install -e . -r requirements/dev.txt
  ```

- `infra/aws_env.sh` is sourced by the scripts in `infra/`. It sets `AWS_PROFILE` from `NEUROLENS_AWS_PROFILE` (falling back to `neurolens`), reads the region from Terraform's `region` output, and checks `aws sts get-caller-identity`. Terraform commands also use `AWS_PROFILE`.
- The account owner's one-time work: create five AWS Identity and Access Management (IAM, AWS's permissions system) policies, create the deploy user, and attach four of the policies. Follow `infra/iam/ACCOUNT_OWNER.md` and `infra/iam/README.md`.
- Quotas, secrets, sign-in and model access are listed in `infra/terraform/README.md` section 0. That section covers the Amazon SageMaker (AWS's managed machine-learning service) graphics processing unit (GPU) quota, the HuggingFace token, the Google OAuth client, the YouTube Data API key, AWS Systems Manager Parameter Store (SSM, AWS's configuration store) secrets, the optional AWS Budgets (AWS's cost-alert service) alert and the Amazon Bedrock (managed access to foundation models) model switch.

## First deploy from an empty account

Follow the linked README sections for the exact commands. The order matters.

1. **Account owner setup.** Create the five IAM policies and the deploy user, attach the four deploy policies, make an access key and switch on the Bedrock model (`infra/iam/ACCOUNT_OWNER.md`, `infra/iam/README.md`).
2. **Prerequisites.** Do `infra/terraform/README.md` section 0: quotas, `HF_TOKEN`, the Google OAuth client, the YouTube API key, the SSM SecureString values, the optional budget, and a working Bedrock playground reply.
3. **Terraform state bucket.** Do section 1: create the versioned, private Amazon S3 (object storage) state bucket by hand and copy `infra/terraform/backend.hcl.example` to `infra/terraform/backend.hcl`.
4. **First apply.** Do section 2. Build the web zip, the `ffprobe` layer and the report SDK layer first, copy `infra/terraform/terraform.tfvars.example` to `infra/terraform/terraform.tfvars`, then `terraform -chdir=infra/terraform init -backend-config=backend.hcl`, `terraform -chdir=infra/terraform plan` and `terraform -chdir=infra/terraform apply`. Run `apply` twice; the second run reports "No changes". Confirm both Amazon Simple Notification Service (SNS, AWS's notification service) confirmation emails (`alert_email`). AWS Lambda (functions that run on demand) runs the web zip and the layers.
5. **Database.** Do section 2e after the first apply: read the database outputs into `.env`, then run `python infra/apply_schema.py --backend data_api` and `python infra/check_aurora.py --backend data_api`. Sign-in writes to these tables, so this step comes before sign-in.
6. **Sign-in and website.** Do section 2f: add `google_redirect_uri` to the Google OAuth client, store the Amazon Cognito (AWS's sign-in service) client secret in Parameter Store, then run `infra/deploy_web.sh` and open `sign_in_test_url` to test Google and email sign-in.
7. **GPU image and endpoint.** Do section 2b. In a new account, apply first with `worker_image` empty, copy `model-data/` from an existing deployment's bucket, then run `infra/build_image.sh`, put its digest in `terraform.tfvars` as `worker_image`, and apply again. The endpoint exists only when `worker_image` is set.
8. **Check.** Do section 3: bucket lifecycle and browser upload rules, endpoint, scaling target, and the Amazon CloudWatch (AWS's monitoring service) dashboard named `neurolens`. Section 5 is a work session; section 6 is teardown.

## Day to day

### Start and stop work

```bash
infra/start_work.sh                         # normal: maximum 1; an upload starts a GPU
infra/start_work.sh --keep-warm --hours 3   # hold one GPU for 1-5 hours, then auto-scale
infra/stop_work.sh                          # emergency pause
```

`infra/start_work.sh` sets the SageMaker endpoint `neurolens`, variant `main`, to a maximum of one. A waiting upload starts a GPU, and seven idle minutes stop it. `--keep-warm` raises the minimum for the chosen hours and sets a scheduled action named `neurolens-keep-warm-end`. `infra/stop_work.sh` deletes that action, sets the scaling target to 0/0, sends the desired count to zero, and prints `ALL STOPPED` only when the scalable target and endpoint read 0. A running job is interrupted and its failure path refunds it. A result other than `ALL STOPPED` means the stop is not proven (`infra/start_work.sh`, `infra/stop_work.sh`).

### Deploy the web app

```bash
infra/deploy_web.sh
```

The script needs a clean git tree and the project virtual environment. It ships the last commit: it rebuilds the web Lambda zip, updates the `neurolens-web` Lambda, stages the public page and samples into `site/`, fills the contact address, syncs to S3, and clears the Amazon CloudFront (AWS's content delivery network) cache (`infra/deploy_web.sh`, `infra/build_web_lambda.sh`). A missing sample file stops the deploy; see `data/videos/README.md`.

### Build the worker image

```bash
infra/build_image.sh
# put the printed digest in infra/terraform/terraform.tfvars as worker_image
terraform -chdir=infra/terraform apply
```

The build needs a clean git tree. It zips the repository at `HEAD`, uploads the source zip under `build/`, starts the AWS CodeBuild (AWS's managed build service) project `neurolens-worker-image`, waits, and prints the image digest. The image goes to Amazon Elastic Container Registry (ECR, AWS's container image registry). The apply creates a new SageMaker model and endpoint configuration and swaps the endpoint to them. A rolled-back endpoint update fails the apply and keeps the existing configuration and model (`infra/build_image.sh`, `infra/terraform/sagemaker.tf`).

### Apply database migrations

```bash
python infra/apply_schema.py --backend data_api
```

The script applies each file in `infra/migrations/` once and records it in `schema_migrations` (`infra/apply_schema.py`).

### Run the whole product on a laptop

```bash
python infra/local_stack.py
```

The local stack uses PostgreSQL, fake S3 and Amazon Simple Queue Service (SQS, AWS's message queue), and fake analysis; it runs the worker code itself, so no separate worker is needed. It runs at `http://127.0.0.1:5003`; `--grant <email> <minutes>` adds credit (`infra/local_stack.py`).

## Admin panel

The `Admin` button appears only for an email in the admin list (`static/app.html`, `neurolens/web/app.py`).

- **Who is an admin.** On AWS, set `admin_emails` in `infra/terraform/terraform.tfvars`; on a laptop, set `NEUROLENS_ADMIN_EMAILS` in `.env`. The list is comma-separated and lowercased; an empty list means nobody can open the panel (`infra/terraform/web.tf`, `neurolens/settings.py`). To delegate, add the person's email and apply or restart. The panel itself has no user management.
- **Keep the GPU warm.** `0 (Auto-scale)` lets the endpoint stop when idle. `Keep 1 GPU` or `Keep 2 GPUs` holds that count for six hours, then the setting expires (`neurolens/billing.py`, `static/app.html`).
- **Grant credit.** Enter an email or leave it blank for your own account, then choose `+5 min`, `+10 min` or `Reset (0)`. The API also accepts `2` minutes. `+5 min` equals the invite credit (`neurolens/web/app.py`, `config.json`).
- **Visitor passes.** Each QR code is a signed two-minute guest pass. The pool starts with ten passes. `+5 passes`, `+10 passes` and `Stop passes` change the pool. A scan creates or joins a `guest-` account; a copied link joins the same session, while a second phone gets a separate guest account (`neurolens/billing.py`, `neurolens/web/app.py`).
- **Access requests.** A signed-in user asks for free minutes with a channel link. The request is stored in `access_requests` and an SNS email goes to `alert_email` (`neurolens/access.py`, `neurolens/notify.py`). List waiting requests with `python infra/access_requests.py list --backend data_api`; decline one with `python infra/access_requests.py decline --backend data_api`. Grant credit in the panel; a grant marks the request granted.

## Settings you can change

- **Most GPUs at once.** `max_instances` in `infra/terraform/terraform.tfvars` is 1 by default. Set it to 2 to run two videos at once, once the account's GPU quota allows two instances, then apply (`infra/terraform/variables.tf`).
- **Longest video.** `max_video_duration_seconds` in `config.json` is 120. A longer limit costs more per video and waits longer: a 120-second 4K video already takes about 40 to 47 minutes on the GPU, and each job is capped at 55 minutes (`worker.max_job_minutes` in `config.json`), so raise that cap with it.
- **First-video wait.** With no GPU running, the first video waits while one starts, usually minutes (`docs/evidence/README.md`). Keep a GPU warm from the admin panel before a busy period.

## Watching cost

A GPU bills only while it runs. Amazon Aurora (a managed PostgreSQL-compatible database) bills only while it is awake; it pauses five minutes after its last use. S3 bills for stored files, Bedrock bills for each report, and logs bill for what they keep (`infra/terraform/README.md`).

The CloudWatch dashboard named `neurolens` shows job counts, waiting jobs, failed jobs, GPU backlog, GPU processing time, Lambda errors and Aurora capacity (`infra/terraform/dashboard.tf`).

Alarms email the SNS topic (`alert_email`):

- **`neurolens-gpu-guard-stopped`.** The GPU guard stopped a GPU because the scale-in alarm did not. Look at the backlog metric. The guard keeps the spend bounded; run `infra/stop_work.sh` if you are unsure.
- **`neurolens-gpu-guard-stuck`.** The guard asked the GPU to stop twice in 30 minutes, so the first stop may not have worked. Run `infra/stop_work.sh`. If the endpoint still runs, delete the `neurolens` endpoint in the SageMaker console and apply Terraform later.
- **`neurolens-gpu-guard-not-running`.** The guard has not run for 30 minutes. Check that the EventBridge rule `neurolens-gpu-guard-every-10-min` is enabled; run `infra/stop_work.sh` if in doubt. Amazon EventBridge is AWS's scheduled-event service.
- **`neurolens-db-awake-6h`.** Aurora has been awake for more than 30% of each of the last six hours. Run `infra/stop_work.sh`, then look for what keeps calling the database: an open page or a retrying Lambda in `/aws/lambda/neurolens-*`.
- **`neurolens-<function>-errors`.** A Lambda failed. The alarm names the function; read its log under `/aws/lambda/`.
- **`neurolens-cognito-delete-failed`.** The user's data is gone, but removing the Cognito login failed. Delete the login by hand; the account's subject is in the `COGNITO_DELETE_FAILED` log line.

The scale-out and scale-in alarms (`neurolens-gpu-scale-out`, `neurolens-gpu-scale-in`) act on Application Auto Scaling instead of sending email (`infra/terraform/sagemaker.tf`).

For a budget, create an AWS Budgets alert in the Billing console at the amount the account owner chooses (`infra/terraform/README.md`). It catches spend above that amount and stops nothing; when it fires, check the dashboard and run `infra/stop_work.sh` if a GPU is running with no work. Terraform creates no budget resource.

## Teardown

Run steps 1-5, 7 and 8 in each account, in order, finishing one account before the next. Run step 6 once, outside AWS. Run step 9 on every laptop that deployed. Nothing is deleted on a date; do this only when the team decides.

1. **Stop the GPU.** Run `infra/stop_work.sh` and wait for `ALL STOPPED`. Do this in your main account, then any backup account.
2. **Delete the worker images.** In each account, delete every image in the `neurolens-worker` ECR repository, tagged and untagged, with `aws ecr list-images --repository-name neurolens-worker --filter tagStatus=ANY` and `aws ecr batch-delete-image`. Terraform cannot delete a repository that still holds images.
3. **Save and empty the bucket.** Copy anything needed from `s3://<bucket>/experiments/` to a laptop. Abort incomplete multipart uploads, then empty the bucket. Do this just before `terraform destroy`; the site keeps writing until then.
4. **Destroy the stack.** In each account, run `terraform -chdir=infra/terraform destroy`. This removes the database with no final snapshot, the sign-in user pool, the GPU endpoint, the Lambdas and the site.
5. **Delete the hand-made secrets and state.** In each account, delete the Parameter Store entries made by hand: `/neurolens/terraform/google_client_secret`, `/neurolens/web/flask_secret_key`, `/neurolens/web/cognito_client_secret` and `/neurolens/web/youtube_api_key`. Check that nothing under `/neurolens/` is left. The Terraform state bucket holds stored state, including secrets: delete every object version and delete marker, then delete the bucket.
6. **Clean the outside accounts.** Once: delete the Google OAuth client, revoke the HuggingFace token, and delete the YouTube API key or its Google Cloud project.
7. **Check for leftovers.** In each account, check that no resource tagged `Project=neurolens` remains. Also check AWS Secrets Manager (AWS's secret store) for a leftover `rds!cluster-*` secret (delete it with force if it remains), no SageMaker endpoint configuration or model named `neurolens*`, nothing in Parameter Store under `/neurolens/`, and no `neurolens-*` IAM role.
8. **Remove the operator's access.** In each account, the account owner deletes the deploy user and the five `neurolens-*` policies; the deploy user cannot delete itself. Remove the operator's profile from every laptop that used it (`~/.aws/credentials` and `~/.aws/config`).
9. **Clean each laptop.** Delete `.env`, `infra/terraform/backend.hcl`, `infra/terraform/terraform.tfvars` and `infra/terraform/.terraform/`. Remove the local test database with `docker rm -fv neurolens-test-pg`. Delete a stored HuggingFace token if one exists (`~/.cache/huggingface/token`). The team's sample videos and `data/output/` may stay.
