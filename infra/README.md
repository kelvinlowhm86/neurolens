# Infra

Operator scripts and the AWS resources they drive. AWS commands run with the profile from
`terraform/README.md` section 0 (`neurolens`, or the named profile for the live account).

## Every work session

- `Admin` button in the web header (visible when signed in with an admin email):
  - Opens the **Admin controls** modal, which dispenses 2-minute QR demo passes (1 pass per phone, auto-rotating every 0.5 s when scanned, with `?booth=<token>` preserved in the URL bar so copying the link to a laptop opens the same guest session) with `+5 passes`, `+10 passes` and `Stop passes` controls, plus a built-in `?` help cheat-sheet.
  - Controls GPU warmth (`Keep N GPUs` up to `max_instances` for `billing.BOOTH_GPU_WARM_HOURS`, or `0 (Auto-scale)`), grants or resets account credit by email (`billing.BOOTH_GRANT_MINUTES`), and shows live SGT per-day funnel stats.
  - Admin emails come from the deployment setting `NEUROLENS_ADMIN_EMAILS` (a comma-separated list, lowercased; empty means nobody can open the panel). On AWS it is Terraform's `admin_emails`; on a laptop it is in `.env`.
  - The pages' contact address comes from `NEUROLENS_CONTACT_EMAIL` on a laptop and Terraform's `contact_email` on AWS, where `deploy_web.sh` fills it in (empty shows the GitHub repo link).
- `start_work.sh`: starts processing: normal on-demand state, or `--keep-warm [--hours 1-5]` for one GPU now.
- `stop_work.sh`: emergency pause: no GPU may start or stay on; prints ALL STOPPED only when proven.
- `access_requests.py list --backend data_api`: lists or declines early-access requests by email. Grant credit in the web admin panel (+5 min is the invite credit).
- `check_aurora.py`: checks the database code on the real database with throwaway jobs and sign-ins.
- `aws_env.sh`: sourced by the AWS scripts; sets the profile and region from Terraform.

## Deploy

- `build_image.sh`: builds the SageMaker container image in CodeBuild and prints its digest.
- `build_web_lambda.sh`: builds `build/web_lambda.zip`, the code of the web Lambda functions.
- `build_ffprobe_layer.sh`: builds the dispatcher's static ffprobe Lambda layer.
- `build_report_layer.sh`: builds the report Lambda's Anthropic SDK layer.
- `deploy_web.sh`: ships the last commit as the website: code, page, public samples and a CloudFront cache clear.
- `apply_schema.py`: applies `migrations/*.sql` once each and records them.
- `terraform/`: creates all AWS resources; see `terraform/README.md`.

## Laptop

- `local_stack.py`: runs the whole product against local fakes: local PostgreSQL, moto S3/SQS and fake-mode analysis.

## One-off data makers

- `make_brain_mesh.py`: makes `static/brain/fsaverage6_pial.bin` and `head.bin`.
- `make_cjk_fonts.py`: makes the report PDF's Chinese/Japanese/Korean fallback fonts.
- `make_model_data.sh`: fills `s3://<bucket>/model-data/` once for SageMaker.
- `make_sample.py`: adds or replaces one dashboard sample or stored comparison from finished jobs.

## Folders

- `terraform/`: Terraform for the bucket, queue, database, website, Lambdas and SageMaker endpoint.
- `iam/`: the deploy-user, web and role-boundary policies pasted into the AWS console; see `iam/README.md`.
- `migrations/`: the SQL schema migrations, applied by `apply_schema.py`.
- `worker_image/`: the SageMaker container's Dockerfile and CodeBuild spec.
