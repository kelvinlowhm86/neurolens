# NeuroLens: AWS resources (Terraform)

This creates everything NeuroLens needs on AWS: the S3 bucket and SQS queue, the network and
database, the website and sign-in, and a SageMaker asynchronous GPU
endpoint, its image repository and build, the job Lambdas and their alarms. Terraform keeps a
record of what it created, so running `apply` twice is safe (the second run reports no changes)
and `destroy` removes everything it created. Everything runs in `us-east-1` (`region` in
`terraform.tfvars`): the hand-applied IAM policies, the role boundary and the worker image's pinned
base image name it explicitly, and the state bucket lives there, so another region fails.

What it creates

- One S3 bucket: public access blocked, encrypted, HTTPS-only, ACLs disabled, browser upload
  (CORS) from `allowed_origins`, and prefix-scoped expiry (`uploads/` after `upload_retention_days`,
  `results/` after `result_retention_days`, both 30; `dataset/` after
  `research.dataset_retention_days` (1095), `sagemaker-output/` after 1, `build/` after 7).
- One SQS queue (visibility `queue.visibility_seconds` (1080 s), a `queue.cancel_seconds`
  (15 s) delivery delay) and the wiring that sends a message whenever a file lands under
  `uploads/`. A second queue (`neurolens-jobs-dlq`) receives a job that failed
  `queue.max_receives` (twice).
- A VPC with private subnets for Aurora only. No NAT Gateway, no internet gateway, no S3 endpoint:
  Aurora is reached through the Data API, and S3 through its public HTTPS endpoint.
- Roles: the SageMaker execution role, the CodeBuild role, the dispatcher, the SageMaker error
  handler, the GPU guard, the reaper, the dead-letter handler and the web function. Each is
  limited to what it needs and capped by a permission boundary.
- The ECR repository `neurolens-worker` (last 3 images) and the CodeBuild project
  `neurolens-worker-image` (privileged Docker, 60-minute timeout).
- The SageMaker model, endpoint configuration (instance pools `ml.g6e.xlarge`,
  `ml.g6.2xlarge`, `ml.g5.2xlarge`, `ml.g6e.2xlarge`, in that order) and the endpoint `neurolens`; its async error topic `neurolens-gpu-errors`.
- Application Auto Scaling: min 0, max `max_instances`; a waiting request starts a GPU,
  `idle_stop_minutes` (seven idle minutes) stop it.
- Lambda functions: the dispatcher (jobs queue, with the ffprobe layer), the SageMaker error
  handler (error topic), the GPU guard (every 10 minutes), the dead-letter handler, the reaper
  (every `reaper_schedule_hours`, 3) and the report function (with the Anthropic SDK layer).
- Alarms on one SNS email topic: the GPU guard stopped a GPU, the GPU guard has not run, Aurora has
  been awake for much of 6 hours, and one error alarm per Lambda.

The endpoint only exists once `worker_image` is set: build the image first (section 2b).

Cost: with everything stopped, the running cost is the stored image, the ECR repository,
Aurora storage and the S3 files, a few dollars a month (the reaper waking Aurora every
`reaper_schedule_hours` (3 hours) is about $3). A GPU bills only while it runs: about $2.61/h for
`ml.g6e.xlarge`, $1.22/h for `ml.g6.2xlarge` (L4), $1.52/h for `ml.g5.2xlarge` (A10G) and
$2.80/h for `ml.g6e.2xlarge` (SageMaker prices, us-east-1), and it stops itself after
`idle_stop_minutes` (7 idle minutes). A deploy starts one instance for about 7 minutes
(roughly $0.50-0.80).

From an empty account: the account-owner steps in `infra/iam/ACCOUNT_OWNER.md` and the human-only steps in section 0, then `infra/setup.sh`; it does section 1,
section 2, section 2c (database tables), section 2a and section 2b. Then section 5 for each work
session and section 6 to tear down.

## 0. Before you start (once)

