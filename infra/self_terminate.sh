#!/bin/bash
# Ends this worker machine (M2a §3, cost guard). Asks Auto Scaling to terminate it AND lower the
# group's desired count: a plain shutdown would make the group launch a replacement, which would
# fail the same way, in a billing loop.
# Called by neurolens-self-terminate.service (with --after-worker-stop) and by UserData's ERR trap.
# If the call fails (for example the NAT instance is down), it logs and stops: the 3-hour alarm is
# the backstop.
set -uo pipefail

LOG=/var/log/neurolens-boot.log
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) self_terminate: $*" | tee -a "$LOG" >&2; }

if [ "${1:-}" = "--after-worker-stop" ]; then
  # `systemctl restart neurolens-worker` (restart_workers.sh) also stops the worker briefly. Give a
  # restart time to bring it back, and stand down if it did.
  sleep 15
  state=$(systemctl is-active neurolens-worker || true)
  if [ "$state" = "active" ] || [ "$state" = "activating" ]; then
    log "worker is $state again (a restart); not terminating"
    exit 0
  fi
fi

IMDS=http://169.254.169.254/latest
TOKEN=$(curl -sf -m 5 -X PUT "$IMDS/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 60") \
  || { log "no instance metadata token; cannot terminate"; exit 1; }
ID=$(curl -sf -m 5 -H "X-aws-ec2-metadata-token: $TOKEN" "$IMDS/meta-data/instance-id")
REGION=$(curl -sf -m 5 -H "X-aws-ec2-metadata-token: $TOKEN" "$IMDS/meta-data/placement/region")

log "terminating $ID"
if aws autoscaling terminate-instance-in-auto-scaling-group --region "$REGION" \
    --instance-id "$ID" --should-decrement-desired-capacity >>"$LOG" 2>&1; then
  log "termination requested"
else
  log "termination call FAILED; the 3-hour alarm is the backstop"
  exit 1
fi
