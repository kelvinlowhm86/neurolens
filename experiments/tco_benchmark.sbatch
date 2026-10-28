#!/bin/bash
#SBATCH --job-name=tco-benchmark
#SBATCH --partition=gpu
#SBATCH --gres=gpu:a100-40:1
#SBATCH --time=03:00:00
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --output=tco_benchmark_%j.log
#
# Experiment 3: time the analysis pipeline on one A100 40 GB, for the on-premise cost side.
# Submit from the repo root with the clips to measure:
#   sbatch experiments/tco_benchmark.sbatch CLIP [CLIP ...]
# The CSV lands in the submit directory as exp3_onprem.csv.
# The first run downloads the model weights into ./models.

set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
source .venv/bin/activate
nvidia-smi

if [ "$#" -lt 1 ]; then
    echo "ERROR: pass at least one clip: sbatch experiments/tco_benchmark.sbatch CLIP [CLIP ...]" >&2
    exit 1
fi

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

python experiments/tco_benchmark.py "$@" --out "$SLURM_SUBMIT_DIR/exp3_onprem.csv"

echo TCO_BENCHMARK_DONE
