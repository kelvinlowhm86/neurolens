"""Experiment 2: the one-minute endpoint backlog and instance-count series (M3e §10), written to
experiment-2/<run_id>/cloudwatch.csv.

    python experiments/export_cloudwatch.py <run_id> \
        --start 2026-10-14T03:00:00Z --end 2026-10-14T05:00:00Z
"""

import argparse

from neurolens.storage import utc_parse, utc_text

COLUMNS = ["minute_utc", "backlog", "instances"]
VARIANT = "main"


def merge_series(series):
    """{column: {minute: value}} -> one row per minute, in time order. Pure."""
    minutes = sorted({m for values in series.values() for m in values})
    return [{"minute_utc": m, **{col: series[col].get(m) for col in COLUMNS[1:]}} for m in minutes]


def main():
    parser = argparse.ArgumentParser(description="Export the CloudWatch series of a run.")
    parser.add_argument("run_id")
    parser.add_argument("--start", required=True, help="UTC, e.g. 2026-10-14T03:00:00Z")
    parser.add_argument("--end", required=True)
    args = parser.parse_args()

    import boto3

    from neurolens import experiment_runs, settings

    cfg = settings.load_settings()
    aws = cfg["aws"]
    endpoint = aws["gpu_endpoint"]
    cloudwatch = boto3.client("cloudwatch", region_name=aws["region"])
    s3 = boto3.client("s3", region_name=aws["region"])

    def query(id_, metric, stat):
        return {
            "Id": id_,
            "MetricStat": {
                "Metric": {
                    "Namespace": "AWS/SageMaker",
                    "MetricName": metric,
                    "Dimensions": [{"Name": "EndpointName", "Value": endpoint}],
                },
                "Period": 60,
                "Stat": stat,
            },
        }

    queries = [
        query("backlog", "ApproximateBacklogSize", "Maximum"),
        # AWS documents the CPU sample count as the way to count running instances.
        query("instances", "CPUUtilization", "SampleCount"),
    ]
    names = {"backlog": "backlog", "instances": "instances"}
    series = {col: {} for col in COLUMNS[1:]}
    for page in cloudwatch.get_paginator("get_metric_data").paginate(
        MetricDataQueries=queries, StartTime=utc_parse(args.start), EndTime=utc_parse(args.end)
    ):
        for result in page["MetricDataResults"]:
            for stamp, value in zip(result["Timestamps"], result["Values"], strict=True):
                series[names[result["Id"]]][utc_text(stamp)] = value

    rows = merge_series(series)
    experiment_runs.put_text(
        s3,
        aws["s3_bucket"],
        "experiment-2",
        args.run_id,
        "cloudwatch.csv",
        experiment_runs.csv_text(COLUMNS, rows),
    )
    print(f"{len(rows)} minutes written to experiments/experiment-2/{args.run_id}/cloudwatch.csv")


if __name__ == "__main__":
    main()
