"""The SQS worker: picks up "a video was uploaded" notices and analyses the video.

The web app never runs the model; this process does. AWS clients are passed in as parameters
so tests can hand in moto (fake S3/SQS) clients.

From M2b the worker never ends its own machine: AWS alone decides how many workers run
(docs/M2b_spec.md §2). On SIGTERM (scale-in, a lost machine, `systemctl stop`) it hands its job
back to the queue at once and returns.

From M3a the database owns job state and money (docs/M3a_spec.md §5): the worker claims a job
before any work, verifies its price after measuring it, and charges or refunds it through
neurolens.billing. The claim's attempt number never leaves handle_record.
"""

import contextlib
import enum
import json
import logging
import os
import signal
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from botocore.exceptions import ClientError

from neurolens import billing, engagement, inference, settings, storage
from neurolens import db as dbmod

logger = logging.getLogger("neurolens")

# Settings the worker needs, as (path through the dict, name shown in errors). The aws values are
# deployment values from the environment (.env on a laptop, env.conf on AWS), never config.json.
REQUIRED_CONFIG = [
    (("aws", "region"), "aws.region (NEUROLENS_AWS_REGION)"),
    (("aws", "s3_bucket"), "aws.s3_bucket (NEUROLENS_S3_BUCKET)"),
    (("aws", "sqs_queue_url"), "aws.sqs_queue_url (NEUROLENS_SQS_QUEUE_URL)"),
    (("paths", "output"), "paths.output"),
    (("max_upload_bytes",), "max_upload_bytes"),
    (("worker", "heartbeat_seconds"), "worker.heartbeat_seconds"),
    (("worker", "max_job_minutes"), "worker.max_job_minutes"),
]

INTERRUPTED = "The job was interrupted. Please upload it again."
MAX_ERROR_CHARS = 200  # the job's error_message is shown to the user as is
# A redelivery comes no sooner than 120 s after the last beat and a claim is stale after 90 s
# (billing.claim), so each beat must refresh the claim within 50 s.
MAX_HEARTBEAT_SECONDS = 50
BOOT_LOG = Path("/var/log/neurolens-boot.log")  # written by the worker's UserData

# Seconds the latest job spent in `transcribing` (build_events, where WhisperX runs), for the
# first-job boot record. Set by handle_record, read by run().
last_transcribing_seconds = None


class Outcome(enum.Enum):
    """Outcome of one S3 record. Any failure is an exception, not an outcome. Every outcome is
    final (the message may be deleted) except BUSY."""

    DONE = "done"  # this worker published the result and it was charged
    DUPLICATE = "duplicate"  # another worker published first; charged once all the same
    REJECTED = "rejected"  # refunded before inference (oversize, unreadable, too long, ...)
    SKIPPED = "skipped"  # unknown job, result already existed, or the job is already finished
    BUSY = "busy"  # another worker holds a fresh claim: leave the message to reappear
    LOST_CLAIM = "lost_claim"  # this worker lost its claim part-way: whoever holds it finishes


class _ClaimLost(Exception):
    """A billing call reported that this worker no longer holds the job."""


class ShutdownRequested(BaseException):
    """The machine or service is stopping. A BaseException (like KeyboardInterrupt), so a
    library's `except Exception:` cannot swallow it."""


class ShutdownSignal:
    """Turns SIGTERM into ShutdownRequested inside a job, or into a flag between jobs.

    A pipeline stage lasts minutes and systemd waits only TimeoutStopSec before killing the
    process, so waiting for the next stage boundary would almost never hand the job back.
    """

    def __init__(self):
        self._requested = False
        self._in_job = False
        self._raised = False
        self._previous = None
        self._installed = False

    def install(self):
        self._previous = signal.signal(signal.SIGTERM, self.handle)
        self._installed = True

    def uninstall(self):
        if self._installed:
            # None means the previous handler was not set from Python: fall back to the default.
            previous = signal.SIG_DFL if self._previous is None else self._previous
            signal.signal(signal.SIGTERM, previous)
            self._installed = False

    def requested(self):
        return self._requested

    @contextlib.contextmanager
    def job(self):
        """Marks record work in progress: a signal in here raises ShutdownRequested."""
        self._in_job = True
        try:
            yield
        finally:
            self._in_job = False

    def handle(self, signum, frame):
        self._requested = True
        if self._in_job and not self._raised:
            self._raised = True  # at most once, so the clean-up that follows is never cut short
            raise ShutdownRequested()


