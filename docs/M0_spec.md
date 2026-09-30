# NeuroLens — M0 Implementation Spec
**Milestone:** Repo structure, tooling and CI (before any AWS work)
**Grounded in:** the actual repo on branch `aws-josh`: `app.py` (300 lines), two notebooks, `static/`, `data/`, `config.sample.json`, `requirements.txt`. If the repo has changed since, re-verify before implementing.

**Ground rules:** M0 changes **no behaviour**. The app must serve the same pages and compute the same numbers as before. No AWS resources, no GPU, nothing billable. Python **3.12** everywhere (venv, CI, later the GPU worker): `tribev2` pins `torch<2.7`, which has no Python 3.14 builds, so the Mac's default Python cannot be used. The pins in §3 were checked with `uv pip compile` (installable together); whether they *run* together is proven by the real-import check (M1 §1b layer 2) and the M2 GPU run. Everything runs on a laptop without `config.json` or the model.

## 0. What exists today
- `app.py` loads `config.json`, sets HuggingFace/torch env vars, loads the TRIBE v2 model, downloads the Destrieux atlas and builds five ROI masks, all **at import time**. It also holds the pure maths (`normalize_01`, `extract_engagement`), `run_inference`, `strip_audio`, the Flask routes (`/`, `/api/samples`, `/data/<path>`, `POST /api/analyse`) and `app.run` (0.0.0.0:5003).
- Importing `app.py` therefore needs `config.json`, `torch`, `tribev2`, the model weights and a GPU. Nothing in it can be tested in isolation.
- `explore.ipynb` and `batch_process.ipynb` do not import any `.py` file. Each holds its own copy of the ROI maths. `batch_process.ipynb` uses working-directory-relative paths (`data/videos/...`, `data/samples.json`, `data/output/...`).
- One `requirements.txt` for everything (web, model, Jupyter). No tests, no CI, no lint config, no `pyproject.toml`.
- TRIBE v2 outputs a `(T, 20484)` array: predicted brain activity per cortical point per second. The engagement scores are NeuroLens's own summary of it (`extract_engagement`). M0 does not change how they are calculated.

## 1. Target layout
```
neurolens/
  __init__.py            empty: no heavy imports, ever
  settings.py            config + paths, no work at import time
  engagement.py          pure maths, numpy only
  inference.py           model + atlas loading, run_inference, strip_audio
  web/
    __init__.py          empty
    app.py               create_app(): Flask routes
app.py                   thin launcher: create_app(); app.run(host, port)
static/  data/           unchanged location
notebooks/               explore.ipynb, batch_process.ipynb
requirements/            base.txt, web.txt, worker.txt, model.txt, notebooks.txt, dev.txt
infra/                   empty (M1 fills it)
tests/  tests/fixtures/
pyproject.toml  AGENTS.md  CLAUDE.md  .github/workflows/ci.yml
```
Created later, in the milestone that needs them: `worker.py`, `neurolens/worker.py`, `neurolens/storage.py` and `neurolens/pricing.py` (M1), `FAKE_INFERENCE` (M1 §1a), billing and database code (M3).

## 2. Code split (a pure move, not a rewrite)
| From `app.py` | To | Rules |
|---|---|---|
| `normalize_01`, `extract_engagement`, `ROI_LABEL_MAP` | `engagement.py` | Signature becomes `extract_engagement(preds_full, preds_noaudio, roi_masks)`: masks are passed in, not read from a global. Add pure `build_roi_masks(labels, label_names)` (the mask loop minus the atlas download). numpy only. |
| Config load, HF/torch env vars, paths, max video duration, host/port | `settings.py` | `load_config()` runs when called, never at import. Root folder is `NEUROLENS_ROOT` if set, else two folders above the file. A missing `config.json` raises a clear error when config is requested. |
| Model loading, atlas download, `run_inference`, `strip_audio` | `inference.py` | `torch`, `tribev2`, `nilearn` are imported inside functions. HF env vars are set **before** any HF import (the current `huggingface_hub not in sys.modules` check is kept). Model and masks load once via `load_model()`. |
| Flask routes | `web/app.py` | `create_app(load_model=True)`. `static_folder` is an absolute path under the root. Tests use `create_app(load_model=False)`. |
| `app.run(...)` | root `app.py` | Same host and port as today. |

The web app still calls `inference` for `/api/analyse` in local use, as today. This is temporary and marked with a code comment; M1 removes it (the web tier must never import `inference`).

**Normalisation rule (do not change):** `extract_engagement` cuts the with-audio and no-audio auditory passes to their shared length *first*, then rescales each to 0–1. The main five ROI columns are rescaled over the full timeline. So `auditory` and `auditory_with_audio` may differ in the same row. `AGENTS.md` records this so nobody "fixes" it. The notebooks' inline copies rescale first and cut afterwards, so 7 of the 14 stored samples in `data/samples.json` (those with a one-second length mismatch) differ in those two columns from what the server would produce. The size of that difference is unmeasured until real model output exists (M2's GPU run).

