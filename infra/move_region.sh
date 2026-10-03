#!/usr/bin/env bash
# Copies the whole system to another region (M2a §4i, plan B when GPUs stay sold out).
#   infra/move_region.sh <new-region>        checks and prints what a move would do (free, read only)
#   infra/move_region.sh <new-region> --go   does the move, stopping at the first failure
# It never deletes anything in the old region: it ends by printing the teardown commands, to run
# only after the new region has completed a real job.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
source infra/aws_env.sh   # AWS_PROFILE, AWS_REGION = the current (old) region
TF=infra/terraform
OLD=$AWS_REGION

NEW=""; GO=0
for arg in "$@"; do
  case "$arg" in
    --go) GO=1 ;;
    -*) echo "unknown option $arg" >&2; exit 2 ;;
    *) NEW="$arg" ;;
  esac
done
[[ "$NEW" =~ ^[a-z]{2}-[a-z]+-[0-9]$ ]] || { echo "usage: $0 <new-region> [--go]" >&2; exit 2; }
[ "$NEW" != "$OLD" ] || { echo "Already in $NEW." >&2; exit 2; }

say() { echo "$(date +%H:%M:%S) $*"; }
confirm() {  # $1 question; returns only on "yes"
  local answer
  read -r -p "$1 Type yes to go on: " answer
  [ "$answer" = yes ] || { say "stopped (nothing further done)"; exit 1; }
}

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
OLD_BUCKET=$(terraform -chdir=$TF output -raw bucket_name)
OLD_KEY=$(python3 -c 'import json; print(json.load(open("infra/terraform/.terraform/terraform.tfstate"))["backend"]["config"]["key"])')
OLD_AMI=$(sed -n 's/^worker_ami_id *= *"\(ami-[0-9a-f]*\)".*/\1/p' $TF/terraform.tfvars)
OLD_SNAP=$( [ -n "$OLD_AMI" ] && aws ec2 describe-images --image-ids "$OLD_AMI" \
  --query 'Images[0].BlockDeviceMappings[?Ebs].Ebs.SnapshotId | [0]' --output text || true)
NEW_BUCKET="neurolens-data-$ACCOUNT-$NEW"
NEW_KEY="$NEW/terraform.tfstate"

# ─── Checks (free, read only) ──────────────────────────────────────────────────────────────────
FAIL=0
ZONES=$(aws ec2 describe-instance-type-offerings --region "$NEW" --location-type availability-zone \
  --filters Name=instance-type,Values=g6e.xlarge --query 'InstanceTypeOfferings[].Location' --output text \
  | tr '\t' '\n' | sort | tr '\n' ' ')
if [ -n "${ZONES// /}" ]; then say "g6e.xlarge offered in: $ZONES"; else say "FAIL: no zone in $NEW offers g6e.xlarge"; FAIL=1; fi

# L-DB2E81BA = "Running On-Demand G and VT instances" (vCPUs).
QUOTA_KNOWN=1
if QUOTA=$(aws service-quotas get-service-quota --region "$NEW" --service-code ec2 --quota-code L-DB2E81BA \
     --query Quota.Value --output text 2>/dev/null); then
  if [ "${QUOTA%.*}" -ge 8 ]; then say "on-demand G quota in $NEW: $QUOTA vCPUs"; else say "FAIL: on-demand G quota in $NEW is $QUOTA vCPUs (need 8)"; FAIL=1; fi
else
  QUOTA_KNOWN=0
  say "could not read the GPU quota (paste the updated neurolens-deploy-services policy, or check it in the console:"
  say "  Service Quotas, $NEW, Amazon EC2, 'Running On-Demand G and VT instances': need 8 or more)"
fi
if aws s3api head-object --bucket "neurolens-tfstate-$ACCOUNT" --key "$NEW_KEY" --region us-east-1 >/dev/null 2>&1; then
  say "note: a Terraform state for $NEW already exists ($NEW_KEY): an earlier move started; --go carries on from it"
fi
[ -n "$OLD_AMI" ] || { say "FAIL: no worker_ami_id in terraform.tfvars"; FAIL=1; }

cat <<EOF

Move $OLD -> $NEW
  data bucket   $OLD_BUCKET -> $NEW_BUCKET
  state key     $OLD_KEY -> $NEW_KEY (state bucket stays in us-east-1)
  image         $OLD_AMI copied to $NEW
  models/       copied bucket to bucket (~18 GB, about \$0.36 of transfer)
  IAM names     roles and instance profiles get the suffix -$NEW (IAM names are global)