class Heartbeat:
    """Keeps a message hidden while its job runs, by extending its visibility every interval,
    then calls `on_beat` (the claim's database touch). The first beat comes after one interval.

    A worker that dies silently stops beating, and the message reappears within
    visibility_seconds. After max_seconds the beats stop (logged as an error), so a hung job is
    retried too.

    Exit waits only for a visibility call already in progress, never for a running on_beat: a
    database call can wait up to 60 s for Aurora to wake, and systemd allows 110 s to stop. A late
    on_beat is harmless (a touch after release or settlement changes nothing, billing.touch).
    """

    def __init__(
        self,
        sqs,
        queue_url,
        receipt_handle,
        *,
        interval_seconds,
        visibility_seconds=120,
        max_seconds=None,
        on_beat=None,
    ):
        self._sqs = sqs
        self._queue_url = queue_url
        self._receipt_handle = receipt_handle
        self._interval = interval_seconds
        self._visibility = visibility_seconds
        self._max_seconds = max_seconds
        self._on_beat = on_beat
        # Re-entrant: if a shutdown signal interrupts __exit__ right after it took the lock, the
        # second attempt in __exit__ can take it again instead of deadlocking.
        self._lock = threading.RLock()
        self._stopped = False
        self._wake = threading.Event()
        self._thread = None
        self._entered = None

    def __enter__(self):
        self._entered = time.monotonic()
        self._thread = threading.Thread(target=self._beat, name="heartbeat", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        try:
            self._stop()
        except BaseException:
            # A shutdown signal can interrupt the first try; the handler raises only once, so
            # this one completes and no beat can follow the release that comes next.
            self._stop()
            raise
        return False

    def _stop(self):
        # A visibility call holds the lock while it runs: none can start after this. The thread
        # (a daemon) is not joined, so a slow on_beat never delays the exit.
        with self._lock:
            self._stopped = True
        self._wake.set()

    def _beat(self):
        while not self._wake.wait(self._interval):
            with self._lock:
                if self._stopped:
                    return
                if (
                    self._max_seconds is not None
                    and time.monotonic() - self._entered >= self._max_seconds
                ):
                    logger.error(
                        f"Job still running after {self._max_seconds:.0f}s: no longer extending "
                        "its message, so it becomes visible again and is retried"
                    )
                    return
                try:
                    self._sqs.change_message_visibility(
                        QueueUrl=self._queue_url,
                        ReceiptHandle=self._receipt_handle,
                        VisibilityTimeout=self._visibility,
                    )
                except Exception:
                    logger.warning("Heartbeat failed; trying again next interval", exc_info=True)
            if self._on_beat is None or self._stopped:
                continue
            try:
                self._on_beat()
            except Exception:
                logger.warning("Heartbeat callback failed; trying again next beat", exc_info=True)


def release(sqs, queue_url, receipt_handle):
    """Make a message visible again at once. Never raises: if this fails, the message becomes
    visible on its own once its visibility timeout runs out."""
    try:
        sqs.change_message_visibility(
            QueueUrl=queue_url, ReceiptHandle=receipt_handle, VisibilityTimeout=0
        )
    except Exception:
        logger.warning(
            "Could not release the message; it reappears after its timeout", exc_info=True
        )


class ScaleInProtection:
    """Marks this machine protected from scale-in while it holds a job (M2b §1a).

    Scale-in decides "the queue is empty" from metrics a few minutes old, so without this a worker
    that has just taken a job could be removed. AWS's documented pattern for long-running queue
    workers. It never changes how many machines run. A failed call is logged, never raised: the job
    then runs unprotected, as safe as without this (a scale-in hands it back to the queue).
    """

    def __init__(self, autoscaling, group_name, instance_id):
        self._autoscaling = autoscaling
        self._group_name = group_name
        self._instance_id = instance_id

    @contextlib.contextmanager
    def hold(self):
        self._set(True)
        try:
            yield
        finally:
            self._set(False)

    def _set(self, protected):
        try:
            self._autoscaling.set_instance_protection(
                InstanceIds=[self._instance_id],
                AutoScalingGroupName=self._group_name,
                ProtectedFromScaleIn=protected,
            )
        except Exception:
            state = "protect" if protected else "unprotect"
            logger.warning(f"Could not {state} this machine against scale-in", exc_info=True)


def _reject(s3, db, bucket, key, job_id, attempt, reason, message, *, delete=True):
    """A job that can never succeed: refund it first, then delete the upload (a lost delete is
    harmless: a redelivery fails its claim on the refunded job, and uploads expire)."""
    if not billing.issue_refund(db, job_id, reason, message, attempt=attempt):
        raise _ClaimLost
    logger.warning(f"Rejected and refunded s3://{bucket}/{key}: {reason}")
    if delete:
        s3.delete_object(Bucket=bucket, Key=key)
    return Outcome.REJECTED


def validate_config(cfg):
    """Raise a clear error naming the first missing key the worker needs."""
    for path, name in REQUIRED_CONFIG:
        node = cfg
        for part in path:
            if not isinstance(node, dict) or part not in node:
                raise ValueError(
                    f"Missing required setting: {name}. Deployment values come from .env "
                    "(env.conf on AWS), behaviour settings from config.json."
                )
            node = node[part]
    if cfg["worker"]["heartbeat_seconds"] > MAX_HEARTBEAT_SECONDS:
        raise ValueError(
            f"worker.heartbeat_seconds must be at most {MAX_HEARTBEAT_SECONDS} (a claim goes "
            "stale after 90 s without a beat)."
        )
    # On AWS the worker protects its machine from scale-in while busy, which needs its group's name.
    if os.environ.get("NEUROLENS_DEPLOYED") and not cfg.get("aws", {}).get("worker_group"):
        raise ValueError(
            "Missing required setting: aws.worker_group (NEUROLENS_WORKER_GROUP), written into "
            "env.conf by the worker's UserData."
        )


def handle_record(bucket, key, *, s3, db, cfg, roi_masks, heartbeat):
    """Analyse one uploaded video. Never touches SQS itself: `heartbeat(on_beat)` gives the
    Heartbeat that keeps this message hidden while the work runs.

    If anything raises after the claim (including ShutdownRequested), the job is handed back
    with release_for_retry and the exception re-raised; money stays reserved.
    """
    job_id = storage.job_id_from_key(key)
    if billing.job_state(db, job_id) is None:
        logger.warning(f"Skipped {key}: no job in the database (a manual or pre-M3 upload)")
        return Outcome.SKIPPED
    if storage.result_exists(s3, bucket, job_id):
        # A no-op unless an earlier worker crashed between writing the result and settling.
        billing.settle_success(db, job_id)
        logger.info(f"Skipped {job_id}: a result already exists")
        return Outcome.SKIPPED

    attempt = billing.claim(db, job_id)
    if attempt is None:
        state = billing.job_state(db, job_id)
        if state is None or state["status"] in billing.TERMINAL:
            logger.info(f"Skipped {job_id}: already finished")
            return Outcome.SKIPPED
        # Never SKIPPED for a job in progress: deleting the message could lose the job if the
        # claim holder's release failed. Left alone, the claim goes stale and is re-claimed.
        logger.info(f"Busy {job_id}: another worker holds it; leaving the message")
        return Outcome.BUSY

    try:
        with heartbeat(lambda: billing.touch(db, job_id, attempt)):
            return _analyse(
                bucket, key, job_id, attempt, s3=s3, db=db, cfg=cfg, roi_masks=roi_masks
            )
    except _ClaimLost:
        logger.warning(f"Lost the claim on {job_id} (attempt {attempt}): stopping, money untouched")
        return Outcome.LOST_CLAIM
    except BaseException as err:
        message = INTERRUPTED if isinstance(err, ShutdownRequested) else _error_text(err)
        try:
            billing.release_for_retry(db, job_id, attempt, message)
        except Exception:
            logger.exception(f"Could not hand {job_id} back; its claim goes stale instead")
        raise


def _analyse(bucket, key, job_id, attempt, *, s3, db, cfg, roi_masks):
    global last_transcribing_seconds

    def stage(name):
        if not billing.set_stage(db, job_id, attempt, name):
            raise _ClaimLost

    def reject(reason, message=None, *, delete=True):
        return _reject(s3, db, bucket, key, job_id, attempt, reason, message, delete=delete)

    # Size backstop first: S3's own size cap should already have stopped a big file, but
    # never download, probe or run inference on one that got through. A queued job can outlive
    # its upload (uploads expire after 2 days, queue messages after 4).
    try:
        size = s3.head_object(Bucket=bucket, Key=key)["ContentLength"]
    except ClientError as err:
        if storage.is_not_found(err):
            return reject("upload_missing", "The uploaded file no longer exists.", delete=False)
        raise
    if size > cfg["max_upload_bytes"]:
        return reject("file_too_large", "The file is larger than the upload limit.")

    # Resolve the output folder before any expensive work, so a bad path fails early.
    out_dir = settings.resolve_paths(cfg, settings.get_root())["output"]
    out_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(key).suffix
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / f"{job_id}{suffix}"
        stage("downloading")
        try:
            s3.download_file(bucket, key, str(local))
        except ClientError as err:
            if storage.is_not_found(err):
                return reject("upload_missing", "The uploaded file no longer exists.", delete=False)
            raise

        # The authoritative duration (the browser's estimate can be wrong or spoofed).
        try:
            duration = inference.probe_duration(local)
        except inference.UnreadableVideo:
            # Not a video, corrupt, audio-only or no duration: it can never succeed, so do not
            # retry it.
            return reject("unreadable_video", "The file is not a readable video.")
        verdict = billing.verify(
            db, job_id, attempt, round(duration * 1000), settings.max_duration(cfg)
        )
        if verdict == billing.LOST_CLAIM:
            raise _ClaimLost
        if verdict != billing.OK:  # refunded by verify: too long, or not enough credit
            logger.warning(f"Rejected and refunded s3://{bucket}/{key}: {verdict}")
            s3.delete_object(Bucket=bucket, Key=key)
            return Outcome.REJECTED

        logger.info(f"Analysing {key} ({duration:.1f}s)")
        t0 = time.time()
        inference.reset_gpu_peak()  # so the result's peak VRAM is this job's alone
        stage("transcribing")
        started = time.monotonic()
        events = inference.build_events(local)
        last_transcribing_seconds = round(time.monotonic() - started, 1)
        stage("inference_full")
        preds_full = inference.predict(events, duration)
        stage("inference_noaudio")
        preds_noaudio = inference.predict(inference.without_audio(events), duration)
        stage("extracting_roi")
        result = engagement.extract_engagement(preds_full, preds_noaudio, roi_masks)

    result["job_id"] = job_id
    result["processing_time_seconds"] = round(time.time() - t0, 1)
    if inference.fake_mode():
        result["fake_inference"] = True
    gpu = inference.gpu_info()
    if gpu is not None:
        result["gpu"] = gpu

    # Publish to S3 without ever replacing an earlier result (a redelivered notice must not
    # overwrite the first answer). Either way the finished job is charged, once.
    published = storage.put_result(s3, bucket, job_id, result)
    if not billing.settle_success(db, job_id):
        logger.warning(f"{job_id} was already settled or refunded; the result is not charged")
    if not published:
        logger.warning(f"Duplicate {job_id}: a result already exists, left unchanged")
        return Outcome.DUPLICATE
    logger.info(f"Done {job_id} in {result['processing_time_seconds']}s")
    return Outcome.DONE


def _error_text(err):
    """A short user-facing error: the exception's first line, never a traceback."""
    lines = [line.strip() for line in str(err).splitlines() if line.strip()]
    text = lines[0] if lines else type(err).__name__
    if "Traceback" in text:
        text = type(err).__name__
    if len(text) > MAX_ERROR_CHARS:
        text = text[: MAX_ERROR_CHARS - 1] + "…"
    return text


def process_message(message, *, s3, sqs, db, cfg, roi_masks, shutdown, max_receives):
    """Handle every record in one SQS message.

    The message is deleted only if every record returned a final outcome (BUSY is not final:
    the message is left to reappear after its visibility timeout). A message with no records,
    like S3's one-off test event, is deleted too, as is one whose body can never be parsed.
    If a record raises, the message is released at once for another attempt (after
    max_receives receives SQS moves it to the dead-letter queue, whose Lambda refunds it). On a
    shutdown the message is released and ShutdownRequested is raised; nothing else escapes.
    """
    queue_url = cfg["aws"]["sqs_queue_url"]
    receipt = message["ReceiptHandle"]
    if shutdown.requested():
        logger.info("Shutdown requested: handing back a message received meanwhile")
        release(sqs, queue_url, receipt)
        raise ShutdownRequested()

    receive_count = int(message.get("Attributes", {}).get("ApproximateReceiveCount", "1"))

    try:
        records = storage.parse_s3_event(message["Body"])
    except Exception:
        logger.exception("Unparseable message body; deleting it (it can never succeed)")
        records = []

    def heartbeat(on_beat):
        return Heartbeat(
            sqs,
            queue_url,
            receipt,
            interval_seconds=cfg["worker"]["heartbeat_seconds"],
            max_seconds=60 * cfg["worker"]["max_job_minutes"],
            on_beat=on_beat,
        )

    job_id = None
    busy = False
    try:
        for bucket, key in records:
            job_id = storage.job_id_from_key(key)
            # Only the record work is inside job(): the release and delete below cannot be
            # interrupted by the signal.
            with shutdown.job():
                outcome = handle_record(
                    bucket, key, s3=s3, db=db, cfg=cfg, roi_masks=roi_masks, heartbeat=heartbeat
                )
            busy = busy or outcome is Outcome.BUSY
    except ShutdownRequested:
        logger.warning(f"Shutdown requested during job {job_id}: releasing its message")
        release(sqs, queue_url, receipt)
        raise
    except Exception as err:
        release(sqs, queue_url, receipt)
        if shutdown.requested():
            # systemd stops the whole service, so a subprocess (WhisperX, ffmpeg) may fail
            # before the signal reaches us: a shutdown, not a failure of the job.
            logger.warning(f"Shutdown requested during job {job_id}: released its message")
            raise ShutdownRequested() from err
        attempt = f"attempt {receive_count} of {max_receives}"
        logger.exception(f"Job {job_id} failed ({attempt}); released it")
        return

    if busy:
        return
    try:
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
    except Exception:
        logger.exception("Could not delete a finished message; it will be redelivered")


def _poll(*, s3, sqs, db, cfg, roi_masks, shutdown, max_receives, protection=None):
    """Long-poll for at most one message and handle it.

    The number of messages received (0 for an empty queue), or None if the receive call failed.
    The error is logged; only ShutdownRequested escapes.
    """
    try:
        resp = sqs.receive_message(
            QueueUrl=cfg["aws"]["sqs_queue_url"],
            WaitTimeSeconds=20,
            MaxNumberOfMessages=1,
            MessageSystemAttributeNames=["ApproximateReceiveCount"],
        )
    except Exception:
        logger.exception("Could not receive from the queue")
        return None
    messages = resp.get("Messages", [])
    for message in messages:
        with protection.hold() if protection else contextlib.nullcontext():
            process_message(
                message,
                s3=s3,
                sqs=sqs,
                db=db,
                cfg=cfg,
                roi_masks=roi_masks,
                shutdown=shutdown,
                max_receives=max_receives,
            )
    return len(messages)


def poll_once(*, s3, sqs, db, cfg, roi_masks, shutdown, max_receives, protection=None):
    """True if the receive call worked (even with no message); False if it failed. With a
    ScaleInProtection, each message is handled while this machine is protected from scale-in."""
    received = _poll(
        s3=s3,
        sqs=sqs,
        db=db,
        cfg=cfg,
        roi_masks=roi_masks,
        shutdown=shutdown,
        max_receives=max_receives,
        protection=protection,
    )
    return received is not None


def read_max_receives(sqs, queue_url):
    """The job queue's maxReceiveCount, from its RedrivePolicy (one source of truth: Terraform)."""
    attrs = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["RedrivePolicy"])
    policy = attrs.get("Attributes", {}).get("RedrivePolicy")
    if not policy:
        raise ValueError(
            f"The job queue {queue_url} has no RedrivePolicy (maxReceiveCount): the worker "
            "cannot tell a final attempt. Apply the Terraform queue settings first."
        )
    return int(json.loads(policy)["maxReceiveCount"])


