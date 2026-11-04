# Experiments

The report experiments and the side trials. Each script's docstring has its columns and options.
Experiments 1–3 have not been run yet. The side trials' results are local under
`data/experiment_results/` (git-ignored).

## Report experiments

Each run writes `experiments/<experiment>/<run_id>/` in the S3 bucket; `finish_run.py` writes its
`manifest.json` once every required file is there.

### Experiment 1 — latency (laptop against the running app)

- `latency_run.py CLIP --run-id <id> --label <label>` — one browser-like run (presign, upload,
  poll, fetch); appends a row to `experiment-1/<run_id>/runs.csv` in S3.
- `latency_breakdown.py --run-id <id> [...]` — reads those rows and prints each stage's median,
  minimum and maximum per clip length.

### Experiment 2 — load bursts (laptop; AWS for the series)

- `locustfile.py` — Locust load generator; `CLIP` is required. Each user does one job, so a burst
  of N users is N simultaneous jobs. Writes per-job times to `JOBS_CSV` and request stats to
  Locust's `--csv` files.
- `export_cloudwatch.py <run_id> --start ... --end ...` — writes the one-minute backlog and
  instance-count series to `experiment-2/<run_id>/cloudwatch.csv`.
- `cold_start.py --since ... --run-id ... --instance-type ...` — joins the workers' boot records
  with scaling history into `experiment-2/<run_id>/cold_start.csv`.

### Experiment 3 — cloud vs on-premise cost (Slurm + laptop)

- `tco_benchmark.py CLIP [CLIP ...]` — times the worker pipeline on a GPU machine; `tco_benchmark.sbatch`
  runs it on Slurm. Writes `exp3_onprem.csv`.
- `tco_compare.py --cloud-csv ... --onprem-csv ...` — combines Experiment 1's rows and the on-prem
  CSV with `tco_assumptions.json`; writes `exp3_tco.csv` and `exp3_tco.md`.

### All report experiments

- `finish_run.py <experiment> <run_id> ...` — writes a run's `manifest.json`, refusing if a required
  file is missing.

## Side trials (local)

- `crop_vs_pad.py CLIP [CLIP ...]` — compares cropped with letterboxed frames; `crop_vs_pad.sbatch`
  runs it on Slurm. Reads `data/videos/gaze/*.mp4`, writes `data/experiment_results/crop_vs_pad/`.
- `gaze_trial.py CLIP [CLIP ...]` — ViNet-S saliency heatmaps; `gaze_trial.sbatch` runs it on Slurm
  and fetches the model. Reads `data/videos/gaze/*.mp4`, writes `data/experiment_results/gaze_trial/`.
- `run_clips.py CLIP [CLIP ...]` — brain response for local clips; `run_clips.sbatch` runs it on
  Slurm, and it also works on a laptop with `FAKE_INFERENCE=1`. Reads `data/videos/cerave_*.mp4`,
  writes `data/experiment_results/run_clips_cerave/`.

The `*.sbatch` files are Slurm submission scripts; submit them from the repo root.
