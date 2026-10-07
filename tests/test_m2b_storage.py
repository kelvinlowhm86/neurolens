"""M2b result helpers in neurolens.storage (moto). Written from docs/M2b_spec.md §1a (storage
interfaces). M3a retires the S3 status objects (docs/M3a_spec.md §5): the get_status/put_status
and `stages` tests are removed with their Aurora equivalents (§10, tests/test_m3a_billing.py
set_stage and tests/test_m3a_worker.py); result_exists/get_result stay."""

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
    result = {"job_id": "job-1", "duration_seconds": 3, "timesteps": [{"t": 0}]}
    storage.put_result(aws.s3, aws.bucket, "job-1", result)
    assert storage.get_result(aws.s3, aws.bucket, "job-1") == result


# ---------------------------------------------------------------- status objects retired (M3a)


def test_the_s3_status_helpers_are_removed():
    """docs/M3a_spec.md §5: job status lives in Aurora; get_status/put_status go."""
    assert not hasattr(storage, "get_status")
    assert not hasattr(storage, "put_status")
