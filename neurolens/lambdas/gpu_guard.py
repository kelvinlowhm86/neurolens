"""GPU guard (docs/M3e_spec.md §6c), every 10 minutes.

Stops the endpoint when an instance is running or still wanted, no keep-warm holds it, and
neither a live claim, a dispatched request nor any recent job activity says work is still coming.
It reads our own database rather than the backlog metric the scale-in alarm uses.
"""

import logging
import os

from neurolens import billing, lambdas
from neurolens.billing import STALE_AFTER_S

logger = logging.getLogger(__name__)

DISPATCHED_S = 3 * 60 * 60
QUIET_S = 30 * 60
VARIANT = "main"


def _scalable_target_id(endpoint):
    return f"endpoint/{endpoint}/variant/{VARIANT}"


def _work_possible(db):
    """True when a live claim, a dispatched request, recent activity, or an active booth
    keep-warm setting says work is coming."""
    with db.transaction() as tx:
        warm = billing._read_booth_gpu_warm(tx)
        rows = tx.execute(
            "SELECT "
            "EXISTS(SELECT 1 FROM jobs WHERE status = :processing "
            "AND updated_at > now() - make_interval(secs => :live)) AS live, "
            "EXISTS(SELECT 1 FROM jobs WHERE status = :queued "
            "AND verified_duration_ms IS NOT NULL "
            "AND created_at > now() - make_interval(secs => :dispatched)) AS dispatched, "
            "EXISTS(SELECT 1 FROM jobs "
            "WHERE updated_at > now() - make_interval(secs => :quiet)) AS recent",
            {
                "processing": "processing",
                "queued": "queued",
                "live": STALE_AFTER_S,
                "dispatched": DISPATCHED_S,
                "quiet": QUIET_S,
            },
        )
    return bool(warm > 0 or rows[0]["live"] or rows[0]["dispatched"] or rows[0]["recent"])


def check(db, *, sagemaker, appscaling, endpoint):
    """Stop the endpoint when every condition allows it. True when it stopped it."""
    description = sagemaker.describe_endpoint(EndpointName=endpoint)
    variants = description.get("ProductionVariants", [])
    variant = variants[0] if variants else {}
    instances = variant.get("CurrentInstanceCount", 0)
    # A desired count with no instance yet is a start AWS has not filled (no GPU free); it keeps
    # trying, and the GPU would arrive later with nobody waiting for it.
    desired = variant.get("DesiredInstanceCount", 0)
    if instances < 1 and desired < 1:
        return False

    targets = appscaling.describe_scalable_targets(
        ServiceNamespace="sagemaker",
        ResourceIds=[_scalable_target_id(endpoint)],
        ScalableDimension="sagemaker:variant:DesiredInstanceCount",
    ).get("ScalableTargets", [])
    if targets and targets[0].get("MinCapacity", 0) >= 1:
        return False

    if _work_possible(db):
        return False

    sagemaker.update_endpoint_weights_and_capacities(
        EndpointName=endpoint,
        DesiredWeightsAndCapacities=[{"VariantName": VARIANT, "DesiredInstanceCount": 0}],
    )
    logger.warning(f"GPU_GUARD_STOPPED {instances} {desired}")
    return True


def handler(event, context):
    lambdas.setup_logging()
    endpoint = os.environ["NEUROLENS_GPU_ENDPOINT"]
    check(
        lambdas.database(),
        sagemaker=lambdas.sagemaker_client(),
        appscaling=lambdas.appscaling_client(),
        endpoint=endpoint,
    )
