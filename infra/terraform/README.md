# NeuroLens: AWS resources (Terraform)

Creates everything NeuroLens needs on AWS: the S3 bucket and SQS queue (M1), and the network, roles,
NAT instance, GPU worker group and alarm (M2a). Terraform keeps a record of what it created, so
running `apply` twice is safe (the second run reports no changes) and `destroy` removes everything
it created. The region is one setting (`region` in `terraform.tfvars`, default `us-east-1`).

**What it creates**

- One S3 bucket: public access blocked, encrypted, HTTPS-only, ACLs disabled, browser upload (CORS)
  allowed from `allowed_origins`, and prefix-scoped expiry (`uploads/` and `status/` after 2 days, rounded to midnight UTC,
  `results/` after 30 days, `code/`, `models/` and `experiments/` never).
- One SQS queue (visibility timeout 1800 s) and the wiring that sends a message to it whenever a file
  lands under `uploads/`. A second queue (`neurolens-jobs-dlq`) receives a job that failed twice.
- A network (VPC): public subnets (one holds the NAT instance, the others only host image builds) and
  private subnets for the GPU workers, which have no inbound access from the internet. A free S3
  gateway endpoint keeps S3 traffic off the NAT.
- A NAT instance (`t4g.micro`): the workers' only way out to the internet (HuggingFace, SQS). It is
  stopped between sessions.
- Three roles (image build, worker, NAT), each limited to what it needs and capped by a permission
  boundary.
- A launch template and Auto Scaling group `neurolens-workers` (0 machines until you start a
  session), created only once `worker_ami_id` is set.
- An SNS topic and an alarm that emails `alert_email` when a GPU worker has been running 3 hours.

**Cost:** with everything stopped, the running cost is the stored image and disks and the S3 files,
roughly $3 a month. Machines bill only while a session is open: the NAT about 0.84 cents an hour, a
GPU worker $1.86 an hour (g6e.xlarge; $2.24 for the g6e.2xlarge fallback). A worker ends itself after
30 idle minutes, but always end a session with `infra/stop_work.sh` (section 5).

## 0. Before you start (once)

1. Terraform 1.10 or newer. Homebrew's plain `terraform` formula is gone, so use HashiCorp's tap:
   `brew tap hashicorp/tap && brew install hashicorp/tap/terraform`, then `terraform version`.
2. The AWS command-line tool (`aws --version`).
3. An AWS profile called `neurolens` (a named set of login keys on your Mac, in `~/.aws`). Run
   `aws configure --profile neurolens` in your own terminal and paste the keys there. **Never paste
   keys into chat, git or a Docker image.**
4. Python 3.12 environment for the repo (see the root `AGENTS.md`) and `git` (the scripts in `infra/`
   find the repo from it).
5. Confirm the account-wide $40/month AWS Budget (alerts at 50%, 80%, 100%) still exists: AWS console,
   Billing, Budgets.

Every command below runs with the project's profile, never another project's:

```bash
export AWS_PROFILE=neurolens
aws sts get-caller-identity      # check the account ID is the one you expect BEFORE anything else
```

## 1. Create the state bucket (once, by hand)

Terraform's record of what exists must not live on one laptop, or teammates will clash. It lives in a
small separate bucket with versioning. `terraform destroy` never touches it.

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
STATE_BUCKET=neurolens-tfstate-$ACCOUNT_ID

aws s3api create-bucket --bucket "$STATE_BUCKET" --region us-east-1
aws s3api put-bucket-versioning --bucket "$STATE_BUCKET" \
  --versioning-configuration Status=Enabled
aws s3api put-public-access-block --bucket "$STATE_BUCKET" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

Then copy `backend.hcl.example` to `backend.hcl` and put that bucket name in it.

