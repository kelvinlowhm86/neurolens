#!/usr/bin/env bash
# First deploy (or repair) in one command, after the human-only work in docs/operating.md:
# prerequisites, the Google/Flask/YouTube Parameter Store secrets and terraform.tfvars.
# It lists every missing prerequisite before changing anything, then creates the state bucket and
# backend file, builds the web zip and both layers, applies, uploads the model files when their
# prefix is empty, applies the database schema, stores the Cognito client secret when missing,
# deploys the site, builds the worker image, puts its digest in terraform.tfvars and applies again.
# A second run skips work that is already done (bucket, backend, artifacts, model files, secret,
# image).
set -euo pipefail

ROOT=$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)
INFRA="$ROOT/infra"
TF_DIR="$INFRA/terraform"
TFVARS="$TF_DIR/terraform.tfvars"
BACKEND="$TF_DIR/backend.hcl"
ENV_FILE="$ROOT/.env"

# Before the first apply there is no Terraform state for aws_env.sh to read, so take the account
# from the caller and the region from terraform.tfvars (the setting's one home). aws_env.sh is
# sourced after the first apply, when Terraform can answer.
export AWS_PROFILE="${NEUROLENS_AWS_PROFILE:-neurolens}"
if ! AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text); then
  echo "Cannot read the AWS account for profile '$AWS_PROFILE'. Check that its credentials work:" >&2
  echo "  AWS_PROFILE=$AWS_PROFILE aws sts get-caller-identity" >&2
  exit 1
fi
tfvars_region() {
  [ -f "$TFVARS" ] \
    && sed -n -E 's/^[[:space:]]*region[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' "$TFVARS" \
      | tail -n 1
}
tfvars_bucket_name() {
  sed -n -E 's/^[[:space:]]*bucket_name[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' "$TFVARS" \
    | tail -n 1
}
tfvars_worker_image() {
  sed -n -E 's/^[[:space:]]*worker_image[[:space:]]*=[[:space:]]*"([^"]*)".*/\1/p' "$TFVARS" \
    | tail -n 1
}
model_data_objects() {
  aws s3 ls "$1" --recursive 2>/dev/null | head -n 1
}
AWS_REGION=$(tfvars_region) || true
if [ -n "$AWS_REGION" ]; then
  export AWS_REGION
fi
STATE_BUCKET="neurolens-tfstate-$AWS_ACCOUNT_ID"
STATE_REGION=us-east-1   # the state bucket lives here regardless of the deployment region

# Secrets a person stores before the first apply. The Cognito client secret does not exist yet:
# the first apply creates the client, so it is stored below from Cognito's own answer.
PRE_APPLY_SECRETS=(
  /neurolens/terraform/google_client_secret
  /neurolens/web/flask_secret_key
  /neurolens/web/youtube_api_key
)

MISSING=""
missing() {
  MISSING="$MISSING  - $*"$'\n'
}

hf_token_present() {
  [ -n "${HF_TOKEN:-}" ] && return 0
  [ -f "$ENV_FILE" ] \
    && grep -qE '^[[:space:]]*HF_TOKEN[[:space:]]*=[[:space:]]*[^[:space:]]' "$ENV_FILE"
}

# ---- Prerequisites: report every gap, then stop before changing anything --------------------
[ -f "$TFVARS" ] || missing \
  "infra/terraform/terraform.tfvars is missing: copy terraform.tfvars.example and fill it in."
hf_token_present || missing \
  "HF_TOKEN (Hugging Face read token) is not set: copy .env.example to .env and fill it in."
for name in "${PRE_APPLY_SECRETS[@]}"; do
  aws ssm get-parameter --name "$name" --with-decryption >/dev/null 2>&1 \
    || missing "Parameter Store secret $name is missing (terraform README section 0)."
done
if [ -n "$MISSING" ]; then
  printf 'Setup cannot start. Missing prerequisites:\n%s' "$MISSING" >&2
  exit 1
fi

run_step() {
  local what=$1
  shift
  if ! "$@"; then
    echo "Setup stopped: $what failed. Nothing further was changed." >&2
    exit 1
  fi
}

