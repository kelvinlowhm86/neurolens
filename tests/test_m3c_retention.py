"""M3c retention curves (docs/M3c_spec.md §3c, §8).

`neurolens.results.parse_retention_report`'s rules (one broken rule per test) and the JSON route
`POST /api/jobs/<job_id>/retention`: ownership, a done job, consent, storage and replacement.
"""

import csv
import io
import json
import uuid

import pytest
from botocore.exceptions import ClientError
from conftest import WEB_USER_ID
from neurolens import billing, storage
from neurolens.web.app import create_app

HEADER = "elapsed_ratio,watch_ratio"
STARTER_CENTS = 10_000
COLUMN_HEADERS = [
    {"name": "elapsedVideoTimeRatio", "columnType": "DIMENSION", "dataType": "FLOAT"},
    {"name": "audienceWatchRatio", "columnType": "METRIC", "dataType": "FLOAT"},
]


@pytest.fixture
def app_for(aws, db, make_cfg, tmp_path):
    def make(s3_client=None, **overrides):
        app = create_app(
            cfg=make_cfg(**overrides),
            data_dir=tmp_path / "data",
            db=db,
            s3_client=s3_client or aws.s3,
        )
        app.config["TESTING"] = True
        return app.test_client()

    return make


@pytest.fixture
def client(app_for):
    return app_for()


class NoCallS3:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError(f"s3.{name} must not be called here")

        return call


def report_text(rows, headers=None):
    report = {
        "kind": "youtubeAnalytics#resultTable",
        "columnHeaders": COLUMN_HEADERS if headers is None else headers,
    }
    if rows is not None:
        report["rows"] = rows
    return json.dumps(report)


def raw_report_text(watch_digits):
    """A real-shaped report with long watchratios, to reach the size limit without extra keys."""
    rows = ",\n".join(f"[{i / 1000:.6f}, 0.{'9' * watch_digits}]" for i in range(1000))
    headers = json.dumps(COLUMN_HEADERS)
    return (
        '{"kind": "youtubeAnalytics#resultTable", "columnHeaders": '
        + headers
        + ', "rows": ['
        + rows
        + "]}"
    )


def valid_rows(n):
    return [[i / n, 0.5] for i in range(n)]


def parse(text):
    from neurolens.results import parse_retention_report

    return parse_retention_report(text)


def stored_pairs(aws, key):
    rows = list(csv.reader(io.StringIO(stored_text(aws, key))))
    assert rows[0] == HEADER.split(",")
    return [(float(elapsed), float(watch)) for elapsed, watch in rows[1:]]


def make_job(db, *, consent=True, done=True, processing=False, user_id=WEB_USER_ID):
    billing.ensure_user(db, user_id, f"{user_id}@example.com", STARTER_CENTS)
    job_id = str(uuid.uuid4())
    key = storage.object_key(user_id, job_id, "video/mp4")
    billing.reserve(db, user_id, job_id, key, "clip.mp4", 3000, research_consent=consent)
    if processing:
        assert billing.claim(db, job_id) == 1
    if done:
        assert billing.settle_success(db, job_id) is True
    return job_id, key


def retention_key(job_id):
    return f"dataset/{job_id}/retention.csv"


def object_exists(aws, key):
    try:
        aws.s3.head_object(Bucket=aws.bucket, Key=key)
    except ClientError as err:
        assert err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound")
        return False
    return True


def stored_text(aws, key):
    return aws.s3.get_object(Bucket=aws.bucket, Key=key)["Body"].read().decode()


# ---------------------------------------------------------------- parse_retention_report


def test_parse_retention_report_returns_elapsed_and_watch_pairs():
    text = report_text([[0.01, 0.99], [0.02, 0.96]])
    assert parse(text) == [(0.01, 0.99), (0.02, 0.96)]


@pytest.mark.parametrize("text", ["", "not json", "{", "[]", "null", "42", '"a string"'])
def test_parse_retention_report_needs_a_json_object(text):
    with pytest.raises(ValueError):
        parse(text)


@pytest.mark.parametrize(
    "missing",
    ["elapsedVideoTimeRatio", "audienceWatchRatio"],
    ids=["elapsed", "watch"],
)
def test_parse_retention_report_requires_both_columns(missing):
    headers = [header for header in COLUMN_HEADERS if header["name"] != missing]
    with pytest.raises(ValueError) as info:
        parse(report_text([[0.0, 0.0], [1.0, 1.0]], headers=headers))
    assert missing in str(info.value)


