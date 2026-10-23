#!/bin/bash
#SBATCH --job-name=gaze-trial
#SBATCH --partition=gpu
#SBATCH --gres=gpu:h100-47:1
#SBATCH --time=01:00:00
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --output=gaze_trial_%j.log
#
# ViNet-S gaze heatmaps for the test clips (trial only, not part of the product).
# Submit from the repo root:  sbatch experiments/gaze_trial.sbatch
# The first run clones the pinned ViNet-S code and downloads its DHF1K checkpoint into ./models.

set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
source .venv/bin/activate
nvidia-smi

# A static ffmpeg/ffprobe build may live in ~/bin.
if [ -d "$HOME/bin" ]; then
    export PATH="$HOME/bin:$PATH"
fi
for tool in ffmpeg ffprobe; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "ERROR: $tool not found on PATH. Put a static build in ~/bin or load a module that provides it." >&2
        exit 1
    fi
done

# Only the extra packages are installed here: torch comes from the project's model environment.
if python -m pip --version >/dev/null 2>&1; then
    python -m pip install --no-cache-dir -r experiments/gaze_trial_requirements.txt
else
    # Some venvs in this repo were built with uv and have no pip.
    uv pip install --python "$(command -v python)" --no-cache \
        -r experiments/gaze_trial_requirements.txt
fi

VINET=models/vinet
REPO=$VINET/repo
COMMIT=d09066b7407d33718800d664a962683d4abd013c
CHECKPOINT=$VINET/DHF1K_vinet_s_rootgrouped_32_bs8_kld_cc.pt
ARCHIVE=$VINET/_vinet_models.tar.gz
MEMBER=final_models/ViNet_S/vinet_s_visual_dataset_models/vinet_s_dhf1k.pt

mkdir -p "$VINET"
if [ ! -d "$REPO/.git" ]; then
    echo "Cloning ViNet-S at $COMMIT"
    git clone https://github.com/ViNet-Saliency/vinet_v2 "$REPO"
fi
git -C "$REPO" fetch --quiet origin "$COMMIT" || true
git -C "$REPO" checkout --quiet "$COMMIT"

if [ ! -f "$CHECKPOINT" ]; then
    echo "Downloading the ViNet-S model archive"
    gdown 12UeAsdiD2xPLmoLRDcE_HjAUjxFdmw5N -O "$ARCHIVE"
    tar -xzf "$ARCHIVE" -C "$VINET" "$MEMBER"
    mv "$VINET/$MEMBER" "$CHECKPOINT"
    rm -f "$ARCHIVE"
    rm -rf "$VINET/final_models"
fi

python experiments/gaze_trial.py data/videos/_test_clips/gaze/*.mp4 \
    --out data/videos/_test_clips/gaze_trial

echo GAZE_TRIAL_DONE
