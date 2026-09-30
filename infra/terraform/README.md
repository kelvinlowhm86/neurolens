# NeuroLens: AWS resources for M1 (Terraform)

Creates the S3 bucket and SQS queue that M1 needs, in `us-east-1`. Terraform keeps a record of what it
created, so running `apply` twice is safe (the second run reports no changes) and `destroy` removes
everything it created.

**What it creates**

- One S3 bucket: public access blocked, encrypted, HTTPS-only, ACLs disabled, browser upload (CORS)
  allowed from `allowed_origins`, and prefix-scoped expiry (`uploads/` and `status/` after 2 days, rounded to midnight UTC,
  `results/` after 30 days, `code/` and `experiments/` never).
- One SQS queue (visibility timeout 900 s) and the wiring that sends a message to it whenever a file
  lands under `uploads/`.

**Cost:** a few megabytes in S3 and a few thousand queue requests are pennies, well inside the AWS
free allowance. The worker's long polling (one request per 20 s) is about 4,300 requests a day.
Still, tear it down (below) when you are not using it.

## 0. Before you start (once)

1. Terraform 1.10 or newer. Homebrew's plain `terraform` formula is gone, so use HashiCorp's tap:
   `brew tap hashicorp/tap && brew install hashicorp/tap/terraform`, then `terraform version`.
2. The AWS command-line tool (`aws --version`).
3. An AWS profile called `neurolens` (a named set of login keys on your Mac, in `~/.aws`). Run
   `aws configure --profile neurolens` in your own terminal and paste the keys there. **Never paste
   keys into chat, git or a Docker image.**
4. Confirm the account-wide $40/month AWS Budget (alerts at 50%, 80%, 100%) still exists: AWS console,
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

## 2. Create the M1 resources

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars    # choose a globally unique bucket_name
terraform init -backend-config=backend.hcl
terraform plan                                  # read it: only creates, nothing destroyed
terraform apply
terraform apply                                 # again: should report "No changes"
```

`terraform output` prints `bucket_name` and `queue_url`. Put them in the `aws` block of the
gitignored `config.json` (`s3_bucket`, `sqs_queue_url`). Only those identifiers go in that file,
never access keys.

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

## 5. Tear down

S3 refuses to delete a non-empty bucket, so empty it first, once results and experiment files are no
longer needed:

```bash
aws s3 rm s3://<bucket_name> --recursive
terraform destroy
```

The state bucket stays (a few kilobytes). Remove it by hand at the very end of the project if you want.
