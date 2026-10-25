#!/bin/sh
# Rebuilds public/music-edit.wav and src/beats.ts from public/music2.mp3.
# Single source of truth: tools/analyze_music.py (measurement + edit + timing).
set -eu
TRAILER_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON=${PYTHON:-"$TRAILER_ROOT/../.venv/bin/python"}
if [ ! -x "$PYTHON" ]; then
  PYTHON=python3
fi
cd "$TRAILER_ROOT"
exec "$PYTHON" tools/analyze_music.py