# A first apply with worker_image already set creates the GPU endpoint. Refuse it unless the
# bucket Terraform will use already holds model-data/: an empty prefix makes the endpoint fail at
# boot, and that failed SageMaker start bills for about 30 minutes.
if [ -n "$(tfvars_worker_image)" ]; then
  IMAGE_BUCKET=$(tfvars_bucket_name)
  if [ -z "$IMAGE_BUCKET" ]; then
    echo "Setup stopped: terraform.tfvars sets worker_image but has no bucket_name to check." >&2
    exit 1
  fi
  if [ -z "$(model_data_objects "s3://$IMAGE_BUCKET/model-data/")" ]; then
    echo "Setup stopped: terraform.tfvars sets worker_image, but s3://$IMAGE_BUCKET/model-data/ is" >&2
    echo "empty or the bucket does not exist yet. The first apply would create the GPU endpoint" >&2
    echo "against an empty prefix, and a failed SageMaker start bills for about 30 minutes." >&2
    echo "Clear worker_image in terraform.tfvars and run setup.sh again: it creates the bucket," >&2
    echo "uploads the model files, builds the image and applies the endpoint." >&2
    exit 1
  fi
fi

# ---- Terraform state bucket and backend ---------------------------------------------------
if ! aws s3api head-bucket --bucket "$STATE_BUCKET" --region "$STATE_REGION" >/dev/null 2>&1; then
  echo "Creating the Terraform state bucket $STATE_BUCKET in $STATE_REGION..."
  aws s3api create-bucket --bucket "$STATE_BUCKET" --region "$STATE_REGION"
  aws s3api put-bucket-versioning --bucket "$STATE_BUCKET" --region "$STATE_REGION" \
    --versioning-configuration Status=Enabled
  aws s3api put-public-access-block --bucket "$STATE_BUCKET" --region "$STATE_REGION" \
    --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
fi
if [ ! -f "$BACKEND" ]; then
  printf 'bucket = "%s"\n' "$STATE_BUCKET" > "$BACKEND"
fi

# ---- Build the artifacts Terraform uploads ------------------------------------------------
[ -f "$ROOT/build/web_lambda.zip" ] \
  || run_step "building the web Lambda zip" "$INFRA/build_web_lambda.sh"
[ -f "$TF_DIR/.build/ffprobe_layer.zip" ] \
  || run_step "building the ffprobe layer" "$INFRA/build_ffprobe_layer.sh"
[ -f "$TF_DIR/.build/report_layer.zip" ] \
  || run_step "building the report layer" "$INFRA/build_report_layer.sh"

# ---- First apply: resources, then the schema and the site ---------------------------------
run_step "initialising Terraform" \
  terraform -chdir="$TF_DIR" init -backend-config=backend.hcl -input=false
run_step "the first Terraform apply" \
  terraform -chdir="$TF_DIR" apply -auto-approve -input=false

# Now the state exists, so Terraform records the region and account the scripts must use.
# shellcheck disable=SC1091
source "$INFRA/aws_env.sh"

BUCKET=$(terraform -chdir="$TF_DIR" output -raw bucket_name)
MODEL_DATA_URI="s3://$BUCKET/model-data/"

# SageMaker copies model-data/ into every GPU instance at start and the container runs offline,
# so an empty prefix makes the endpoint fail at boot.
if [ -z "$(model_data_objects "$MODEL_DATA_URI")" ]; then
  run_step "uploading the model files to $MODEL_DATA_URI" \
    python3 "$INFRA/upload_model_data.py" --bucket "$BUCKET"
fi

# The first apply creates the Cognito client. Store its secret once, before the site needs it.
store_cognito_client_secret() {
  local name=/neurolens/web/cognito_client_secret pool_id client_id secret
  if aws ssm get-parameter --name "$name" --with-decryption >/dev/null 2>&1; then
    return 0
  fi
  pool_id=$(terraform -chdir="$TF_DIR" output -raw cognito_user_pool_id)
  client_id=$(terraform -chdir="$TF_DIR" output -raw cognito_client_id)
  secret=$(aws cognito-idp describe-user-pool-client \
    --user-pool-id "$pool_id" --client-id "$client_id" \
    --query UserPoolClient.ClientSecret --output text)
  if [ -z "$secret" ] || [ "$secret" = "None" ]; then
    echo "Cognito returned no client secret for $client_id." >&2
    return 1
  fi
  aws ssm put-parameter --name "$name" --type SecureString --value "$secret" >/dev/null
}

# apply_schema.py reads the deployment values through its environment, so the operator does not
# have to copy them into .env before the first deploy.
NEUROLENS_DB_CLUSTER_ARN=$(terraform -chdir="$TF_DIR" output -raw db_cluster_arn)
NEUROLENS_DB_SECRET_ARN=$(terraform -chdir="$TF_DIR" output -raw db_secret_arn)
NEUROLENS_DB_NAME=$(terraform -chdir="$TF_DIR" output -raw db_name)
export NEUROLENS_DB_CLUSTER_ARN NEUROLENS_DB_SECRET_ARN NEUROLENS_DB_NAME
run_step "applying the database schema" \
  python3 "$INFRA/apply_schema.py" --backend data_api
run_step "storing the Cognito client secret" store_cognito_client_secret
run_step "deploying the website" "$INFRA/deploy_web.sh"

# ---- Worker image: build once, record its digest, then apply the endpoint ------------------
set_tfvars_worker_image() {
  local digest=$1 tmp
  tmp=$(mktemp)
  if grep -qE '^[[:space:]]*worker_image[[:space:]]*=' "$TFVARS"; then
    sed -E "s|^[[:space:]]*worker_image[[:space:]]*=.*|worker_image = \"$digest\"|" \
      "$TFVARS" > "$tmp"
  else
    cat "$TFVARS" > "$tmp"
    printf '\nworker_image = "%s"\n' "$digest" >> "$tmp"
  fi
  mv "$tmp" "$TFVARS"
}

if [ -z "$(tfvars_worker_image)" ]; then
  if [ -z "$(model_data_objects "$MODEL_DATA_URI")" ]; then
    echo "Setup stopped: $MODEL_DATA_URI is empty, so the GPU endpoint would fail at start." >&2
    echo "A failed SageMaker start bills for about 30 minutes and cannot be cancelled." >&2
    echo "Run python3 $INFRA/upload_model_data.py, then run setup.sh again." >&2
    exit 1
  fi
  if ! BUILD_OUT=$("$INFRA/build_image.sh"); then
    echo "Setup stopped: building the GPU image failed." >&2
    exit 1
  fi
  printf '%s\n' "$BUILD_OUT"
  DIGEST=$(printf '%s\n' "$BUILD_OUT" | sed -n 's/^Digest: //p' | tail -n 1)
  if [ -z "$DIGEST" ]; then
    echo "Setup stopped: build_image.sh did not print an image digest." >&2
    exit 1
  fi
  set_tfvars_worker_image "$DIGEST"
  run_step "the Terraform apply for the GPU endpoint" \
    terraform -chdir="$TF_DIR" apply -auto-approve -input=false
fi

echo "Setup complete: Terraform state in $STATE_BUCKET, website deployed."
