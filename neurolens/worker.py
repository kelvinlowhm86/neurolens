"""The SQS worker: picks up "a video was uploaded" notices and analyses the video.

The web app never runs the model; this process does. AWS clients are passed in as parameters
so tests can hand in moto (fake S3/SQS) clients.
"""

import enum
import json
import logging
import tempfile
import time
from pathlib import Path

from neurolens import engagement, inference, settings, storage

logger = logging.getLogger("neurolens")


class Outcome(enum.Enum):
    """Final outcome of one S3 record. Any failure is an exception, not an outcome."""

    DONE = "done"
    REJECTED = "rejected"  # oversize or too long; the object is deleted


def _reject(s3, bucket, key, reason):
    logger.warning(f"Rejected s3://{bucket}/{key}: {reason}")
    s3.delete_object(Bucket=bucket, Key=key)
    return Outcome.REJECTED


def handle_record(bucket, key, *, s3, cfg, roi_masks):
    """Analyse one uploaded video. Never touches SQS."""
    job_id = storage.job_id_from_key(key)

    # Size backstop first: S3's own size cap should already have stopped a big file, but
    # never download, probe or run inference on one that got through.
    size = s3.head_object(Bucket=bucket, Key=key)["ContentLength"]
    if size > cfg["max_upload_bytes"]:
        return _reject(s3, bucket, key, f"{size} bytes exceeds max_upload_bytes")

    suffix = Path(key).suffix
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / f"{job_id}{suffix}"
        s3.download_file(bucket, key, str(local))

        # The authoritative duration check (the browser's estimate can be wrong or spoofed).
        try:
            duration = inference.probe_duration(local)
        except inference.UnreadableVideo:
            # Not a video, corrupt, or no duration: it can never succeed, so do not retry it.
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

    # A local folder is fine for M1; M2a moves results to S3.
    out_dir = settings.resolve_paths(cfg, settings.get_root())["output"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{job_id}.json").write_text(json.dumps(result))
    logger.info(f"Done {job_id} in {result['processing_time_seconds']}s")
    return Outcome.DONE


def process_message(message, *, s3, sqs, cfg, roi_masks):
    """Handle every record in one SQS message.

    The message is deleted only if every record returned an Outcome (a message with no
    records, like S3's one-off test event, is deleted too). If a record raises, the traceback
    is logged and the message is left so SQS redelivers it after the visibility timeout.
    """
    try:
        for bucket, key in storage.parse_s3_event(message["Body"]):
            handle_record(bucket, key, s3=s3, cfg=cfg, roi_masks=roi_masks)
    except Exception:
        logger.exception("Failed to process message; leaving it on the queue")
        return
    sqs.delete_message(QueueUrl=cfg["aws"]["sqs_queue_url"], ReceiptHandle=message["ReceiptHandle"])


def run():
    """Load config and model once, then poll the queue forever."""
    import boto3

    cfg = settings.load_config()
    inference.load_model(cfg)  # in real mode this takes minutes on first run
    roi_masks = inference.roi_masks()
    aws = cfg["aws"]
    s3 = boto3.client("s3", region_name=aws["region"])
    sqs = boto3.client("sqs", region_name=aws["region"])

    logger.info(f"Worker ready. Polling {aws['sqs_queue_url']}")
    while True:
        resp = sqs.receive_message(
            QueueUrl=aws["sqs_queue_url"], WaitTimeSeconds=20, MaxNumberOfMessages=1
        )
        for message in resp.get("Messages", []):
            process_message(message, s3=s3, sqs=sqs, cfg=cfg, roi_masks=roi_masks)
