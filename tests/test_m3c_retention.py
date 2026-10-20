"""M3c retention curves (docs/M3c_spec.md §3c, §8).

`neurolens.results.parse_retention_csv`'s rules (one broken rule per test) and the JSON route
`POST /api/jobs/<job_id>/retention`: ownership, a done job, consent, storage and replacement.
"""

import uuid

import pytest
from botocore.exceptions import ClientError
from conftest import WEB_USER_ID
from neurolens import billing, storage
from neurolens.web.app import create_app

HEADER = "elapsed_ratio,watch_ratio"
STARTER_CENTS = 10_000


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


def csv_text(rows):
    return HEADER + "\n" + "".join(f"{elapsed},{watch}\n" for elapsed, watch in rows)


def valid_rows(n):
    return [(i / n, 0.5) for i in range(n)]


def parse(text):
    from neurolens.results import parse_retention_csv

    return parse_retention_csv(text)


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


# ---------------------------------------------------------------- parse_retention_csv


def test_parse_retention_csv_returns_elapsed_and_watch_pairs():
    text = csv_text([(0, 0), (0.5, 1.2), (1, 0.25)])
    assert parse(text) == [(0.0, 0.0), (0.5, 1.2), (1.0, 0.25)]


@pytest.mark.parametrize(
    "header",
    [
        "elapsed,watch",
        "elapsed_ratio,watch_ratio,extra",
        "Elapsed_ratio,watch_ratio",
        "elapsed_ratio, watch_ratio",
    ],
)
def test_parse_retention_csv_requires_the_exact_header(header):
    with pytest.raises(ValueError) as info:
        parse(header + "\n0.0,0\n1.0,1\n")
    assert "1" in str(info.value)


@pytest.mark.parametrize("rows", [[], [(0.0, 0.0)]], ids=["header_only", "one_row"])
def test_parse_retention_csv_needs_at_least_two_rows(rows):
    with pytest.raises(ValueError):
        parse(csv_text(rows))


def test_parse_retention_csv_accepts_1000_rows():
    assert len(parse(csv_text(valid_rows(1000)))) == 1000


def test_parse_retention_csv_refuses_more_than_1000_rows():
    with pytest.raises(ValueError):
        parse(csv_text(valid_rows(1001)))


@pytest.mark.parametrize("elapsed", [-0.1, 1.1, 2.0])
def test_parse_retention_csv_requires_elapsed_ratios_in_0_to_1(elapsed):
    text = csv_text([(0.0, 0.0), (elapsed, 0.5)])
    with pytest.raises(ValueError) as info:
        parse(text)
    assert "3" in str(info.value)


@pytest.mark.parametrize(
    "rows",
    [[(0.0, 0.0), (0.0, 1.0)], [(0.5, 0.0), (0.4, 1.0)]],
    ids=["equal", "decreasing"],
)
def test_parse_retention_csv_requires_strictly_increasing_elapsed_ratios(rows):
    with pytest.raises(ValueError) as info:
        parse(csv_text(rows))
    assert "3" in str(info.value)


def test_parse_retention_csv_refuses_a_negative_watch_ratio():
    text = csv_text([(0.0, 0.0), (0.5, -0.01)])
    with pytest.raises(ValueError) as info:
        parse(text)
    assert "3" in str(info.value)


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_parse_retention_csv_refuses_a_non_finite_watch_ratio(value):
    text = HEADER + f"\n0.0,0\n0.5,{value}\n"
    with pytest.raises(ValueError) as info:
        parse(text)
    assert "3" in str(info.value)


@pytest.mark.parametrize("value", ["abc", ""], ids=["text", "empty"])
def test_parse_retention_csv_refuses_a_non_number(value):
    with pytest.raises(ValueError):
        parse(HEADER + f"\n0.0,0\n0.5,{value}\n")


# ---------------------------------------------------------------- the route


def test_retention_post_stores_the_csv_for_a_done_consented_job(client, aws, db):
    job_id, _ = make_job(db, consent=True)
    text = csv_text([(0.0, 0.0), (0.5, 1.2), (1.0, 0.9)])
    resp = client.post(f"/api/jobs/{job_id}/retention", json={"csv": text})
    assert 200 <= resp.status_code < 300
    assert parse(stored_text(aws, retention_key(job_id))) == parse(text)