### Interfaces fixed by this spec (the tests are written against exactly these)
- `neurolens.engagement`:
  - `ROI_LABEL_MAP: dict[str, list[str]]`, the same five entries as today.
  - `normalize_01(arr) -> ndarray`.
  - `build_roi_masks(labels_full, label_names, roi_label_map=ROI_LABEL_MAP) -> dict[str, ndarray[bool]]`. A label matches when the target text is contained in the label name, as today.
  - `extract_engagement(preds_full, preds_noaudio, roi_masks) -> {"duration_seconds": int, "timesteps": [...]}`, same dict shape as today.
- `neurolens.settings`:
  - `get_root() -> Path`: `NEUROLENS_ROOT` if set, else the repo root.
  - `load_config(root=None) -> dict`: raises `FileNotFoundError` mentioning `config.sample.json` when `config.json` is missing.
  - `resolve_paths(cfg, root) -> dict[str, Path]` with keys `models`, `data`, `output`. No side effects.
  - `configure_env(cfg, paths) -> None`: sets the HF/torch/nilearn env vars; raises if `huggingface_hub` was already imported.
  - `max_duration(cfg: dict | None) -> int`: `cfg["max_video_duration_seconds"]`, default 120 when missing or when `cfg` is `None`. There is no module-level `MAX_DURATION` constant, because `settings` does no work at import.
  - `ensure_dirs(paths) -> None`: creates the three folders (today this happens at import; now it happens in `load_model()` and `create_app(load_model=True)`).
- `neurolens.inference`: `load_model(cfg=None) -> None` fills module-level state; `run_inference(video_path)` and `strip_audio(input_path, output_path)` keep today's signatures (M1 forbids changing them); `gpu_info() -> dict | None` replaces the `torch.cuda` block currently inside the `/api/analyse` route.
- `neurolens.web.app`: `create_app(load_model: bool = True, data_dir: Path | None = None) -> Flask`.
  - With `data_dir` given and `load_model=False`, it needs no `config.json`, and the max duration is `max_duration(None)` = 120, as today.
  - `neurolens.web.app` must **not** import `neurolens.inference` at module level. It imports it inside `create_app` only when `load_model=True`. This is what lets the import-hygiene test pass while `/api/analyse` still works locally.
  - With `load_model=False`, `POST /api/analyse` with a file returns 503 `{"error": "Model not loaded"}` without calling ffprobe. With no file it returns 400, as today.
  - `CORS`, the route paths and the JSON shapes are unchanged.

