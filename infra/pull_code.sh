#!/bin/bash
# Fetches the last deployed code bundle (M2a §3). Runs before every worker start (the service's
# ExecStartPre) and once from UserData, so a deploy reaches a machine with a plain restart.
# Needs NEUROLENS_S3_BUCKET and NEUROLENS_AWS_REGION (from /opt/neurolens/env.conf).
set -euo pipefail
: "${NEUROLENS_S3_BUCKET:?not set}" "${NEUROLENS_AWS_REGION:?not set}"

APP=/opt/neurolens/app
BIN=/opt/neurolens/bin
PY=/opt/neurolens/venv/bin/python
SRC="s3://$NEUROLENS_S3_BUCKET/code"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

aws s3 cp "$SRC/latest.zip" "$TMP/code.zip" --region "$NEUROLENS_AWS_REGION" --only-show-errors
aws s3 cp "$SRC/latest.revision" "$TMP/revision" --region "$NEUROLENS_AWS_REGION" --only-show-errors
"$PY" -m zipfile -e "$TMP/code.zip" "$TMP/code"   # Python's unzip: the image need not have unzip
test -f "$TMP/code/worker.py"   # a broken bundle must not replace working code

# Replace the package folder whole, so a file deleted in git is gone here too. config.json is not
# in the bundle, so the machine's own copy is kept.
mkdir -p "$APP" "$BIN"
rm -rf "$APP/neurolens"
cp -a "$TMP/code/." "$APP/"
install -m 755 "$TMP/code/infra/pull_code.sh" "$TMP/code/infra/self_terminate.sh" "$BIN/"
cp "$TMP/revision" "$APP/REVISION"
echo "pulled code $(cat "$APP/REVISION")"
