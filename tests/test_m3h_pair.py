"""M3h §2, §13: one canonical pair for every comparison.

A-vs-B and B-vs-A are the same pair: both ids are lowercase UUID text in sorted order. The key
sits under results/ so it expires with the results and matches the report Lambda's IAM grant.
"""

import json
import uuid

import pytest
from neurolens import storage


def ids():
    return str(uuid.uuid4()), str(uuid.uuid4())


def test_comparison_pair_is_the_same_either_way_round_and_sorted():
    x, y = ids()

    a, b = storage.comparison_pair(x, y)

    assert (a, b) == storage.comparison_pair(y, x)
    assert a == a.lower() and b == b.lower()
    assert a < b


def test_comparison_pair_rejects_the_same_id_twice():
    x, _ = ids()

    with pytest.raises(ValueError):
        storage.comparison_pair(x, x)


@pytest.mark.parametrize("bad", ["not-a-uuid", "", "1234"])
def test_comparison_pair_rejects_a_non_uuid(bad):
    x, _ = ids()

    with pytest.raises(ValueError):
        storage.comparison_pair(x, bad)


def test_comparison_key_sits_under_results():
    a, b = storage.comparison_pair(*ids())

    assert storage.comparison_key(a, b) == f"results/{a}_{b}.report.json"


def test_get_comparison_report_reads_the_stored_answer(aws):
    a, b = storage.comparison_pair(*ids())
    report = {"headline": "Which cut wins", "differences": []}
    aws.s3.put_object(
        Bucket=aws.bucket,
        Key=storage.comparison_key(a, b),
        Body=json.dumps(report).encode(),
    )

    assert storage.get_comparison_report(aws.s3, aws.bucket, a, b) == report


def test_get_comparison_report_is_none_when_missing(aws):
    a, b = storage.comparison_pair(*ids())

    assert storage.get_comparison_report(aws.s3, aws.bucket, a, b) is None
