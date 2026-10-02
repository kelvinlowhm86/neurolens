#!/usr/bin/env bash
# Builds the GPU worker image (M2a §2). Run from your Mac.
#
#   infra/build_ami.sh --cpu-rehearsal [--plain-ubuntu]
#       t3.large (about 8 cents an hour): installs the software only (§2 step 3), prints success
#       or the failing step, and always terminates. No token, no weights, no image.
#   infra/build_ami.sh <clip.mp4> [--refresh-weights]
#       g6e.xlarge on-demand ($1.86 an hour, about an hour): full build, weights to S3 models/,
#       then the software-only image. Prints the AMI ID, snapshot size, peak RAM and VRAM.
#
# Needs: terraform applied (network, roles), and infra/deploy_code.sh run (code/latest.zip).
# Money guards: the build instance is terminated when this script exits (errors and Ctrl-C too);
# every remote step has a deadline; and the instance shuts itself down (= terminates) after 4 hours
# even if this Mac sleeps or loses its connection.
# The HuggingFace token never touches this Mac: the instance reads it from Parameter Store.
set -euo pipefail
export AWS_PROFILE="${NEUROLENS_AWS_PROFILE:-neurolens}" AWS_REGION=us-east-1
cd "$(git rev-parse --show-toplevel)"

REHEARSAL=0; PLAIN=0; REFRESH=0; CLIP=""
for arg in "$@"; do
  case "$arg" in
    --cpu-rehearsal) REHEARSAL=1 ;;
    --plain-ubuntu) PLAIN=1 ;;
    --refresh-weights) REFRESH=1 ;;
    -*) echo "unknown option $arg" >&2; exit 2 ;;
    *) CLIP="$arg" ;;
  esac
done
if [ "$REHEARSAL" = 0 ] && [ ! -f "$CLIP" ]; then
  echo "usage: $0 --cpu-rehearsal [--plain-ubuntu]  |  $0 <clip.mp4> [--refresh-weights]" >&2; exit 2
fi

say() { echo "$(date +%H:%M:%S) $*"; }
tf() { terraform -chdir=infra/terraform output -raw "$1"; }
BUCKET=$(tf bucket_name); SUBNETS=$(tf build_subnet_ids)
SG=$(tf no_inbound_security_group_id); PROFILE=$(tf build_instance_profile)

aws s3api head-object --bucket "$BUCKET" --key code/latest.zip >/dev/null 2>&1 \
  || { echo "s3://$BUCKET/code/latest.zip missing: run infra/deploy_code.sh first." >&2; exit 1; }

if [ "$PLAIN" = 1 ]; then
  AMI_PARAM=/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id
else
  AMI_PARAM=/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id
fi
BASE_AMI=$(aws ssm get-parameter --name "$AMI_PARAM" --query Parameter.Value --output text)
if [ "$REHEARSAL" = 1 ]; then TYPE=t3.large; else TYPE=g6e.xlarge; fi

# ─── Remote steps (run on the instance as root through SSM Run Command) ────────────────────────

# Put in front of every step. Everything goes to the build log; only log() lines reach this Mac
# (SSM returns just the first 24,000 characters of output). On failure, the last 40 log lines are
# sent back, so the error is visible after the instance is gone.
step_preamble() {
  cat <<'EOF'
set -euo pipefail
export HOME=/root AWS_DEFAULT_REGION=us-east-1
LOG=/var/log/neurolens-build.log
exec 3>&1 >>"$LOG" 2>&1
log() { echo "$(date -u +%H:%M:%S) $*" | tee /dev/fd/3; }
EXIT_HOOKS=()
on_exit() {
  rc=$?
  for hook in "${EXIT_HOOKS[@]}"; do eval "$hook" || true; done
  if [ "$rc" != 0 ]; then echo "--- last 40 lines of $LOG ---" >&3; tail -n 40 "$LOG" >&3; fi
  exit "$rc"
}
trap on_exit EXIT
EOF
}

