# Notebooks

These are a teammate's original local prototype (Destrieux atlas, five regions and engagement
scores rescaled to 0–1). They need their own GPU and model weights and are not part of the AWS app.

- `explore.ipynb` — loads TRIBE v2, runs one clip with and without audio, extracts per-region
  engagement, plots the charts and writes `data/output/neurolens_output.png` and
  `data/output/neurolens_output.json`.
- `batch_process.ipynb` — runs several local videos and appends them to its own sample list,
  `data/output/prototype_samples.json` (`timesteps` with `engagement_overall` and five regions).
  The app does not read it: the app's samples (`data/samples.json`) are made by
  `infra/make_sample.py`.

## Running `explore.ipynb`

It runs the prototype's pipeline end to end on a sample video. Its outputs use the prototype's maths, not the app's (see "What it shows" in the main README). Follow these steps to run it on a Linux machine with GPU access.

### Prerequisites

- Linux (Ubuntu 22.04+ recommended)
- Python 3.12 (not 3.13+: `tribev2` needs an older PyTorch)
- NVIDIA GPU with about 20 GB free VRAM (the worker's measured peak is 19.94 GB; a 40 GB card is comfortable)
- CUDA 12.x + matching PyTorch
- ffmpeg installed (`sudo apt install ffmpeg`)
- HuggingFace account with LLaMA 3.2-3B license accepted

### Setup

```bash
# 1. From the repository root

# 2. Create a virtual environment (Python 3.12)
python3.12 -m venv .venv
source .venv/bin/activate

# 3. Install the project and everything the notebooks need, including TRIBE v2
#    (requirements/ has one file per machine type; NumPy is pinned in base.txt)
pip install -e . -r requirements/notebooks.txt

# 4. Put your secrets in .env (git-ignored): HuggingFace token, and later the AWS bucket name.
#    Shared settings live in the committed config.json.
cp .env.example .env    # then edit .env

# 5. Authenticate with HuggingFace (one-time)
#    Make sure you've accepted the LLaMA 3.2-3B license at:
#    https://huggingface.co/meta-llama/Llama-3.2-3B
huggingface-cli login

# 6. Launch Jupyter
jupyter notebook notebooks/explore.ipynb
```

### What the notebook does

The notebook runs 21 code cells in sequence (the last one is empty):

| Cell | What it does | Time |
|------|-------------|------|
| 1 | GPU check | instant |
| 2 | Locate the project root, load `config.json` | instant |
| 3 | NumPy version check | instant |
| 4–5 | HuggingFace auth, pre-download LLaMA 3.2-3B | ~5 min (first run, 6 GB download) |
| 6 | Load TRIBE v2 checkpoint | ~10 sec |
| 7 | Set the sample video path (`VIDEO_PATH`) and probe it | ~5 sec |
| 8 | Build events, run full inference (video + audio) | ~2–5 min (first run loads V-JEPA2 ~14 GB) |
| 9–10 | Inspect raw `(T, 20484)` output and the segment list | instant |
| 11–12 | Load Destrieux atlas, map Destrieux labels to the ROIs | ~5 sec |
| 13 | Extract per-region engagement timeseries | instant |
| 14–15 | Strip audio, re-run inference (video-only) | ~2–5 min |
| 16 | Compute audio contribution delta | instant |
| 17 | Normalise all scores to 0–1 | instant |
| 18 | Plot 6-panel engagement chart, save PNG | instant |
| 19 | Export structured JSON output | instant |
| 20 | Print per-GPU VRAM use | instant |
| 21 | (empty) | — |

**Total first-run time:** approximately 15–20 minutes (dominated by model downloads).  
**Subsequent runs:** approximately 5–10 minutes (inference only, models cached).

### Expected outputs

After running all cells, you'll have two files in `data/output/`:

- `neurolens_output.png` — a 6-panel dark-theme chart showing overall engagement, per-ROI activation, and audio contribution over the video timeline
- `neurolens_output.json` — the prototype's JSON, with per-second engagement scores for all five regions (the app's own result format is in `docs/M3d_spec.md`)

### GPU notes

TRIBE v2 runs on one GPU and peaks at about 20 GB of GPU memory, so a 24 GB card can be tight and a 40 GB card is comfortable. The notebook prints VRAM use at each stage to help debug memory issues.

### Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ImportError: cannot import name '_center'` | An older tribev2 with a newer NumPy | The repo pins `numpy==2.2.6` in `requirements/base.txt`, the version the pinned tribev2 uses; reinstall from `requirements/` then restart the kernel |
| `ReadTimeout` during `model.predict()` | LLaMA download times out | Run the pre-download cell (Cell 5) first |
| `CUDA out of memory` on `predict()` | Insufficient VRAM | The model peaks at about 20 GB; free that much VRAM |
| `ValueError` on `get_events_dataframe()` | Temp file not flushed | The notebook handles this — don't modify file I/O |
| `401 Unauthorized` | LLaMA license not accepted | Visit [meta-llama/Llama-3.2-3B](https://huggingface.co/meta-llama/Llama-3.2-3B) and accept |
