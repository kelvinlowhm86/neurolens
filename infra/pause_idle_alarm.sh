#!/usr/bin/env bash
# Pauses the idle alarm's action (M2a §4f) for long manual work on a worker (Session Manager), which
# the alarm cannot see. The alarm still emails; it no longer ends the worker. start_work.sh and
# stop_work.sh switch the action back on, so a pause lasts at most until the session ends.
# While paused, only the 3-hour email covers a worker left running.
set -euo pipefail
source "$(dirname "$0")/aws_env.sh"   # AWS_PROFILE, AWS_REGION (M2a §4i)
aws cloudwatch disable-alarm-actions --alarm-names neurolens-worker-idle
echo "Idle alarm paused until the next start_work.sh or stop_work.sh. Only the 3-hour email covers a forgotten worker now."