With --go: policies to paste -> HF token parameter (you create it) -> image copy -> terraform apply
-> models copy -> deploy_code.sh -> printed steps for one real job -> printed teardown for $OLD.
EOF
[ "$FAIL" = 0 ] || { say "checks failed: not moving"; exit 1; }
[ "$GO" = 1 ] || { say "dry run only (add --go to move)"; exit 0; }

# ─── The move ──────────────────────────────────────────────────────────────────────────────────
[ "$QUOTA_KNOWN" = 1 ] || confirm "Is the on-demand G quota in $NEW at least 8 vCPUs?"
[ -z "$(git status --porcelain)" ] || { say "uncommitted changes: commit first (deploy_code.sh ships the last commit)"; exit 1; }

# 1. Policies covering both regions (the old one stays usable until its teardown), and the
#    new-region-only versions to paste after the teardown.
POL=$(mktemp -d "${TMPDIR:-/tmp}/neurolens-policies.XXXX")
python3 - "$OLD" "$NEW" "$POL" <<'PY'
import json, pathlib, sys
old, new, out = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3])

def both(v):
    if isinstance(v, str):
        return [v, v.replace(old, new)] if old in v else v
    if isinstance(v, list):
        r = []
        for x in v:
            y = both(x)
            r += y if isinstance(y, list) and isinstance(x, str) else [y]
        return r
    if isinstance(v, dict):
        return {k: both(x) for k, x in v.items()}
    return v

def only_new(v):
    if isinstance(v, str):
        return v.replace(old, new)
    if isinstance(v, list):
        return [only_new(x) for x in v]
    if isinstance(v, dict):
        return {k: only_new(x) for k, x in v.items()}
    return v

for f in sorted(pathlib.Path("infra/iam").glob("*.json")):
    doc = json.loads(f.read_text())
    for name, fn in (("both-regions", both), (f"{new}-only", only_new)):
        doc2 = dict(doc, Statement=[{k: (fn(x) if k in ("Resource", "Condition") else x)
                                     for k, x in s.items()} for s in doc["Statement"]])
        text = json.dumps(doc2, indent=1)
        size = len(json.dumps(doc2, separators=(",", ":")))
        if size > 6144:
            sys.exit(f"{f.name} ({name}) is {size} characters, over the 6,144 limit for a managed policy")
        (out / f"{f.stem}.{name}.json").write_text(text + "\n")
PY
say "policies written to $POL:"
ls -1 "$POL" | sed 's/^/    /'
echo "  Paste the three *.both-regions.json files over the policies of the same name (IAM, Policies, Edit),"
echo "  as in infra/iam/README.md. The *.$NEW-only.json files are for after the teardown of $OLD."
confirm "All three both-regions policies pasted?"

# 2. The HuggingFace token: typed only by you, never through this script.
echo "  Create the HuggingFace token in $NEW: Systems Manager, Parameter Store (region $NEW), name"
echo "  /neurolens/hf_token, type SecureString, the same value as in $OLD."
confirm "Token parameter created in $NEW?"
[ "$(aws ssm get-parameter --region "$NEW" --name /neurolens/hf_token --query Parameter.Type --output text)" = SecureString ] \
  || { say "FAIL: /neurolens/hf_token in $NEW is missing or not a SecureString"; exit 1; }

# 3. The image (runs in the background on AWS's side while Terraform builds the network).
NAME=$(aws ec2 describe-images --image-ids "$OLD_AMI" --query 'Images[0].Name' --output text)
NEW_AMI=$(aws ec2 describe-images --region "$NEW" --owners self --filters "Name=name,Values=$NAME" \
  --query 'Images[0].ImageId' --output text)
if [ "$NEW_AMI" = None ] || [ -z "$NEW_AMI" ]; then
  NEW_AMI=$(aws ec2 copy-image --region "$NEW" --source-region "$OLD" --source-image-id "$OLD_AMI" \
    --name "$NAME" --description "Copy of $OLD_AMI ($OLD)" --copy-image-tags --query ImageId --output text)
  say "copying image $OLD_AMI -> $NEW_AMI ($NAME); its snapshot is billed in $NEW from now"
else
  say "image $NAME already in $NEW: $NEW_AMI"
fi

# 4. Terraform for the new region. The old files are kept next to the new ones for the teardown.
for f in terraform.tfvars backend.hcl; do
  [ -f "$TF/$f.$OLD" ] || cp "$TF/$f" "$TF/$f.$OLD"
