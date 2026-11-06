# TRIBE v2 reference notes

Notes on Meta's TRIBE v2 demo, code and paper, and what NeuroLens takes from them.

## Meta's Official TRIBE v2 Demo

**URL:** [aidemos.atmeta.com/tribev2](https://aidemos.atmeta.com/tribev2/)

The official demo is a JavaScript single-page application that visualises predicted neural activity while stimuli are played. The following describes the live interface (April 2026).

### Layout

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

The **True vs Predicted** toggle is notable — it lets users visually validate the model's accuracy by switching between the actual fMRI recording and the predicted activation for the same video clip. The `Predicted`, `Normal` and `Close` options were selected.

### Right panel — Browse Examples view

- **Tab bar** at the top with four options: `Browse Examples` (selected, filled pill), `Compare Performance`, `Explore In-Silico`, `Learn about Mu...` (truncated)
- **Description text** explaining that this compares TRIBE v2 predictions with real brain scans, noting that real scans include noise and wandering thoughts while the model predicts isolated stimulus responses
- **Video thumbnail grid** — 3 columns, at least 3 rows visible, each thumbnail showing a frame from a pre-loaded clip with a centered play button overlay. Content includes nature scenes, elderly people, suburban houses, a running figure, a close-up face — diverse naturalistic stimuli from the training datasets

### Right panel — Playback view

When a clip is selected, the grid is replaced with:

- **Video player** — large, embedded, showing the active clip
- **Transport controls** below the video:
  - Pause/play button (‖)
  - Mute toggle (speaker with ×)
  - Speed button (`2×`)
  - **Timeline scrubber** — horizontal bar with a blue dot indicator showing playback position
- **"← Back" button** to return to the grid

The brain heatmap on the left **updates in real time** as the video plays — significantly more activation is visible (large red/yellow/white hotspots across temporal and visual cortex) than in the idle state.

### What it does NOT do

- Does not accept user-uploaded videos — only pre-loaded stimuli from the training datasets
- Does not expose ROI-level data, engagement scores, or per-region numerical breakdowns
- Does not offer an audio-vs-no-audio comparison mode
- Does not provide downloadable data or API access — visualisation only
- Does not show any timeseries charts, graphs, or quantitative metrics
- No timeline annotations or per-second numerical engagement scores

### What we take from it for NeuroLens

**Borrow:**

- The **synchronised playback** pattern: a 3D brain map that updates as the video plays, on one timeline with the video.
- The **transport controls**: play/pause, scrubber, mute.
- The **toggle concept**: our chart can overlay the muted (no-audio) response on the normal one, as Meta toggles between real fMRI and predicted.

**Add:**

- **Numbers next to the map**: 7 labelled signals in 3 groups (see, hear, understand) and an overall line, second by second on a chart anyone can read, plus the strongest and weakest moments.
- **User video upload**: the demo only works with pre-loaded clips.
- **Audio dependence**: the second, muted pass shows how much of the response needs the soundtrack.
- **Compare**: two of the user's own videos side by side.

---

## Code from GitHub & Colab Demo

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

This is what enables our **audio dependence** measure: the worker runs a second pass on the video's events with the audio and word events removed (`without_audio` in `neurolens/inference.py`) and compares it with the full pass.

### Known gotchas from the community

These issues were identified by the DataCamp tutorial and community usage:

| Issue | Fix |
|---|---|
| **NumPy 2.x conflict** — `neuralset` (internal dependency) fails with `ImportError: cannot import name '_center' from 'numpy._core.umath'` | NumPy is now pinned to `2.2.6` in `requirements/base.txt`, the version `tribev2` forces; if this error returns, revisit that pin |
| **HuggingFace download timeout** — LLaMA 3.2-3B (~6 GB) download times out mid-inference with default 10s timeout | Set `HF_HUB_DOWNLOAD_TIMEOUT=300` and pre-download with `snapshot_download()` |
| **Temp file race condition** — if you pass a temp file path to `get_events_dataframe()` before the file is flushed to disk, it reads an empty file | Always call `flush()` → `os.fsync()` → `close()` before passing the path |
| **LLaMA is gated** — inference silently fails or throws auth errors if you haven't accepted the Meta license | Accept at [huggingface.co/meta-llama/Llama-3.2-3B](https://huggingface.co/meta-llama/Llama-3.2-3B), then `huggingface-cli login` |
| **T4 GPU OOM** — 16 GB VRAM is insufficient; model loads but crashes on `predict()` | The model peaks at about 20 GB, so a 16 GB card is not enough |
| **VRAM spike on first predict** — model checkpoint is ~1 GB but encoders are loaded lazily on first modality use, spiking to about 20 GB | Pre-warm by running a short dummy prediction after loading |

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

The `modality dropout (p=0.3)` during training means any modality can be zeroed out and the model still produces meaningful predictions. This is critical for our muted pass: when the audio events are removed, the model handles the missing modality rather than crashing.
