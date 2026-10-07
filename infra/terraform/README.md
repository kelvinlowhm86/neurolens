# NeuroLens: AWS resources (Terraform)

Creates everything NeuroLens needs on AWS: the S3 bucket and SQS queue (M1), and the network, roles,
NAT instance, GPU worker group and alarm (M2a). Terraform keeps a record of what it created, so
running `apply` twice is safe (the second run reports no changes) and `destroy` removes everything
it created. The region is one setting (`region` in `terraform.tfvars`, default `us-east-1`).

**What it creates**

- One S3 bucket: public access blocked, encrypted, HTTPS-only, ACLs disabled, browser upload (CORS)
  allowed from `allowed_origins`, and prefix-scoped expiry (`uploads/` after 2 days, rounded to midnight UTC,
  `results/` after 30 days, `code/`, `models/` and `experiments/` never).
- One SQS queue (visibility timeout 120 s, kept extended by the worker's heartbeat) and the wiring that sends a message to it whenever a file
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
- Two alarms on one SNS email topic: the **idle alarm** ends a worker itself (AWS sets the group to 0)
  when a worker has been in service for 90 minutes with no job picked up or finished; a second alarm
  only emails `alert_email` when a worker has been running for 3 hours.

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
shows the command that lists them), and `alert_email` is where both alarms write. AWS then
sends a confirmation email: click its link or the alarm cannot reach you.

`terraform output` prints `region`, `bucket_name` and `queue_url`. Put them in the gitignored `.env`
at the repo root as `NEUROLENS_AWS_REGION`, `NEUROLENS_S3_BUCKET` and `NEUROLENS_SQS_QUEUE_URL`
(see `.env.example`). The committed
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
`["t3.large"]` is the cheap CPU rehearsal that tests the wiring without a GPU (no weight sync);
`worker_fake_job_seconds` then makes each fake job take that long, so it can be interrupted.

The worker's `config.json` is the committed one with the machine's paths filled in: change the root
`config.json` and apply, and new workers get it (running ones keep theirs).

## 2d. How many workers run (M2b)

Only AWS decides; the worker never ends its own machine.

- **Scale out:** a job waiting in the queue for a minute adds a worker (two when two or more wait),
  up to the group's max. A new worker counts as "starting" for 15 minutes
  (`scale_out_warmup_seconds`), so one waiting job never launches a second worker while the first boots.
- **Scale in:** when the queue has had no waiting or running job for 15 minutes, the group goes to 0.
  A worker holding a job protects its own machine from scale-in until the job ends, because the
  queue's numbers reach CloudWatch a few minutes late.
- **Circuit breaker:** if a worker is in service but nothing has moved in the queue for 90 minutes
  (the idle alarm), AWS ends it and you get an email. A small Lambda (`infra/lambda/breaker.py`)
  runs when that alarm fires and again every 5 minutes: while that alarm is on and no warm hold is running, it removes the
  workers' protection and sets the group's max to 0, so a broken worker is not replaced over and
  over. Nothing launches again until the next `start_work.sh`. This caps a failure at about
  1.5-2 hours of GPU (about $3-4). If the Lambda itself fails, a second alarm emails you.
- **Warm hold:** `start_work.sh --worker --hours N` keeps one worker for N hours (1 to 4) even with no
  jobs: the group's minimum is 1, which scale-in, the idle alarm and the breaker all respect. An
  AWS-side timer (`neurolens-warm-hold-end`) sets the minimum back to 0 at the end, even if your
  laptop is off; then the worker goes once the queue has been empty for 15 minutes.

To change the region: edit `region`, `zones` and `build_extra_zones` together, make sure GPU quota
exists in the new region, then rebuild the image there. Every script in `infra/` reads the region
from `aws_env.sh`, which asks Terraform only (never a copy such as `.env`). Region-move checklist: M4 §1.

## 2e. The database and the settlement Lambdas (M3a)

`database.tf` creates Aurora PostgreSQL (Serverless v2, 0-2 ACU): users, credit and job state. It
pauses after 10 idle minutes (0 ACU, storage only) and wakes in about 15 s on the next call. Nothing
connects to it over the network: the app, the worker and the Lambdas use the RDS Data API (HTTPS,
checked by IAM), and the password lives only in Secrets Manager, created and rotated by RDS.
`lambdas.tf` adds two Lambdas: the **dead-letter handler** settles or refunds every job that failed
twice, and the **reaper** settles or refunds stuck jobs every 5 minutes, but only during a session:
`start_work.sh` switches its schedule on and `stop_work.sh` runs it once and switches it off, so
Aurora can pause between sessions. Alarms email you if Aurora stays awake for 6 hours or a Lambda fails.

