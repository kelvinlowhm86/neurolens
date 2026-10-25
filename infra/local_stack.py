"""Run the whole product on a laptop against local fakes: no real AWS, no real credentials.

    python infra/local_stack.py
    python infra/local_stack.py --grant you@example.com 5

It needs a local PostgreSQL (default postgresql://postgres@localhost:55432/postgres), starts
moto's S3/SQS server on port 5005, applies the schema, then serves the web app on 5003 and passes
each queued upload through the real dispatcher into fake-mode analysis.
"""

import argparse
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DEFAULT_DSN = "postgresql://postgres@localhost:55432/postgres"
LOCAL_BUCKET = "neurolens-local"
LOCAL_QUEUE = "neurolens-local-jobs"
MOTO_PORT = 5005
WEB_PORT = 5003


def _local_dsn(dsn):
    from urllib.parse import urlparse

    host = urlparse(dsn).hostname
    if host not in ("localhost", "127.0.0.1"):
        raise SystemExit("The local stack needs a local PostgreSQL DSN (localhost or 127.0.0.1).")
    return dsn


def _own_aws_environment(scratch):
    """Throwaway credentials and a scratch AWS config, so no real profile is ever read."""
    scratch = Path(scratch)
    (scratch / "credentials").write_text(
        "[default]\naws_access_key_id = testing\naws_secret_access_key = testing\n"
    )
    (scratch / "config").write_text("[default]\nregion = us-east-1\n")
    env = {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_REGION": "us-east-1",
        "AWS_ENDPOINT_URL": f"http://127.0.0.1:{MOTO_PORT}",
        "AWS_SHARED_CREDENTIALS_FILE": str(scratch / "credentials"),
        "AWS_CONFIG_FILE": str(scratch / "config"),
    }
    os.environ.pop("AWS_PROFILE", None)
    os.environ.update(env)


def _config(dsn):
    from neurolens import settings

    cfg = settings.load_config(ROOT)
    cfg["paths"] = {"models": "./models", "data": "./data"}
    cfg["aws"] = {"region": "us-east-1", "s3_bucket": LOCAL_BUCKET}
    cfg["db"] = {"backend": "postgres", "dsn": dsn}
    cfg["server"] = {"host": "127.0.0.1", "port": WEB_PORT}
    cfg["auth"] = {"mode": "dev", "dev_user_id": "dev-user", "dev_email": "dev@localhost"}
    return cfg


def _make_s3(s3):
    s3.create_bucket(Bucket=LOCAL_BUCKET)
    s3.put_bucket_cors(
        Bucket=LOCAL_BUCKET,
        CORSConfiguration={
            "CORSRules": [
                {
                    "AllowedMethods": ["POST", "GET"],
                    "AllowedOrigins": [
                        f"http://127.0.0.1:{WEB_PORT}",
                        f"http://localhost:{WEB_PORT}",
                    ],
                    "AllowedHeaders": ["*"],
                    "MaxAgeSeconds": 3000,
                }
            ]
        },
    )


def _make_queue(sqs, s3):
    queue_url = sqs.create_queue(QueueName=LOCAL_QUEUE, Attributes={"DelaySeconds": "15"})[
        "QueueUrl"
    ]
    queue_arn = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])[
        "Attributes"
    ]["QueueArn"]
    s3.put_bucket_notification_configuration(
        Bucket=LOCAL_BUCKET,
        NotificationConfiguration={
            "QueueConfigurations": [
                {
                    "QueueArn": queue_arn,
                    "Events": ["s3:ObjectCreated:*"],
                    "Filter": {"Key": {"FilterRules": [{"Name": "prefix", "Value": "uploads/"}]}},
                }
            ]
        },
    )
    return queue_url


class LocalSageMaker:
    """Hands an async invocation straight to the fake-mode worker, as SageMaker would."""

    def __init__(self, *, s3, db, cfg, roi_masks):
        self.s3 = s3
        self.db = db
        self.cfg = cfg
        self.roi_masks = roi_masks

    def invoke_endpoint_async(self, *, InputLocation, InferenceId, **kwargs):
        from urllib.parse import urlparse

        from neurolens import worker

        parsed = urlparse(InputLocation)
        bucket, key = parsed.netloc, parsed.path.lstrip("/")
        suffix = Path(key).suffix or ".mp4"
        with tempfile.TemporaryDirectory(prefix="neurolens-local-") as folder:
            video = Path(folder) / f"video{suffix}"
            self.s3.download_file(bucket, key, str(video))
            outcome = worker.handle_job(
                InferenceId,
                str(video),
                s3=self.s3,
                db=self.db,
                cfg=self.cfg,
                roi_masks=self.roi_masks,
            )
        print(f"Job {InferenceId}: {outcome.value}")
        return {"ResponseMetadata": {"HTTPStatusCode": 202}}