# §2 step 3: software. Same for the rehearsal and the real build.
step_install() {
  cat <<'EOF'
export DEBIAN_FRONTEND=noninteractive
cloud-init status --wait >/dev/null || true
APT="apt-get -q -y -o DPkg::Lock::Timeout=600"

log "[install] apt: ffmpeg, git, curl"
$APT update
$APT install ffmpeg git curl
if ! command -v aws >/dev/null; then
  log "[install] aws CLI missing (plain Ubuntu): installing v2"
  curl -sSfo /tmp/awscliv2.zip https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip
  python3 -m zipfile -e /tmp/awscliv2.zip /tmp/awscli && chmod -R +x /tmp/awscli/aws
  /tmp/awscli/aws/install && rm -rf /tmp/awscli /tmp/awscliv2.zip
fi

mkdir -p /opt/neurolens/cache /opt/neurolens/bin
DISK=$(lsblk -dno NAME,MODEL | awk '/Instance Storage/ {print "/dev/" $1; exit}')
if [ -n "$DISK" ]; then
  mountpoint -q /opt/neurolens/cache || { mkfs.ext4 -q -F "$DISK"; mount "$DISK" /opt/neurolens/cache; }
  log "[install] fast local disk $DISK mounted at /opt/neurolens/cache"
else
  log "[install] no instance-store disk: /opt/neurolens/cache is a folder on the root disk"
fi

log "[install] Python 3.12 (uv) and the virtualenv"
export UV_PYTHON_INSTALL_DIR=/opt/neurolens/python UV_CACHE_DIR=/opt/neurolens/cache/uv
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
uv python install 3.12
[ -x /opt/neurolens/venv/bin/python ] || uv venv --python 3.12 /opt/neurolens/venv

log "[install] code bundle"
rm -rf /opt/neurolens/app && mkdir -p /opt/neurolens/app
aws s3 cp "s3://$BUCKET/code/latest.zip" /tmp/neurolens-code.zip --only-show-errors
/opt/neurolens/venv/bin/python -m zipfile -e /tmp/neurolens-code.zip /opt/neurolens/app
rm /tmp/neurolens-code.zip

log "[install] requirements/model.txt (torch with CUDA libraries)"
uv pip install --python /opt/neurolens/venv/bin/python -r /opt/neurolens/app/requirements/model.txt

log "[install] worker units and scripts (worker service left disabled)"
install -m 755 /opt/neurolens/app/infra/pull_code.sh /opt/neurolens/app/infra/self_terminate.sh /opt/neurolens/bin/
install -m 644 /opt/neurolens/app/infra/neurolens-worker.service \
  /opt/neurolens/app/infra/neurolens-self-terminate.service /etc/systemd/system/
systemctl daemon-reload
systemctl disable neurolens-worker || true

log "[install] checks: $(systemd --version | head -1)"
test "$(systemd --version | awk 'NR==1 {print $2}')" -ge 249   # OnSuccess= needs 249+
test "$(systemctl is-enabled neurolens-worker || true)" = disabled
command -v ffmpeg ffprobe aws >/dev/null
# Imports as the worker does: `python worker.py` from the app folder puts it on the import path.
# (Assigned first: a failure inside "$(...)" used as an argument would not stop the step.)
VERSIONS=$(cd /opt/neurolens/app && /opt/neurolens/venv/bin/python -c '
import sys, torch, tribev2, nilearn, boto3, transformers, neurolens.worker, neurolens.inference
print("python", sys.version.split()[0], "| torch", torch.__version__, "| torch CUDA", torch.version.cuda,
      "| GPU visible", torch.cuda.is_available())' | tail -n 1)
log "[install] $VERSIONS"
if command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null 2>&1; then
  log "[install] GPU: $(nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader), $(nvidia-smi | grep -o 'CUDA Version: [0-9.]*')"
  /opt/neurolens/venv/bin/python -c "import torch; assert torch.cuda.is_available(), 'torch cannot see the GPU'"
fi
log "[install] INSTALL OK"
EOF
}

# §2 steps 4-7: weights through the worker's own code, offline proof, peak RAM/VRAM, sync to S3.
step_weights() {
  cat <<'EOF'
APP=/opt/neurolens/app; PY=/opt/neurolens/venv/bin/python; CACHE=/opt/neurolens/cache
echo "$CONFIG_B64" | base64 -d > "$APP/config.json"
aws s3 cp "s3://$BUCKET/smoke/clip.mp4" /tmp/neurolens-clip.mp4 --only-show-errors

cat > /tmp/neurolens-pipeline.py <<'PY'
"""The worker's pipeline once on the smoke clip: both passes, so every encoder loads."""
import json, logging, sys
from pathlib import Path
from neurolens import engagement, inference, settings
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
cfg = settings.load_settings()
inference.load_model(cfg)
clip = Path(sys.argv[1]); silent_clip = clip.with_suffix(".noaudio.mp4")
print("MARK full-pass start", flush=True)
full = inference.run_inference(clip)
print("MARK full-pass end", flush=True)
inference.strip_audio(clip, silent_clip)
silent = inference.run_inference(silent_clip)
engagement.extract_engagement(full, silent, inference.roi_masks())
print("PIPELINE OK", full.shape, silent.shape, "gpu:", json.dumps(inference.gpu_info()), flush=True)
PY

run_pipeline() {  # $1 clip, $2 its own output file. Fails if speech was not transcribed.
  (cd "$APP" && NEUROLENS_ROOT="$APP" PYTHONPATH="$APP" PYTHONUNBUFFERED=1 "$PY" /tmp/neurolens-pipeline.py "$1") \
    2>&1 | tee "$2"
  # The clip has speech: "whisperx failed" in the with-audio pass means the text features (and the
  # Llama model) were silently skipped, so the image and the weights would be incomplete.
  if sed -n '/MARK full-pass start/,/MARK full-pass end/p' "$2" | grep -qi "whisperx failed"; then
    log "[weights] FAILED: WhisperX transcribed no speech from a clip with speech (text features skipped)"
    return 1
  fi
  log "[weights] $(grep 'PIPELINE OK' "$2" | tail -n 1)"
}

if [ "$REFRESH" = 0 ] && [ -n "$(aws s3 ls "s3://$BUCKET/models/" | head -n 1)" ]; then
  log "[weights] models/ already in S3: syncing it down instead of downloading (--refresh-weights redoes it)"
  aws s3 sync "s3://$BUCKET/models/" "$CACHE/" --only-show-errors
  DOWNLOADED=0
else
  log "[weights] full pipeline with the HuggingFace token (downloads ~20 GB)"
  ( peak=0
    while sleep 1; do
      used=$(awk '/MemTotal/ {t=$2} /MemAvailable/ {a=$2} END {print t-a}' /proc/meminfo)
      if [ "$used" -gt "$peak" ]; then peak=$used; echo "$peak" > /tmp/neurolens-peak-ram-kb; fi
    done ) 3>&- &
  SAMPLER=$!
  # A leftover sampler would hold this step open until its deadline (GPU billing).
  EXIT_HOOKS+=('kill "$SAMPLER" 2>/dev/null')
  # The token lives only in this subshell's environment (settings.apply_env reads HF_TOKEN), never
  # in a file.
  ( HF_TOKEN=$(aws ssm get-parameter --name /neurolens/hf_token --with-decryption \
      --query Parameter.Value --output text)
    export HF_TOKEN
    run_pipeline /tmp/neurolens-clip.mp4 /tmp/neurolens-run1.log )
  kill "$SAMPLER"
  log "[weights] PEAK RAM: $(awk '{printf "%.1f GB", $1/1048576}' /tmp/neurolens-peak-ram-kb)"
  DOWNLOADED=1
fi

# A changed copy under a new name, so no per-video feature cache can stand in for the models.
# Positive evidence that the text features ran: the Llama model's weights are in the cache (sturdier
# than the absence of a log message, whose wording a tribev2 update could change).
LLAMA=$("$PY" -c 'import json, sys; print(json.load(open(sys.argv[1]))["model"]["llama_repo_id"])' "$APP/config.json")
LLAMA_DIR="$CACHE/models/hub/models--${LLAMA//\//--}"
if ! find "$LLAMA_DIR" -name "*.safetensors" 2>/dev/null | grep -q .; then
  log "[weights] FAILED: no $LLAMA weights in $LLAMA_DIR: the text features did not run"
  exit 1
fi
log "[weights] $LLAMA weights present: $(du -sh "$LLAMA_DIR" | cut -f1)"

log "[weights] offline proof: fresh process, HF_HUB_OFFLINE=1, no token, a different file"
ffmpeg -y -loglevel error -i /tmp/neurolens-clip.mp4 -c copy -metadata comment=offline-proof /tmp/neurolens-offline.mp4
( unset HF_TOKEN; export HF_HUB_OFFLINE=1
  run_pipeline /tmp/neurolens-offline.mp4 /tmp/neurolens-run2.log )

if [ "$DOWNLOADED" = 1 ]; then
  T=$(aws ssm get-parameter --name /neurolens/hf_token --with-decryption --query Parameter.Value --output text)
  HIT=$(find "$CACHE" -type f -size -1M -print0 | xargs -0 grep -lsF -- "$T" || true)
  unset T
  if [ -n "$HIT" ]; then log "[weights] FAILED: the token is in the cache: $HIT"; exit 1; fi
  log "[weights] weights to s3://$BUCKET/models/ (no xet download cache, no token files)"
  aws s3 sync "$CACHE/" "s3://$BUCKET/models/" --only-show-errors \
    --exclude "models/xet/*" --exclude "uv/*" --exclude "lost+found/*" \
    --exclude "*/token" --exclude "*/stored_tokens"
fi
log "[weights] cache: $(du -sh "$CACHE/models" "$CACHE/data" 2>/dev/null | tr '\n\t' '  ')"
log "[weights] outside the cache, kept in the image: $(du -sh /root/.cache/* 2>/dev/null | tr '\n\t' '  ')"
log "[weights] WEIGHTS OK"
EOF
}

# §2 step 8: software only in the image. Proves no file outside the cache still holds the token.
# /root/.cache is kept: anything the libraries put there at run time (seen in the weights step's
# last line) must also be there on the workers.
step_scrub() {
  cat <<'EOF'
rm -rf /opt/neurolens/app /opt/neurolens/output /tmp/neurolens-*
T=$(aws ssm get-parameter --name /neurolens/hf_token --with-decryption --query Parameter.Value --output text)
FOUND=$(grep -rlsF --exclude-dir=cache -- "$T" /opt /root /home /etc /tmp /var/log /var/lib/amazon /var/lib/cloud || true)
unset T
if [ -n "$FOUND" ]; then log "[scrub] TOKEN FOUND in: $FOUND"; exit 1; fi
log "[scrub] no file outside the cache holds the token"
sync
umount /opt/neurolens/cache 2>/dev/null || true
log "[scrub] SCRUB OK"
EOF
}

# ─── Laptop side ───────────────────────────────────────────────────────────────────────────────

INSTANCE=""
cleanup() {
  set +e
  trap '' INT
  # Terminate first, print after: a closed terminal must not stop the terminate call. Also catch
  # an instance launched just before a Ctrl-C, whose ID this script never received.
  IDS=$(aws ec2 describe-instances --filters Name=tag:Project,Values=neurolens Name=tag:Role,Values=build \
        Name=instance-state-name,Values=pending,running,stopping,stopped \
        --query 'Reservations[].Instances[].InstanceId' --output text)
  IDS=$(echo "$INSTANCE $IDS" | tr ' \t' '\n\n' | sort -u | tr '\n' ' ')
  if [ -n "${IDS// /}" ]; then
    # shellcheck disable=SC2086
    if aws ec2 terminate-instances --instance-ids $IDS --output text >/dev/null; then
      say "terminated build instance(s): $IDS"
    else
      echo "!! could not terminate $IDS: terminate it in the EC2 console NOW (it bills by the second)" >&2
    fi
  fi
}
trap cleanup EXIT

ssm_run() {  # $1 label, $2 deadline in seconds; stdin: the step script; env lines from $STEP_ENV
  local label=$1 timeout=$2 b64 params cmd status errors=0 deadline
  b64=$( { printf '%s\n' "${STEP_ENV:-}"; step_preamble; cat; } | base64 | tr -d '\n')
  params=$(python3 -c 'import json,sys; print(json.dumps({
    "commands": ["echo " + sys.argv[1] + " | base64 -d > /tmp/neurolens-step.sh",
                 "bash /tmp/neurolens-step.sh; rc=$?; rm -f /tmp/neurolens-step.sh; exit $rc"],
    "executionTimeout": [sys.argv[2]]}))' "$b64" "$timeout")
  cmd=$(aws ssm send-command --instance-ids "$INSTANCE" --document-name AWS-RunShellScript \
        --comment "neurolens build: $label" --parameters "$params" --query Command.CommandId --output text)
  say "$label: running (full log on the instance: /var/log/neurolens-build.log)"
  deadline=$(( $(date +%s) + timeout + 600 ))
  while :; do
    sleep 20
    if status=$(aws ssm get-command-invocation --command-id "$cmd" --instance-id "$INSTANCE" \
                 --query Status --output text 2>/dev/null); then
      errors=0
    else
      errors=$((errors + 1)); status=Unknown
      [ "$errors" -ge 10 ] && { say "FAILED: cannot read the status of step $label (10 errors in a row)"; exit 1; }
    fi
    case "$status" in Pending|InProgress|Delayed|Unknown) ;; *) break ;; esac
    [ "$(date +%s)" -gt "$deadline" ] && { say "FAILED: step $label passed its deadline"; exit 1; }
  done
  aws ssm get-command-invocation --command-id "$cmd" --instance-id "$INSTANCE" \
    --query StandardOutputContent --output text | tail -n 60
  [ "$status" = Success ] || { say "FAILED at step: $label ($status)"; exit 1; }
}

