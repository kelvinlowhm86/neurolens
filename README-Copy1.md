# NeuroLens — Video Engagement Analysis via Predicted Brain Activity

## Overview

NeuroLens uses Meta's **TRIBE v2** (TRImodal Brain Encoder) to predict how the human brain responds to video content, second by second. By analysing predicted fMRI activation across key cortical regions, it produces a temporal engagement profile for any uploaded video — surfacing which moments are most neurally stimulating, which sensory channels drive attention, and how different content types (faces, scenes, speech) compete for brain resources.

This is not emotion classification. It is a **neural engagement analyser** — a computational proxy for what a neuromarketing lab produces with a real fMRI scanner at thousands of dollars per hour.

---

## GrowthDesk Studios — Why This Matters

NeuroLens is built by [GrowthDesk Studios](https://growthdesk.com/), the creative and performance marketing arm of GrowthDesk — an Asia Pacific marketing technology company operating at the intersection of proprietary intelligence (PULSE), specialised MarTech (SKALE, DREA), and human expertise. By integrating computational neuroscience into our video creative workflow, NeuroLens gives GrowthDesk Studios a capability that no other social media or performance marketing agency in the region can offer: the ability to objectively measure, second by second, how a piece of video content stimulates the human brain — before it ever goes live.

- **Data science–grounded creative for real estate and FMCG.** Instead of relying on subjective creative reviews or post-launch vanity metrics, GrowthDesk Studios uses NeuroLens to pre-test video ads for property launches (DREA) and shopper activation campaigns (SKALE) against predicted neural engagement — identifying weak moments, validating whether hero product shots or talent reveals actually drive cortical activation, and optimising edits before media spend is committed.
- **AI-centric execution as a competitive moat.** NeuroLens extends GrowthDesk's existing intelligence layer (PULSE) into the creative process itself. Where PULSE surfaces market and competitor signals to inform strategy, NeuroLens provides a neuroscience-grade signal on creative quality — closing the loop from insight to execution with AI at every stage.
- **Differentiation in a crowded APAC market.** Social media and performance marketing in Asia Pacific is dominated by agencies competing on reach, cost efficiency, and content volume. NeuroLens shifts the conversation to creative effectiveness measured at the neural level — a fundamentally different value proposition that positions GrowthDesk Studios as a science-first creative partner, not just another content factory.

---

## Core Objectives

### 1. Temporal Engagement Scoring

**Question:** Which seconds of the video are most stimulating?

**Method:** For each one-second timestep, sum the predicted activation magnitude across all sensory regions of interest (ROIs). Produce a per-second engagement score that can be plotted as a timeline, revealing peaks and troughs in neural stimulation throughout the video.

### 2. Regional Content Comparison

**Question:** Do faces and people drive more engagement than landscapes and environments?

**Method:** Extract activation from functionally distinct cortical regions and compare them over time:

- **FFA** (Fusiform Face Area) — responds to faces
- **EBA** (Extrastriate Body Area) — responds to human bodies
- **PPA** (Parahippocampal Place Area) — responds to scenes and environments
- **STS** (Superior Temporal Sulcus) — responds to biological motion and social cues
- **A1/A5** (Auditory Cortex) — responds to sound and speech

By plotting these five region traces side by side against the video timeline, we can see exactly when and why engagement shifts — a face appearing, a scene change, dialogue starting.

### 3. Audio Contribution Analysis

**Question:** Does the audio track actually add engagement, or is the video carrying the load?

**Method:** Run the same video through the model twice — once as full video (visual + audio), once as video-only (audio stripped). Compare auditory cortex activation between the two runs. The delta reveals the audio track's independent contribution to neural engagement at each second.

---

## Architecture

The project has two main parts: a **Python backend** that runs inference and serves results via API, and a **web frontend** that handles upload, playback, and visualisation.

```
┌─────────────────────────────────────────────────────────┐
│                      FRONTEND                           │
│                                                         │
│   ┌──────────┐  ┌──────────┐  ┌──────────────────────┐ │
│   │  Upload   │  │  Video   │  │   Charts Dashboard   │ │
│   │  Dropzone │  │  Player  │  │                      │ │
│   │          │  │          │  │  - Overall engagement │ │
│   │          │  │          │  │  - FFA (faces)        │ │
│   │          │  │          │  │  - EBA (bodies)       │ │
│   │          │  │          │  │  - PPA (scenes)       │ │
│   │          │  │          │  │  - STS (social)       │ │
│   │          │  │          │  │  - A1/A5 (audio)      │ │
│   └──────────┘  └──────────┘  └──────────────────────┘ │
│                        │                                │
│                        │ POST /api/analyse              │
│                        ▼                                │
├─────────────────────────────────────────────────────────┤
│                      BACKEND                            │
│                                                         │
│   ┌─────────────────────────────────────────────────┐   │
│   │  Flask API  (/api/analyse)                      │   │
│   │                                                 │   │
│   │  1. Receive video blob                          │   │
│   │  2. Save to temp file                           │   │
│   │  3. Run TRIBE v2 inference (full video)         │   │
│   │  4. Run TRIBE v2 inference (video-only, no audio│)  │
│   │  5. Extract ROI activations per timestep        │   │
│   │  6. Compute engagement scores                   │   │
│   │  7. Return JSON response                        │   │
│   └─────────────────────────────────────────────────┘   │
│                        │                                │
│                        ▼                                │
│   ┌─────────────────────────────────────────────────┐   │
│   │  TRIBE v2 Model (GPU)                           │   │
│   │                                                 │   │
│   │  Input:  video.mp4                              │   │
│   │  Output: (T, 20484) predicted fMRI activations  │   │
│   │                                                 │   │
│   │  Encoders:                                      │   │
│   │    - V-JEPA2-Giant  (video)   ~14 GB VRAM       │   │
│   │    - LLaMA 3.2-3B   (text)   ~7 GB VRAM        │   │
│   │    - Wav2Vec-BERT    (audio)  ~1 GB VRAM        │   │
│   │    - TRIBE transformer        ~6-10 GB VRAM     │   │
│   │                                                 │   │
│   │  Hardware: 2× RTX 4090 (48 GB total)            │   │
│   └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

---

## Part 1 — Backend

### Phase 1A: Exploration Notebook (`explore.ipynb`)

A Jupyter notebook that validates the full pipeline on a sample video before building the API. It will:

1. **Load TRIBE v2** from HuggingFace with dual-GPU device mapping across 2× RTX 4090
2. **Run inference** on a short sample video (10–30 seconds)
3. **Inspect the raw output** — print `preds.shape`, examine the `(T, 20484)` matrix, understand the `segments` list
4. **Map vertices to ROIs** — use the fsaverage5 atlas to identify which of the 20,484 vertices belong to FFA, EBA, PPA, STS, and auditory cortex
5. **Extract per-region timeseries** — for each timestep, compute mean activation within each ROI
6. **Compute overall engagement** — sum or average activation across all sensory ROIs per second
7. **Run the audio comparison** — strip audio from the sample video using ffmpeg, re-run inference, compute the delta in auditory cortex activation
8. **Plot everything** — produce matplotlib charts showing all traces aligned to the video timeline
9. **Define the JSON output schema** that the API will return

Expected output schema:

```json
{
  "duration_seconds": 30,
  "timesteps": [
    {
      "t": 0,
      "engagement_overall": 0.73,
      "regions": {
        "ffa_faces": 0.82,
        "eba_bodies": 0.45,
        "ppa_scenes": 0.61,
        "sts_social": 0.38,
        "auditory": 0.67
      },
      "audio_contribution": 0.21
    },
    {
      "t": 1,
      "engagement_overall": 0.81,
      "regions": {
        "ffa_faces": 0.91,
        "eba_bodies": 0.52,
        "ppa_scenes": 0.44,
        "sts_social": 0.55,
        "auditory": 0.72
      },
      "audio_contribution": 0.28
    }
  ]
}
```

### Phase 1B: Flask API (`app.py`)

A Flask application exposing a single endpoint:

```
POST /api/analyse
Content-Type: multipart/form-data
Body: { video: <file blob> }

Response: 200 OK
Content-Type: application/json
Body: { <engagement JSON as defined above> }
```

The API will:

1. Accept a video file upload (mp4, mov, webm — max 120 seconds for initial version)
2. Save to a temporary path
3. Run TRIBE v2 inference on the full video (video + audio)
4. Strip the audio track using ffmpeg and run inference again (video-only)
5. Extract ROI activations and compute engagement scores using the same logic validated in the notebook
6. Compute the audio contribution delta per timestep
7. Clean up temp files
8. Return the structured JSON response

Considerations:

- The model should be loaded once at app startup, not per request
- Inference on a 60-second video will take meaningful time — the endpoint should return a job ID and support polling, or use a synchronous response with a loading state on the frontend
- CORS must be enabled for the frontend to call the API
- Video duration should be validated and capped

---

## Part 2 — Frontend

A single-page web application (React or plain HTML) with three sections:

### Upload Area

- Drag-and-drop zone or file picker for video upload
- Shows upload progress
- Validates file type and duration before sending
- Triggers `POST /api/analyse` with the video blob
- Displays a loading/processing state while the backend runs inference

### Video Player

- Plays the uploaded video using a standard HTML5 video element
- Includes a visible timeline/scrubber
- A vertical playhead cursor on the charts below stays synchronised with the video's current playback position — as the video plays, the cursor moves across the charts in real time

### Charts Dashboard

Six time-series charts, all sharing the same X axis (seconds), stacked vertically and synchronised to the video player:

1. **Overall Engagement** — the summed engagement score per second (area chart, prominent)
2. **FFA — Faces** — fusiform face area activation (line chart)
3. **EBA — Bodies** — extrastriate body area activation (line chart)
4. **PPA — Scenes** — parahippocampal place area activation (line chart)
5. **STS — Social Cues** — superior temporal sulcus activation (line chart)
6. **Auditory Cortex** — shown as two overlapping traces: with-audio and without-audio, so the audio contribution is visible as the gap between the two lines

All charts share a synchronised hover/crosshair. Clicking any point on a chart seeks the video to that second.

---

## Hardware Requirements

- **GPU:** 2× NVIDIA RTX 4090 (24 GB VRAM each, 48 GB total)
- **VRAM usage:** ~28–32 GB across both cards
- **System RAM:** 32 GB minimum, 64 GB recommended
- **Storage:** ~25 GB for model weights and dependencies
- **CUDA:** 12.x with PyTorch 2.x
- **Python:** 3.10+

## Dependencies

- `tribev2` — TRIBE v2 model and inference code
- `torch` — PyTorch with CUDA support
- `flask` + `flask-cors` — API server
- `ffmpeg` — audio stripping for the comparison run
- `nilearn` — fsaverage5 atlas and ROI mapping
- `numpy`, `scipy` — numerical operations
- `matplotlib` — notebook visualisation

## Gated Model Access

The TRIBE v2 text encoder depends on **LLaMA 3.2-3B**, which is a gated model on HuggingFace. Before first run:

1. Go to [meta-llama/Llama-3.2-3B](https://huggingface.co/meta-llama/Llama-3.2-3B) and accept the license
2. Create a HuggingFace read access token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
3. Run `huggingface-cli login` and paste the token

TRIBE v2 itself (`facebook/tribev2`) is freely downloadable under CC BY-NC 4.0.

---

## Test Drive: Running the Exploration Notebook

The `explore.ipynb` notebook validates the full NeuroLens pipeline end-to-end on a sample video. Follow these steps to run it on a Linux machine with GPU access.

### Prerequisites

- Linux (Ubuntu 22.04+ recommended)
- Python 3.10+
- NVIDIA GPU with 40 GB+ VRAM (A100 40 GB, or 2× RTX 4090)
- CUDA 12.x + matching PyTorch
- ffmpeg installed (`sudo apt install ffmpeg`)
- HuggingFace account with LLaMA 3.2-3B license accepted

### Setup

```bash
# 1. Clone or create your project directory
mkdir neurolens && cd neurolens

# 2. Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Pin NumPy FIRST (critical — must happen before tribev2 install)
pip install "numpy>=1.26.4,<2.1.0"

# 4. Install remaining requirements
pip install -r requirements.txt

# 5. Install TRIBE v2 from GitHub (separate step — uses git+https)
pip install "tribev2[plotting] @ git+https://github.com/facebookresearch/tribev2.git"

# 6. Authenticate with HuggingFace (one-time)
#    Make sure you've accepted the LLaMA 3.2-3B license at:
#    https://huggingface.co/meta-llama/Llama-3.2-3B
huggingface-cli login

# 7. Launch Jupyter
jupyter notebook explore.ipynb
```

### What the notebook does

The notebook runs 18 code cells in sequence:

| Cell | What it does | Time |
|------|-------------|------|
| 1–2 | GPU check, NumPy version check | instant |
| 3–4 | HuggingFace auth, pre-download LLaMA 3.2-3B | ~5 min (first run, 6 GB download) |
| 5 | Load TRIBE v2 checkpoint | ~10 sec |
| 6 | Download Sintel trailer (480p, 52s) | ~5 sec |
| 7 | Run full inference (video + audio) | ~2–5 min (first run loads V-JEPA2 ~14 GB) |
| 8–9 | Inspect raw `(T, 20484)` output | instant |
| 10–11 | Load Destrieux atlas, build ROI vertex masks | ~5 sec |
| 12 | Extract per-region engagement timeseries | instant |
| 13–14 | Strip audio, re-run inference (video-only) | ~2–5 min |
| 15 | Compute audio contribution delta | instant |
| 16 | Normalise all scores to 0–1 | instant |
| 17 | Plot 6-panel engagement chart, save PNG | instant |
| 18 | Export structured JSON output | instant |

**Total first-run time:** approximately 15–20 minutes (dominated by model downloads).  
**Subsequent runs:** approximately 5–10 minutes (inference only, models cached).

### Expected outputs

After running all cells, you'll have two files in the working directory:

- `neurolens_output.png` — a 6-panel dark-theme chart showing overall engagement, per-ROI activation, and audio contribution over the video timeline
- `neurolens_output.json` — the structured JSON matching the API output schema, with per-second engagement scores for all five regions

### Dual RTX 4090 notes

If running on 2× RTX 4090 (48 GB total, 24 GB each) rather than a single A100:

- The default `TribeModel.from_pretrained()` may try to load everything onto GPU 0 and OOM
- If this happens, set `CUDA_VISIBLE_DEVICES=0,1` and try passing `device_map="auto"` if supported
- Alternatively, patch `tribev2/demo_utils.py` to distribute encoders across devices (V-JEPA2 + LLaMA on GPU 0, Wav2Vec-BERT + TRIBE transformer on GPU 1)
- The notebook includes VRAM monitoring at each stage to help debug memory issues

### Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ImportError: cannot import name '_center'` | NumPy ≥ 2.1 | `pip install "numpy>=1.26.4,<2.1.0"` then restart kernel |
| `ReadTimeout` during `model.predict()` | LLaMA download times out | Run the pre-download cell (Cell 4) first |
| `CUDA out of memory` on `predict()` | Insufficient VRAM | Need 40 GB+ single GPU, or multi-GPU setup |
| `ValueError` on `get_events_dataframe()` | Temp file not flushed | The notebook handles this — don't modify file I/O |
| `401 Unauthorized` | LLaMA license not accepted | Visit [meta-llama/Llama-3.2-3B](https://huggingface.co/meta-llama/Llama-3.2-3B) and accept |

---

## Reference: Meta's Official TRIBE v2 Demo

**URL:** [aidemos.atmeta.com/tribev2](https://aidemos.atmeta.com/tribev2/)

The official demo is a JavaScript single-page application that visualises predicted neural activity while stimuli are played. The following analysis is based on direct screenshots of the live interface.

### Layout

Screenshots of the live demo (April 2026):

![Meta Demo — Browse Examples](meta-demo-browse.png)
*Screenshot 1: Browse Examples view — video grid on right, idle brain on left*

![Meta Demo — Playback](meta-demo-playback.png)
*Screenshot 2: Playback view — video playing on right, activated brain on left*

The demo uses a **fixed two-panel split** on a dark (`#000`) background:

```
┌──────────────────────────┬───────────────────────────────────┐
│                          │                                   │
│     3D BRAIN MODEL       │     CONTENT PANEL                 │
│                          │                                   │
│   - WebGL rendered       │   Tab bar:                        │
│   - Rotatable/zoomable   │   [Browse Examples]               │
│   - Side profile view    │   [Compare Performance]           │
│   - Hot colormap overlay │   [Explore In-Silico]             │
│     (black→red→yellow    │   [Learn about Mu...]             │
│      →white)             │                                   │
│                          │   Description text                │
│                          │                                   │
│                          │   Video grid / Video player       │
│                          │                                   │
├──────────────────────────┤                                   │
│ [True] [Predicted]       │                                   │
│ [Normal] [Inflated]      │                                   │
│ [Open] [Close]           │                                   │
└──────────────────────────┴───────────────────────────────────┘
```

### Left panel — 3D Brain Visualisation

- **3D mesh** of the brain rendered in WebGL (grey/white low-poly surface), shown in a lateral (side) profile view against a dark circular vignette background with a faint head silhouette outline for anatomical orientation
- **Activity heatmap** overlaid on the mesh using a `hot` colormap: inactive regions stay grey, active regions glow black → red → orange → yellow → white
- **Activity legend** at the top: a horizontal gradient bar labelled "Low" to "High" with the label "Activity"
- **"Show Guide" button** in the top-left corner (opens explanatory overlay)

**Toggle controls** at the bottom of the brain panel (pill-shaped buttons, white text on dark, selected state has a white background):

| Toggle group | Options | Purpose |
|---|---|---|
| Data source | `True` / `Predicted` | Switch between real fMRI scan data and TRIBE v2's prediction for the same stimulus |
| Mesh style | `Normal` / `Inflated` | Normal shows anatomically folded cortex; Inflated "unfolds" the sulci to reveal hidden activation in the folds |
| View mode | `Open` / `Close` | Open splits the brain into two halves exposing the medial surface; Close shows the intact exterior |

The **True vs Predicted** toggle is notable — it lets users visually validate the model's accuracy by switching between the actual fMRI recording and the predicted activation for the same video clip. In the screenshots, the `Predicted` and `Normal` and `Close` options are selected.

### Right panel — Browse Examples view (Screenshot 1)

- **Tab bar** at the top with four options: `Browse Examples` (selected, filled pill), `Compare Performance`, `Explore In-Silico`, `Learn about Mu...` (truncated)
- **Description text** explaining that this compares TRIBE v2 predictions with real brain scans, noting that real scans include noise and wandering thoughts while the model predicts isolated stimulus responses
- **Video thumbnail grid** — 3 columns, at least 3 rows visible, each thumbnail showing a frame from a pre-loaded clip with a centered play button overlay. Content includes nature scenes, elderly people, suburban houses, a running figure, a close-up face — diverse naturalistic stimuli from the training datasets

### Right panel — Playback view (Screenshot 2)

When a clip is selected, the grid is replaced with:

- **Video player** — large, embedded, showing the active clip (a nature/landscape scene with trees and sky in the screenshot)
- **Transport controls** below the video:
  - Pause/play button (‖)
  - Mute toggle (speaker with ×)
  - Speed button (`2×`)
  - **Timeline scrubber** — horizontal bar with a blue dot indicator showing playback position
- **"← Back" button** to return to the grid

The brain heatmap on the left **updates in real time** as the video plays — in Screenshot 2, significantly more activation is visible (large red/yellow/white hotspots across temporal and visual cortex) compared to the idle state in Screenshot 1.

### What it does NOT do

- Does not accept user-uploaded videos — only pre-loaded stimuli from the training datasets
- Does not expose ROI-level data, engagement scores, or per-region numerical breakdowns
- Does not offer an audio-vs-no-audio comparison mode
- Does not provide downloadable data or API access — visualisation only
- Does not show any timeseries charts, graphs, or quantitative metrics
- No timeline annotations or per-second numerical engagement scores

### What we take from it for NeuroLens

**Borrow:**

- The **synchronised playback** pattern — brain state updates as video plays. This is the core UX and it works well.
- The **dark theme** — brain activity heatmaps are most readable on black. Our charts should also be on a dark background.
- The **transport controls** — play/pause, scrubber, mute. Standard and effective.
- The **True vs Predicted toggle concept** — we should let users toggle between "with audio" and "without audio" views in our charts, similar to how Meta lets users toggle between real fMRI and predicted.

**Improve on:**

- Replace the 3D brain (requires neuroscience literacy) with **line charts that anyone can read**. The brain is beautiful but most users can't map "the yellow blob in the temporal region" to "faces are driving engagement here." Our per-region labelled charts solve this.
- Add **quantitative metrics** — the demo shows only a visual heatmap with no numbers. We produce engagement scores per second.
- Add **user video upload** — the demo only works with pre-loaded clips. NeuroLens accepts any video.
- Add **audio contribution analysis** — the demo has no way to isolate audio's effect. Our dual-inference approach (with audio vs without) directly addresses this.
- Add **timeline annotations** — mark peaks and label what's driving them ("face appears", "dialogue starts", "scene change").

---

## Reference: Code from GitHub & Colab Demo

**Repo:** [github.com/facebookresearch/tribev2](https://github.com/facebookresearch/tribev2)
**Demo notebook:** [tribe_demo.ipynb](https://github.com/facebookresearch/tribev2/blob/main/tribe_demo.ipynb) (also opens in [Google Colab](https://colab.research.google.com/github/facebookresearch/tribev2/blob/main/tribe_demo.ipynb))

### Repository structure

```
tribev2/
├── main.py              # Experiment pipeline: Data, TribeExperiment
├── model.py             # FmriEncoder: Transformer-based multimodal→fMRI model
├── pl_module.py         # PyTorch Lightning training module
├── demo_utils.py        # TribeModel and helpers for inference — THIS IS OUR MAIN ENTRY POINT
├── eventstransforms.py  # Custom event transforms (word extraction, chunking, …)
├── utils.py             # Multi-study loading, splitting, subject weighting
├── utils_fmri.py        # Surface projection (MNI / fsaverage) and ROI analysis
├── grids/
│   ├── defaults.py      # Full default experiment configuration
│   └── test_run.py      # Quick local test entry point
├── plotting/            # Brain visualization (PyVista & Nilearn backends)
└── studies/             # Dataset definitions (Algonauts2025, Lahner2024, …)
```

### Key code patterns we will use

**1. Loading the model**

```python
from tribev2.demo_utils import TribeModel
from pathlib import Path

CACHE_FOLDER = Path("./cache")
model = TribeModel.from_pretrained("facebook/tribev2", cache_folder=CACHE_FOLDER)
```

The checkpoint download is ~1 GB. The three frozen encoders (V-JEPA2, LLaMA, Wav2Vec-BERT) are downloaded lazily on first use of each modality via HuggingFace Hub.

**2. Running inference on video**

```python
df = model.get_events_dataframe(video_path="path/to/video.mp4")
preds, segments = model.predict(events=df)
print(preds.shape)  # (n_timesteps, 20484)
```

`get_events_dataframe()` is the preprocessing step. For video input it automatically:
1. Extracts audio from the video track
2. Transcribes speech into word-level events with timestamps using **WhisperX**
3. Builds a unified events DataFrame with video, audio, and text columns aligned to a 2 Hz grid

`model.predict()` runs the transformer and subject block, returning:
- `preds`: numpy array of shape `(T, 20484)` — one cortical prediction per second
- `segments`: list of time segments with their associated events

Predictions are **offset by 5 seconds** to compensate for hemodynamic lag.

**3. Hemisphere splitting for visualisation**

The 20,484 vertices are split evenly: indices 0–10,241 are the **left hemisphere**, indices 10,242–20,483 are the **right hemisphere** (fsaverage5 convention):

```python
N_PER_HEMI = 10242

def split_hemis(v):
    return v[:N_PER_HEMI], v[N_PER_HEMI:]
```

**4. Brain surface visualisation (from Colab demo)**

```python
from nilearn import datasets as nl_datasets
from nilearn.plotting import view_surf

fsavg = nl_datasets.fetch_surf_fsaverage(mesh='fsaverage5')

lh_data, rh_data = split_hemis(preds[t])
view = view_surf(
    surf_mesh=fsavg['infl_left'],
    surf_map=lh_data,
    bg_map=fsavg['sulc_left'],
    cmap='hot',
    threshold='20%',
    black_bg=True,
    colorbar=True,
)
```

**5. Running text-only or audio-only inference**

The modality dropout design (`p=0.3` during training) means any subset of modalities works at inference:

```python
# Text only
df = model.get_events_dataframe(text_path="path/to/script.txt")

# Audio only
df = model.get_events_dataframe(audio_path="path/to/audio.wav")
```

This is what enables our **audio contribution analysis** — we run video-only (by stripping audio with ffmpeg first) and compare against full video.

### Known gotchas from the community

These issues were identified by the DataCamp tutorial and community usage:

| Issue | Fix |
|---|---|
| **NumPy 2.x conflict** — `neuralset` (internal dependency) fails with `ImportError: cannot import name '_center' from 'numpy._core.umath'` | Pin `numpy>=1.26.4,<2.1.0` before installing tribev2 |
| **HuggingFace download timeout** — LLaMA 3.2-3B (~6 GB) download times out mid-inference with default 10s timeout | Set `HF_HUB_DOWNLOAD_TIMEOUT=300` and pre-download with `snapshot_download()` |
| **Temp file race condition** — if you pass a temp file path to `get_events_dataframe()` before the file is flushed to disk, it reads an empty file | Always call `flush()` → `os.fsync()` → `close()` before passing the path |
| **LLaMA is gated** — inference silently fails or throws auth errors if you haven't accepted the Meta license | Accept at [huggingface.co/meta-llama/Llama-3.2-3B](https://huggingface.co/meta-llama/Llama-3.2-3B), then `huggingface-cli login` |
| **T4 GPU OOM** — 16 GB VRAM is insufficient; model loads but crashes on `predict()` | Minimum 40 GB (A100) or split across 2× RTX 4090 |
| **VRAM spike on first predict** — model checkpoint is ~1 GB but encoders are loaded lazily on first modality use, spiking to 28–32 GB | Pre-warm by running a short dummy prediction after loading |

### Architecture detail (from the paper)

The internal processing pipeline for video input runs as follows:

```
Video frames ──→ V-JEPA2-Giant (frozen) ──→ D=1280 embeddings at 2 Hz
Audio track  ──→ Wav2Vec-BERT (frozen)  ──→ D=1024 embeddings at 2 Hz
Transcribed  ──→ LLaMA 3.2-3B (frozen)  ──→ D=2048 embeddings at 2 Hz
       │                │                        │
       └────────────────┴────────────────────────┘
                        │
                  Project to D_model = 1152
                  Concatenate (3 × 384)
                        │
              ┌─────────▼─────────┐
              │  8-layer, 8-head  │
              │  Transformer      │
              │  (100s context)   │
              └─────────┬─────────┘
                        │
                  Decimate to 1 Hz
                        │
              ┌─────────▼─────────┐
              │  Subject Block    │
              │  (linear project  │
              │   to 20,484       │
              │   vertices)       │
              └─────────┬─────────┘
                        │
                  Output: (T, 20484)
```

The `modality dropout (p=0.3)` during training means any modality can be zeroed out and the model still produces meaningful predictions. This is critical for our audio comparison — when we strip audio before inference, the model gracefully handles the missing modality rather than crashing.

---

## License

This project is for **non-commercial research use only**, in compliance with TRIBE v2's CC BY-NC 4.0 license.
