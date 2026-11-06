#!/usr/bin/env bash
# Fills s3://<bucket>/model-data/ once (docs/M3e_spec.md §4d). SageMaker copies that prefix,
# uncompressed, to /opt/ml/model when an instance starts. It is a server-side copy of the
# existing models/ prefix without HuggingFace's blobs/ (snapshots/ holds full copies), keeping
# the prefix's own layout, including data/hcp_mmp/.
set -euo pipefail

ROOT=$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)
# shellcheck disable=SC1091
source "$ROOT/infra/aws_env.sh"

BUCKET=$(terraform -chdir="$ROOT/infra/terraform" output -raw bucket_name)
SRC="s3://$BUCKET/models/"
DST="s3://$BUCKET/model-data/"

aws s3 cp "$SRC" "$DST" --recursive --exclude "*/blobs/*" --only-show-errors

count_and_size() {
  aws s3 ls "$1" --recursive | awk '{count++; bytes += $3} END {print count + 0, bytes + 0}'
}
SRC_TOTAL=$(aws s3 ls "$SRC" --recursive | grep -v '/blobs/' | awk '{count++; bytes += $3} END {print count + 0, bytes + 0}')
DST_TOTAL=$(count_and_size "$DST")

if [ "${SRC_TOTAL%% *}" = "0" ]; then
  echo "No objects under $SRC: the weights have not been uploaded." >&2
  exit 1
fi

if [ "$SRC_TOTAL" != "$DST_TOTAL" ]; then
  echo "Mismatch: source (without blobs) has '$SRC_TOTAL', model-data has '$DST_TOTAL'." >&2
  echo "Objects and bytes must agree; check the copy before applying Terraform." >&2
  exit 1
fi

read -r COUNT BYTES <<<"$DST_TOTAL"
echo "model-data/ holds $COUNT objects, $BYTES bytes (matches models/ without blobs/)."