if [ "$REHEARSAL" = 0 ]; then
  say "uploading the smoke clip to s3://$BUCKET/smoke/clip.mp4"
  aws s3 cp "$CLIP" "s3://$BUCKET/smoke/clip.mp4" --only-show-errors
fi

say "launching $TYPE from $(aws ec2 describe-images --image-ids "$BASE_AMI" --query 'Images[0].Name' --output text)"
# Dead-man switch: the instance shuts itself down after 4 hours, and a shutdown from inside means
# terminate. The script's own stop-instances call (before the image) is an API stop, not affected.
# A GPU type can be sold out in one zone: try the next zone's subnet on InsufficientInstanceCapacity
# only; any other error stops here.
for SUBNET in $SUBNETS; do
  if OUT=$(aws ec2 run-instances --image-id "$BASE_AMI" --instance-type "$TYPE" \
      --subnet-id "$SUBNET" --security-group-ids "$SG" --iam-instance-profile "Name=$PROFILE" \
      --metadata-options HttpTokens=required --instance-initiated-shutdown-behavior terminate \
      --user-data $'#!/bin/bash\nshutdown -h +240 "neurolens build: 4-hour limit"' \
      --tag-specifications \
        'ResourceType=instance,Tags=[{Key=Project,Value=neurolens},{Key=Name,Value=neurolens-build},{Key=Role,Value=build}]' \
        'ResourceType=volume,Tags=[{Key=Project,Value=neurolens},{Key=Name,Value=neurolens-build}]' \
      --query 'Instances[0].[InstanceId,Placement.AvailabilityZone]' --output text 2>&1); then
    read -r INSTANCE ZONE <<<"$OUT"
    break
  fi
  case "$OUT" in
    *InsufficientInstanceCapacity*) say "no $TYPE capacity in subnet $SUBNET's zone; trying the next" ;;
    *) echo "$OUT" >&2; exit 1 ;;
  esac
