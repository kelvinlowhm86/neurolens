"""Worker failure types. Permanent ones end the job with a refund; the rest let SQS retry."""


class PermanentJobError(Exception):
    """The input itself is unusable — retrying cannot help. Job → FAILED, credits released."""

    code = "INVALID_MEDIA"

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        if code:
            self.code = code


class InvalidMedia(PermanentJobError):
    code = "INVALID_MEDIA"


class TooLong(PermanentJobError):
    code = "TOO_LONG"


class NotEnoughCredits(PermanentJobError):
    code = "INSUFFICIENT_CREDITS"


class SpotInterrupted(Exception):
    """This instance is being reclaimed. Hand the job back to the queue."""


class LostOwnership(Exception):
    """Another worker took the job over (our heartbeat went stale). Stop quietly."""
