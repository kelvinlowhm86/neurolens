#!/usr/bin/env bash
# Builds the dispatcher's ffprobe Lambda layer (docs/M3e_spec.md §3): a static Linux x86-64
# ffprobe in bin/, zipped where Terraform uploads it. Pinned by release URL and SHA-256.
set -euo pipefail

# old-releases/ keeps fixed URLs; releases/ only ever holds the newest build.
FFPROBE_URL="https://johnvansickle.com/ffmpeg/old-releases/ffmpeg-6.0.1-amd64-static.tar.xz"
FFPROBE_SHA256="28268bf402f1083833ea269331587f60a242848880073be8016501d864bd07a5"

ROOT=$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)
OUT="$ROOT/infra/terraform/.build/ffprobe_layer.zip"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

curl -fsSL "$FFPROBE_URL" -o "$TMP/ffmpeg.tar.xz"
if command -v sha256sum >/dev/null 2>&1; then
  echo "$FFPROBE_SHA256  $TMP/ffmpeg.tar.xz" | sha256sum -c -
else
  echo "$FFPROBE_SHA256  $TMP/ffmpeg.tar.xz" | shasum -a 256 -c -
fi

tar -xJf "$TMP/ffmpeg.tar.xz" -C "$TMP"
BINARY=$(find "$TMP" -type f -name ffprobe -print -quit)
if [ -z "$BINARY" ]; then
  echo "The release did not contain ffprobe." >&2
  exit 1
fi

mkdir -p "$TMP/layer/bin" "$(dirname "$OUT")"
cp "$BINARY" "$TMP/layer/bin/ffprobe"
chmod 755 "$TMP/layer/bin/ffprobe"
rm -f "$OUT"
(cd "$TMP/layer" && zip -qr "$OUT" .)
echo "Built $OUT ($(du -h "$OUT" | cut -f1))."