done
[ -n "$INSTANCE" ] || { say "FAILED: no zone has $TYPE capacity right now; try again later"; exit 1; }
say "instance $INSTANCE launched in $ZONE (billed from now); waiting for Session Manager"
aws ec2 wait instance-running --instance-ids "$INSTANCE"
for i in $(seq 1 60); do
  [ "$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$INSTANCE" \
        --query 'InstanceInformationList[0].PingStatus' --output text)" = Online ] && break
  [ "$i" = 60 ] && { say "FAILED: instance never came Online in Session Manager"; exit 1; }
  sleep 10
done

STEP_ENV="export BUCKET=$BUCKET"
step_install | ssm_run "install software" 3600

if [ "$REHEARSAL" = 1 ]; then
  say "REHEARSAL OK: the software install works on $TYPE."
  exit 0
fi

CONFIG_B64=$(python3 -c 'import json; c = json.load(open("config.json")); c["paths"] = {
  "models": "/opt/neurolens/cache/models", "data": "/opt/neurolens/cache/data", "output": "/opt/neurolens/output"}
print(json.dumps(c, indent=2))' | base64 | tr -d '\n')
STEP_ENV="export BUCKET=$BUCKET REFRESH=$REFRESH CONFIG_B64=$CONFIG_B64"
step_weights | ssm_run "weights and pipeline" 7200
STEP_ENV=""
step_scrub | ssm_run "scrub" 1800

