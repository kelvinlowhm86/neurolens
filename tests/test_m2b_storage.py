"""M2b job status and result helpers in neurolens.storage (moto). Written from docs/M2b_spec.md
§1a (storage interfaces) and §4 (status objects at status/{job_id}.json)."""

import json
from datetime import UTC, datetime

from botocore.exceptions import ClientError
from neurolens import storage

T0 = datetime(2026, 10, 14, 3, 22, 10, tzinfo=UTC)
T1 = datetime(2026, 10, 14, 3, 22, 41, tzinfo=UTC)
T2 = datetime(2026, 10, 14, 3, 25, 2, tzinfo=UTC)


def raw_status(aws, job_id):
    """The status object exactly as stored at §4's key, or None."""
    try:
        body = aws.s3.get_object(Bucket=aws.bucket, Key=f"status/{job_id}.json")["Body"]
    except ClientError as err:
        assert err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound")
        return None
    return json.loads(body.read())


def write_raw_status(aws, job_id, obj):
    aws.s3.put_object(Bucket=aws.bucket, Key=f"status/{job_id}.json", Body=json.dumps(obj))


# ---------------------------------------------------------------- result_exists / get_result


def test_result_exists_is_false_without_a_result(aws):
    assert storage.result_exists(aws.s3, aws.bucket, "job-1") is False


def test_result_exists_is_true_after_put_result(aws):
    storage.put_result(aws.s3, aws.bucket, "job-1", {"job_id": "job-1"})
    assert storage.result_exists(aws.s3, aws.bucket, "job-1") is True
    assert storage.result_exists(aws.s3, aws.bucket, "job-2") is False


def test_get_result_returns_the_stored_json_or_none(aws):
    assert storage.get_result(aws.s3, aws.bucket, "job-1") is None
    result = {"job_id": "job-1", "duration_seconds": 3, "timesteps": [{"t": 0}]}
    storage.put_result(aws.s3, aws.bucket, "job-1", result)
    assert storage.get_result(aws.s3, aws.bucket, "job-1") == result


# ---------------------------------------------------------------- get_status


def test_get_status_returns_none_when_there_is_no_status_object(aws):
    assert storage.get_status(aws.s3, aws.bucket, "job-1") is None


def test_get_status_reads_status_job_id_json(aws):
    obj = {"job_id": "job-1", "status": "processing", "stage": "downloading", "stages": []}
    write_raw_status(aws, "job-1", obj)
    assert storage.get_status(aws.s3, aws.bucket, "job-1") == obj


# ---------------------------------------------------------------- put_status


def test_put_status_with_no_existing_object_creates_one_with_job_id_and_stages(aws):
    storage.put_status(aws.s3, aws.bucket, "job-1", "processing", now=T0)
    obj = raw_status(aws, "job-1")
    assert obj is not None, "put_status must write status/{job_id}.json"
    assert obj["job_id"] == "job-1"
    assert obj["status"] == "processing"
    assert obj["stage"] is None
    assert obj["error"] is None
    assert obj["stages"] == []
    assert obj["updated_at"] == "2026-10-14T03:22:10Z"  # §4's format, from the injected now


def test_put_status_appends_stages_in_order_with_the_injected_now(aws):
    storage.put_status(aws.s3, aws.bucket, "job-1", "processing", stage="downloading", now=T0)
    storage.put_status(aws.s3, aws.bucket, "job-1", "processing", stage="transcribing", now=T1)
    storage.put_status(aws.s3, aws.bucket, "job-1", "processing", stage="inference_full", now=T2)
    obj = storage.get_status(aws.s3, aws.bucket, "job-1")
    assert obj["stages"] == [
        {"stage": "downloading", "at": "2026-10-14T03:22:10Z"},
        {"stage": "transcribing", "at": "2026-10-14T03:22:41Z"},
        {"stage": "inference_full", "at": "2026-10-14T03:25:02Z"},
    ]
    assert obj["stage"] == "inference_full"
    assert obj["updated_at"] == "2026-10-14T03:25:02Z"


def test_put_status_without_a_stage_appends_nothing_and_sets_stage_and_error(aws):
    storage.put_status(aws.s3, aws.bucket, "job-1", "processing", stage="downloading", now=T0)
    storage.put_status(
        aws.s3, aws.bucket, "job-1", "failed", error="The file is not a readable video.", now=T1
    )
    obj = storage.get_status(aws.s3, aws.bucket, "job-1")
    assert obj["status"] == "failed"
    assert obj["stage"] is None
    assert obj["error"] == "The file is not a readable video."
    assert obj["stages"] == [{"stage": "downloading", "at": "2026-10-14T03:22:10Z"}]
    assert obj["updated_at"] == "2026-10-14T03:22:41Z"


def test_put_status_retrying_keeps_the_earlier_stages(aws):
    storage.put_status(aws.s3, aws.bucket, "job-1", "processing", stage="downloading", now=T0)
    storage.put_status(
        aws.s3, aws.bucket, "job-1", "processing", stage="retrying", error="boom", now=T1
    )
    obj = storage.get_status(aws.s3, aws.bucket, "job-1")
    assert obj["status"] == "processing"
    assert obj["stage"] == "retrying"
    assert obj["error"] == "boom"
    assert [s["stage"] for s in obj["stages"]] == ["downloading", "retrying"]


def test_put_status_without_now_still_writes_updated_at(aws):
    storage.put_status(aws.s3, aws.bucket, "job-1", "processing", stage="downloading")
    obj = storage.get_status(aws.s3, aws.bucket, "job-1")
    assert isinstance(obj["updated_at"], str) and obj["updated_at"]
    assert obj["stages"][0]["at"] == obj["updated_at"]


def test_put_status_never_changes_a_done_object(aws):
    storage.put_status(aws.s3, aws.bucket, "job-1", "processing", stage="downloading", now=T0)
    storage.put_status(aws.s3, aws.bucket, "job-1", "done", now=T1)
    done = raw_status(aws, "job-1")
    assert done["status"] == "done"

    storage.put_status(aws.s3, aws.bucket, "job-1", "failed", error="late failure", now=T2)
    storage.put_status(aws.s3, aws.bucket, "job-1", "processing", stage="retrying", now=T2)
    storage.put_status(aws.s3, aws.bucket, "job-1", "processing", stage="extracting_roi", now=T2)
    assert raw_status(aws, "job-1") == done


def test_put_status_never_changes_a_done_object_written_by_someone_else(aws):
    """Whatever wrote the done object (another worker), it is left exactly as it is."""
    done = {
        "job_id": "job-1",
        "status": "done",
        "stage": None,
        "updated_at": "2026-10-14T03:30:00Z",
        "stages": [{"stage": "downloading", "at": "2026-10-14T03:22:10Z"}],
        "error": None,
    }
    write_raw_status(aws, "job-1", done)
    storage.put_status(aws.s3, aws.bucket, "job-1", "failed", error="interrupted", now=T2)
    assert raw_status(aws, "job-1") == done


def test_status_objects_of_different_jobs_are_independent(aws):
    storage.put_status(aws.s3, aws.bucket, "job-1", "processing", stage="downloading", now=T0)
    storage.put_status(aws.s3, aws.bucket, "job-2", "failed", error="x", now=T1)
    assert storage.get_status(aws.s3, aws.bucket, "job-1")["status"] == "processing"
    assert storage.get_status(aws.s3, aws.bucket, "job-2")["status"] == "failed"
    assert storage.get_status(aws.s3, aws.bucket, "job-2")["job_id"] == "job-2"
