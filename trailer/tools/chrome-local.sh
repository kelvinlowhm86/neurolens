#!/bin/sh
# Keep Chromium's renderer/GPU in this process, for restricted macOS environments.
TRAILER_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CANDIDATE1="$TRAILER_ROOT/node_modules/.remotion/chrome-headless-shell/mac-arm64/chrome-headless-shell-mac-arm64/chrome-headless-shell"
CANDIDATE2="$HOME/.cache/puppeteer/chrome-headless-shell/mac_arm-156.0.8078.47/chrome-headless-shell-mac-arm64/chrome-headless-shell"

if [ -x "$CANDIDATE1" ]; then
  CHROME_BIN="$CANDIDATE1"
elif [ -x "$CANDIDATE2" ]; then
  CHROME_BIN="$CANDIDATE2"
else
  CHROME_BIN=$(find "$HOME/.cache/puppeteer/chrome-headless-shell" -name chrome-headless-shell -perm +111 2>/dev/null | head -n 1)
fi

exec "$CHROME_BIN" --single-process --in-process-gpu "$@"