done
python3 - "$TF" "$OLD" "$NEW" "$NEW_BUCKET" "$NEW_AMI" "$NEW_KEY" "$ZONES" <<'PY'
import pathlib, re, sys
tf, old, new, bucket, ami, key, zones = sys.argv[1:]
zones = zones.split()
src = (pathlib.Path(tf) / f"terraform.tfvars.{old}").read_text()
values = {
    "region": f'"{new}"',
    "zones": "[" + ", ".join(f'"{z}"' for z in zones) + "]",
    "build_extra_zones": "[" + ", ".join(f'"{z}"' for z in zones[1:]) + "]",
    "bucket_name": f'"{bucket}"',
    "worker_ami_id": f'"{ami}"',
}
for name, value in values.items():
    src, n = re.subn(rf"^{name}\s*=.*$", f"{name} = {value}", src, flags=re.M)
    if n != 1:
        sys.exit(f"terraform.tfvars.{old}: expected exactly one '{name} =' line, found {n}")
(pathlib.Path(tf) / "terraform.tfvars").write_text(src)
backend = (pathlib.Path(tf) / f"backend.hcl.{old}").read_text()
backend = re.sub(r'^key\s*=.*\n?', "", backend, flags=re.M).rstrip("\n") + f'\nkey = "{key}"\n'
(pathlib.Path(tf) / "backend.hcl").write_text(backend)
PY
say "terraform.tfvars and backend.hcl now describe $NEW (old ones kept as *.$OLD)"
terraform -chdir=$TF init -reconfigure -backend-config=backend.hcl -input=false >/dev/null
say "Terraform now points at $NEW_KEY. Review the plan and answer yes to create $NEW:"
terraform -chdir=$TF apply
export AWS_REGION="$NEW" NEUROLENS_AWS_REGION="$NEW"
[ "$(terraform -chdir=$TF output -raw region)" = "$NEW" ] || { say "FAIL: Terraform's region output is not $NEW"; exit 1; }

# 5. Weights, code, and the image being ready.
say "copying models/ $OLD_BUCKET -> $NEW_BUCKET"
aws s3 sync "s3://$OLD_BUCKET/models/" "s3://$NEW_BUCKET/models/" --source-region "$OLD" --region "$NEW" --only-show-errors
infra/deploy_code.sh
say "waiting for the image copy to be available (often 10-30 minutes)"
for i in $(seq 1 180); do
  state=$(aws ec2 describe-images --region "$NEW" --image-ids "$NEW_AMI" --query 'Images[0].State' --output text)
  [ "$state" = available ] && break
  [ "$state" = pending ] || { say "FAIL: image copy state is $state"; exit 1; }
  [ "$i" = 180 ] && { say "FAIL: image copy not available after 90 minutes (check it in the console)"; exit 1; }
  sleep 30
done

say "MOVE DONE: $NEW is set up. Nothing in $OLD was changed."
cat <<EOF

Next, in $NEW:
  1. Confirm the alarm email subscription for $NEW (AWS sends a new confirmation email).
  2. Put the new values in .env: NEUROLENS_S3_BUCKET=$NEW_BUCKET, NEUROLENS_AWS_REGION=$NEW,
     NEUROLENS_SQS_QUEUE_URL=$(terraform -chdir=$TF output -raw queue_url)
  3. One real job: infra/start_work.sh --worker, upload a clip through the web app, read
     results/<job id>.json, then infra/stop_work.sh.

Only after that job has passed, tear down $OLD (deletes its data for good):
  cp -R $TF "\${TMPDIR:-/tmp}/neurolens-tf-$OLD" && cd "\${TMPDIR:-/tmp}/neurolens-tf-$OLD"
  cp terraform.tfvars.$OLD terraform.tfvars && cp backend.hcl.$OLD backend.hcl
  terraform init -reconfigure -backend-config=backend.hcl
  AWS_PROFILE=neurolens aws s3 rm s3://$OLD_BUCKET --recursive --region $OLD
  AWS_PROFILE=neurolens terraform destroy
  AWS_PROFILE=neurolens aws ec2 deregister-image --region $OLD --image-id $OLD_AMI
  AWS_PROFILE=neurolens aws ec2 delete-snapshot --region $OLD --snapshot-id $OLD_SNAP
  cd - && rm -rf "\${TMPDIR:-/tmp}/neurolens-tf-$OLD" && rm $TF/terraform.tfvars.$OLD $TF/backend.hcl.$OLD
Then paste the $POL/*.$NEW-only.json policies, and delete $POL.
EOF