def boot_record(boot_log_text, ready_utc, instance_id, instance_type):
    """What this machine saw of its own boot, as UTC timestamps (None for a missing step).

    Pure. Reads the UserData log (`<UTC time> <step>` lines). The launch time is not known here:
    experiments/cold_start.py takes it from AWS's scaling history.
    """
    steps = []
    for line in boot_log_text.splitlines():
        stamp, _, text = line.strip().partition(" ")
        if stamp:
            steps.append((stamp, text))

    def first(match):
        return next((stamp for stamp, text in steps if match(text)), None)

    return {
        "instance_id": instance_id,
        "instance_type": instance_type,
        "userdata_start_utc": steps[0][0] if steps else None,
        "weight_sync_start_utc": first(lambda t: t == "weight sync start"),
        "weight_sync_end_utc": first(lambda t: t.startswith("weight sync done")),
        "worker_start_utc": first(lambda t: t == "worker started"),
        "ready_utc": storage.utc_text(ready_utc),
    }


def _instance_identity():
    """(instance id, instance type) from the EC2 instance metadata service (IMDSv2)."""
    from urllib.request import Request, urlopen

    base = "http://169.254.169.254/latest"
    token_request = Request(
        f"{base}/api/token", method="PUT", headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"}
    )
    token = urlopen(token_request, timeout=2).read().decode()

    def get(path):
        request = Request(f"{base}/meta-data/{path}", headers={"X-aws-ec2-metadata-token": token})
        return urlopen(request, timeout=2).read().decode()

    return get("instance-id"), get("instance-type")


