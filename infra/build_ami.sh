#!/usr/bin/env bash
# Builds the GPU worker image (M2a §2). Run from your Mac.
#
#   infra/build_ami.sh --cpu-rehearsal
#       t3.large (about 8 cents an hour): installs the NVIDIA driver (with its reboot) and the
#       software only (§2 steps 3-4), prints success or the failing step, and always terminates.
#       No token, no weights, no image.
#   infra/build_ami.sh <clip.mp4> [--refresh-weights]
#       g6e.xlarge on-demand ($1.86 an hour, about an hour; g6e.2xlarge at $2.24 if the smaller
#       size is sold out in every zone): full build, weights to S3 models/,
#       then the software-only image. Prints the AMI ID, snapshot size, peak RAM and VRAM.
#
# Base image: plain Ubuntu 22.04 plus the NVIDIA server driver (§2).
# Needs: terraform applied (network, roles), and infra/deploy_code.sh run (code/latest.zip).
# Money guards: the build instance is terminated when this script exits (errors and Ctrl-C too);
# every remote step has a deadline; and the instance shuts itself down (= terminates) after 4 hours
# even if this Mac sleeps or loses its connection.
# The HuggingFace token never touches this Mac: the instance reads it from Parameter Store.
set -euo pipefail
source "$(dirname "$0")/aws_env.sh"   # AWS_PROFILE, AWS_REGION (M2a §4i)
cd "$(git rev-parse --show-toplevel)"

REHEARSAL=0; REFRESH=0; CLIP=""
for arg in "$@"; do
  case "$arg" in
    --cpu-rehearsal) REHEARSAL=1 ;;
    --refresh-weights) REFRESH=1 ;;
    -*) echo "unknown option $arg" >&2; exit 2 ;;
    *) CLIP="$arg" ;;
  esac
done
if [ "$REHEARSAL" = 0 ] && [ ! -f "$CLIP" ]; then
  echo "usage: $0 --cpu-rehearsal  |  $0 <clip.mp4> [--refresh-weights]" >&2; exit 2
fi

say() { echo "$(date +%H:%M:%S) $*"; }
tf() { terraform -chdir=infra/terraform output -raw "$1"; }
BUCKET=$(tf bucket_name); SUBNETS=$(tf build_subnet_ids)
SG=$(tf no_inbound_security_group_id); PROFILE=$(tf build_instance_profile)

aws s3api head-object --bucket "$BUCKET" --key code/latest.zip >/dev/null 2>&1 \
  || { echo "s3://$BUCKET/code/latest.zip missing: run infra/deploy_code.sh first." >&2; exit 1; }

AMI_PARAM=/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id
BASE_AMI=$(aws ssm get-parameter --name "$AMI_PARAM" --query Parameter.Value --output text)
# Root disk: 50 GB gp3 (the base image's own 8 GB is too small; this size becomes the image's). It
# must be the image's root device.
ROOT_DEV=$(aws ec2 describe-images --image-ids "$BASE_AMI" --query 'Images[0].RootDeviceName' --output text)
ROOT_GB=50
# Tried in order. g6e.2xlarge has the same GPU (more RAM and CPU, $2.24 an hour): only a fallback
# when the smaller size is sold out everywhere. The image works on either; workers use the Terraform
# worker_instance_types, whatever built the image.
if [ "$REHEARSAL" = 1 ]; then TYPES=t3.large; else TYPES="g6e.xlarge g6e.2xlarge"; fi

# ─── Remote steps (run on the instance as root through SSM Run Command) ────────────────────────

