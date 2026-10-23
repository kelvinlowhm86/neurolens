# NeuroLens: rules for people and AI tools

NeuroLens predicts brain activity for a video (TRIBE v2) and turns it into a signed, second-by-second brain response (7 signals in 3 groups, a 3D map), served by a Flask web app. Course project: not for commercial use (the model is CC BY-NC).

## Layout
- `neurolens/` the package: `settings.py` (config + paths), `response.py` (signals from the brain-area map, pure maths, numpy only), `inference.py` (model + atlas loading, `build_events`, `without_audio`, `predict`, fake mode), `media.py` (ffprobe duration), `pricing.py`, `storage.py` (S3 helpers), `db.py` (Aurora/Postgres access), `billing.py` (credits, ledger, sign-in users), `results.py`, `experiment_runs.py`, `worker.py` (the SageMaker container: `/ping`, `/invocations`, claim, charge/refund), `lambdas/` (dispatcher, SageMaker error handler, GPU guard, reaper, dead-letter handler), `web/` (`app.py` `create_app()` and Flask routes, `auth.py`, `lambda_handler.py`).
- `app.py` and `worker.py` at the root are thin launchers (`python app.py`, port 5003). `static/` the page. `data/` samples, videos, thumbnails.
- `notebooks/` the two `.ipynb` files (need a GPU). `requirements/` per-machine dependency lists. `infra/` Terraform, IAM policies, the worker image and its build scripts, deploy and start/stop scripts, DB migrations. `experiments/` operator scripts for the study runs. `tests/` pytest. `docs/` specs.

## Commands
- Set up: `python3.12 -m venv .venv && source .venv/bin/activate && pip install -e . -r requirements/dev.txt`
- Test: `pytest`. Lint: `ruff check .`. Format: `ruff format .`

## Rules
- Heavy imports (`torch`, `tribev2`, `nilearn`, `huggingface_hub`) only inside functions, never at module top of `neurolens/`.
- The web code never imports `neurolens.inference` (nor `torch`): analysis happens only in the SageMaker container (`python worker.py serve`; set `FAKE_INFERENCE=1` on a laptop). `create_app(data_dir=None, cfg=None)` never reads `config.json`; the launcher `app.py` loads it.
- Read config only through `neurolens.settings`. Nothing runs at import time.
- Settings: `config.json` is committed and shared (behaviour settings only, no secrets). Secrets and deployment-specific values (`HF_TOKEN`, `NEUROLENS_S3_BUCKET`, `NEUROLENS_SQS_QUEUE_URL`, `NEUROLENS_GPU_ENDPOINT`) go in `.env` (git-ignored; copy `.env.example`) and are read through `settings.load_settings`. Never commit `.env`, tokens or AWS keys, and never put them in Docker images.
- Never edit a test to make it pass: stop and ask.
- Do not change `extract_response` without regenerating `tests/fixtures/extract_response_golden.json` and saying so.
- Values rule, do not "fix": `extract_response` gives signed means with no absolute value and no rescaling (0 = typical film/TV viewing, the model's built-in scale), so videos can be compared. The muted (no-audio) series are cut to the shared length of the two passes.
- Work on branch `aws-josh`, not `main`. Specs in `docs/` describe the current design only; history lives in git.