1. Terraform 1.10 or newer: `brew tap hashicorp/tap && brew install hashicorp/tap/terraform`.
2. The AWS command-line tool (`aws --version`).
3. A named AWS profile in `~/.aws` (`aws configure --profile neurolens`). **Never paste keys into
   chat, git or a Docker image.**
4. Python 3.12 for the repo (`AGENTS.md`) and `git`. Set up the repo's virtual environment (the
   operator scripts `apply_schema.py`, `access_requests.py` and the others use it), from the repo
   root:
   ```bash
   python3.12 -m venv .venv && source .venv/bin/activate && pip install -e . -r requirements/dev.txt
   ```
5. Quotas and accounts (console; quota requests can take days):
   - SageMaker endpoint quota for `ml.g6e.xlarge` (1 is enough: the app runs one GPU at a time).
   - Lambda concurrency: if the account's limit is well below 1,000, request 1,000 (free).
   - A HuggingFace read token for the model weights (it goes in the repo's git-ignored `.env`
     as `HF_TOKEN`, see `.env.example`).
   - A Google OAuth client for sign-in: in the Google Cloud console, set up the OAuth consent
     screen first if asked, then APIs & Services > Credentials > Create OAuth client ID > type
     "Web application". This gives `google_client_id`; its secret is stored in item 6. The
     redirect URI is added after the first apply (section 2a).
   - A YouTube Data API v3 key: in the same Google Cloud project, enable "YouTube Data API v3",
     then Credentials > Create API key and restrict the key to that API. No billing is needed.
     The key is stored in item 6.
