# NeuroLens: AWS resources (Terraform)

Creates everything NeuroLens needs on AWS: the S3 bucket and SQS queue (M1), the network and
database (M2a/M3a), the website and sign-in (M3b), and from M3e a SageMaker asynchronous GPU
endpoint, its image repository and build, the job Lambdas and their alarms. Terraform keeps a
record of what it created, so running `apply` twice is safe (the second run reports no changes)
and `destroy` removes everything it created. The region is one setting (`region` in
`terraform.tfvars`, default `us-east-1`).

**What it creates**

- One S3 bucket: public access blocked, encrypted, HTTPS-only, ACLs disabled, browser upload
  (CORS) from `allowed_origins`, and prefix-scoped expiry (`uploads/` and `results/` after 30 days,
  `dataset/` and `academic/` after 1095, `sagemaker-output/` after 1, `build/` after 7,
  `experiments/` never).
- One SQS queue (visibility 1080 s, a 15 s delivery delay) and the wiring that sends a message
  whenever a file lands under `uploads/`. A second queue (`neurolens-jobs-dlq`) receives a job that
  failed twice.
- A VPC with private subnets for Aurora only. No NAT Gateway, no internet gateway, no S3 endpoint:
  Aurora is reached through the Data API, and S3 through its public HTTPS endpoint.
- Roles: the SageMaker execution role, the CodeBuild role, the dispatcher, the SageMaker error
  handler, the GPU guard, the reaper, the dead-letter handler and the web function. Each is
  limited to what it needs and capped by a permission boundary.
- The ECR repository `neurolens-worker` (last 3 images) and the CodeBuild project
  `neurolens-worker-image` (privileged Docker, 60-minute timeout).
- The SageMaker model, endpoint configuration (instance pools `ml.g6e.xlarge`,
  `ml.g6.2xlarge`, `ml.g5.2xlarge`, `ml.g6e.2xlarge`, in that order) and the endpoint `neurolens`; its async error topic `neurolens-gpu-errors`.
- Application Auto Scaling: min 0, max `max_instances`; a waiting request starts a GPU, seven
  idle minutes stop it.
- Lambda functions: the dispatcher (jobs queue, with the ffprobe layer), the SageMaker error
  handler (error topic), the GPU guard (every 10 minutes), the dead-letter handler, the reaper
  (every 3 hours) and the report function (with the Anthropic SDK layer).
- Alarms on one SNS email topic: the GPU guard stopped a GPU, the GPU guard has not run, Aurora has
  been awake for much of 6 hours, and one error alarm per Lambda.

The endpoint only exists once `worker_image` is set: build the image first (§2b).

**Cost:** with everything stopped, the running cost is the stored image, the ECR repository,
Aurora storage and the S3 files, a few dollars a month (the reaper waking Aurora every 3 hours is
about $3). A GPU bills only while it runs: about $1.86/h for `g6e.xlarge` ($2.24/h for the
`g6.2xlarge` fallback), and it stops itself 7 idle minutes after the last job. A deploy starts one
instance for about 7 minutes (roughly $0.50–0.80).

**From an empty account:** §0, §1, §2, §2f, §2b; then §5 for each work session and §6 to tear
down.

## 0. Before you start (once)

1. Terraform 1.10 or newer: `brew tap hashicorp/tap && brew install hashicorp/tap/terraform`.
2. The AWS command-line tool (`aws --version`).
3. A named AWS profile `neurolens` in `~/.aws` (`aws configure --profile neurolens`). **Never
   paste keys into chat, git or a Docker image.**
4. Python 3.12 for the repo (`AGENTS.md`) and `git`.
5. Quotas and accounts (console; quota requests can take days):
   - SageMaker endpoint quota for `ml.g6e.xlarge` (one instance is enough; the account's GPU quota
     is 2).
   - Lambda concurrency: if the account's limit is well below 1,000, request 1,000 (free).
   - A HuggingFace read token for the model weights (it goes in the repo's git-ignored `.env`
     as `HF_TOKEN`, see `.env.example`) and a Google OAuth client for sign-in.
6. Secrets in Parameter Store (`SecureString`, never in git or Terraform files):
   `/neurolens/terraform/google_client_secret` and
   `/neurolens/web/flask_secret_key` (at least 32 random bytes).
7. Confirm the $40/month AWS Budget alerts still exist (Billing, Budgets).
8. In the console, with the region set to us-east-1, open Bedrock and submit Anthropic's
   use-case form if asked; Claude Sonnet 4.6 must answer in the playground.

Every command runs with this profile:

```bash
export AWS_PROFILE=neurolens
aws sts get-caller-identity      # check the account ID before anything else
```

## 1. Create the state bucket (once, by hand)

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
STATE_BUCKET=neurolens-tfstate-$ACCOUNT_ID
aws s3api create-bucket --bucket "$STATE_BUCKET" --region us-east-1
aws s3api put-bucket-versioning --bucket "$STATE_BUCKET" --versioning-configuration Status=Enabled
aws s3api put-public-access-block --bucket "$STATE_BUCKET" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

Copy `backend.hcl.example` to `backend.hcl` and put that bucket name in it.

