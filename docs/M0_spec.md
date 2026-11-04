# NeuroLens — M0 Implementation Spec
Only the parts of this milestone still in use are described here; the rest is in later specs (see docs/README.md).
**Milestone:** Repo structure, tooling and CI (before any AWS work)
**Grounded in:** the repository on branch `aws-josh`.

**Ground rules:** M0 changes **no behaviour**. The app must serve the same pages and compute the same numbers as before. No AWS resources, no GPU, nothing billable. Python **3.12** everywhere (venv, CI, later the GPU worker): `tribev2` pins `torch<2.7`, which has no Python 3.14 builds, so the Mac's default Python cannot be used. The pins in §3 were checked with `uv pip compile` (installable together); whether they *run* together is proven by the real-import check and the GPU run (M3e §12). Everything runs on a laptop without `config.json` or the model.

## 3. Tooling
- **`pyproject.toml`:** package `neurolens`, `requires-python >=3.12,<3.14`, no `dependencies` (the `requirements/` files are the single source), installed with `pip install -e .`. Ruff rules `E, F, I, B`, line length 100, `*.ipynb` excluded. pytest `testpaths = tests`.
- **`requirements/`:** `base.txt` (`numpy==2.2.6`, the version `tribev2` forces, so worker and CI run the same numpy. This replaces the README's old `numpy<2.1` pin, which guarded an `ImportError: _center` seen with an earlier `tribev2`; if that error reappears at the real-import check, revisit this pin), `web.txt` (flask and the web dependencies; no numpy, which the web code never imports), `worker.txt` (`-r base.txt`, nilearn: everything the worker needs *except* the model, so fake mode runs on a laptop), `model.txt` (`-r worker.txt`, `transformers>=4.45,<5`, `tribev2` via its `git+https` URL: only on the GPU machine or for the real-import check), `notebooks.txt` (`-r model.txt`, jupyter, ipykernel, ipywidgets, matplotlib, packaging), `dev.txt` (`-r web.txt`, `-r worker.txt`, pytest, ruff). Each file installs only what its machine needs: the web server never gets `torch`, the GPU machine never gets Jupyter. The old `requirements.txt` is removed.
- **`.gitignore` additions:** `.ruff_cache/`, `.pytest_cache/`, `.coverage`, `dist/`, `build/`, `*.tfstate*`, `.terraform/`.
- **Claude Code hook:** a ruff format/check PostToolUse hook in `.claude/settings.local.json` (personal). CI is what enforces the rules for everyone.

## 4. Notebooks
`git mv` both into `notebooks/`. In `batch_process.ipynb`, replace working-directory-relative paths with paths built from the project root the notebook already finds (`_find_project_root()`). Leave their duplicate maths untouched (re-running needs a GPU). Before the notebooks are pushed, clear the `explore.ipynb` cell 4 output that prints the first 8 and last 4 characters of the HF token.

## 6. CI
`.github/workflows/ci.yml`, on push and pull request: Python 3.12, `pip install -e .`, `pip install -r requirements/dev.txt`, `ruff check .`, `ruff format --check .`, `pytest`. CI never installs `torch` or `tribev2`. GitHub Actions is free for public repositories and has a monthly free allowance for private ones.

## 7. `AGENTS.md` and `CLAUDE.md`
`CLAUDE.md` contains one line: `@AGENTS.md` (teammates may use other tools). `AGENTS.md` is short: one-line project summary; layout map; commands (`pip install -e .`, `pytest`, `ruff check .`, `ruff format .`); rules: heavy imports only inside functions, web must not import `inference`, config only through `settings`, never commit secrets or tokens (`config.json` is committed, shared settings only), never edit a test to make it pass (stop and ask instead), do not change `extract_response` without regenerating `tests/fixtures/extract_response_golden.json` and saying so, keep the values rule (signed means, no rescaling; M3d §2); branch `aws-josh`; specs describe the current design only.

## 8. Order of work (one commit each, on `aws-josh`)
1. Golden fixture (generated from the original code, before any move).
2. `pyproject.toml`, `requirements/`, `.gitignore` (needed to run pytest at all).
3. **All tests written, by the separate test-writing agent. They fail.** Commit them. From here on `git diff` on `tests/` must stay empty.
4. Code split into the package layout (AGENTS.md). Every test turns green without edits.
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
