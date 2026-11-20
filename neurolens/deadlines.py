"""Deadlines shared by the reaper and the dispatcher. Terraform owns the reaper schedule."""

from neurolens import settings

INVOCATION_TIMEOUT_S = 60 * 60
QUEUE_TTL_MAX_S = 2 * 60 * 60
HEARTBEAT_STALE_S = 10 * 60
FAILED_RETRY_S = 2 * 24 * 60 * 60


def job_deadline_s(cfg):
    """How long a job may stay open, from config `queue.job_deadline_hours`."""
    return settings.job_deadline_hours(cfg) * 60 * 60


def dispatch_deadline_s(cfg):
    """The dispatcher's share of the job deadline (the rest is the invocation timeout)."""
    return job_deadline_s(cfg) - INVOCATION_TIMEOUT_S