def test_a_new_retention_upload_replaces_the_old_one(client, aws, db):
    job_id, _ = make_job(db, consent=True)
    first = csv_text([(0.0, 0.0), (1.0, 0.5)])
    second = csv_text([(0.0, 0.2), (0.5, 0.6), (1.0, 0.9)])
    first_resp = client.post(f"/api/jobs/{job_id}/retention", json={"csv": first})
    second_resp = client.post(f"/api/jobs/{job_id}/retention", json={"csv": second})
    assert 200 <= first_resp.status_code < 300
    assert 200 <= second_resp.status_code < 300
    assert parse(stored_text(aws, retention_key(job_id))) == parse(second)


@pytest.mark.parametrize("processing", [False, True], ids=["queued", "processing"])
def test_retention_before_the_job_is_done_is_409_job_not_done(client, aws, db, processing):
    job_id, _ = make_job(db, consent=True, done=False, processing=processing)
    resp = client.post(f"/api/jobs/{job_id}/retention", json={"csv": csv_text(valid_rows(3))})
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "job_not_done"
    assert not object_exists(aws, retention_key(job_id))


def test_retention_on_a_failed_job_is_409_job_not_done(client, aws, db):
    job_id, _ = make_job(db, consent=True, done=False)
    assert billing.issue_refund(db, job_id, "unreadable_video", queued_before_s=0) is True
    resp = client.post(f"/api/jobs/{job_id}/retention", json={"csv": csv_text(valid_rows(3))})
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "job_not_done"
    assert not object_exists(aws, retention_key(job_id))


def test_retention_body_without_a_string_csv_is_400(client, aws, db):
    job_id, _ = make_job(db, consent=True)
    for body in ({}, {"csv": None}, {"csv": 123}, {"csv": ["elapsed_ratio,watch_ratio"]}):
        resp = client.post(f"/api/jobs/{job_id}/retention", json=body)
        assert resp.status_code == 400, body
    assert not object_exists(aws, retention_key(job_id))


def test_retention_without_consent_is_409_no_consent(client, aws, db):
    job_id, _ = make_job(db, consent=False, done=True)
    resp = client.post(f"/api/jobs/{job_id}/retention", json={"csv": csv_text(valid_rows(3))})
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "no_consent"
    assert not object_exists(aws, retention_key(job_id))


def test_retention_on_another_users_job_is_404_with_no_s3_call(app_for, db):
    job_id, _ = make_job(db, consent=True, done=True, user_id="someone-else")
    s3 = NoCallS3()
    resp = app_for(s3_client=s3).post(
        f"/api/jobs/{job_id}/retention", json={"csv": csv_text(valid_rows(3))}
    )
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"
    assert s3.calls == []


def test_retention_on_a_deleted_job_is_404(client, aws, db, pg):
    job_id, _ = make_job(db, consent=True, done=True)
    pg.conn.execute("UPDATE jobs SET deleted_at = now() WHERE job_id = %s::uuid", job_id)
    resp = client.post(f"/api/jobs/{job_id}/retention", json={"csv": csv_text(valid_rows(3))})
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"
    assert not object_exists(aws, retention_key(job_id))


def test_a_broken_csv_is_400_invalid_retention_csv_with_its_line_number(client, aws, db):
    job_id, _ = make_job(db, consent=True)
    text = HEADER + "\n0.0,0\n0.5,-1\n"
    resp = client.post(f"/api/jobs/{job_id}/retention", json={"csv": text})
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "invalid_retention_csv"
    assert "3" in str(data)
    assert not object_exists(aws, retention_key(job_id))


def test_retention_text_over_64_kb_is_refused(client, aws, db):
    job_id, _ = make_job(db, consent=True)
    rows = [(f"{i / 1000:.25f}", "0." + "1234567890" * 5) for i in range(1000)]
    text = csv_text(rows)
    assert len(text.encode()) > 64 * 1024
    # SPEC-AMBIGUITY: §3c caps the text at 64 KB but names no error code for it.
    resp = client.post(f"/api/jobs/{job_id}/retention", json={"csv": text})
    assert resp.status_code in (400, 413)
    assert not object_exists(aws, retention_key(job_id))


def test_a_valid_csv_just_under_64_kb_is_accepted(client, aws, db):
    job_id, _ = make_job(db, consent=True)
    rows = [(f"{i / 1000:.6f}", "0." + "9" * 53) for i in range(1000)]
    text = csv_text(rows)
    assert 63 * 1024 < len(text.encode()) < 64 * 1024
    resp = client.post(f"/api/jobs/{job_id}/retention", json={"csv": text})
    assert 200 <= resp.status_code < 300
    assert parse(stored_text(aws, retention_key(job_id))) == parse(text)
