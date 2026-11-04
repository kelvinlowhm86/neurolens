# NeuroLens — M1 Implementation Spec
Only the parts of this milestone still in use are described here; the rest is in later specs (see docs/README.md).
**Milestone:** Cloud Storage & Task Queue Decoupling (29 Sept – 8 Oct)
**Builds on:** M0 (the `neurolens/` package, tests-first suite, CI). Re-read `AGENTS.md` before implementing.

**Ground rules for all of M1:** everything runs in AWS region `us-east-1` (the `g6e` GPU family used from M2a onward is not offered in Singapore, so do not "correct" the region). All M1 work runs without a GPU, in fake-model mode. Python 3.12, as in M0.

**Tests first, as in M0:** a separate agent writes every test from this spec before the implementation exists; the implementer may not edit them and asks Josh if one looks wrong. The break-it check (M0 §8) is repeated for the worker's size and duration rejections.

## 2. Configuration: shared settings in `config.json`, secrets and identifiers in `.env`
One rule: **behaviour settings are shared; anything specific to a person or a deployment is not.**
- **`config.json`** is committed, holds no secrets, and teammates may edit it: paths, model names, limits, timeouts and behaviour switches. See the committed file for the current set.
- **`.env`** is gitignored and per person (`.env.example` is committed, with placeholders). `KEY=VALUE` lines; blank lines and `#` comments ignored; one pair of surrounding quotes around a value is removed. It holds `HF_TOKEN`, `NEUROLENS_S3_BUCKET` and `NEUROLENS_AWS_REGION`; later milestones add their deployment-specific identifiers here (and, on AWS, to the SageMaker model and Lambda environments).

