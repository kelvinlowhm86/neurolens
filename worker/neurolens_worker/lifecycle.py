"""
The worker's own lifecycle: identity, the `workers` table (cold-start timings for Experiment 2),
scale-in protection while busy, and self-termination when idle (how the pool scales to zero).

On AWS the Auto Scaling Group only ever scales *out*; scale-in happens only here, so the ASG can
never kill a GPU in the middle of a job. In dev mode (APP_ENV=dev) the AWS calls are skipped.
"""

import logging
import socket
from datetime import datetime, timezone

from neurolens_common import db
from neurolens_common.config import Settings

from .spot import imds_get

logger = logging.getLogger("neurolens.worker.lifecycle")

TIMESTAMP_COLUMNS = {"container_started_at", "model_loaded_at", "warm_ready_at", "last_job_at", "terminated_at"}


class Lifecycle:
    def __init__(self, cfg: Settings, autoscaling_client=None, ec2_client=None):
        self.cfg = cfg
        self.on_aws = cfg.app_env == "aws"
        self.autoscaling = autoscaling_client
        self.ec2 = ec2_client
        if self.on_aws:
            self.worker_id = imds_get("meta-data/instance-id") or socket.gethostname()
            self.instance_type = imds_get("meta-data/instance-type")
            self.lifecycle = imds_get("meta-data/instance-life-cycle")    # "spot" | "on-demand"
        else:
            self.worker_id = f"dev-{socket.gethostname()}"
            self.instance_type = "dev"
            self.lifecycle = "dev"

    # ─── workers table ─────────────────────────────────────────────────────

    def register(self) -> None:
        launched = self._launch_time()
        with db.transaction() as conn:
            conn.execute(
                """INSERT INTO workers (instance_id, instance_type, lifecycle, launched_at, container_started_at)
                   VALUES (%s, %s, %s, %s, now())
                   ON CONFLICT (instance_id) DO UPDATE
                      SET container_started_at = now(), terminated_at = NULL, termination_reason = NULL""",
                (self.worker_id, self.instance_type, self.lifecycle, launched),
            )

    def mark(self, column: str) -> None:
        assert column in TIMESTAMP_COLUMNS
        with db.transaction() as conn:
            conn.execute(f"UPDATE workers SET {column} = now() WHERE instance_id = %s", (self.worker_id,))

    def job_done(self) -> None:
        with db.transaction() as conn:
            conn.execute(
                "UPDATE workers SET last_job_at = now(), jobs_completed = jobs_completed + 1 WHERE instance_id = %s",
                (self.worker_id,),
            )

    def _launch_time(self) -> datetime | None:
        if not (self.on_aws and self.ec2):
            return datetime.now(timezone.utc)
        try:
            r = self.ec2.describe_instances(InstanceIds=[self.worker_id])
            return r["Reservations"][0]["Instances"][0]["LaunchTime"]
        except Exception:
            logger.warning("Could not read instance launch time", exc_info=True)
            return None

    # ─── scale-in protection + scale to zero ───────────────────────────────

    def protect(self, on: bool) -> None:
        if not (self.on_aws and self.autoscaling and self.cfg.asg_name):
            return
        try:
            self.autoscaling.set_instance_protection(
                InstanceIds=[self.worker_id], AutoScalingGroupName=self.cfg.asg_name, ProtectedFromScaleIn=on,
            )
        except Exception:
            logger.warning("Could not set scale-in protection=%s", on, exc_info=True)

    def someone_is_uploading(self) -> bool:
        """A user minted an upload URL recently — they expect this GPU to be here when it lands."""
        with db.transaction() as conn:
            return conn.execute(
                """SELECT EXISTS (SELECT 1 FROM jobs WHERE status = 'AWAITING_UPLOAD'
                                     AND created_at > now() - interval '10 minutes') AS busy"""
            ).fetchone()["busy"]

    def record_termination(self, reason: str) -> None:
        with db.transaction() as conn:
            conn.execute(
                "UPDATE workers SET terminated_at = now(), termination_reason = %s WHERE instance_id = %s",
                (reason, self.worker_id),
            )

    def self_terminate(self, reason: str) -> None:
        """Leave the pool and shrink desired capacity by one. Dev mode just records it."""
        self.record_termination(reason)
        if not (self.on_aws and self.autoscaling):
            logger.info("Would self-terminate (%s); skipped outside AWS", reason)
            return
        logger.info("Self-terminating: %s", reason)
        self.autoscaling.terminate_instance_in_auto_scaling_group(
            InstanceId=self.worker_id, ShouldDecrementDesiredCapacity=True,
        )
