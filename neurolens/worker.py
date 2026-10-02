"""The SQS worker: picks up "a video was uploaded" notices and analyses the video.

The web app never runs the model; this process does. AWS clients are passed in as parameters
so tests can hand in moto (fake S3/SQS) clients.
"""

import enum
import logging
import tempfile
import time
from pathlib import Path

from botocore.exceptions import ClientError

from neurolens import engagement, inference, settings, storage

logger = logging.getLogger("neurolens")

NOT_FOUND_CODES = ("404", "NoSuchKey", "NotFound")

# Keys the worker needs in config.json, as (path through the dict, name shown in errors).
REQUIRED_CONFIG = [
    (("aws", "region"), "aws.region"),
    (("aws", "s3_bucket"), "aws.s3_bucket"),
    (("aws", "sqs_queue_url"), "aws.sqs_queue_url"),
    (("paths", "output"), "paths.output"),
    (("max_upload_bytes",), "max_upload_bytes"),
]


class Outcome(enum.Enum):
    """Final outcome of one S3 record. Any failure is an exception, not an outcome."""

    DONE = "done"
    REJECTED = "rejected"  # oversize, too long or unreadable; the object is deleted
    GONE = "gone"  # the object no longer exists (duplicate notice or expired): nothing to do
    DUPLICATE = "duplicate"  # a result for this job already exists; it is left unchanged


def _reject(s3, bucket, key, reason):
    logger.warning(f"Rejected s3://{bucket}/{key}: {reason}")
    s3.delete_object(Bucket=bucket, Key=key)
    return Outcome.REJECTED


def _is_not_found(err):
    return isinstance(err, ClientError) and err.response.get("Error", {}).get("Code") in (
        NOT_FOUND_CODES
    )


def validate_config(cfg):
    """Raise a clear error naming the first missing key the worker needs."""
    for path, name in REQUIRED_CONFIG:
        node = cfg
        for part in path:
            if not isinstance(node, dict) or part not in node:
                raise ValueError(f"config.json is missing required setting: {name}")
            node = node[part]


def handle_record(bucket, key, *, s3, cfg, roi_masks):
    """Analyse one uploaded video. Never touches SQS."""
    job_id = storage.job_id_from_key(key)

    # Size backstop first: S3's own size cap should already have stopped a big file, but
    # never download, probe or run inference on one that got through.
    try:
        size = s3.head_object(Bucket=bucket, Key=key)["ContentLength"]
    except ClientError as err:
        if _is_not_found(err):
            logger.warning(f"Gone s3://{bucket}/{key}: object does not exist")
            return Outcome.GONE
        raise
    if size > cfg["max_upload_bytes"]:
        return _reject(s3, bucket, key, f"{size} bytes exceeds max_upload_bytes")

    # Resolve the output folder before any expensive work, so a bad path fails early.
    out_dir = settings.resolve_paths(cfg, settings.get_root())["output"]
    out_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(key).suffix
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / f"{job_id}{suffix}"
        try:
            s3.download_file(bucket, key, str(local))
        except ClientError as err:
            if _is_not_found(err):
                logger.warning(f"Gone s3://{bucket}/{key}: object vanished before download")
                return Outcome.GONE
            raise

        # The authoritative duration check (the browser's estimate can be wrong or spoofed).
        try:
            duration = inference.probe_duration(local)
        except inference.UnreadableVideo:
            # Not a video, corrupt, audio-only or no duration: it can never succeed, so do not
            # retry it.
            return _reject(s3, bucket, key, "not a readable video")
        if duration > settings.max_duration(cfg):
            return _reject(s3, bucket, key, f"{duration:.1f}s exceeds the maximum duration")

        logger.info(f"Analysing {key} ({duration:.1f}s)")
        t0 = time.time()
        preds_full = inference.run_inference(local)
        noaudio = Path(tmp) / f"{job_id}.noaudio{suffix}"
        inference.strip_audio(local, noaudio)
        preds_noaudio = inference.run_inference(noaudio)
        result = engagement.extract_engagement(preds_full, preds_noaudio, roi_masks)

    result["job_id"] = job_id
    result["processing_time_seconds"] = round(time.time() - t0, 1)
    if inference.fake_mode():
        result["fake_inference"] = True
    gpu = inference.gpu_info()
    if gpu is not None:
        result["gpu"] = gpu

    # Publish to S3 without ever replacing an earlier result (a redelivered notice must not
    # overwrite the first answer).
    if not storage.put_result(s3, bucket, job_id, result):
        logger.warning(f"Duplicate {job_id}: a result already exists, left unchanged")
        return Outcome.DUPLICATE
    logger.info(f"Done {job_id} in {result['processing_time_seconds']}s")
    return Outcome.DONE


