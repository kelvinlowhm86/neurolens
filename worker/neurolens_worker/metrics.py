"""
CloudWatch custom metrics (namespace NeuroLens) for the dashboard and Experiments 1–2.

On AWS they go out via PutMetricData; locally they are only logged. The authoritative per-job
timings are the timestamp columns in `jobs` / `workers` — these metrics are for live graphs.
"""

import logging

logger = logging.getLogger("neurolens.worker.metrics")

NAMESPACE = "NeuroLens"


class Metrics:
    def __init__(self, cloudwatch_client=None, dimensions: dict[str, str] | None = None):
        self.cw = cloudwatch_client
        self.dimensions = [{"Name": k, "Value": v} for k, v in (dimensions or {}).items() if v]

    def put(self, values: dict[str, float], unit: str = "Seconds") -> None:
        values = {k: v for k, v in values.items() if v is not None}
        logger.info("metrics %s", values)
        if not self.cw or not values:
            return
        try:
            self.cw.put_metric_data(Namespace=NAMESPACE, MetricData=[
                {"MetricName": k, "Value": float(v), "Unit": unit, "Dimensions": self.dimensions}
                for k, v in values.items()
            ])
        except Exception:
            logger.warning("PutMetricData failed", exc_info=True)

    def count(self, name: str, n: int = 1) -> None:
        self.put({name: n}, unit="Count")
