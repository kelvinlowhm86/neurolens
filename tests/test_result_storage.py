"""Result helpers in neurolens.storage (moto).

The S3 status objects are retired: the get_status/put_status and `stages` tests are gone with
their Aurora equivalents (tests/test_billing.py `set_stage` and the worker tests);
result_exists/get_result stay.
"""

from neurolens import storage


# ---------------------------------------------------------------- result_exists / get_result


def test_result_exists_is_false_without_a_result(aws):
    assert storage.result_exists(aws.s3, aws.bucket, "job-1") is False


def test_result_exists_is_true_after_put_result(aws):
    storage.put_result(aws.s3, aws.bucket, "job-1", {"job_id": "job-1"})
    assert storage.result_exists(aws.s3, aws.bucket, "job-1") is True
    assert storage.result_exists(aws.s3, aws.bucket, "job-2") is False


def test_get_result_returns_the_stored_json_or_none(aws):
    assert storage.get_result(aws.s3, aws.bucket, "job-1") is None
    result = {
        "format": 2,
        "job_id": "job-1",
        "duration_seconds": 0,
        "signals": {},
        "muted": {},
    }
    storage.put_result(aws.s3, aws.bucket, "job-1", result)
    assert storage.get_result(aws.s3, aws.bucket, "job-1") == result


# ---------------------------------------------------------------- status objects retired
