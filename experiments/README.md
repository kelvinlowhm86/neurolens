# Experiments

This folder holds the report experiments. Each script's docstring lists its columns and options. The AWS scripts run from the repo root with the profile variables from `infra/terraform/README.md` §0 exported.

## Report experiments

The Experiment 1 and 2 scripts write `experiments/<experiment>/<run_id>/` in the S3 bucket. Locust writes its per-job times to the local `JOBS_CSV` and its request stats to its `--csv` files; you upload both to the run folder (below). `finish_run.py` writes a run's `manifest.json` once every required file is in S3. Experiment 3 runs locally and writes local files only.

### Experiment 1: latency ([Final report, section 4](../docs/course/Final_report.pdf))

- `latency_run.py CLIP --run-id <id> --label <label> [--api http://localhost:5003]`: one browser-like run (presign, upload, poll, fetch); appends a row to `experiment-1/<run_id>/runs.csv` in S3. It needs the web app running and a warm endpoint.
- `latency_breakdown.py --run-id <id> [--run-id <id> ...]`: reads those rows and prints each stage's median, minimum and maximum per clip length.

### Experiment 2: load bursts and cold starts ([Final report, section 4](../docs/course/Final_report.pdf))

- `locustfile.py`: Locust load generator; `CLIP` is required. Install with `pip install -r requirements/experiments.txt`. Each user does one job, so a burst of N users is N simultaneous jobs. Writes per-job times to `JOBS_CSV` and request stats to Locust's `--csv` files. Example:
  ```bash
  CLIP=data/videos/<clip>.mp4 BURST_SIZE=5 JOBS_CSV=jobs.csv locust -f experiments/locustfile.py \
      --headless -u 5 -r 5 --host http://localhost:5003 --csv locust --run-time 3h
  ```
  With `--csv locust`, Locust writes `locust_stats.csv` (plus its history, failures and exceptions files). After the run, upload the per-job times and the stats to the run folder:
  ```bash
  BUCKET=$(terraform -chdir=infra/terraform output -raw bucket_name)
  aws s3 cp jobs.csv "s3://$BUCKET/experiments/experiment-2/<run_id>/jobs.csv"
  aws s3 cp locust_stats.csv "s3://$BUCKET/experiments/experiment-2/<run_id>/locust_stats.csv"
  ```
- `export_cloudwatch.py <run_id> --start ... --end ...`: writes the one-minute backlog and instance-count series to `experiment-2/<run_id>/cloudwatch.csv`.
- `cold_start.py --since ... --run-id ... --instance-type ...`: joins the workers' boot records with scaling history into `experiment-2/<run_id>/cold_start.csv`.

### Experiment 3: cloud vs on-premise cost ([Final report, section 4](../docs/course/Final_report.pdf))

This experiment runs locally and writes local files only: it has no S3 run and no manifest.

- `tco_benchmark.py CLIP [CLIP ...] [--runs N] [--out PATH]`: times the worker pipeline on a GPU machine; `tco_benchmark.sbatch` runs it on Slurm. Writes a CSV with `clip`, `video_seconds`, `run`, `total_seconds` and `gpu_name`.
- `tco_compare.py --cloud-csv ... --onprem-csv ... [--assumptions experiments/tco_assumptions.json] [--out-dir .]`: combines Experiment 1's rows and the on-prem CSV with the assumptions; writes `exp3_tco.csv` and `exp3_tco.md`.

`tco_assumptions.json` holds the cloud price, idle wait, cold start, videos per cold session and fixed monthly cost, plus the on-premise hardware, power and amortisation inputs. The on-prem GPU's name, price and power draw start empty: fill them in for the card the benchmark actually ran on. The compare script stops while they are empty or when the CSV's `gpu_name` names a different card.

## All report experiments

- `finish_run.py <experiment> <run_id> --series ... --started ... --finished ... --instance-type ... --image-digest ...`: writes an Experiment 1 or 2 run's `manifest.json`, refusing if a required file is missing. It refuses `experiment-3`, which has no S3 run.
