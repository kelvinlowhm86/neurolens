"""Shared fixtures for the M1 tests (docs/M1_spec.md section 6a).

Everything AWS-related runs against moto, an in-memory fake of S3 and SQS. Fake credentials
are set for every test so that an accidental real AWS call fails instead of costing money.
"""

import importlib
import subprocess
import uuid
from types import SimpleNamespace

import numpy as np
import pytest

BUCKET = "neurolens-test-bucket"
ROI_NAMES = ["ffa_faces", "eba_bodies", "ppa_scenes", "sts_social", "auditory"]


@pytest.fixture(autouse=True)
def aws_env(monkeypatch, tmp_path_factory):
    """Fake credentials and no access to the developer's real AWS files or profile."""
    empty = tmp_path_factory.mktemp("aws_empty")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(empty / "credentials"))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(empty / "config"))
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture
def aws():
    """A moto S3 bucket and SQS queue, plus boto3 clients for them."""
    import boto3
    from moto import mock_aws

    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        sqs = boto3.client("sqs", region_name="us-east-1")
        queue_url = sqs.create_queue(QueueName="neurolens-test-queue")["QueueUrl"]
        yield SimpleNamespace(s3=s3, sqs=sqs, bucket=BUCKET, queue_url=queue_url)


@pytest.fixture
def make_cfg(aws, tmp_path):
    """Build a config dict like config.sample.json, pointing at the moto resources."""
    (tmp_path / "output").mkdir(exist_ok=True)

    def make(with_aws=True, **overrides):
        cfg = {
            "hf_token": "hf_not_a_real_token",
            "paths": {
                "models": str(tmp_path / "models"),
                "data": str(tmp_path / "data"),
                "output": str(tmp_path / "output"),
            },
            "model": {
                "repo_id": "facebook/tribev2",
                "llama_repo_id": "meta-llama/Llama-3.2-3B",
                "pre_download_llama": False,
            },
            "aws": {
                "region": "us-east-1",
                "s3_bucket": aws.bucket,
                "sqs_queue_url": aws.queue_url,
            },
            "hf_download_timeout": 300,
            "max_video_duration_seconds": 120,
            "max_upload_bytes": 300000000,
        }
        if not with_aws:
            del cfg["aws"]
        cfg.update(overrides)
        return cfg

    return make


def _make_clip(path, seconds=3, audio=True):
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i"]
    cmd += [f"testsrc=size=160x120:rate=10:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True, capture_output=True)


@pytest.fixture(scope="session")
def clip_path(tmp_path_factory):
    """A 3-second mp4 with an audio track, generated with ffmpeg (no binary files in git)."""
    path = tmp_path_factory.mktemp("clips") / "clip.mp4"
    _make_clip(path, seconds=3, audio=True)
    return path


@pytest.fixture(scope="session")
def clip2_path(tmp_path_factory):
    """A different 2-second mp4 (different size from clip_path)."""
    path = tmp_path_factory.mktemp("clips2") / "clip2.mp4"
    _make_clip(path, seconds=2, audio=False)
    return path


@pytest.fixture
def roi_masks_small():
    """Tiny hand-made ROI masks (100 vertices each) so nothing downloads the atlas."""
    masks = {}
    for i, name in enumerate(ROI_NAMES):
        mask = np.zeros(20484, dtype=bool)
        mask[i * 100 : (i + 1) * 100] = True
        masks[name] = mask
    return masks


@pytest.fixture
def new_key():
    """Returns (job_id, key) for a fresh upload key."""

    def make(ext=".mp4"):
        job_id = str(uuid.uuid4())
        return job_id, f"uploads/placeholder-user/{job_id}{ext}"

    return make


@pytest.fixture
def patch_everywhere(monkeypatch):
    """Replace a function wherever the implementation might look it up.

    The spec names the functions by module (for example neurolens.inference.run_inference) but
    not how the worker imports them, so patch the module attribute and, if present, the copy
    in the worker / web modules.
    """

    def patch(name, fn, *modules):
        patched = 0
        for modname in modules:
            mod = importlib.import_module(modname)
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, fn)
                patched += 1
        assert patched, f"{name} not found in any of {modules}"

    return patch


class SpyS3:
    """Wraps a boto3 client: records every call and refuses the listed ones."""

    def __init__(self, inner, forbid=()):
        self._inner = inner
        self._forbid = set(forbid)
        self.calls = []

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def wrapper(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name in self._forbid:
                raise AssertionError(f"s3.{name} must not be called here")
            return attr(*args, **kwargs)

        return wrapper

    def names(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def spy_s3(aws):
    def make(forbid=()):
        return SpyS3(aws.s3, forbid=forbid)

    return make


@pytest.fixture
def queue_message(aws):
    """Put a body on the queue and receive it, like the worker would.

    VisibilityTimeout=0 makes an undeleted message visible again at once, so
    `remaining()` can tell whether process_message deleted it.
    """

    def put(body):
        aws.sqs.send_message(QueueUrl=aws.queue_url, MessageBody=body)
        resp = aws.sqs.receive_message(
            QueueUrl=aws.queue_url, MaxNumberOfMessages=1, VisibilityTimeout=0
        )
        return resp["Messages"][0]

    return put


@pytest.fixture
def remaining(aws):
    def look():
        resp = aws.sqs.receive_message(
            QueueUrl=aws.queue_url, MaxNumberOfMessages=10, VisibilityTimeout=0
        )
        return resp.get("Messages", [])

    return look