def process_message(message, *, s3, sqs, cfg, roi_masks):
    """Handle every record in one SQS message.

    The message is deleted only if every record returned an Outcome (a message with no
    records, like S3's one-off test event, is deleted too), or if its body can never be parsed.
    If a record raises, the traceback is logged and the message is left so SQS redelivers it
    after the visibility timeout. No exception escapes.
    """
    try:
        records = storage.parse_s3_event(message["Body"])
    except Exception:
        logger.exception("Unparseable message body; deleting it (it can never succeed)")
        records = []

    try:
        for bucket, key in records:
            handle_record(bucket, key, s3=s3, cfg=cfg, roi_masks=roi_masks)
    except Exception:
        logger.exception("Failed to process message; leaving it on the queue")
        return

    try:
        sqs.delete_message(
            QueueUrl=cfg["aws"]["sqs_queue_url"], ReceiptHandle=message["ReceiptHandle"]
        )
    except Exception:
        logger.exception("Could not delete a finished message; it will be redelivered")


def _poll(*, s3, sqs, cfg, roi_masks):
    """Long-poll for at most one message and handle it.

    The number of messages received (0 for an empty queue), or None if the receive call failed.
    The error is logged and never escapes.
    """
    try:
        resp = sqs.receive_message(
            QueueUrl=cfg["aws"]["sqs_queue_url"], WaitTimeSeconds=20, MaxNumberOfMessages=1
        )
    except Exception:
        logger.exception("Could not receive from the queue")
        return None
    messages = resp.get("Messages", [])
    for message in messages:
        process_message(message, s3=s3, sqs=sqs, cfg=cfg, roi_masks=roi_masks)
    return len(messages)


def poll_once(*, s3, sqs, cfg, roi_masks):
    """True if the receive call worked (even with no message); False if it failed."""
    return _poll(s3=s3, sqs=sqs, cfg=cfg, roi_masks=roi_masks) is not None


def run():
    """Validate config, load the model once, then poll the queue forever."""
    import boto3

    cfg = settings.load_settings()
    validate_config(cfg)  # before anything slow: a bad config fails fast, not after a model load
    inference.load_model(cfg)  # in real mode this takes minutes on first run
    roi_masks = inference.roi_masks()
    aws = cfg["aws"]
    s3 = boto3.client("s3", region_name=aws["region"])
    sqs = boto3.client("sqs", region_name=aws["region"])

    # Absent means never exit; on AWS the machine shuts itself down once the worker returns.
    idle_minutes = cfg.get("worker", {}).get("idle_exit_minutes")

    logger.info(f"Worker ready. Polling {aws['sqs_queue_url']}")
    delay = 0
    last_activity = time.monotonic()
    while True:
        received = _poll(s3=s3, sqs=sqs, cfg=cfg, roi_masks=roi_masks)
        if received is None:
            delay = min(max(delay * 2, 2), 30)  # back off: an outage neither kills nor spins
            logger.warning(f"Retrying the queue in {delay}s")
            time.sleep(delay)
        else:
            delay = 0
            if received:
                last_activity = time.monotonic()  # after the job, so its run time is not idle
        if idle_minutes is not None and time.monotonic() - last_activity >= idle_minutes * 60:
            logger.info(f"No messages for {idle_minutes} minutes; exiting")
            return
