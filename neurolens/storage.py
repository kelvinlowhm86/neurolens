"""S3 helpers shared by the web app and the worker. No torch, no neurolens.inference."""

import json
import uuid
from datetime import UTC, datetime
from pathlib import PurePosixPath
from urllib.parse import unquote_plus

from botocore.exceptions import ClientError

# The extension of the stored object comes from the declared content type, never from the
# user's filename.
CONTENT_TYPE_EXTENSIONS = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
}
EXTENSION_CONTENT_TYPES = {ext: ct for ct, ext in CONTENT_TYPE_EXTENSIONS.items()}

UPLOAD_PREFIX = "uploads/"
NOT_FOUND_CODES = ("404", "NoSuchKey", "NotFound")
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"  # every timestamp in job stages and boot records


def object_key(user_id, job_id, content_type):
    """uploads/{user_id}/{job_id}{ext}, the extension from the content type (KeyError for an
    unsupported type)."""
    return f"{UPLOAD_PREFIX}{user_id}/{job_id}{CONTENT_TYPE_EXTENSIONS[content_type]}"


def presign_upload(s3, bucket, object_key, max_bytes, expires_in=300):
    """A presigned POST: the browser uploads straight to S3, bounded to max_bytes.

    Uses a POST policy (not a presigned PUT) because only a POST policy can make S3 itself
    enforce a size cap (`content-length-range`). The form pins the Content-Type that the key's
    extension stands for.
    """
    content_type = EXTENSION_CONTENT_TYPES[PurePosixPath(object_key).suffix]
    post = s3.generate_presigned_post(
        Bucket=bucket,
        Key=object_key,
        Fields={"Content-Type": content_type},
        Conditions=[
            ["content-length-range", 1, max_bytes],
            {"Content-Type": content_type},
        ],
        ExpiresIn=expires_in,
    )
    return {"url": post["url"], "fields": post["fields"], "expires_in": expires_in}


def parse_s3_event(body):
    """(bucket, key) for every record under uploads/. Anything else gives no records.

    The SQS message body is the raw S3 event JSON. S3 also sends one `s3:TestEvent` (no
    `Records`) when a notification is first created: that returns [].
    """
    event = json.loads(body)
    records = []
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])  # S3 URL-encodes keys in events
        if key.startswith(UPLOAD_PREFIX):
            records.append((bucket, key))
    return records


def job_id_from_key(key):
    """The job id in an upload key, or None when the file name is not one (a manual upload such
    as uploads/ad.mp4: no job can have that id)."""
    stem = PurePosixPath(key).stem
    try:
        return stem if str(uuid.UUID(stem)) == stem else None
    except ValueError:
        return None


def put_result(s3, bucket, job_id, result):
    """Publish results/<job_id>.json, never replacing an existing result.

    True if written; False if a result already exists (S3 answers 412 to a conditional write
    with IfNoneMatch="*"). Any other error is raised so the caller retries the job. NaN or
    infinity raises ValueError before anything is written: JSON cannot carry them, so the job
    fails (and is refunded) instead of publishing a result the browser cannot parse.
    """
    try:
        s3.put_object(
            Bucket=bucket,
            Key=f"results/{job_id}.json",
            Body=json.dumps(result, allow_nan=False).encode(),
            ContentType="application/json",
            IfNoneMatch="*",
        )
    except ClientError as err:
        if err.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 412 or (
            err.response.get("Error", {}).get("Code") == "PreconditionFailed"
        ):
            return False
        raise
    return True


def brain_bytes(preds):
    """The raw with-audio predictions as float16 little-endian, row-major, no header (M3d §5).

    Raises ValueError if any value is not finite after the cast: float16 overflows above 65,504,
    and a NaN or infinity must never reach a stored brain file.
    """
    import numpy as np

    with np.errstate(over="ignore"):
        data = np.asarray(preds, dtype="<f2")
    if not np.isfinite(data).all():
        raise ValueError("Brain predictions contain a value that float16 cannot represent")
    return data.tobytes(order="C")


def put_brain(s3, bucket, job_id, preds):
    """Write results/<job_id>.brain, replacing an earlier copy (M3d §5)."""
    s3.put_object(
        Bucket=bucket,
        Key=f"results/{job_id}.brain",
        Body=brain_bytes(preds),
        ContentType="application/octet-stream",
    )


def put_dataset(s3, bucket, job_id, upload_key, result, brain):
    """Keep a consented job's video, result and brain under dataset/<job_id>/ (M3c §3b, M3d §5).

    The video is copied from the upload key, keeping its extension. The result is serialised first,
    so a NaN or infinity raises ValueError before either write. All writes replace earlier copies:
    a redelivered job writes the same content. Any S3 error is raised to the worker's failure path.
    """
    body = json.dumps(result, allow_nan=False).encode()
    ext = PurePosixPath(upload_key).suffix
    s3.copy_object(
        Bucket=bucket,
        CopySource={"Bucket": bucket, "Key": upload_key},
        Key=f"dataset/{job_id}/video{ext}",
    )
    s3.put_object(
        Bucket=bucket,
        Key=f"dataset/{job_id}/result.json",
        Body=body,
        ContentType="application/json",
    )
    s3.put_object(
        Bucket=bucket,
        Key=f"dataset/{job_id}/brain",
        Body=brain,
        ContentType="application/octet-stream",
    )


def is_not_found(err):
    """True for S3's "no such object" error (head_object says 404, get_object NoSuchKey)."""
    return isinstance(err, ClientError) and err.response.get("Error", {}).get("Code") in (
        NOT_FOUND_CODES
    )


def utc_text(moment):
    """A timezone-aware datetime as UTC text, e.g. 2026-10-14T03:22:10Z."""
    return moment.astimezone(UTC).strftime(TIME_FORMAT)


def utc_parse(text):
    """The inverse of utc_text."""
    return datetime.strptime(text, TIME_FORMAT).replace(tzinfo=UTC)


def _get_json(s3, bucket, key):
    """The JSON object stored at key, or None if there is none. Other errors are raised."""
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"]
    except ClientError as err:
        if is_not_found(err):
            return None
        raise
    return json.loads(body.read())


def object_exists(s3, bucket, key):
    """True if the object exists; False for S3's "no such object". Other errors are raised."""
    try:
        s3.head_object(Bucket=bucket, Key=key)
    except ClientError as err:
        if is_not_found(err):
            return False
        raise
    return True


def delete_object(s3, bucket, key):
    """Delete one object. S3 treats a key that is already gone as success."""
    s3.delete_object(Bucket=bucket, Key=key)


def result_exists(s3, bucket, job_id):
    return object_exists(s3, bucket, f"results/{job_id}.json")


def get_result(s3, bucket, job_id):
    return _get_json(s3, bucket, f"results/{job_id}.json")
