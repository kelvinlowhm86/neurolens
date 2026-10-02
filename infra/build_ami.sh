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
# The build instance is ALWAYS terminated when this script exits, including on errors and Ctrl-C.
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
BUCKET=$(tf bucket_name); SUBNET=$(tf public_subnet_id)
SG=$(tf no_inbound_security_group_id); PROFILE=$(tf build_instance_profile)

aws s3api head-object --bucket "$BUCKET" --key code/latest.zip >/dev/null 2>&1 \
  || { echo "s3://$BUCKET/code/latest.zip missing: run infra/deploy_code.sh first." >&2; exit 1; }

if [ "$PLAIN" = 1 ]; then
  AMI_PARAM=/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id
else
  AMI_PARAM=/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id
fi
BASE_AMI=$(aws ssm get-parameter --name "$AMI_PARAM" --query Parameter.Value --output text)

if [ "$REHEARSAL" = 1 ]; then TYPE=t3.large; ON_SHUTDOWN=terminate; else TYPE=g6e.xlarge; ON_SHUTDOWN=stop; fi

# ─── Remote steps (run on the instance as root through SSM Run Command) ────────────────────────

# §2 step 3: software. Same for the rehearsal and the real build.
step_install() {
  cat <<'EOF'
set -euo pipefail
exec > >(tee -a /var/log/neurolens-build.log) 2>&1
export HOME=/root DEBIAN_FRONTEND=noninteractive
log() { echo "$(date -u +%H:%M:%S) [install] $*"; }
cloud-init status --wait >/dev/null || true
APT="apt-get -q -y -o DPkg::Lock::Timeout=600"

log "apt: ffmpeg, git, curl"
$APT update >/dev/null
$APT install ffmpeg git curl >/dev/null
if ! command -v aws >/dev/null; then
  log "aws CLI missing (plain Ubuntu): installing v2"
  curl -sSfo /tmp/awscliv2.zip https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip
  python3 -m zipfile -e /tmp/awscliv2.zip /tmp/awscli && chmod -R +x /tmp/awscli/aws
  /tmp/awscli/aws/install >/dev/null && rm -rf /tmp/awscli /tmp/awscliv2.zip
fi

log "fast local disk at /opt/neurolens/cache"
mkdir -p /opt/neurolens/cache /opt/neurolens/bin
DISK=$(lsblk -dno NAME,MODEL | awk '/Instance Storage/ {print "/dev/" $1; exit}')
if [ -n "$DISK" ]; then
  mountpoint -q /opt/neurolens/cache || { mkfs.ext4 -q -F "$DISK"; mount "$DISK" /opt/neurolens/cache; }
  log "mounted $DISK"
else
  log "no instance-store disk: using a folder on the root disk"
fi

log "Python 3.12 (uv) and the virtualenv"
export UV_PYTHON_INSTALL_DIR=/opt/neurolens/python UV_CACHE_DIR=/opt/neurolens/cache/uv
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh >/dev/null
uv python install 3.12 >/dev/null
[ -x /opt/neurolens/venv/bin/python ] || uv venv -q --python 3.12 /opt/neurolens/venv

log "code bundle"
rm -rf /opt/neurolens/app && mkdir -p /opt/neurolens/app
aws s3 cp "s3://$BUCKET/code/latest.zip" /tmp/neurolens-code.zip --only-show-errors
/opt/neurolens/venv/bin/python -m zipfile -e /tmp/neurolens-code.zip /opt/neurolens/app
rm /tmp/neurolens-code.zip

log "pip install -r requirements/model.txt (several minutes: torch with CUDA libraries)"
uv pip install -q --python /opt/neurolens/venv/bin/python -r /opt/neurolens/app/requirements/model.txt

log "worker units and scripts (worker service left disabled)"
install -m 755 /opt/neurolens/app/infra/pull_code.sh /opt/neurolens/app/infra/self_terminate.sh /opt/neurolens/bin/
install -m 644 /opt/neurolens/app/infra/neurolens-worker.service \
  /opt/neurolens/app/infra/neurolens-self-terminate.service /etc/systemd/system/
systemctl daemon-reload
systemctl disable neurolens-worker >/dev/null 2>&1 || true

log "checks"
systemd --version | head -1
test "$(systemd --version | awk 'NR==1 {print $2}')" -ge 249   # OnSuccess= needs 249+
test "$(systemctl is-enabled neurolens-worker || true)" = disabled
command -v ffmpeg ffprobe aws >/dev/null
cd /opt/neurolens/app
/opt/neurolens/venv/bin/python - <<'PY'
import sys, torch, tribev2, nilearn, boto3, transformers  # noqa: F401
print("python", sys.version.split()[0], "| torch", torch.__version__, "| torch CUDA", torch.version.cuda,
      "| GPU visible", torch.cuda.is_available())
PY
if command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
  nvidia-smi | grep -o "CUDA Version: [0-9.]*"
  /opt/neurolens/venv/bin/python -c "import torch; assert torch.cuda.is_available(), 'torch cannot see the GPU'"
fi
log "INSTALL OK"
EOF
}