def test_parse_retention_report_finds_columns_by_name_in_any_order():
    headers = [
        {"name": "relativeRetentionPerformance", "columnType": "METRIC", "dataType": "FLOAT"},
        {"name": "audienceWatchRatio", "columnType": "METRIC", "dataType": "FLOAT"},
        {"name": "elapsedVideoTimeRatio", "columnType": "DIMENSION", "dataType": "FLOAT"},
    ]
    text = report_text([[0.5, 0.9, 0.0], [0.4, 0.8, 1.0]], headers=headers)
    assert parse(text) == [(0.0, 0.9), (1.0, 0.8)]


def test_parse_retention_report_without_rows_says_youtube_has_no_data():
    with pytest.raises(ValueError) as info:
        parse(report_text(None))
    assert "no data" in str(info.value).lower()


@pytest.mark.parametrize("rows", [[], [[0.0, 0.0]]], ids=["none", "one"])
def test_parse_retention_report_needs_at_least_two_rows(rows):
    with pytest.raises(ValueError):
        parse(report_text(rows))


def test_parse_retention_report_accepts_1000_rows():
    assert len(parse(report_text(valid_rows(1000)))) == 1000


def test_parse_retention_report_refuses_more_than_1000_rows():
    with pytest.raises(ValueError) as info:
        parse(report_text(valid_rows(1001)))
    assert "1001" in str(info.value)


@pytest.mark.parametrize("elapsed", [-0.1, 1.1, 2.0])
def test_parse_retention_report_requires_elapsed_ratios_in_0_to_1(elapsed):
    text = report_text([[0.0, 0.0], [elapsed, 0.5]])
    with pytest.raises(ValueError) as info:
        parse(text)
    assert "row 2" in str(info.value)


@pytest.mark.parametrize(
    "rows",
    [[[0.0, 0.0], [0.0, 1.0]], [[0.5, 0.0], [0.4, 1.0]]],
    ids=["equal", "decreasing"],
)
def test_parse_retention_report_requires_strictly_increasing_elapsed_ratios(rows):
    with pytest.raises(ValueError) as info:
        parse(report_text(rows))
    assert "row 2" in str(info.value)