## 2. Create the resources

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars    # bucket_name, region and zones, alert_email
terraform init -backend-config=backend.hcl
terraform plan                                  # read it: only creates, nothing destroyed
terraform apply
terraform apply                                 # again: should report "No changes"
```

In `terraform.tfvars`, `zones` must be zones of `region` that offer `g6e.xlarge` (the example file
shows the command that lists them), and `alert_email` is where the 3-hour alarm writes. AWS then
sends a confirmation email: click its link or the alarm cannot reach you.

`terraform output` prints `bucket_name` and `queue_url`. Put them in the gitignored `.env` at the
repo root as `NEUROLENS_S3_BUCKET` and `NEUROLENS_SQS_QUEUE_URL` (see `.env.example`). The committed
`config.json` holds no account-specific values, and access keys never go in the project at all.

## 2b. The GPU worker group (M2a)

The worker group needs the software image, which is built after the first apply:

1. Run `infra/deploy_code.sh` (ships the last commit to S3 as `code/latest.zip`).
2. Run `infra/build_ami.sh <clip.mp4>` (about an hour on a GPU, so run it in your own Terminal app, not
   in a chat tool that kills long commands). It prints the image ID (`ami-...`). It always terminates its
   machine when it ends, and shuts itself down after 4 hours at the latest.
3. Put the ID in `terraform.tfvars` as `worker_ami_id` and apply again. This creates the launch
   template and the Auto Scaling group, at zero machines.

`worker_instance_types` lists the GPU types in order of preference (default `g6e.xlarge`, then
`g6e.2xlarge` when the first is sold out in every zone). `worker_fake_inference = true` with
`["t3.large"]` is the cheap CPU rehearsal that tests the wiring without a GPU.

To change the region: edit `region`, `zones` and `build_extra_zones` together, make sure GPU quota
exists in the new region, then rebuild the image there. Every script in `infra/` reads the region
from `aws_env.sh`, which asks Terraform; set `NEUROLENS_AWS_REGION` to override it.

## 2c. Applying a saved plan (when you want to review exactly what will change)

Some tools refuse to let an assistant run `terraform apply`. The safe pattern is: write the plan to
a file, read it, then apply that exact file yourself.

```bash
terraform plan -out=tfplan        # read the output: what is created, changed, destroyed
terraform apply tfplan            # in Claude Code, type:  ! terraform apply tfplan   (from infra/terraform)
rm tfplan                         # delete the plan file afterwards
```

`terraform validate` checks the files are well formed and costs nothing.

## 3. Check it

```bash
aws s3api get-bucket-lifecycle-configuration --bucket <bucket_name>   # 2 days, 2 days, 30 days
aws s3api get-bucket-cors --bucket <bucket_name>
aws s3 cp some.mp4 s3://<bucket_name>/uploads/placeholder-user/test.mp4
aws sqs receive-message --queue-url <queue_url>                       # one message within seconds
```

## 4. Add a browser address later (M3)

Add it to `allowed_origins` in `terraform.tfvars` and run `terraform apply`. Terraform owns the whole
CORS document, so the list is the single place.

## 5. A work session: start and stop (M2a)

Nothing runs between sessions. From the repo root:

```bash
infra/start_work.sh              # NAT instance on; the worker group may run one machine, none yet
infra/start_work.sh --worker     # also starts one GPU worker (billed from now)
infra/deploy_code.sh             # after a commit: ship new code (workers pick it up on restart)
infra/restart_workers.sh         # restart the worker service on running workers, show the revision
infra/stop_work.sh               # END EVERY SESSION WITH THIS
```

A new worker takes about 6 minutes to be ready (software sync, then the model loads). The first job
on a fresh worker adds about 6 minutes of one-time warm-up. `stop_work.sh` sets the group to zero,
waits for the workers to go, stops the NAT instance and then lists any neurolens machine still
running: read that last line, it should say there is none.

`stop_work.sh` needs the region, which it reads from Terraform's output. If Terraform cannot be
reached it stops with instructions rather than guessing; run it as
`NEUROLENS_AWS_REGION=us-east-1 infra/stop_work.sh` in that case.

## 6. Tear down

Stop the session first (section 5). The worker image and its disk snapshot are not Terraform's: deregister the
image and delete its snapshot by hand. S3 refuses to delete a non-empty bucket, so empty it first, once results and experiment files are no
longer needed:

```bash
aws s3 rm s3://<bucket_name> --recursive
terraform destroy
```

The state bucket stays (a few kilobytes). Remove it by hand at the very end of the project if you want.