# §2 steps 4-7: weights through the worker's own code, offline proof, peak RAM/VRAM, sync to S3.
step_weights() {
  cat <<'EOF'
set -euo pipefail
exec > >(tee -a /var/log/neurolens-build.log) 2>&1
export HOME=/root
log() { echo "$(date -u +%H:%M:%S) [weights] $*"; }
APP=/opt/neurolens/app; PY=/opt/neurolens/venv/bin/python
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
clip = Path(sys.argv[1]); silent_clip = clip.with_name("neurolens-clip.noaudio.mp4")
full = inference.run_inference(clip)
inference.strip_audio(clip, silent_clip)
silent = inference.run_inference(silent_clip)
engagement.extract_engagement(full, silent, inference.roi_masks())
print("PIPELINE OK", full.shape, silent.shape, "gpu:", json.dumps(inference.gpu_info()))
PY
run_pipeline() { (cd "$APP" && NEUROLENS_ROOT="$APP" "$PY" /tmp/neurolens-pipeline.py /tmp/neurolens-clip.mp4); }

if [ "$REFRESH" = 0 ] && [ -n "$(aws s3 ls "s3://$BUCKET/models/" | head -1)" ]; then
  log "models/ already in S3: syncing it down instead of downloading (use --refresh-weights to redo)"
  aws s3 sync "s3://$BUCKET/models/" /opt/neurolens/cache/ --only-show-errors
  DOWNLOADED=0
else
  log "full pipeline with the HuggingFace token (downloads ~20 GB)"
  ( umask 077
    printf 'HF_TOKEN=%s\n' "$(aws ssm get-parameter --name /neurolens/hf_token --with-decryption \
      --query Parameter.Value --output text)" > "$APP/.env" )
  ( peak=0
    while sleep 1; do
      used=$(awk '/MemTotal/ {t=$2} /MemAvailable/ {a=$2} END {print t-a}' /proc/meminfo)
      if [ "$used" -gt "$peak" ]; then peak=$used; echo "$peak" > /tmp/neurolens-peak-ram-kb; fi
    done ) &
  SAMPLER=$!
  # On any exit: a leftover sampler would hold this step open until its timeout (GPU billing).
  trap 'kill "$SAMPLER" 2>/dev/null || true; rm -f "$APP/.env"' EXIT
  run_pipeline
  kill "$SAMPLER"
  rm -f "$APP/.env"
  log "PEAK RAM: $(awk '{printf "%.1f GB", $1/1048576}' /tmp/neurolens-peak-ram-kb)"
  DOWNLOADED=1
fi

log "offline proof: fresh process, HF_HUB_OFFLINE=1, no token"
( unset HF_TOKEN; export HF_HUB_OFFLINE=1; run_pipeline )

if [ "$DOWNLOADED" = 1 ]; then
  log "weights to s3://$BUCKET/models/ (without the xet download cache)"
  aws s3 sync /opt/neurolens/cache/ "s3://$BUCKET/models/" --only-show-errors \
    --exclude "models/xet/*" --exclude "uv/*" --exclude "lost+found/*"
fi
du -sh /opt/neurolens/cache/models /opt/neurolens/cache/data 2>/dev/null || true
log "files written outside the cache (these end up in the image):"
du -sh /root/.cache/* 2>/dev/null || echo "  none under /root/.cache"
log "WEIGHTS OK"
EOF
}

# §2 step 8: software only in the image. Proves no file still holds the token.
step_scrub() {
  cat <<'EOF'
set -euo pipefail
exec > >(tee -a /var/log/neurolens-build.log) 2>&1
log() { echo "$(date -u +%H:%M:%S) [scrub] $*"; }
rm -rf /opt/neurolens/app /opt/neurolens/output /root/.cache/uv /tmp/neurolens-*
T=$(aws ssm get-parameter --name /neurolens/hf_token --with-decryption --query Parameter.Value --output text)
FOUND=$(grep -rlsF --exclude-dir=cache -- "$T" /opt /root /home /etc /tmp /var/log /var/lib/amazon 2>/dev/null || true)
unset T
if [ -n "$FOUND" ]; then log "TOKEN FOUND in: $FOUND"; exit 1; fi
log "no file outside the cache holds the token"
sync
umount /opt/neurolens/cache 2>/dev/null || true
log "SCRUB OK"
EOF
}

# ─── Laptop side ───────────────────────────────────────────────────────────────────────────────

INSTANCE=""
cleanup() {
  if [ -n "$INSTANCE" ]; then
    say "terminating build instance $INSTANCE"
    aws ec2 terminate-instances --instance-ids "$INSTANCE" --output text >/dev/null || \
      echo "!! could not terminate $INSTANCE: terminate it in the console NOW (it bills by the second)" >&2
  fi
}
trap cleanup EXIT

ssm_run() {  # $1 label, $2 timeout seconds; stdin: the script; extra env lines from $STEP_ENV
  local label=$1 timeout=$2 b64 params cmd status
  b64=$( { printf '%s\n' "${STEP_ENV:-}"; cat; } | base64 | tr -d '\n')
  params=$(python3 -c 'import json,sys; print(json.dumps({
    "commands": ["echo " + sys.argv[1] + " | base64 -d > /tmp/neurolens-step.sh",
                 "bash /tmp/neurolens-step.sh; rc=$?; rm -f /tmp/neurolens-step.sh; exit $rc"],
    "executionTimeout": [sys.argv[2]]}))' "$b64" "$timeout")
  cmd=$(aws ssm send-command --instance-ids "$INSTANCE" --document-name AWS-RunShellScript \
        --comment "neurolens build: $label" --parameters "$params" --query Command.CommandId --output text)
  say "$label: running (log on the instance: /var/log/neurolens-build.log)"
  while :; do
    sleep 20
    status=$(aws ssm get-command-invocation --command-id "$cmd" --instance-id "$INSTANCE" \
             --query Status --output text 2>/dev/null || echo Pending)
    case "$status" in Pending|InProgress|Delayed) continue ;; esac
    break
  done
  aws ssm get-command-invocation --command-id "$cmd" --instance-id "$INSTANCE" \
    --query StandardOutputContent --output text | tail -n 25
  if [ "$status" != Success ]; then
    aws ssm get-command-invocation --command-id "$cmd" --instance-id "$INSTANCE" \
      --query StandardErrorContent --output text | tail -n 25 >&2
    say "FAILED at step: $label ($status)"; exit 1
  fi
}

if [ "$REHEARSAL" = 0 ]; then
  say "uploading the smoke clip to s3://$BUCKET/smoke/clip.mp4"
  aws s3 cp "$CLIP" "s3://$BUCKET/smoke/clip.mp4" --only-show-errors
fi

say "launching $TYPE from $(aws ec2 describe-images --image-ids "$BASE_AMI" --query 'Images[0].Name' --output text)"
INSTANCE=$(aws ec2 run-instances --image-id "$BASE_AMI" --instance-type "$TYPE" \
  --subnet-id "$SUBNET" --security-group-ids "$SG" --iam-instance-profile "Name=$PROFILE" \
  --metadata-options HttpTokens=required --instance-initiated-shutdown-behavior "$ON_SHUTDOWN" \
  --tag-specifications \
    'ResourceType=instance,Tags=[{Key=Project,Value=neurolens},{Key=Name,Value=neurolens-build},{Key=Role,Value=build}]' \
    'ResourceType=volume,Tags=[{Key=Project,Value=neurolens},{Key=Name,Value=neurolens-build}]' \
  --query 'Instances[0].InstanceId' --output text)
say "instance $INSTANCE launched (billed from now); waiting for Session Manager"
aws ec2 wait instance-running --instance-ids "$INSTANCE"
for i in $(seq 1 60); do
  [ "$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$INSTANCE" \
        --query 'InstanceInformationList[0].PingStatus' --output text)" = Online ] && break
  [ "$i" = 60 ] && { say "FAILED: instance never came Online in Session Manager"; exit 1; }
  sleep 10
done

STEP_ENV="export BUCKET=$BUCKET"
step_install | ssm_run "install software" 5400

if [ "$REHEARSAL" = 1 ]; then
  say "REHEARSAL OK: the software install works on $TYPE. Terminating."
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
N=$(( $(aws ec2 describe-images --owners self --filters Name=tag:Project,Values=neurolens \
        "Name=name,Values=neurolens-worker-v*" --query 'length(Images)' --output text) + 1 ))
NAME="neurolens-worker-v$N"
AMI=$(aws ec2 create-image --instance-id "$INSTANCE" --name "$NAME" \
  --description "NeuroLens worker software (no weights, no token), code $(git rev-parse --short HEAD)" \
  --tag-specifications "ResourceType=image,Tags=[{Key=Project,Value=neurolens},{Key=Name,Value=$NAME}]" \
                       "ResourceType=snapshot,Tags=[{Key=Project,Value=neurolens},{Key=Name,Value=$NAME}]" \
  --query ImageId --output text)
say "image $AMI ($NAME) being created; waiting until available (often 10-30 minutes)"
until [ "$(aws ec2 describe-images --image-ids "$AMI" --query 'Images[0].State' --output text)" = available ]; do
  state=$(aws ec2 describe-images --image-ids "$AMI" --query 'Images[0].State' --output text)
  [ "$state" = failed ] && { say "FAILED: image creation failed"; exit 1; }
  sleep 30
done
SNAP=$(aws ec2 describe-images --image-ids "$AMI" \
  --query 'Images[0].BlockDeviceMappings[?Ebs].Ebs.SnapshotId | [0]' --output text)
SIZE=$(aws ec2 describe-snapshots --snapshot-ids "$SNAP" \
  --query 'Snapshots[0].[VolumeSize,FullSnapshotSizeInBytes]' --output text)
say "IMAGE READY: $AMI ($NAME). Snapshot $SNAP, volume/full size (GB, bytes): $SIZE"
say "Next: put worker_ami_id = \"$AMI\" in infra/terraform/terraform.tfvars, plan and apply."
