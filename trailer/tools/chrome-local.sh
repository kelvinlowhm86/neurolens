#!/bin/sh
# Keep Chromium's renderer/GPU in this process, for restricted macOS environments.
TRAILER_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "$TRAILER_ROOT/node_modules/.remotion/chrome-headless-shell/mac-arm64/chrome-headless-shell-mac-arm64/chrome-headless-shell" --single-process --in-process-gpu "$@"
