"""
Worker process: boot → load model → warm up → poll SQS → process → (idle) leave the pool.

The model loads and warms *before* the first poll. With the web tier's pre-warm nudge, this
happens while the user is still uploading, so the GPU is usually ready when the file lands.
"""

import logging
import threading
import time

from neurolens_common import aws
from neurolens_common.config import Settings

from .inference import make_backend
from .jobqueue import JobQueue
from .lifecycle import Lifecycle
from .metrics import Metrics
from .pipeline import JobRunner
from .spot import SpotWatcher

logger = logging.getLogger("neurolens.worker")


def run(cfg: Settings) -> None:
    on_aws = cfg.app_env == "aws"
    if not cfg.sqs_queue_url:
        raise RuntimeError("SQS_QUEUE_URL is not set")

    lc = Lifecycle(cfg, aws.client("autoscaling") if on_aws else None, aws.client("ec2") if on_aws else None)
    lc.register()
    metrics = Metrics(aws.client("cloudwatch") if on_aws else None,
                      {"InstanceType": lc.instance_type, "Lifecycle": lc.lifecycle})
    interrupted = threading.Event()
    if on_aws:
        SpotWatcher(interrupted).start()

    backend = make_backend(cfg)
    t0 = time.monotonic()
    backend.load()
    t_load = time.monotonic() - t0
    lc.mark("model_loaded_at")
    t0 = time.monotonic()
    backend.warmup()
    t_warm = time.monotonic() - t0
    lc.mark("warm_ready_at")
    metrics.put({"ModelLoadSeconds": t_load, "WarmupSeconds": t_warm})
    logger.info("Worker %s ready (%s backend; load %.1fs, warm-up %.1fs)", lc.worker_id, backend.name, t_load, t_warm)

    queue = JobQueue(aws.client("sqs"), cfg.sqs_queue_url)
    runner = JobRunner(cfg, backend, aws.client("s3"), queue, lc, metrics, interrupted)
    idle_since = time.monotonic()

    while not interrupted.is_set():
        msg = queue.receive(wait_s=20, visibility_s=300)
        if msg is None:
            if on_aws and time.monotonic() - idle_since > cfg.idle_shutdown_seconds and _safe_to_leave(lc, queue):
                lc.self_terminate("idle")
                return
            continue
        lc.protect(True)
        try:
            runner.handle(msg)
        finally:
            lc.protect(False)
            idle_since = time.monotonic()

    # AWS reclaims the instance itself; desired capacity stays put so the ASG can replace it.
    logger.warning("Spot interruption: stopping the poll loop")
    lc.record_termination("spot")


def _safe_to_leave(lc: Lifecycle, queue: JobQueue) -> bool:
    """Leave only if no work is queued and nobody is mid-upload expecting this GPU."""
    try:
        return queue.visible_count() == 0 and not lc.someone_is_uploading()
    except Exception:
        logger.warning("Idle check failed; staying up", exc_info=True)
        return False
