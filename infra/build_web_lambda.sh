#!/usr/bin/env bash
# Builds build/web_lambda.zip, the code of both web functions (M3b §3a): the last commit's
# neurolens/ package and config.json, plus the web dependencies installed for Lambda's own
# platform (Linux arm64, Python 3.12) so nothing built on this Mac breaks there. Terraform needs
# the zip once, to create the functions; after that deploy_web.sh rebuilds and ships it.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

OUT=build/web_lambda.zip
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p build

python3 -m pip install --quiet --disable-pip-version-check \
  --platform manylinux2014_aarch64 --implementation cp --python-version 3.12 --only-binary=:all: \
  --target "$TMP/pkg" -r requirements/web.txt
git archive HEAD neurolens config.json | tar -x -C "$TMP/pkg"

rm -f "$OUT"
(cd "$TMP/pkg" && zip -qr "$OLDPWD/$OUT" . -x '*/__pycache__/*' '*.dist-info/RECORD')
echo "Built $OUT ($(du -h "$OUT" | cut -f1)) from $(git rev-parse --short HEAD)."