say "stopping the instance for a consistent image"
aws ec2 stop-instances --instance-ids "$INSTANCE" --output text >/dev/null
aws ec2 wait instance-stopped --instance-ids "$INSTANCE"
# Highest existing version + 1 (a count would reuse a name after an old image is deleted).
LAST=$(aws ec2 describe-images --owners self --filters "Name=name,Values=neurolens-worker-v*" \
       --query 'Images[].Name' --output text | tr '\t' '\n' | sed -n 's/^neurolens-worker-v\([0-9]*\)$/\1/p' \
       | sort -n | tail -n 1)
NAME="neurolens-worker-v$(( ${LAST:-0} + 1 ))"
AMI=$(aws ec2 create-image --instance-id "$INSTANCE" --name "$NAME" \
  --description "NeuroLens worker software (no weights, no token), code $(git rev-parse --short HEAD)" \
  --tag-specifications "ResourceType=image,Tags=[{Key=Project,Value=neurolens},{Key=Name,Value=$NAME}]" \
                       "ResourceType=snapshot,Tags=[{Key=Project,Value=neurolens},{Key=Name,Value=$NAME}]" \
  --query ImageId --output text)
say "image $AMI ($NAME) being created; waiting until available (often 10-30 minutes; the stopped instance costs only its disk)"
for i in $(seq 1 180); do
  state=$(aws ec2 describe-images --image-ids "$AMI" --query 'Images[0].State' --output text)
  [ "$state" = available ] && break
  [ "$state" = pending ] || { say "FAILED: image state is $state"; exit 1; }
  [ "$i" = 180 ] && { say "FAILED: image not available after 90 minutes (check it in the console)"; exit 1; }
  sleep 30
done
SNAP=$(aws ec2 describe-images --image-ids "$AMI" \
  --query 'Images[0].BlockDeviceMappings[?Ebs].Ebs.SnapshotId | [0]' --output text)
SIZE=$(aws ec2 describe-snapshots --snapshot-ids "$SNAP" \
  --query 'Snapshots[0].[VolumeSize,FullSnapshotSizeInBytes]' --output text)
say "IMAGE READY: $AMI ($NAME). Snapshot $SNAP, volume/full size (GB, bytes): $SIZE"
say "Next: put worker_ami_id = \"$AMI\" in infra/terraform/terraform.tfvars, plan and apply."
