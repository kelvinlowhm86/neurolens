#!/usr/bin/env bash
# Builds the report Lambda's Anthropic SDK layer (docs/M3g_spec.md §5c, §8): manylinux x86-64
# wheels under python/, zipped where Terraform uploads it. Pin matches requirements/lambdas.txt.
set -euo pipefail

ANTHROPIC_VERSION="2.3.0"

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
OUT="$ROOT/infra/terraform/.build/report_layer.zip"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

python3 -m pip install --quiet --disable-pip-version-check \
  --platform manylinux2014_x86_64 --only-binary=:all: --python-version 3.12 \
  --target "$TMP/layer/python" "anthropic[bedrock]==$ANTHROPIC_VERSION"

mkdir -p "$(dirname "$OUT")"
rm -f "$OUT"
(cd "$TMP/layer" && zip -qr "$OUT" .)
echo "Built $OUT ($(du -h "$OUT" | cut -f1))."
