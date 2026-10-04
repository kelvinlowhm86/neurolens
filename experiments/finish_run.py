"""Write a run's manifest.json once all its files are in S3 (docs/M2b_spec.md §10).

    python experiments/finish_run.py experiment-1 <run_id> --series exp1-final \
        --started 2026-10-14T03:00:00Z --finished 2026-10-14T04:30:00Z \
        --instance-type g6e.xlarge --ami-id ami-...

Refuses if a file the experiment needs is missing, so a half-finished run never looks complete.
"""

import argparse

from neurolens.experiment_runs import EXPERIMENT_FILES


def main():
    parser = argparse.ArgumentParser(description="Write a run's manifest.json.")
    parser.add_argument("experiment", choices=sorted(EXPERIMENT_FILES))
    parser.add_argument("run_id")
    parser.add_argument("--series", required=True, help="name grouping runs that belong together")
    parser.add_argument("--started", required=True, help="UTC, e.g. 2026-10-14T03:00:00Z")
    parser.add_argument("--finished", required=True)
    parser.add_argument("--instance-type", required=True)
    parser.add_argument("--ami-id", required=True)
    args = parser.parse_args()

    import boto3

    from neurolens import experiment_runs, settings

    cfg = settings.load_settings()
    s3 = boto3.client("s3", region_name=cfg["aws"]["region"])
    manifest = experiment_runs.write_manifest(
        s3,
        cfg["aws"]["s3_bucket"],
        args.experiment,
        args.run_id,
        series=args.series,
        started_utc=args.started,
        finished_utc=args.finished,
        environment={"type": "aws", "instance_type": args.instance_type, "ami_id": args.ami_id},
    )
    print(f"manifest written: {manifest['files']}")


if __name__ == "__main__":
    main()
