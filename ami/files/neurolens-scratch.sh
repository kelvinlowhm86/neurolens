#!/usr/bin/env bash
# Put the worker's scratch space on the instance-store NVMe if there is one (g6e.2xlarge: 450 GB, free,
# faster than EBS). Wiped on stop — fine for per-job scratch. Without one, scratch stays on the root volume.
set -euo pipefail
SCRATCH=/opt/neurolens/scratch
mkdir -p "$SCRATCH"
mountpoint -q "$SCRATCH" && exit 0
DEV=$(lsblk -dpno NAME,MODEL | awk '/Instance Storage/ {print $1; exit}')
if [[ -n "$DEV" ]]; then
  mkfs.ext4 -q -F "$DEV"
  mount -o noatime "$DEV" "$SCRATCH"
  echo "Scratch on instance store $DEV"
fi
chown 10001 "$SCRATCH"