6. Secrets in Parameter Store (`SecureString`, never in git or Terraform files). Run each command
   from the repo root with the profile variables below exported; the value is not echoed.

   Google OAuth client secret (from item 5):
   ```bash
   read -rs S && aws ssm put-parameter --name /neurolens/terraform/google_client_secret \
     --type SecureString --value "$S" --overwrite && unset S
   ```

   Flask session key (generated, at least 32 random bytes):
   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))" | {
     read -r S && aws ssm put-parameter --name /neurolens/web/flask_secret_key \
       --type SecureString --value "$S" --overwrite
   }
   ```

   YouTube Data API v3 key (from item 5; the site does not start without it):
   ```bash
   read -rs S && aws ssm put-parameter --name /neurolens/web/youtube_api_key \
     --type SecureString --value "$S" --overwrite && unset S
   ```
7. Optional: an AWS Budget alert (Billing, Budgets), at an amount the account owner chooses.
8. In the console, with the region set to us-east-1, open Bedrock and submit Anthropic's
   use-case form if asked; Claude Sonnet 4.6 must answer in the playground.

Every command runs with these three variables exported. `AWS_PROFILE` is read by `aws` and
`terraform`; `NEUROLENS_AWS_PROFILE` is read only by the `infra/*.sh` scripts (they ignore
`.env`); `AWS_REGION` is the region. Use the same profile name for the first two:

```bash
export AWS_PROFILE=neurolens
export NEUROLENS_AWS_PROFILE=neurolens    # the same value: infra/*.sh read only this
export AWS_REGION=us-east-1
aws sts get-caller-identity      # check the account ID before anything else
```

## 1. The Terraform state bucket

`infra/setup.sh` creates the versioned, private state bucket when it is missing and writes
`backend.hcl`. These are the same commands to do it by hand, from the repo root:

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
STATE_BUCKET=neurolens-tfstate-$ACCOUNT_ID
aws s3api create-bucket --bucket "$STATE_BUCKET" --region us-east-1
aws s3api put-bucket-versioning --bucket "$STATE_BUCKET" --versioning-configuration Status=Enabled
aws s3api put-public-access-block --bucket "$STATE_BUCKET" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

Copy the backend example and put that bucket name in it (from the repo root):

```bash
cp infra/terraform/backend.hcl.example infra/terraform/backend.hcl
```

## 2. Create the resources

`infra/setup.sh` runs these build, init and apply steps. To do them by hand, from the repo root:

```bash
cp infra/terraform/terraform.tfvars.example infra/terraform/terraform.tfvars
infra/build_web_lambda.sh                       # the web function's zip: the first apply needs it
infra/build_ffprobe_layer.sh                    # the dispatcher's ffprobe layer: so does it
infra/build_report_layer.sh                     # the report function's Anthropic SDK layer: so does it
terraform -chdir=infra/terraform init -backend-config=backend.hcl
terraform -chdir=infra/terraform plan           # read it: only creates on an empty account
terraform -chdir=infra/terraform apply
terraform -chdir=infra/terraform apply          # again: should report "No changes"
```

`zones` are the private subnets for Aurora (at least two). `alert_email` receives the alarms and
early-access notices; AWS sends two confirmation emails after the first apply (one per topic,
`alarm.tf` and `web.tf`). Click both, or alarms and early-access notices are silently dropped.

`terraform -chdir=infra/terraform output` prints `region`, `bucket_name`, `gpu_endpoint` and the
database values. Put them in the git-ignored `.env` at the repo root as `NEUROLENS_AWS_REGION`,
`NEUROLENS_S3_BUCKET`, `NEUROLENS_GPU_ENDPOINT`,
`NEUROLENS_DB_CLUSTER_ARN`, `NEUROLENS_DB_SECRET_ARN` and `NEUROLENS_DB_NAME`
(see `.env.example`).

## 2a. Sign-in

Run section 2c first: sign-in writes to its tables.

After the first apply:

1. Add `google_redirect_uri` (from `terraform -chdir=infra/terraform output -raw google_redirect_uri`)
   as an authorised redirect URI in the Google OAuth client.
2. `infra/setup.sh` reads the Cognito app client's secret and stores it in Parameter Store as
   the SecureString `/neurolens/web/cognito_client_secret`, only when it is not there yet. It
   then runs `infra/deploy_web.sh` for you; run that again only after changing the page or
   samples. It also publishes the committed public samples (`data/videos/README.md`). Then open
   `sign_in_test_url` and sign in once with Google and once with email and password.

## 2b. The GPU image and endpoint

`infra/worker_image/Dockerfile` pins AWS's PyTorch base image by digest. To move to a newer one,
look up its digest (the deploy user may read that repository) and replace tag and digest there:

From the repo root:

```bash
aws ecr batch-get-image --region us-east-1 --registry-id 763104351884 \
  --repository-name pytorch-inference --image-ids imageTag=2.6.0-gpu-py312-cu124-ubuntu22.04-sagemaker \
  --query 'images[0].imageId.imageDigest' --output text
```

### Model files (once per deployment)

SageMaker copies `s3://<bucket>/model-data/` into `/opt/ml/model` whenever an instance starts, and
the container runs with `HF_HUB_OFFLINE=1`. The prefix holds:

- the HuggingFace cache under `models/hub/`: the TRIBE v2 model, its three encoders, and WhisperX's
  `faster-whisper-large-v3` speech model;
- WhisperX's torchaudio alignment weights under `models/torch/hub/checkpoints/`;
- the two Glasser brain maps under `data/hcp_mmp/`;
- nilearn's Destrieux atlas under `data/nilearn/` (the two annot files and nilearn's README).

The bucket exists only after the first apply, so the first apply must have `worker_image` empty
(no endpoint; `sagemaker.tf`). `infra/setup.sh` then checks the prefix: when it is empty it runs
`infra/upload_model_data.py`, which downloads the model repository from `config.json`, TRIBE v2's
three encoders and the Whisper model from HuggingFace with `HF_TOKEN`, fetches the alignment
weights and Destrieux files, fetches the maps with their SHA-256 checks, uploads the folder layout,
checks the object count and total size, and deletes its temporary folder. The HuggingFace token
needs the LLaMA licence accepted first (`README.md`, "Gated model access"). `setup.sh` refuses to
build the image and apply the endpoint while the prefix is empty: a failed SageMaker start bills
for about 30 minutes and cannot be cancelled.

To do it by hand, from the repo root after the first apply:

```bash
python infra/upload_model_data.py --bucket <bucket_name>
infra/build_image.sh            # zips HEAD, builds in CodeBuild, prints the image digest
# put the digest in infra/terraform/terraform.tfvars as worker_image = "sha256:..."
terraform -chdir=infra/terraform apply
```

`max_instances` defaults to 1. There is no CPU rehearsal: SageMaker refuses a CUDA image on a CPU
instance type, so the first checks run on the GPU with the real model.

The container's logs are in CloudWatch at `/aws/sagemaker/Endpoints/neurolens`; the build's at
`/aws/codebuild/neurolens-worker-image`.

## 2c. The database

`database.tf` creates Aurora PostgreSQL (Serverless v2, 0-1 ACU): users, credit and job state. It
pauses 5 minutes after its last use and wakes in about 15 s on the next call. Everything reaches it
through the RDS Data API; the password lives in Secrets Manager.

`infra/setup.sh` applies the schema for you. To do it by hand after the first apply, from the repo
root:

```bash
terraform -chdir=infra/terraform output -raw db_cluster_arn
terraform -chdir=infra/terraform output -raw db_secret_arn
terraform -chdir=infra/terraform output -raw db_name
# copy the three into .env as NEUROLENS_DB_CLUSTER_ARN, NEUROLENS_DB_SECRET_ARN, NEUROLENS_DB_NAME
python infra/apply_schema.py --backend data_api
python infra/check_aurora.py --backend data_api     # PASS expected
```

## 2d. Applying a saved plan

From the repo root:

```bash
terraform -chdir=infra/terraform plan -out=tfplan
terraform -chdir=infra/terraform apply tfplan
rm infra/terraform/tfplan
```

`terraform validate` checks the files are well formed and costs nothing.

## 3. Check it

Any directory (no repo files needed):

```bash
aws s3api get-bucket-lifecycle-configuration --bucket <bucket_name>
aws s3api get-bucket-cors --bucket <bucket_name>
aws sagemaker describe-endpoint --endpoint-name neurolens
aws application-autoscaling describe-scalable-targets --service-namespace sagemaker \
  --resource-ids endpoint/neurolens/variant/main
```

The dashboard is CloudWatch > Dashboards > neurolens (free for the first 3 dashboards in an account).

## 4. Add a browser address later

Add it to `allowed_origins` in `infra/terraform/terraform.tfvars` and run
`terraform -chdir=infra/terraform apply`.

## 5. A work session: start and stop

Nothing runs between sessions. The endpoint's normal state is `max_instances` (1 by default);
an upload starts a GPU by itself and it stops after `idle_stop_minutes` (7 idle minutes). From
the repo root:

```bash
infra/start_work.sh                     # normal state: max_instances, scale out on demand
infra/start_work.sh --keep-warm --hours 3   # one GPU now, kept 3 hours (AWS ends it)
infra/stop_work.sh                      # emergency pause; prints ALL STOPPED only when proven
```

`stop_work.sh` deletes any keep-warm action, sets the endpoint's scaling maximum and minimum to 0
and checks both, sets the desired count to 0 and waits until the endpoint shows 0.
A running job dies and is refunded. `start_work.sh` puts the maximum back to `max_instances` (1 by default).

## 5b. Demo routine

1. The day before: one full dry run.
2. 45-60 minutes before: `infra/start_work.sh --keep-warm --hours 3`.
3. One short warm-up job, so the first-job costs are paid before the audience arrives.
4. Demo. If it overruns, run step 2 again (it moves the end).
5. Straight after: `infra/stop_work.sh`, and wait for **ALL STOPPED**.

## 6. Tear down

`infra/teardown.sh` runs the guarded teardown: stop the GPU, destroy the stack, delete the
hand-made secrets and check for leftovers. The steps it cannot do are in
`docs/operating.md` ("Teardown").