## 2. Create the resources

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars    # bucket_name, region, zones, alert_email, auth, google
../build_web_lambda.sh                          # the web function's zip: the first apply needs it
../build_ffprobe_layer.sh                       # the dispatcher's ffprobe layer: so does it
../build_report_layer.sh                        # the report function's Anthropic SDK layer: so does it
terraform init -backend-config=backend.hcl
terraform plan                                  # read it: only creates on an empty account
terraform apply
terraform apply                                 # again: should report "No changes"
```

`zones` are the private subnets for Aurora (at least two). `alert_email` receives the alarms;
click the confirmation email AWS sends or no alarm can reach you.

`terraform output` prints `region`, `bucket_name`, `gpu_endpoint` and the database
values. Put them in the git-ignored `.env` at the repo root as `NEUROLENS_AWS_REGION`,
`NEUROLENS_S3_BUCKET`, `NEUROLENS_GPU_ENDPOINT`,
`NEUROLENS_DB_CLUSTER_ARN`, `NEUROLENS_DB_SECRET_ARN` and `NEUROLENS_DB_NAME`
(see `.env.example`).

## 2f. Sign-in (M3b)

After the first apply:

1. Add `google_redirect_uri` as an authorised redirect URI in the Google OAuth client.
2. Copy the Cognito app client's secret into `/neurolens/web/cognito_client_secret`.
3. `infra/deploy_web.sh` publishes the site; open `sign_in_test_url` and sign in once with Google
   and once with email and password.

## 2b. The GPU image and endpoint (M3e)

`infra/worker_image/Dockerfile` pins AWS's PyTorch base image by digest. To move to a newer one,
look up its digest (the deploy user may read that repository) and replace tag and digest there:

```bash
aws ecr batch-get-image --region us-east-1 --registry-id 763104351884 \
  --repository-name pytorch-inference --image-ids imageTag=2.6.0-gpu-py312-cu124-ubuntu22.04-sagemaker \
  --query 'images[0].imageId.imageDigest' --output text
```

Then, from the repo root:

```bash
infra/make_model_data.sh        # once: copies models/ to model-data/ without blobs/, checks counts
infra/build_image.sh            # zips HEAD, builds in CodeBuild, prints the image digest
# put the digest in terraform.tfvars as worker_image = "sha256:..."
terraform -chdir=infra/terraform apply
```

`max_instances` stays 1. There is no CPU rehearsal: SageMaker refuses a CUDA image on a CPU
instance type, so the first checks run on the GPU with the real model.

The container's logs are in CloudWatch at `/aws/sagemaker/Endpoints/neurolens`; the build's at
`/aws/codebuild/neurolens-worker-image`.

## 2e. The database

`database.tf` creates Aurora PostgreSQL (Serverless v2, 0–1 ACU): users, credit and job state. It
pauses 5 minutes after its last use and wakes in about 15 s on the next call. Everything reaches it
through the RDS Data API; the password lives in Secrets Manager.

After the first apply:

```bash
terraform -chdir=infra/terraform output db_cluster_arn db_secret_arn db_name
# copy the three into .env as NEUROLENS_DB_CLUSTER_ARN, NEUROLENS_DB_SECRET_ARN, NEUROLENS_DB_NAME
python infra/apply_schema.py --backend data_api
python infra/check_aurora.py --backend data_api     # PASS expected
```

## 2c. Applying a saved plan

```bash
cd infra/terraform
terraform plan -out=tfplan
terraform apply tfplan
rm tfplan
```

`terraform validate` checks the files are well formed and costs nothing.

## 3. Check it

```bash
aws s3api get-bucket-lifecycle-configuration --bucket <bucket_name>
aws s3api get-bucket-cors --bucket <bucket_name>
aws sagemaker describe-endpoint --endpoint-name neurolens
aws application-autoscaling describe-scalable-targets --service-namespace sagemaker \
  --resource-ids endpoint/neurolens/variant/main
```

The dashboard is CloudWatch > Dashboards > neurolens (free for the first 3 dashboards in an account).

## 4. Add a browser address later

Add it to `allowed_origins` in `terraform.tfvars` and `terraform apply`.

## 5. A work session: start and stop

Nothing runs between sessions. The endpoint's normal state is max 1; an upload starts a GPU by
itself and it stops 7 idle minutes after the last job.

```bash
infra/start_work.sh                     # normal state: max 1, scale out on demand
infra/start_work.sh --keep-warm --hours 3   # one GPU now, kept 3 hours (AWS ends it)
infra/stop_work.sh                      # emergency pause; prints ALL STOPPED only when proven
```

`stop_work.sh` deletes any keep-warm action, sets the endpoint's scaling maximum and minimum to 0
and checks both, sets the desired count to 0 and waits until the endpoint shows 0.
A running job dies and is refunded. `start_work.sh` puts the maximum back to 1.

## 5b. Demo routine

1. The day before (a week before for the TA demo): one full dry run.
2. 45–60 minutes before: `infra/start_work.sh --keep-warm --hours 3`.
3. One short warm-up job, so the first-job costs are paid before the audience arrives.
4. Demo. If it overruns, run step 2 again (it moves the end).
5. Straight after: `infra/stop_work.sh`, and wait for **ALL STOPPED**.

## 6. Tear down

`terraform destroy` deletes the database, its backups, its secret, the endpoint, the Lambdas and
the queue. Empty the bucket first (S3 refuses to delete a non-empty bucket), and delete the images
in ECR by hand:

```bash
aws s3 rm s3://<bucket_name> --recursive
terraform destroy
```

The state bucket stays (a few kilobytes). Remove it by hand at the very end, with the Parameter
Store secrets and the Google OAuth client.