def _put_boot_json(s3, bucket, name, obj):
    """experiments/boots/<name>.json. Logs and continues on error: never stops the worker."""
    try:
        s3.put_object(
            Bucket=bucket,
            Key=f"experiments/boots/{name}.json",
            Body=json.dumps(obj).encode(),
            ContentType="application/json",
        )
        logger.info(f"Boot record experiments/boots/{name}.json written")
    except Exception:
        logger.exception(f"Could not write boot record {name}")


def _record_boot(s3, bucket, identity):
    """Write this GPU boot's record; return the instance id (None when not recorded)."""
    if identity is None or inference.fake_mode():
        return None
    instance_id, instance_type = identity
    try:
        record = boot_record(BOOT_LOG.read_text(), datetime.now(UTC), instance_id, instance_type)
    except Exception:
        logger.exception("Could not build the boot record")
        return None
    _put_boot_json(s3, bucket, instance_id, record)
    return instance_id


def run():
    """Validate config, load the model once, then poll the queue until a shutdown request."""
    global last_transcribing_seconds
    import boto3

    shutdown = ShutdownSignal()
    shutdown.install()  # first: a SIGTERM during the model load must not kill the process
    try:
        cfg = settings.load_settings()
        validate_config(cfg)  # before anything slow: a bad config fails fast
        aws = cfg["aws"]
        s3 = boto3.client("s3", region_name=aws["region"])
        sqs = boto3.client("sqs", region_name=aws["region"])
        max_receives = read_max_receives(sqs, aws["sqs_queue_url"])
        db = dbmod.from_config(cfg)  # before the model load: a bad db setting fails fast
        inference.load_model(cfg)  # in real mode this takes minutes on first run
        roi_masks = inference.roi_masks()

        identity = protection = None
        if os.environ.get("NEUROLENS_DEPLOYED"):
            try:
                identity = _instance_identity()
            except Exception:
                logger.exception(
                    "Could not read instance metadata: running without scale-in protection"
                )
        if identity is not None:
            autoscaling = boto3.client("autoscaling", region_name=aws["region"])
            protection = ScaleInProtection(autoscaling, aws["worker_group"], identity[0])
        instance_id = _record_boot(s3, aws["s3_bucket"], identity)
        last_transcribing_seconds = None

        logger.info(f"Worker ready. Polling {aws['sqs_queue_url']} (max receives {max_receives})")
        delay = 0
        while not shutdown.requested():
            try:
                received = _poll(
                    s3=s3,
                    sqs=sqs,
                    db=db,
                    cfg=cfg,
                    roi_masks=roi_masks,
                    shutdown=shutdown,
                    max_receives=max_receives,
                    protection=protection,
                )
            except ShutdownRequested:
                break
            if instance_id and last_transcribing_seconds is not None:
                _put_boot_json(
                    s3,
                    aws["s3_bucket"],
                    f"{instance_id}-first-job",
                    {
                        "instance_id": instance_id,
                        "first_job_transcribing_s": last_transcribing_seconds,
                    },
                )
                instance_id = None  # the first job only
            if received is None:
                delay = min(max(delay * 2, 2), 30)  # back off: an outage neither kills nor spins
                logger.warning(f"Retrying the queue in {delay}s")
                time.sleep(delay)
            else:
                delay = 0
        logger.info("Shutdown requested: stopped polling")
    finally:
        shutdown.uninstall()
