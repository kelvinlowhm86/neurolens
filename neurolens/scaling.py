"""Read whether the GPU endpoint is paused (stop_work.sh)."""


def endpoint_paused(appscaling, endpoint):
    """True when the SageMaker endpoint's scalable target has a maximum of 0 (stop_work.sh), so
    no upload could ever run. A missing target counts as paused. Raises if it cannot be read."""
    targets = appscaling.describe_scalable_targets(
        ServiceNamespace="sagemaker",
        ResourceIds=[f"endpoint/{endpoint}/variant/main"],
        ScalableDimension="sagemaker:variant:DesiredInstanceCount",
    )["ScalableTargets"]
    return not targets or targets[0].get("MaxCapacity", 0) == 0
