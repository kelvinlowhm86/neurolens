# NeuroLens: rules for people and AI tools

NeuroLens predicts brain activity for a video (TRIBE v2) and turns it into per-region "engagement" curves, served by a Flask web app. Course project: not for commercial use (the model is CC BY-NC).

## Layout
- `neurolens/` the package: `settings.py` (config + paths), `engagement.py` (pure maths, numpy only), `inference.py` (model + atlas loading, `build_events`, `without_audio`, `predict`, `probe_duration`, fake mode), `pricing.py`, `storage.py` (S3 helpers), `worker.py` (SQS poll loop), `web/app.py` (`create_app()`, Flask routes).
- `app.py` thin launcher (`python app.py`, port 5003). `static/` the page. `data/` samples, videos, thumbnails.
- `notebooks/` the two `.ipynb` files (need a GPU). `requirements/` per-machine dependency lists. `infra/` AWS-only files (later milestones). `tests/` pytest. `docs/` specs.

## Commands
- Set up: `python3.12 -m venv .venv && source .venv/bin/activate && pip install -e . -r requirements/dev.txt`
- Test: `pytest`. Lint: `ruff check .`. Format: `ruff format .`

## Rules
- Heavy imports (`torch`, `tribev2`, `nilearn`, `huggingface_hub`) only inside functions, never at module top of `neurolens/`.
- The web code never imports `neurolens.inference` (nor `torch`): analysis happens only in the worker (`python worker.py`; set `FAKE_INFERENCE=1` on a laptop). `create_app(data_dir=None, cfg=None)` never reads `config.json`; the launcher `app.py` loads it.
- Read config only through `neurolens.settings`. Nothing runs at import time.
- Settings: `config.json` is committed and shared (behaviour settings only, no secrets). Secrets and deployment-specific values (`HF_TOKEN`, `NEUROLENS_S3_BUCKET`, `NEUROLENS_SQS_QUEUE_URL`) go in `.env` (git-ignored; copy `.env.example`) and are read through `settings.load_settings`. Never commit `.env`, tokens or AWS keys, and never put them in Docker images.
- Never edit a test to make it pass: stop and ask.
- Do not change `extract_engagement` without updating `tests/fixtures/extract_engagement_golden.json` and saying so.
- Normalisation rule, do not "fix": `extract_engagement` cuts the with-audio and no-audio auditory passes to their shared length first, then rescales each to 0-1. So `auditory` and `auditory_with_audio` can differ in the same row.
- Work on branch `aws-josh`, not `main`. Specs in `docs/` describe the current design only; history lives in git.
