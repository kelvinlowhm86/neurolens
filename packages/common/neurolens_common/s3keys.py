"""
S3 key layout and S3-event parsing.

The S3 ObjectCreated event carries no session context, so the web tier encodes the user and job
into the object key when it mints the upload URL; the worker reads them back from the key.

    uploads/{user_id}/{job_id}/input{ext}
    results/{user_id}/{job_id}/result.json
    results/{user_id}/{job_id}/report.pdf
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import unquote_plus
from uuid import UUID

ALLOWED_EXTS = {".mp4", ".mov", ".webm"}


def input_key(user_id: UUID, job_id: UUID, ext: str) -> str:
    ext = ext.lower()
    if ext not in ALLOWED_EXTS:
        raise ValueError(f"unsupported extension {ext!r}")
    return f"uploads/{user_id}/{job_id}/input{ext}"


def result_key(user_id: UUID, job_id: UUID) -> str:
    return f"results/{user_id}/{job_id}/result.json"


def report_key(user_id: UUID, job_id: UUID) -> str:
    return f"results/{user_id}/{job_id}/report.pdf"


def parse_input_key(key: str) -> tuple[UUID, UUID, str]:
    """uploads/{user}/{job}/input.ext → (user_id, job_id, ext). Raises ValueError on anything else."""
    parts = key.split("/")
    if len(parts) != 4 or parts[0] != "uploads":
        raise ValueError(f"not an upload key: {key!r}")
    name = PurePosixPath(parts[3])
    if name.stem != "input" or name.suffix.lower() not in ALLOWED_EXTS:
        raise ValueError(f"not an upload key: {key!r}")
    return UUID(parts[1]), UUID(parts[2]), name.suffix.lower()


@dataclass(frozen=True)
class UploadEvent:
    bucket: str
    key: str
    user_id: UUID
    job_id: UUID
    ext: str
    size: int
    event_time: datetime


def parse_s3_event(body: str) -> list[UploadEvent]:
    """
    Parse an SQS message body carrying an S3 notification.

    Returns [] for S3's one-off `s3:TestEvent` and for records that are not upload keys,
    so the caller can simply delete those messages.
    """
    msg = json.loads(body)
    if msg.get("Event") == "s3:TestEvent":
        return []
    events = []
    for rec in msg.get("Records", []):
        if not rec.get("eventName", "").startswith("ObjectCreated"):
            continue
        key = unquote_plus(rec["s3"]["object"]["key"])
        try:
            user_id, job_id, ext = parse_input_key(key)
        except ValueError:
            continue
        events.append(UploadEvent(
            bucket=rec["s3"]["bucket"]["name"],
            key=key,
            user_id=user_id,
            job_id=job_id,
            ext=ext,
            size=int(rec["s3"]["object"].get("size", 0)),
            event_time=datetime.fromisoformat(rec["eventTime"].replace("Z", "+00:00")),
        ))
    return events