**Loading (`neurolens.settings`).** Entry points (the launcher `app.py`, the GPU container's start-up, and `inference.load_model(cfg=None)`) call `load_settings`; `load_config` stays file-only.
- `load_dotenv(root=None) -> None`: reads `<root>/.env` if it exists (no file is not an error) and sets each variable in `os.environ` **only if it is not already set**, so a real environment variable always wins over the file.
- `apply_env(cfg: dict) -> dict`: returns a copy of `cfg` with `HF_TOKEN` as `hf_token`, `NEUROLENS_AWS_REGION` as `aws.region` and `NEUROLENS_S3_BUCKET` as `aws.s3_bucket`, for each of those variables that is set and non-empty (creating the `aws` dict if needed). Later milestones add their own mappings. It never changes its input and adds nothing for an unset variable.
- `load_settings(root=None) -> dict`: `load_dotenv(root)`, then `apply_env(load_config(root))`.
- `configure_env(cfg, paths)`: `hf_token` in `cfg` is optional; when it is absent `HF_TOKEN` is left as it is (fake mode needs no token).
- `load_config` raises `FileNotFoundError` naming `config.json` when it is missing.

**Do not put AWS access keys anywhere in the project.** boto3 uses the default credential chain (`~/.aws/credentials`, `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` env vars, or a role in later milestones). The container's start-up check names any missing setting.

The fake-model switch is an environment variable, not a config field, so a real deployment can never be left in fake mode by a stale config file.

## 3. AWS resource provisioning (Terraform)
Create `infra/terraform/` — a Terraform configuration using the AWS provider in `us-east-1`, with `default_tags` `Project=neurolens`. Terraform tracks what exists, so running `terraform apply` twice is safe by design and `terraform destroy` removes everything it created.

**Budget guardrail.** An account-wide AWS Budget of $40/month with email alerts at 50%, 80% and 100% of actual spend already exists (created by hand, not by Terraform). Confirm it is still there before the first `terraform apply`.

**Remote state.** Terraform's record of what exists (its *state*) must not live on one laptop, or teammates will clash. Store it in a separate, small S3 bucket (e.g. `neurolens-tfstate-<account-id>`) with versioning on, using the S3 backend with S3-native locking (`use_lockfile = true`). Create that one bucket once, by hand or with a tiny `infra/terraform/bootstrap/` configuration, and document the step in `infra/terraform/README.md`. The state bucket is never destroyed by `terraform destroy`.

**Variables used by this part:** `bucket_name`, `queue_name`, `allowed_origins` (list, e.g. `["http://localhost:5003"]`). Later specs add the rest (M3e §9). Outputs include `bucket_name` and `region`; the queue is read by name, not output.

The configuration:
1. Creates the S3 bucket (`bucket_name`, region `us-east-1`)
2. Applies baseline security settings to the bucket:
   - Enable S3 Block Public Access (all four settings) on the bucket.
   - Set Object Ownership to 'Bucket owner enforced' (disables ACLs entirely — the presigned-URL upload flow does not use or need ACLs).
   - Enable default server-side encryption (SSE-S3 is sufficient for this project; SSE-KMS is a reasonable upgrade but not required).
   - Attach a bucket policy statement that denies any request where `aws:SecureTransport` is `false`, ensuring all access — including the presigned POST URL and any GET — must use HTTPS.
   None of these settings change behavior for direct browser uploads via presigned URLs, since access is via a signed request rather than a public ACL or plaintext connection.
3. Sets prefix-scoped lifecycle rules, not one blanket bucket-wide rule (M3f §5b, M3e §9):
   - `uploads/*` and `results/*` expire after **30 days** (S3 rounds expiry to midnight UTC, so up to 31). Uploads are the large objects; results are a few KB. A refunded or cancelled upload is deleted at once.
   - `dataset/*` expires after **1095 days**: shared videos and retention curves kept for research.
   - `sagemaker-output/*` expires after **1 day** and `build/*` after **7 days**.
   - `experiments/*` never expires; M4 reads it for the final report.
4. Sets a CORS configuration allowing `POST` and `GET` from `allowed_origins`, plus the CloudFront address. Terraform owns the whole CORS document: to add an origin later, add it to the variable and apply. S3 CORS applies per bucket, not per prefix; the `uploads/` restriction is enforced by the `key` condition in the signed POST policy.
5. Creates the SQS job queue (`queue_name`) with visibility timeout **1080 s**, `delay_seconds` **15** (so the user can cancel before dispatch) and a dead-letter queue (`<queue_name>-dlq`, 14-day retention) with `maxReceiveCount = 2`. M3e §3 has the dispatcher and the queue's role.
6. Wires S3 `ObjectCreated:*` → the SQS queue, filtered to the `uploads/` prefix only, with the SQS queue policy that lets the bucket send messages (S3→SQS needs an explicit queue access policy; this is a common gotcha). Add `depends_on` so the notification is created after the queue policy. The prefix filter is required: later milestones write `results/`, `dataset/`, `experiments/` and other objects to the same bucket, and an unfiltered notification would enqueue them as spurious jobs.
7. Outputs the bucket name for the deployment's settings.

Terraform does **not** create IAM users or configure credentials. Run it with the `neurolens` AWS CLI profile (`AWS_PROFILE=neurolens`); never with another project's profile. `infra/terraform/README.md` says to check `aws sts get-caller-identity` first.

**Teardown:** `terraform destroy`. S3 refuses to delete a non-empty bucket, so the README documents emptying it first (`aws s3 rm s3://<bucket> --recursive`) once results and experiment files are no longer needed.

## 7. The old endpoint is gone
`POST /api/analyse`, the `load_model` parameter of `create_app`, and the lazy `neurolens.inference` import inside it no longer exist. **The web tier never imports `neurolens.inference`** (nor `torch`): analysis happens only in the GPU container. Consequences:
- `create_app(cfg=...)`; the launcher `app.py` loads the settings, builds the app and runs it on `settings.server_address(cfg)`. It does not log GPU information, since the web machine has no GPU.
- The test suite has no tests for `/api/analyse`, never passes `load_model` to `create_app`, and the import-hygiene test asserts that importing `neurolens.web.app` and calling `create_app` never loads `neurolens.inference`.
- `AGENTS.md` states the rule without the M0 exception.