## 3. Tooling
- **`pyproject.toml`:** package `neurolens`, `requires-python >=3.12,<3.14`, no `dependencies` (the `requirements/` files are the single source), installed with `pip install -e .`. Ruff rules `E, F, I, B`, line length 100, `*.ipynb` excluded. pytest `testpaths = tests`.
- **`requirements/`:** `base.txt` (`numpy==2.2.6`, the version `tribev2` forces, so web, worker and CI all run the same numpy. This replaces the README's old `numpy<2.1` pin, which guarded an `ImportError: _center` seen with an earlier `tribev2`; if that error reappears at the real-import check, revisit this pin), `web.txt` (`-r base.txt`, flask, flask-cors), `worker.txt` (`-r base.txt`, nilearn: everything the worker needs *except* the model, so fake mode runs on a laptop), `model.txt` (`-r worker.txt`, `transformers>=4.45,<5`, `tribev2` via its `git+https` URL: only on the GPU machine or for the real-import check), `notebooks.txt` (`-r model.txt`, jupyter, ipykernel, ipywidgets, matplotlib, packaging), `dev.txt` (`-r web.txt`, `-r worker.txt`, pytest, ruff). Each file installs only what its machine needs: the web server never gets `torch`, the GPU machine never gets Jupyter. The old `requirements.txt` is removed.
- **`.gitignore` additions:** `.ruff_cache/`, `.pytest_cache/`, `.coverage`, `dist/`, `build/`, `*.tfstate*`, `.terraform/`.
- **Claude Code hook:** a ruff format/check PostToolUse hook in `.claude/settings.local.json` (personal). CI is what enforces the rules for everyone.

## 4. Notebooks
`git mv` both into `notebooks/`. In `batch_process.ipynb`, replace working-directory-relative paths with paths built from the project root the notebook already finds (`_find_project_root()`). Leave their duplicate maths untouched (re-running needs a GPU). Before the notebooks are pushed, clear the `explore.ipynb` cell 4 output that prints the first 8 and last 4 characters of the HF token.

## 5. Tests (laptop only: no GPU, no `config.json`, no `torch`)
**Tests come first, and the implementer may not touch them.** All tests below are written from this spec, against the interfaces in §2, before any code exists in `neurolens/`. They start red (failing because the package is missing), and the split in §8 step 4 must turn them green without editing a single test file. If a test looks wrong, the implementer stops and asks Josh instead of changing it. The tests are written by a separate fresh agent that sees only this spec and the original `app.py`, never the new code, so they cannot share the implementer's blind spots. Once green, the tests are sanity-checked by deliberately breaking the code (§8 step 7).
1. **Golden test (written *before* the move).** Copy the original `normalize_01` and `extract_engagement` into a throwaway script (setting the `roi_masks` and `ROI_LABEL_MAP` globals it reads), run them on the inputs below, and save `tests/fixtures/extract_engagement_golden.json` containing the outputs **and this recipe**, so the test rebuilds identical inputs:
   - `rng = numpy.random.default_rng(0)`; draws in this order: `preds_full = rng.standard_normal((12, 20484))`, then `preds_noaudio = rng.standard_normal((12, 20484))` for case `equal`; for case `short`, a fresh `default_rng(0)` and shapes `(12, 20484)` then `(11, 20484)`; for case `short_edge`, a fresh `default_rng(1)` and the same shapes as `short` (its dropped last row is the minimum or maximum of the auditory series, so cut-then-rescale and rescale-then-cut give different numbers; seed 0 cannot tell them apart).
   - Masks: for the five ROIs in `ROI_LABEL_MAP` order, ROI `i` is `True` on vertices `[i*200, i*200 + 200)` and `False` elsewhere. `test_engagement.py` compares the moved function against it and must pass unchanged after the move. This is M1 §1b layer 1.
2. `test_engagement.py` also checks: constant series gives zeros, output length and keys, `None` rows exactly where the shorter pass ends, `build_roi_masks` on a tiny fake atlas.
3. `test_settings.py`: `NEUROLENS_ROOT` override; missing `config.json` error only when asked; `resolve_paths` with `config.sample.json` gives `<root>/models`, `<root>/data`, `<root>/output` and creates nothing; `configure_env` sets `HF_HOME` and `NILEARN_DATA` to the expected folders (use `monkeypatch`, so real env is untouched).
4. `test_web.py` (`create_app(load_model=False, data_dir=<repo>/data)`): `GET /` serves `index.html`; `/api/samples` equals `data/samples.json`; a thumbnail under `/data/output/...` is served; a path-traversal request for `config.json` outside `data/` returns 404; `POST /api/analyse` with no file returns 400 and with a file returns 503.
5. `test_import_hygiene.py`: in a fresh subprocess, import `neurolens`, `neurolens.engagement`, `neurolens.settings`, `neurolens.web.app`; assert `torch`, `huggingface_hub` and `nilearn` are **not** in `sys.modules`.
6. `test_samples_schema.py`: `data/samples.json` keeps the keys the frontend reads.

## 6. CI
`.github/workflows/ci.yml`, on push and pull request: Python 3.12, `pip install -e .`, `pip install -r requirements/dev.txt`, `ruff check .`, `ruff format --check .`, `pytest`. CI never installs `torch` or `tribev2`. GitHub Actions is free for public repositories and has a monthly free allowance for private ones.

## 7. `AGENTS.md` and `CLAUDE.md`
`CLAUDE.md` contains one line: `@AGENTS.md` (teammates may use other tools). `AGENTS.md` is short: one-line project summary; layout map; commands (`pip install -e .`, `pytest`, `ruff check .`, `ruff format .`); rules: heavy imports only inside functions, web must not import `inference`, config only through `settings`, never commit `config.json` or tokens, never edit a test to make it pass (stop and ask instead), do not change `extract_engagement` without updating the golden fixture and saying so, keep the normalisation rule in §2; branch `aws-josh`; specs describe the current design only.

## 8. Order of work (one commit each, on `aws-josh`)
1. Golden fixture (generated from the original code, before any move).
2. `pyproject.toml`, `requirements/`, `.gitignore` (needed to run pytest at all).
3. **All tests written, by the separate test-writing agent (§5). They fail.** Commit them. From here on `git diff` on `tests/` must stay empty.
4. Code split (§2). Every test turns green without edits.
5. Notebooks moved and path-fixed (§4).
6. CI (§6), `AGENTS.md`, `CLAUDE.md`, README setup/run section, ruff hook.
7. **Break-it check:** temporarily change the code in three ways (rescale-first instead of cut-first; wrong root folder; import `torch` in `neurolens/__init__.py`), confirm the golden, settings and import-hygiene tests each fail, then undo. A test that cannot fail proves nothing.
8. Tell the team, then merge.

## 9. Done when
- `pytest`, `ruff check .`, `ruff format --check .` pass locally and in GitHub Actions.
- A fresh venv installs with `pip install -e . -r requirements/dev.txt` from a clean clone.
- The golden test passed before and after the move without edits.
- `python app.py` starts on the Mac and the page loads sample videos and thumbnails. A real upload waits for a machine with the model (M2).
- `git diff --stat main..aws-josh` shows moves, not rewrites of `static/`, `data/` or the specs.
