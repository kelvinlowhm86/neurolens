#!/bin/bash
#SBATCH --job-name=crop-vs-pad
#SBATCH --partition=gpu
#SBATCH --gres=gpu:h100-47:1
#SBATCH --time=03:00:00
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --output=crop_vs_pad_%j.log
#
# Compare TRIBE v2's crop with a letterboxed pad of the same clips.
# Submit from the repo root:  sbatch experiments/crop_vs_pad.sbatch
# The first run downloads the model weights into ./models.

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

python experiments/crop_vs_pad.py data/videos/_test_clips/gaze/*.mp4 \
    --out data/videos/_test_clips/gaze_results

echo CROP_VS_PAD_DONE
