#!/bin/sh
set -eu
TRAILER_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ffmpeg -hide_banner -loglevel error -y \
  -i "$TRAILER_ROOT/public/music.mp3" \
  -af 'atrim=start=34.5:end=77.5,asetpts=PTS-STARTPTS,afade=t=in:st=0:d=0.08,afade=t=out:st=41.7:d=1.3,volume=0.6' \
  -c:a pcm_s16le "$TRAILER_ROOT/public/music-edit.wav"
