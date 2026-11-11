# Experiments

This folder holds the report experiments and the side trials. Each script's docstring lists its columns and options.
Run them from the repo root with the profile variables from `infra/terraform/README.md` §0
exported. Experiments 1-3 have not been run yet. The side trials' results are local under
`data/experiment_results/` (git-ignored).

## Report experiments

Each run writes `experiments/<experiment>/<run_id>/` in the S3 bucket; `finish_run.py` writes its
`manifest.json` once every required file is there.

### Experiment 1: latency (laptop against the running app)

- `latency_run.py CLIP --run-id <id> --label <label>`: one browser-like run (presign, upload,
  poll, fetch); appends a row to `experiment-1/<run_id>/runs.csv` in S3.
- `latency_breakdown.py --run-id <id> [...]`: reads those rows and prints each stage's median,
  minimum and maximum per clip length.

### Experiment 2: load bursts (laptop; AWS for the series)

- `locustfile.py`: Locust load generator; `CLIP` is required. Install with
  `pip install -r requirements/experiments.txt`. Each user does one job, so a burst of N users is
  N simultaneous jobs. Writes per-job times to `JOBS_CSV` and request stats to Locust's `--csv`
  files. Example:
  ```bash
  CLIP=data/videos/<clip>.mp4 BURST_SIZE=5 JOBS_CSV=jobs.csv locust -f experiments/locustfile.py \
      --headless -u 5 -r 5 --host http://localhost:5003 --csv locust --run-time 3h
  ```
- `export_cloudwatch.py <run_id> --start ... --end ...`: writes the one-minute backlog and
  instance-count series to `experiment-2/<run_id>/cloudwatch.csv`.
- `cold_start.py --since ... --run-id ... --instance-type ...`: joins the workers' boot records
  with scaling history into `experiment-2/<run_id>/cold_start.csv`.

### Experiment 3: cloud vs on-premise cost (Slurm + laptop)

- `tco_benchmark.py CLIP [CLIP ...]`: times the worker pipeline on a GPU machine; `tco_benchmark.sbatch`
  runs it on Slurm. Writes `exp3_onprem.csv`.
- `tco_compare.py --cloud-csv ... --onprem-csv ...`: combines Experiment 1's rows and the on-prem
  CSV with `tco_assumptions.json`; writes `exp3_tco.csv` and `exp3_tco.md`.

The on-prem GPU's name, price and power draw in `tco_assumptions.json` start empty: fill them in for
the card the benchmark actually ran on. The compare script stops while they are empty or when the
CSV's `gpu_name` names a different card.

### All report experiments

- `finish_run.py <experiment> <run_id> ...`: writes a run's `manifest.json`, refusing if a required
  file is missing.

## Side trials (local)

- `crop_vs_pad.py CLIP [CLIP ...] --out data/experiment_results/crop_vs_pad`: compares cropped
  with letterboxed frames; `crop_vs_pad.sbatch` runs it on Slurm. Reads `data/videos/gaze/*.mp4`,
  writes `data/experiment_results/crop_vs_pad/`.
- `gaze_trial.py CLIP [CLIP ...] --out data/experiment_results/gaze_trial`: ViNet-S saliency
  heatmaps; `gaze_trial.sbatch` runs it on Slurm and fetches the model. Reads
  `data/videos/gaze/*.mp4`, writes `data/experiment_results/gaze_trial/`.
- `run_clips.py CLIP [CLIP ...] --out DIR [--save-predictions]`: brain response for local clips,
  one `DIR/<clip>.json` each; `--save-predictions` adds `DIR/<clip>.npy`, the prediction for every
  point of the brain surface. `run_clips.sbatch` runs it on Slurm with the same arguments, and it
  also works on a laptop with `FAKE_INFERENCE=1`.

The `*.sbatch` files are Slurm submission scripts; submit them from the repo root.

## Ad Meter pilot (local + Slurm)

Question: does the predicted brain response separate well-liked from disliked ads, and does it do
so better than measures that need no brain model? The ads are the top 15 and bottom 15 of USA
Today's Ad Meter for Super Bowl LX (2026), in the cut that aired. `ad_meter_2026.csv` lists them
with their rank, score and YouTube source. The clips are third-party: they stay local under
`data/videos/ad_meter_2026/` (git-ignored) and are never published.

The rule, fixed before any result was seen:

- Main score per ad: the mean of the overall signal (with sound). Tested one way only: higher
  means better liked.
- Measure: pick one top ad and one bottom ad at random; how often does the top ad score higher?
  (the AUC; 0.5 is a coin flip).
- Simple measures compared against it: ad length, share of frames with a face (YuNet), mean
  loudness, cuts per second. Each may count in either direction; the best of the four is the bar.

| Main score's AUC | Verdict |
|---|---|
| below 0.60 | No signal |
| 0.60 to below 0.70 | Unclear |
| 0.70 or above, and above the best simple measure | Real hint (worth a run on about 150 ads) |
| 0.70 or above, but not above the best simple measure | Adds nothing over simple measures |

The other NeuroLens scores in the report (share of seconds above typical, first 5 seconds, each
signal's mean) are secondary and never decide the verdict. Limits: Ad Meter measures liking, not
sales; comparing only the best and worst flatters any method; 30 ads is a small sample.

Steps (results land in `data/experiment_results/ad_meter_2026/`):

1. Download each clip named in `ad_meter_2026.csv` as `data/videos/ad_meter_2026/<clip>.mp4`:
   `yt-dlp -f "bv*[height<=720][vcodec^=avc1]+ba[ext=m4a]/b[height<=720][ext=mp4]" --merge-output-format mp4 -o "<clip>.%(ext)s" "https://www.youtube.com/watch?v=<youtube_id>"`
2. Brain response, on Slurm:
   `sbatch --time=03:00:00 experiments/run_clips.sbatch data/videos/ad_meter_2026/*.mp4 --out data/experiment_results/ad_meter_2026 --save-predictions`
   (the saved predictions allow exploratory region groupings later; they never decide the verdict)
3. Simple measures, on the laptop (needs `opencv-python-headless`, pinned in
   `gaze_trial_requirements.txt`; fetches the 0.2 MB YuNet model once, SHA-256 checked):
   `python experiments/ad_meter_baseline.py data/videos/ad_meter_2026/*.mp4 --out data/experiment_results/ad_meter_2026/baseline.csv`
4. Verdict:
   `python experiments/ad_meter_analyse.py --ads experiments/ad_meter_2026.csv --brain data/experiment_results/ad_meter_2026 --baseline data/experiment_results/ad_meter_2026/baseline.csv --out data/experiment_results/ad_meter_2026/verdict.md`
