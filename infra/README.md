# Infra

Operator scripts and the AWS resources they drive. AWS commands run with the `neurolens` profile.

## Every work session

- `start_work.sh` — starts processing: normal on-demand state, or `--keep-warm [--hours 1-4]` for one GPU now.
- `stop_work.sh` — emergency pause: no GPU may start or stay on; prints ALL STOPPED only when proven.
- `grant_credit.py` — gives a user credit by email (the email is asked for at a prompt).
- `access_requests.py` — lists or declines early-access requests by email.
- `check_aurora.py` — checks the database code on the real database with throwaway jobs and sign-ins.
- `aws_env.sh` — sourced by the AWS scripts; sets the profile and region from Terraform.

## Deploy

- `build_image.sh` — builds the SageMaker container image in CodeBuild and prints its digest.
- `build_web_lambda.sh` — builds `build/web_lambda.zip`, the code of the web Lambda functions.
- `build_ffprobe_layer.sh` — builds the dispatcher's static ffprobe Lambda layer.
- `build_report_layer.sh` — builds the report Lambda's Anthropic SDK layer.
- `deploy_web.sh` — ships the last commit as the website: code, page, public samples and a CloudFront cache clear.
- `apply_schema.py` — applies `migrations/*.sql` once each and records them.
- `terraform/` — creates all AWS resources; see `terraform/README.md`.

## Laptop

- `local_stack.py` — runs the whole product against local fakes: local PostgreSQL, moto S3/SQS and fake-mode analysis.

## One-off data makers

- `make_brain_mesh.py` — makes `static/brain/fsaverage6_pial.bin` and `head.bin`.
- `make_cjk_fonts.py` — makes the report PDF's Chinese/Japanese/Korean fallback fonts.
- `make_model_data.sh` — fills `s3://<bucket>/model-data/` once for SageMaker.
- `make_sample.py` — adds or replaces one dashboard sample or stored comparison from finished jobs.

## Folders

- `terraform/` — Terraform for the bucket, queue, database, website, Lambdas and SageMaker endpoint.
- `iam/` — the deploy-user, web and role-boundary policies pasted into the AWS console; see `iam/README.md`.
- `migrations/` — the SQL schema migrations, applied by `apply_schema.py`.
- `worker_image/` — the SageMaker container's Dockerfile and CodeBuild spec.
