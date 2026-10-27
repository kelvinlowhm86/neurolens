"""
Pre-warm: start a GPU the moment a user asks for an upload URL, so the worker boots and loads
TRIBE v2 *while the video is still uploading*. The queue-depth alarm is only the backup path.

Only ever raises desired capacity; scale-in is owned by the workers (idle self-termination).
"""

import logging
import math

from neurolens_common import jobs
from neurolens_common.config import Settings

from .services import Services

logger = logging.getLogger("neurolens.web.warm_pool")


def nudge(conn, cfg: Settings, svc: Services) -> str:
    """Make sure enough GPUs are running or starting for current demand. Returns what it did."""
    if not cfg.prewarm_enabled:
        return "disabled"
    if cfg.app_env != "aws" or svc.asg is None or not cfg.asg_name:
        return "dev_noop"
    needed = min(cfg.max_workers, math.ceil(jobs.count_demand(conn) / cfg.jobs_per_worker))
    group = svc.describe_asg(cfg.asg_name, ttl_s=0)
    current = group["DesiredCapacity"] if group else 0
    if needed <= current:
        return "already_sufficient"
    svc.asg.set_desired_capacity(AutoScalingGroupName=cfg.asg_name, DesiredCapacity=needed, HonorCooldown=False)
    logger.info("Pre-warm: desired capacity %d → %d", current, needed)
    return f"scaled_{current}_to_{needed}"


def capacity(conn, cfg: Settings, svc: Services) -> dict:
    """What the UI shows in its GPU chip: workers ready to take jobs, and workers still starting."""
    ready = conn.execute(
        """SELECT count(*) AS n FROM workers
            WHERE warm_ready_at IS NOT NULL AND terminated_at IS NULL"""
    ).fetchone()["n"]
    starting = 0
    group = svc.describe_asg(cfg.asg_name) if cfg.asg_name else None
    if group:
        alive = [i for i in group.get("Instances", []) if i["LifecycleState"] in ("Pending", "Pending:Wait",
                                                                                  "Pending:Proceed", "InService")]
        starting = max(0, max(len(alive), group["DesiredCapacity"]) - ready)
    return {"workers_ready": ready, "workers_starting": starting}