def _grant(db, cfg, email, dollars):
    from neurolens import billing

    cents = round(float(dollars) * 100)
    if cents <= 0:
        raise SystemExit("--grant needs an amount greater than 0")
    existing = billing.user_by_email(db, email)
    if existing is None:
        dev_email = cfg["auth"]["dev_email"]
        if email.lower() == dev_email.lower():
            user_id = cfg["auth"]["dev_user_id"]
        else:
            user_id = str(uuid.uuid4())
        billing.ensure_user(db, user_id, email, 0)
    billing.grant_credit(db, email, cents)
    print(f"Added ${cents / 100:.2f} to {email}.")


def _serve_web(app):
    from werkzeug.serving import make_server

    server = make_server("127.0.0.1", WEB_PORT, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _dispatch_loop(sqs, queue_url, s3, sagemaker, db, cfg):
    from neurolens import storage
    from neurolens.lambdas import dispatcher

    while True:
        response = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=1,
            VisibilityTimeout=1080,
        )
        for message in response.get("Messages", []):
            for bucket, key in storage.parse_s3_event(message["Body"]):
                try:
                    result = dispatcher.dispatch(
                        bucket,
                        key,
                        s3=s3,
                        sagemaker=sagemaker,
                        db=db,
                        endpoint="local",
                        max_upload_bytes=cfg["max_upload_bytes"],
                        max_duration_s=cfg.get("max_video_duration_seconds", 120),
                    )
                    print(f"{key}: {result}")
                except Exception as err:  # leave the message for a retry
                    print(f"{key}: dispatch failed: {type(err).__name__}: {err}", file=sys.stderr)
                    continue
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])
        time.sleep(0.2)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dsn", default=DEFAULT_DSN, help="local PostgreSQL DSN")
    parser.add_argument(
        "--grant",
        nargs=2,
        metavar=("EMAIL", "DOLLARS"),
        help="give a local account credit, then continue",
    )
    args = parser.parse_args(argv)

    dsn = _local_dsn(args.dsn)
    scratch = Path(tempfile.mkdtemp(prefix="neurolens-local-aws-"))
    _own_aws_environment(scratch)
    os.environ["FAKE_INFERENCE"] = "1"

    import boto3
    from moto.server import ThreadedMotoServer

    from infra.apply_schema import apply_migrations
    from neurolens import db as dbmod
    from neurolens import inference, settings
    from neurolens.web.app import create_app

    cfg = _config(dsn)
    db = dbmod.from_config(cfg)
    try:
        apply_migrations(db)
    except Exception as err:
        raise SystemExit(
            f"Could not reach or build the local database at {dsn}: {type(err).__name__}: {err}"
        ) from err
    if args.grant:
        _grant(db, cfg, *args.grant)

    settings.ensure_dirs(settings.resolve_paths(cfg, ROOT))
    inference.load_model(cfg)

    moto = ThreadedMotoServer(ip_address="127.0.0.1", port=MOTO_PORT, verbose=False)
    moto.start()
    web_server = None
    try:
        s3 = boto3.client("s3")
        sqs = boto3.client("sqs")
        _make_s3(s3)
        queue_url = _make_queue(sqs, s3)
        app = create_app(cfg=cfg, db=db, s3_client=s3)
        web_server, web_thread = _serve_web(app)
        print(
            f"NeuroLens is at http://127.0.0.1:{WEB_PORT} "
            f"(fake S3/SQS on {MOTO_PORT}); Ctrl-C stops it"
        )
        _dispatch_loop(
            sqs,
            queue_url,
            s3,
            LocalSageMaker(s3=s3, db=db, cfg=cfg, roi_masks=inference.roi_masks()),
            db,
            cfg,
        )
    except KeyboardInterrupt:
        print("\nStopping the local stack.")
    finally:
        if web_server is not None:
            web_server.shutdown()
            web_thread.join(timeout=5)
        moto.stop()
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