# Put in front of every step. Everything goes to the build log; only log() lines reach this Mac
# (SSM returns just the first 24,000 characters of output). On failure, the last 40 log lines are
# sent back, so the error is visible after the instance is gone.
step_preamble() {
  cat <<'EOF'
set -euo pipefail
export HOME=/root AWS_DEFAULT_REGION=$REGION
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

# §2 step 3: the NVIDIA driver, from Ubuntu's own packages: prebuilt, signed kernel modules for the
# AWS kernel (no DKMS compile) and the headless user-space libraries plus nvidia-smi. Branch 570
# supports CUDA 12.4, which torch 2.6's wheels bring with them. The modules package may pull a
# newer kernel; the reboot that follows starts it.
step_driver() {
  cat <<'EOF'
export DEBIAN_FRONTEND=noninteractive
cloud-init status --wait >/dev/null || true
APT="apt-get -q -y -o DPkg::Lock::Timeout=600"
BRANCH=570
log "[driver] apt: NVIDIA $BRANCH-server driver (prebuilt modules for the AWS kernel), running kernel $(uname -r)"
$APT update
$APT install "linux-modules-nvidia-$BRANCH-server-aws" "nvidia-headless-no-dkms-$BRANCH-server" \
  "nvidia-utils-$BRANCH-server"
# The kernel the reboot will start must have the module, or the GPU would be missing after it.
NEWEST=$(ls -1 /lib/modules | sort -V | tail -n 1)
find "/lib/modules/$NEWEST" -name 'nvidia.ko*' | grep -q . \
  || { log "[driver] FAILED: no nvidia module for kernel $NEWEST"; exit 1; }
log "[driver] module present for kernel $NEWEST: $(dpkg-query -W -f '${Version}' "nvidia-utils-$BRANCH-server")"
log "[driver] DRIVER INSTALLED (reboot next)"
EOF
}

# After the reboot: the right kernel runs, the module loads, and (on a GPU) the driver sees the GPU.
step_driver_check() {
  cat <<'EOF'
log "[driver] after reboot: kernel $(uname -r)"
# The money guard: a reboot cancels a scheduled shutdown; the deadline unit must have re-armed it.
test -f /run/systemd/shutdown/scheduled \
  || { log "[driver] FAILED: the 4-hour shutdown was not re-armed after the reboot"; exit 1; }
log "[driver] 4-hour shutdown re-armed: $(sed -n 's/^USEC=//p' /run/systemd/shutdown/scheduled | cut -c1-10 | xargs -I{} date -u -d @{} +%H:%M) UTC"
modinfo -F version nvidia >/dev/null || { log "[driver] FAILED: no nvidia module for the running kernel"; exit 1; }
if grep -qsx 0x10de /sys/bus/pci/devices/*/vendor; then   # 0x10de: NVIDIA's PCI vendor ID
  nvidia-smi >/dev/null || { log "[driver] FAILED: nvidia-smi cannot talk to the GPU"; exit 1; }
  log "[driver] GPU: $(nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader), $(nvidia-smi | grep -o 'CUDA Version: [0-9.]*')"
else
  log "[driver] no NVIDIA GPU on this machine (CPU rehearsal): GPU check skipped"
fi
log "[driver] DRIVER OK"
EOF
}

# §2 step 4: software. Same for the rehearsal and the real build.
step_install() {
  cat <<'EOF'
export DEBIAN_FRONTEND=noninteractive
cloud-init status --wait >/dev/null || true
APT="apt-get -q -y -o DPkg::Lock::Timeout=600"

# Ubuntu's automatic package updates would start at every worker boot, through the small NAT
# instance, holding the apt lock: off in the image.
log "[install] automatic package updates off"
systemctl disable --now apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service 2>/dev/null || true
for unit in apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service; do
  if systemctl is-enabled "$unit" 2>/dev/null | grep -qx enabled; then log "[install] FAILED: $unit still enabled"; exit 1; fi
done

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
# Keep in step with worker_userdata.sh.tftpl. The Deep Learning base image mounts the instance-store
# disk itself at boot (so mkfs is refused): reuse that mount when there is one.
lsblk -o NAME,MODEL,SIZE,TYPE,MOUNTPOINT
DISK=$(lsblk -dno NAME,MODEL | awk '/Instance Storage/ {print "/dev/" $1; exit}')
if mountpoint -q /opt/neurolens/cache; then
  log "[install] /opt/neurolens/cache already mounted"
elif [ -z "$DISK" ]; then
  log "[install] no instance-store disk: /opt/neurolens/cache is a folder on the root disk"
elif MNT=$(lsblk -nro MOUNTPOINT "$DISK" | grep -m1 .); then
  mount --bind "$MNT" /opt/neurolens/cache
  log "[install] fast local disk $DISK already mounted at $MNT by the base image: bound to /opt/neurolens/cache"
elif mkfs.ext4 -q -F "$DISK" && mount "$DISK" /opt/neurolens/cache; then
  log "[install] fast local disk $DISK formatted and mounted at /opt/neurolens/cache"
else
  log "[install] could not use $DISK: /opt/neurolens/cache is a folder on the root disk (slower)"
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
      "| transformers", transformers.__version__,
      "| GPU visible", torch.cuda.is_available())' | tail -n 1)
log "[install] $VERSIONS"
if command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null 2>&1; then
  log "[install] GPU: $(nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader), $(nvidia-smi | grep -o 'CUDA Version: [0-9.]*')"
  /opt/neurolens/venv/bin/python -c "import torch; assert torch.cuda.is_available(), 'torch cannot see the GPU'"
fi
log "[install] root disk (becomes the image): $(df -h --output=used,size / | tail -n 1)"
log "[install] INSTALL OK"
EOF
}

# §2 steps 5-8: weights through the worker's own code, offline proof, peak RAM/VRAM, sync to S3.
step_weights() {
  cat <<'EOF'
APP=/opt/neurolens/app; PY=/opt/neurolens/venv/bin/python; CACHE=/opt/neurolens/cache
echo "$CONFIG_B64" | base64 -d > "$APP/config.json"
# File names unique to this build: tribev2 keeps per-video features ("neuralset.extractors.*" folders
# in the cache), and a cached result for the same file would let a run skip the encoders.
RUN_ID=$(date +%s)
CLIP=/tmp/neurolens-clip-$RUN_ID.mp4; OFFLINE=/tmp/neurolens-offline-$RUN_ID.mp4
aws s3 cp "s3://$BUCKET/smoke/clip.mp4" "$CLIP" --only-show-errors

cat > /tmp/neurolens-pipeline.py <<'PY'
"""The worker's pipeline once on the smoke clip (its calls, §7a): both passes, so every encoder loads."""
import json, logging, sys
from pathlib import Path
from neurolens import engagement, inference, settings
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
cfg = settings.load_settings()
inference.load_model(cfg)
clip = Path(sys.argv[1])
duration = inference.probe_duration(clip)
events = inference.build_events(clip)
print("MARK full-pass start", flush=True)
full = inference.predict(events, duration)
print("MARK full-pass end", flush=True)
silent = inference.predict(inference.without_audio(events), duration)
engagement.extract_engagement(full, silent, inference.roi_masks())
print("PIPELINE OK", full.shape, silent.shape, "gpu:", json.dumps(inference.gpu_info()), flush=True)
PY

run_pipeline() {  # $1 clip, $2 its own output file. Fails if speech was not transcribed or an encoder did not run.
  touch "$2.start"
  (cd "$APP" && NEUROLENS_ROOT="$APP" PYTHONPATH="$APP" PYTHONUNBUFFERED=1 "$PY" /tmp/neurolens-pipeline.py "$1") \
    2>&1 | tee "$2"
  # The clip has speech: "whisperx failed" in the with-audio pass means the text features (and the
  # Llama model) were silently skipped, so the image and the weights would be incomplete.
  if sed -n '/MARK full-pass start/,/MARK full-pass end/p' "$2" | grep -qi "whisperx failed"; then
    log "[weights] FAILED: WhisperX transcribed no speech from a clip with speech (text features skipped)"
    return 1
  fi
  # Positive proof that the encoders ran in this run: each one wrote new features. (A run that reused
  # cached features would pass every other check while proving nothing about the models.)
  for enc in HuggingFaceVideo HuggingFaceText Wav2VecBert; do
    if ! find "$CACHE/models" -path "*neuralset.extractors.*$enc*" -type f -newer "$2.start" 2>/dev/null | grep -q .; then
      log "[weights] FAILED: the $enc encoder wrote no new features: it did not run on $1"
      return 1
    fi
  done
  log "[weights] $(grep 'PIPELINE OK' "$2" | tail -n 1) (video, text and audio encoders ran)"
}

if [ "$REFRESH" = 0 ] && [ -n "$(aws s3 ls "s3://$BUCKET/models/" | head -n 1)" ]; then
  log "[weights] models/ already in S3: syncing it down instead of downloading (--refresh-weights redoes it)"
  aws s3 sync "s3://$BUCKET/models/" "$CACHE/" --only-show-errors --exclude "*/blobs/*" \
    --exclude "models/neuralset.extractors.*"
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
    run_pipeline "$CLIP" /tmp/neurolens-run1.log )
  kill "$SAMPLER"
  log "[weights] PEAK RAM: $(awk '{printf "%.1f GB", $1/1048576}' /tmp/neurolens-peak-ram-kb)"
  DOWNLOADED=1
fi

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
# A changed copy under a new name, so the second run cannot reuse the first run's features either.
ffmpeg -y -loglevel error -i "$CLIP" -c copy -metadata comment="offline-proof $RUN_ID" "$OFFLINE"
( unset HF_TOKEN; export HF_HUB_OFFLINE=1
  run_pipeline "$OFFLINE" /tmp/neurolens-run2.log )

if [ "$DOWNLOADED" = 1 ]; then
  T=$(aws ssm get-parameter --name /neurolens/hf_token --with-decryption --query Parameter.Value --output text)
  HIT=$(find "$CACHE" -type f -size -1M -print0 | xargs -0 grep -lsF -- "$T" || true)
  unset T
  if [ -n "$HIT" ]; then log "[weights] FAILED: the token is in the cache: $HIT"; exit 1; fi
  # The sync follows HuggingFace's snapshot symlinks, so snapshots/ holds full copies; blobs/ would
  # be the same bytes again.
  # Weights only: per-video features (neuralset.extractors.*) stay on this machine; in S3 they would
  # reach every later build and let its pipeline check skip the encoders.
  log "[weights] weights to s3://$BUCKET/models/ (no xet download cache, no blobs, no features, no token files)"
  aws s3 sync "$CACHE/" "s3://$BUCKET/models/" --only-show-errors \
    --exclude "models/xet/*" --exclude "uv/*" --exclude "lost+found/*" --exclude "*/blobs/*" \
    --exclude "models/neuralset.extractors.*" --exclude "*/token" --exclude "*/stored_tokens"
fi
log "[weights] cache: $(du -sh "$CACHE/models" "$CACHE/data" 2>/dev/null | tr '\n\t' '  ')"
log "[weights] outside the cache, kept in the image: $(du -sh /root/.cache/* 2>/dev/null | tr '\n\t' '  ')"
log "[weights] WEIGHTS OK"
EOF
}

# §2 step 9: software only in the image. Proves no file outside the cache still holds the token.
# /root/.cache is kept: anything the libraries put there at run time (seen in the weights step's
# last line) must also be there on the workers.
step_scrub() {
  cat <<'EOF'
rm -rf /opt/neurolens/app /tmp/neurolens-*
# The build's 4-hour deadline must not reach the image: on a worker it would shut the machine down.
systemctl disable neurolens-build-deadline.service 2>/dev/null || true
rm -f /etc/systemd/system/neurolens-build-deadline.service /usr/local/sbin/neurolens-build-deadline \
  /var/lib/neurolens-build-deadline
systemctl daemon-reload
if ls /etc/systemd/system/neurolens-build-deadline.service /etc/systemd/system/*/neurolens-build-deadline.service \
     /usr/local/sbin/neurolens-build-deadline /var/lib/neurolens-build-deadline 2>/dev/null | grep -q .; then
  log "[scrub] FAILED: the build deadline is still installed"; exit 1
fi
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
  b64=$( { printf 'export REGION=%s\n%s\n' "$AWS_REGION" "${STEP_ENV:-}"; step_preamble; cat; } | base64 | tr -d '\n')
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

remote_boot_id() {  # prints the instance's current boot ID, or nothing if it cannot be reached now
  local cmd
  cmd=$(aws ssm send-command --instance-ids "$INSTANCE" --document-name AWS-RunShellScript \
        --comment "neurolens build: boot id" --timeout-seconds 30 \
        --parameters 'commands=["cat /proc/sys/kernel/random/boot_id"]' \
        --query Command.CommandId --output text 2>/dev/null) || return 0
  aws ssm wait command-executed --command-id "$cmd" --instance-id "$INSTANCE" 2>/dev/null || return 0
  aws ssm get-command-invocation --command-id "$cmd" --instance-id "$INSTANCE" \
    --query StandardOutputContent --output text 2>/dev/null | tr -d '[:space:]' || true
}

# A build machine that is still shutting down (e.g. a failed run moments ago) bills nothing but still
# counts against the vCPU quota (VcpuLimitExceeded): wait for it first.
OLD=$(aws ec2 describe-instances --filters Name=tag:Project,Values=neurolens Name=tag:Role,Values=build \
      Name=instance-state-name,Values=shutting-down --query 'Reservations[].Instances[].InstanceId' --output text)
if [ -n "$OLD" ]; then
  say "waiting for the previous build machine to finish terminating (it holds the vCPU quota): $OLD"
  # shellcheck disable=SC2086
  aws ec2 wait instance-terminated --instance-ids $OLD
fi

if [ "$REHEARSAL" = 0 ]; then
  say "uploading the smoke clip to s3://$BUCKET/smoke/clip.mp4"
  aws s3 cp "$CLIP" "s3://$BUCKET/smoke/clip.mp4" --only-show-errors
fi

say "launching ($TYPES) from $(aws ec2 describe-images --image-ids "$BASE_AMI" --query 'Images[0].Name' --output text)"
# Dead-man switch: the instance shuts itself down after 4 hours, and a shutdown from inside means
# terminate. The script's own stop-instances call (before the image) is an API stop, not affected.
# A reboot cancels a scheduled shutdown, so the deadline is also written to a file and a boot-time
# unit re-arms it for the time left (the scrub step removes both before the image is made).
read -r -d '' USER_DATA <<'UD' || true   # read -d '' ends at end of input with status 1
#!/bin/bash
echo $(( $(date +%s) + 240 * 60 )) > /var/lib/neurolens-build-deadline
cat > /usr/local/sbin/neurolens-build-deadline <<'SH'
#!/bin/bash
left=$(( ( $(cat /var/lib/neurolens-build-deadline) - $(date +%s) ) / 60 ))
shutdown -h "+$(( left > 0 ? left : 0 ))" "neurolens build: 4-hour limit"
SH
chmod 755 /usr/local/sbin/neurolens-build-deadline
cat > /etc/systemd/system/neurolens-build-deadline.service <<'UNIT'
[Unit]
Description=NeuroLens build machine: shut down at the 4-hour deadline (re-armed after a reboot)
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/neurolens-build-deadline
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now neurolens-build-deadline.service
UD
# A GPU type can be sold out in one zone: try the next zone's subnet on InsufficientInstanceCapacity
# only; any other error stops here.
for TYPE in $TYPES; do
 for SUBNET in $SUBNETS; do
  if OUT=$(aws ec2 run-instances --image-id "$BASE_AMI" --instance-type "$TYPE" \
      --subnet-id "$SUBNET" --security-group-ids "$SG" --iam-instance-profile "Name=$PROFILE" \
      --metadata-options HttpTokens=required --instance-initiated-shutdown-behavior terminate \
      --user-data "$USER_DATA" \
      --block-device-mappings "DeviceName=$ROOT_DEV,Ebs={VolumeSize=$ROOT_GB,VolumeType=gp3,DeleteOnTermination=true}" \
      --tag-specifications \
        'ResourceType=instance,Tags=[{Key=Project,Value=neurolens},{Key=Name,Value=neurolens-build},{Key=Role,Value=build}]' \
        'ResourceType=volume,Tags=[{Key=Project,Value=neurolens},{Key=Name,Value=neurolens-build}]' \
      --query 'Instances[0].[InstanceId,Placement.AvailabilityZone]' --output text 2>&1); then
    read -r INSTANCE ZONE <<<"$OUT"
    break 2
  fi
  case "$OUT" in
    *InsufficientInstanceCapacity*) say "no $TYPE capacity in subnet $SUBNET's zone; trying the next" ;;
    *) echo "$OUT" >&2; exit 1 ;;
  esac
 done
done
[ -n "$INSTANCE" ] || { say "FAILED: no zone has capacity for $TYPES right now; try again later"; exit 1; }
say "instance $INSTANCE ($TYPE) launched in $ZONE (billed from now); waiting for Session Manager"
aws ec2 wait instance-running --instance-ids "$INSTANCE"
for i in $(seq 1 60); do
  [ "$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$INSTANCE" \
        --query 'InstanceInformationList[0].PingStatus' --output text)" = Online ] && break
  [ "$i" = 60 ] && { say "FAILED: instance never came Online in Session Manager"; exit 1; }
  sleep 10
done

step_driver | ssm_run "NVIDIA driver" 1800
# The boot ID changes with every boot: a step that reports a new one ran after the reboot.
BOOT_BEFORE=$(remote_boot_id)
[ -n "$BOOT_BEFORE" ] || { say "FAILED: cannot read the instance's boot ID"; exit 1; }
say "rebooting to load the driver"
aws ec2 reboot-instances --instance-ids "$INSTANCE"
for i in $(seq 1 40); do
  sleep 15
  BOOT_NOW=$(remote_boot_id)
  [ -n "$BOOT_NOW" ] && [ "$BOOT_NOW" != "$BOOT_BEFORE" ] && break
  [ "$i" = 40 ] && { say "FAILED: the instance did not come back from its reboot within 10 minutes"; exit 1; }
done
say "back after the reboot"
step_driver_check | ssm_run "driver check" 600

STEP_ENV="export BUCKET=$BUCKET"
step_install | ssm_run "install software" 3600

if [ "$REHEARSAL" = 1 ]; then
  say "REHEARSAL OK: the driver and software install work on $TYPE."
  exit 0
fi

CONFIG_B64=$(python3 -c 'import json; c = json.load(open("config.json")); c["paths"] = {
  "models": "/opt/neurolens/cache/models", "data": "/opt/neurolens/cache/data"}
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
