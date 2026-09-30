"""Tests for neurolens.storage (moto, no real AWS). Written from docs/M1_spec.md 1, 4a, 6a."""

import base64
import json
import uuid

import pytest
from neurolens.storage import job_id_from_key, parse_s3_event, presign_upload

BUCKET = "neurolens-test-bucket"


def s3_event(*pairs):
    """An S3 notification body as it arrives in an SQS message (no SNS wrapper)."""
    return json.dumps(
        {
            "Records": [
                {
                    "eventSource": "aws:s3",
                    "eventName": "ObjectCreated:Post",
                    "s3": {"bucket": {"name": b}, "object": {"key": k, "size": 123}},
                }
                for b, k in pairs
            ]
        }
    )


def policy_conditions(fields):
    return json.loads(base64.b64decode(fields["policy"]))["conditions"]


# ---------------------------------------------------------------- parse_s3_event


def test_parse_single_record():
    key = "uploads/placeholder-user/abc.mp4"
    assert parse_s3_event(s3_event(("b1", key))) == [("b1", key)]


def test_parse_several_records_in_order():
    pairs = [
        ("b1", "uploads/placeholder-user/a.mp4"),
        ("b2", "uploads/placeholder-user/b.mov"),
        ("b1", "uploads/placeholder-user/c.webm"),
    ]
    assert parse_s3_event(s3_event(*pairs)) == pairs


def test_parse_drops_keys_outside_uploads():
    keep = ("b1", "uploads/placeholder-user/a.mp4")
    body = s3_event(
        ("b1", "status/a.json"),
        keep,
        ("b1", "results/a.json"),
        ("b1", "code/latest.zip"),
        ("b1", "experiments/run1.csv"),
        ("b1", "uploads-old/a.mp4"),
    )
    assert parse_s3_event(body) == [keep]


def test_parse_all_records_outside_uploads_gives_empty_list():
    assert parse_s3_event(s3_event(("b1", "results/a.json"))) == []


def test_parse_test_event_returns_empty_list():
    body = json.dumps(
        {
            "Service": "Amazon S3",
            "Event": "s3:TestEvent",
            "Time": "2026-10-01T00:00:00.000Z",
            "Bucket": "b1",
            "RequestId": "x",
            "HostId": "y",
        }
    )
    assert parse_s3_event(body) == []


def test_parse_returns_tuples_of_strings():
    [(bucket, key)] = parse_s3_event(s3_event(("b1", "uploads/placeholder-user/a.mp4")))
    assert isinstance(bucket, str) and isinstance(key, str)


# ---------------------------------------------------------------- job_id_from_key


def test_job_id_from_key():
    job_id = str(uuid.uuid4())
    assert job_id_from_key(f"uploads/placeholder-user/{job_id}.mp4") == job_id
    assert job_id_from_key(f"uploads/placeholder-user/{job_id}.mov") == job_id
    assert job_id_from_key(f"uploads/placeholder-user/{job_id}.webm") == job_id


# ---------------------------------------------------------------- presign_upload


def test_presign_upload_result_shape(aws):
    out = presign_upload(aws.s3, BUCKET, "video/mp4", 1000)
    assert set(out) == {"job_id", "url", "fields", "object_key", "expires_in"}
    assert isinstance(out["url"], str) and BUCKET in out["url"]
    assert isinstance(out["fields"], dict)
    assert out["expires_in"] == 300  # the documented default


def test_presign_upload_expires_in_is_passed_through(aws):
    out = presign_upload(aws.s3, BUCKET, "video/mp4", 1000, expires_in=60)
    assert out["expires_in"] == 60


def test_presign_upload_job_id_is_a_fresh_uuid4(aws):
    a = presign_upload(aws.s3, BUCKET, "video/mp4", 1000)
    b = presign_upload(aws.s3, BUCKET, "video/mp4", 1000)
    assert uuid.UUID(a["job_id"]).version == 4
    assert a["job_id"] != b["job_id"]


@pytest.mark.parametrize(
    "content_type, ext",
    [("video/mp4", ".mp4"), ("video/quicktime", ".mov"), ("video/webm", ".webm")],
)
def test_presign_upload_key_extension_follows_content_type(aws, content_type, ext):
    out = presign_upload(aws.s3, BUCKET, content_type, 1000)
    assert out["object_key"] == f"uploads/placeholder-user/{out['job_id']}{ext}"
    assert out["fields"]["key"] == out["object_key"]
    assert job_id_from_key(out["object_key"]) == out["job_id"]


def test_presign_upload_is_a_post_policy_with_the_size_cap(aws):
    """Only what our code controls: the policy we generate carries the size cap.

    moto-limited: moto does not enforce content-length-range when a file is uploaded, so
    real enforcement by S3 is checked by hand in chunk E (M1 spec section 9, item 11).
    """
    out = presign_upload(aws.s3, BUCKET, "video/mp4", 12345)
    assert "policy" in out["fields"]
    ranges = [
        c
        for c in policy_conditions(out["fields"])
        if isinstance(c, list) and c[0] == "content-length-range"
    ]
    assert len(ranges) == 1
    assert ranges[0][-1] == 12345


def test_presign_upload_policy_binds_bucket_and_key(aws):
    out = presign_upload(aws.s3, BUCKET, "video/mp4", 1000)
    conditions = policy_conditions(out["fields"])
    assert {"bucket": BUCKET} in conditions
    key = out["object_key"]
    assert {"key": key} in conditions or ["eq", "$key", key] in conditions
