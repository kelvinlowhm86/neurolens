"""Deadlines shared by the reaper and the dispatcher. Terraform owns the reaper schedule."""

JOB_DEADLINE_S = 4 * 60 * 60
INVOCATION_TIMEOUT_S = 60 * 60
DISPATCH_DEADLINE_S = JOB_DEADLINE_S - INVOCATION_TIMEOUT_S
QUEUE_TTL_MAX_S = 2 * 60 * 60
HEARTBEAT_STALE_S = 10 * 60
FAILED_RETRY_S = 2 * 24 * 60 * 60