def test_parse_retention_report_refuses_a_negative_watch_ratio():
    with pytest.raises(ValueError) as info:
        parse(report_text([[0.0, 0.0], [0.5, -0.01]]))
    assert "row 2" in str(info.value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_parse_retention_report_refuses_a_non_finite_watch_ratio(value):
    with pytest.raises(ValueError) as info:
        parse(report_text([[0.0, 0.0], [0.5, value]]))
    assert "row 2" in str(info.value)


@pytest.mark.parametrize(
    "value",
    ["0.5", None, True, False],
    ids=["string", "null", "true", "false"],
)
def test_parse_retention_report_refuses_a_non_number(value):
    with pytest.raises(ValueError) as info:
        parse(report_text([[0.0, 0.0], [0.5, value]]))
    assert "row 2" in str(info.value)


@pytest.mark.parametrize(
    "row",
    [[0.5], [0.5, 0.5, 0.5], "0.5,0.5", {"elapsedVideoTimeRatio": 0.5}],
    ids=["short", "long", "text", "object"],
)
def test_parse_retention_report_refuses_a_row_of_the_wrong_shape(row):
    with pytest.raises(ValueError) as info:
        parse(report_text([[0.0, 0.0], row]))
    assert "row 2" in str(info.value)


# ---------------------------------------------------------------- the route


def test_retention_post_stores_the_two_column_csv_for_a_done_consented_job(client, aws, db):
    job_id, _ = make_job(db, consent=True)
    text = report_text([[0.0, 0.0], [0.5, 1.2], [1.0, 0.9]])
    resp = client.post(f"/api/jobs/{job_id}/retention", json={"report": text})
    assert 200 <= resp.status_code < 300
    assert stored_pairs(aws, retention_key(job_id)) == parse(text)
    assert stored_text(aws, retention_key(job_id)).splitlines()[0] == HEADER


def test_a_new_retention_upload_replaces_the_old_one(client, aws, db):
    job_id, _ = make_job(db, consent=True)
    first = report_text([[0.0, 0.0], [1.0, 0.5]])
    second = report_text([[0.0, 0.2], [0.5, 0.6], [1.0, 0.9]])
    first_resp = client.post(f"/api/jobs/{job_id}/retention", json={"report": first})
    second_resp = client.post(f"/api/jobs/{job_id}/retention", json={"report": second})
    assert 200 <= first_resp.status_code < 300
    assert 200 <= second_resp.status_code < 300
    assert stored_pairs(aws, retention_key(job_id)) == parse(second)


@pytest.mark.parametrize("processing", [False, True], ids=["queued", "processing"])
def test_retention_before_the_job_is_done_is_409_job_not_done(client, aws, db, processing):
    job_id, _ = make_job(db, consent=True, done=False, processing=processing)
    resp = client.post(f"/api/jobs/{job_id}/retention", json={"report": report_text(valid_rows(3))})
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "job_not_done"
    assert not object_exists(aws, retention_key(job_id))


def test_retention_on_a_failed_job_is_409_job_not_done(client, aws, db):
    job_id, _ = make_job(db, consent=True, done=False)
    assert billing.issue_refund(db, job_id, "unreadable_video", queued_before_s=0) is True
    resp = client.post(f"/api/jobs/{job_id}/retention", json={"report": report_text(valid_rows(3))})
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "job_not_done"
    assert not object_exists(aws, retention_key(job_id))


def test_retention_body_without_a_string_report_is_400(client, aws, db):
    job_id, _ = make_job(db, consent=True)
    for body in ({}, {"report": None}, {"report": 123}, {"report": ["a report"]}):
        resp = client.post(f"/api/jobs/{job_id}/retention", json=body)
        assert resp.status_code == 400, body
    assert not object_exists(aws, retention_key(job_id))


def test_retention_without_consent_is_409_no_consent(client, aws, db):
    job_id, _ = make_job(db, consent=False, done=True)
    resp = client.post(f"/api/jobs/{job_id}/retention", json={"report": report_text(valid_rows(3))})
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "no_consent"
    assert not object_exists(aws, retention_key(job_id))


def test_retention_on_another_users_job_is_404_with_no_s3_call(app_for, db):
    job_id, _ = make_job(db, consent=True, done=True, user_id="someone-else")
    s3 = NoCallS3()
    resp = app_for(s3_client=s3).post(
        f"/api/jobs/{job_id}/retention", json={"report": report_text(valid_rows(3))}
    )
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"
    assert s3.calls == []


def test_retention_on_a_deleted_job_is_404(client, aws, db, pg):
    job_id, _ = make_job(db, consent=True, done=True)
    pg.conn.execute("UPDATE jobs SET deleted_at = now() WHERE job_id = %s::uuid", (job_id,))
    resp = client.post(f"/api/jobs/{job_id}/retention", json={"report": report_text(valid_rows(3))})
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"
    assert not object_exists(aws, retention_key(job_id))


def test_a_broken_report_is_400_invalid_retention_report_with_its_row_number(client, aws, db):
    job_id, _ = make_job(db, consent=True)
    text = report_text([[0.0, 0.0], [0.5, -1.0]])
    resp = client.post(f"/api/jobs/{job_id}/retention", json={"report": text})
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "invalid_retention_report"
    assert "row 2" in str(data)
    assert not object_exists(aws, retention_key(job_id))


def test_retention_report_over_64_kb_is_refused(client, aws, db):
    job_id, _ = make_job(db, consent=True)
    text = raw_report_text(50)
    assert len(text.encode()) > 64 * 1024
    resp = client.post(f"/api/jobs/{job_id}/retention", json={"report": text})
    assert resp.status_code == 413
    assert resp.get_json()["error"] == "invalid_retention_report"
    assert not object_exists(aws, retention_key(job_id))


def test_a_valid_report_just_under_64_kb_is_accepted(client, aws, db):
    job_id, _ = make_job(db, consent=True)
    text = raw_report_text(49)
    assert 63 * 1024 < len(text.encode()) < 64 * 1024
    resp = client.post(f"/api/jobs/{job_id}/retention", json={"report": text})
    assert 200 <= resp.status_code < 300
    assert stored_pairs(aws, retention_key(job_id)) == parse(text)
