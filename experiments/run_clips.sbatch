#!/bin/bash
#SBATCH --job-name=run-clips
#SBATCH --partition=gpu
#SBATCH --gres=gpu:h100-47:1
#SBATCH --time=01:00:00
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --output=run_clips_%j.log
#
# Brain response for local clips (experiments/run_clips.py).
# Submit from the repo root:  sbatch experiments/run_clips.sbatch
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

python experiments/run_clips.py data/videos/_test_clips/cerave/*.mp4 \
    --out data/videos/_test_clips/cerave_results

echo RUN_CLIPS_DONE
