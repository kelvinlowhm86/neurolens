"""S3 helpers shared by the web app and the worker. No torch, no neurolens.inference."""

import json
import uuid
from pathlib import PurePosixPath
from urllib.parse import unquote_plus

# The extension of the stored object comes from the declared content type, never from the
# user's filename.
CONTENT_TYPE_EXTENSIONS = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
}

UPLOAD_PREFIX = "uploads/"


def presign_upload(s3, bucket, content_type, max_bytes, expires_in=300):
    """A presigned POST: the browser uploads straight to S3, bounded to max_bytes.

    Uses a POST policy (not a presigned PUT) because only a POST policy can make S3 itself
    enforce a size cap (`content-length-range`).
    """
    ext = CONTENT_TYPE_EXTENSIONS[content_type]  # KeyError for an unsupported type
    job_id = str(uuid.uuid4())
    # TODO(M3): replace placeholder-user with authenticated user_id
    object_key = f"{UPLOAD_PREFIX}placeholder-user/{job_id}{ext}"
    post = s3.generate_presigned_post(
        Bucket=bucket,
        Key=object_key,
        Conditions=[["content-length-range", 1, max_bytes]],
        ExpiresIn=expires_in,
    )
    return {
        "job_id": job_id,
        "url": post["url"],
        "fields": post["fields"],
        "object_key": object_key,
        "expires_in": expires_in,
    }


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
    return PurePosixPath(key).stem
