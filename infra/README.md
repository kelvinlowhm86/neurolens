# Infra

Operator scripts and the AWS resources they drive. AWS commands run with the profile from
`terraform/README.md` section 0 (your named AWS profile, `neurolens` in the examples).

## Day to day

- `Admin` button in the web header (visible when signed in with an admin email):
  - Opens the **Admin controls** modal, which dispenses 2-minute QR demo passes (1 pass per phone, rotating to a new code once a phone claims it, with `?visitor=<token>` preserved in the URL bar so copying the link to a laptop opens the same guest session) with `+5 passes`, `+10 passes` and `Stop passes` controls, plus a built-in `?` help cheat-sheet.
  - Controls GPU warmth (`Keep N GPUs` up to `max_instances` for `visitor.gpu_warm_hours`, or `0 (Auto-scale)`), grants or resets account credit by email (`visitor.grant_minutes`), and shows per-day sign-up and usage stats (Singapore time).
  - Admin emails come from the deployment setting `NEUROLENS_ADMIN_EMAILS` (a comma-separated list, lowercased; empty means nobody can open the panel). On AWS it is Terraform's `admin_emails`; on a laptop it is in `.env`.
  - The pages' contact address comes from `NEUROLENS_CONTACT_EMAIL` on a laptop and Terraform's `contact_email` on AWS, where `deploy_web.sh` fills it in (empty shows the GitHub repo link).
- `start_work.sh`: starts processing: normal on-demand state, or `--keep-warm [--hours 1-5]` for one GPU now.
- `stop_work.sh`: emergency pause: no GPU may start or stay on; prints ALL STOPPED only when proven.
- `access_requests.py list --backend data_api`: lists waiting early-access requests; `access_requests.py decline --backend data_api` declines one by email. Grant credit in the web admin panel (+5 min is the invite credit).
- `check_aurora.py`: checks the database code on the real database with temporary test jobs and sign-ins.
- `aws_env.sh`: sourced by the AWS scripts; sets the profile and region from Terraform.

## Deploy

- `setup.sh`: first deploy in one command after the human-only steps: checks its required
  Terraform variables, the model token and the secrets before changing anything, then creates the
  state bucket and backend, builds the
  artifacts, applies, uploads the model files when their prefix is empty, applies the schema,
  stores the Cognito client secret when missing, deploys the site, builds the image and applies
  again; a second run skips finished work.
- `upload_model_data.py`: fills `s3://<bucket>/model-data/` from HuggingFace, torchaudio, nilearn
  and the HCP maps for a new deployment; `setup.sh` runs it when the prefix is empty.
- `build_image.sh`: builds the SageMaker container image in CodeBuild and prints its digest.
- `build_web_lambda.sh`: builds `build/web_lambda.zip`, the code of the web Lambda functions.
- `build_ffprobe_layer.sh`: builds the dispatcher's static ffprobe Lambda layer.
- `build_report_layer.sh`: builds the report Lambda's Anthropic SDK layer.
- `deploy_web.sh`: ships the last commit as the website: code, page, public samples and a CloudFront cache clear.
- `apply_schema.py`: applies `migrations/*.sql` once each and records them.
- `terraform/`: creates all AWS resources; see `terraform/README.md`.
- `teardown.sh`: guarded teardown: stops the GPU, destroys the stack, deletes the secrets you
  create by hand in Parameter Store and checks for leftovers; it never deletes the state bucket or
  the deploy user (see
  `docs/operating.md`, "Teardown").

## Laptop

- `local_stack.py`: runs the whole product against local fakes: local PostgreSQL, moto S3/SQS and fake-mode analysis.

## One-off data makers

- `make_brain_mesh.py`: makes `static/brain/fsaverage6_pial.bin` and `head.bin`.
- `make_cjk_fonts.py`: makes the report PDF's Chinese/Japanese/Korean fallback fonts.
- `make_sample.py`: adds or replaces one dashboard sample or stored comparison from finished jobs.

## Folders

- `terraform/`: Terraform for the bucket, queue, database, website, Lambdas and SageMaker endpoint.
- `iam/`: the deploy-user, web and role-boundary policies pasted into the AWS console; see `iam/README.md`.
- `migrations/`: the SQL schema migrations, applied by `apply_schema.py`.
- `worker_image/`: the SageMaker container's Dockerfile and CodeBuild spec.