After the first apply (from the repo root):

```bash
terraform -chdir=infra/terraform output db_cluster_arn db_secret_arn db_name
# copy the three into .env as NEUROLENS_DB_CLUSTER_ARN, NEUROLENS_DB_SECRET_ARN, NEUROLENS_DB_NAME
python infra/apply_schema.py --backend data_api    # creates the tables; a second run changes nothing
python infra/db_smoke.py --backend data_api        # one throwaway job through billing: PASS expected
```

Cost: about 6 cents an hour while awake (0.5 ACU), $0.40 a month for the secret, storage cents.

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
aws s3 cp some.mp4 s3://<bucket_name>/uploads/test.mp4
aws sqs receive-message --queue-url <queue_url>                       # one message within seconds
# From M3a a worker skips such a hand upload (no job in the database): real jobs come from the page.
```

## 4. Add a browser address later (M3)

Add it to `allowed_origins` in `terraform.tfvars` and run `terraform apply`. Terraform owns the whole
CORS document, so the list is the single place.

## 5. A work session: start and stop (M2a)

Nothing runs between sessions. From the repo root:

```bash
infra/start_work.sh                     # NAT instance on; an upload now starts a worker by itself
                                        # (refuses unless every alarm and the breaker are on)
infra/start_work.sh --max 2             # the same with up to two workers (Experiment 2 only)
infra/start_work.sh --worker --hours 3  # warm hold: one GPU worker now, kept 3 hours (billed from now)
infra/deploy_code.sh                    # after a commit: ship new code (workers pick it up on restart)
infra/restart_workers.sh                # restart the worker service on running workers, show the revision (refuses while one has a job; --now forces)
infra/connect_worker.sh                 # open a shell on the worker (Session Manager), after a warning
infra/stop_work.sh                      # END EVERY SESSION WITH THIS
```

A new worker takes about 6.5 minutes to be ready (software sync, then the model loads), plus 1-3
minutes for the queue's metric to start it after an upload. Restarting the worker service hands a
running job back to the queue at once; another attempt finishes it.

`stop_work.sh` ends a warm hold, removes the workers' scale-in protection (a running job is handed
back and runs next session), sets the worker group to zero (even while it is still waiting for a
GPU and has no machine yet), waits for the workers to go, runs the reaper once and switches its
schedule off, stops the NAT instance and then checks (including that the dead-letter queue is
empty: a message left there keeps waking Aurora). It prints
**ALL STOPPED** only when every check succeeded. Anything it could not prove prints
**NOT CONFIRMED** with the reason: read it and act on it. It needs the region from Terraform; if
Terraform cannot answer, it stops nothing and prints the `terraform init` command to fix it. An
image-build machine is listed, not stopped, with the command to end it if no build is running.

**Manual work on a worker.** Scale-in ends a worker 15 minutes after the queue empties, and none of
the rules can see work done by hand (Session Manager). Start a warm hold first
(`start_work.sh --worker --hours N`); run it again to extend. During a hold of more than 90 minutes
without jobs, the idle alarm still emails, but changes nothing.

**After a breaker email.** The group is at max 0. Look at the worker's log first (the email names no
cause: a crash loop, broken code, a frozen job or a stopped NAT instance), fix it, then
`start_work.sh` again. `start_work.sh` and `stop_work.sh` print a note when they find the group at
max 0 with the NAT instance still running.

## 5b. Demo routine (M2b)

1. The day before (the week before for the TA demo): one full dry run of steps 2-5.
2. 45-60 minutes before: `infra/start_work.sh --worker --hours 3` (demo length, early start and a
   margin). Starting early leaves time to fall back to the no-AWS backup (M4) if no GPU is free.
3. Wait for "Worker ready" (`infra/connect_worker.sh`, or the result of step 4).
4. One warm-up job: upload a short sample clip, so the first-job costs are paid before the audience
   arrives and the whole chain is proven that day.
5. Demo. If it overruns the hold, run step 2 again (it moves the end).
6. Straight after: `infra/stop_work.sh`, and wait for **ALL STOPPED**.

Cost: about 1.5-2 hours of GPU, roughly $3-4.

## 6. Tear down

Stop the session first (section 5). `terraform destroy` also deletes the database, its backups and
its secret, with no final snapshot: export anything you need first. The worker image and its disk snapshot are not Terraform's: deregister the
image and delete its snapshot by hand. S3 refuses to delete a non-empty bucket, so empty it first, once results and experiment files are no
longer needed:

```bash
aws s3 rm s3://<bucket_name> --recursive
terraform destroy
```

The state bucket stays (a few kilobytes). Remove it by hand at the very end of the project if you want.
