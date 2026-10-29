"""M3g §11: the report Lambda and its request builder (docs/M3g_spec.md §5).

The fake Anthropic client stands in for Bedrock; storage is moto S3 and report states are real
PostgreSQL rows. No network, Bedrock or paid call is made.
"""

import base64
import io
import json
import uuid
import zipfile
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from conftest import STARTER_CENTS, USER_ID
from neurolens import billing, results, storage

REPORT = {
    "model": "test-report-model",
    "max_tokens": 4321,
    "max_tries": 2,
    "stale_minutes": 5,
}
REPORT_KEY = "results/{job_id}.report.json"


def report_cfg(**overrides):
    return {**REPORT, **overrides}


def job_in_state(db, state="done", *, user_id=USER_ID):
    billing.ensure_user(db, user_id, f"{user_id}@example.com", STARTER_CENTS)
    job_id = str(uuid.uuid4())
    key = f"uploads/{user_id}/{job_id}.mp4"
    billing.reserve(db, user_id, job_id, key, "clip.mp4", 3000)
    if state in ("processing", "done", "failed"):
        assert billing.claim(db, job_id) == 1
    if state == "done":
        assert billing.settle_success(db, job_id) is True
    elif state == "failed":
        assert billing.issue_refund(db, job_id, "stalled", attempt=1) is True
    else:
        assert state in ("queued", "processing")
    return job_id, key


def done_job(db):
    return job_in_state(db, "done")


def make_result(duration=3, *, frames=None):
    signals = {
        name: [round(0.02 * (number + 1) + 0.1 * t, 2) for t in range(duration)]
        for number, name in enumerate(results.SERIES)
    }
    muted = {name: [round(value / 2, 2) for value in signals[name]] for name in results.SERIES}
    result = {
        "format": 2,
        "duration_seconds": duration,
        "signals": signals,
        "muted": muted,
    }
    if frames is not None:
        result["frames"] = frames
    return result


def frames_zip(frames, words=()):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for number in reversed(range(len(frames))):
            archive.writestr(f"{number:04d}.jpg", frames[number])
        archive.writestr("words.json", json.dumps(list(words)))
    return buffer.getvalue()


def put_result(aws, job_id, *, frames=3):
    result = make_result(frames=frames)
    assert storage.put_result(aws.s3, aws.bucket, job_id, result) is True
    return result


def put_frames(aws, job_id, frames, words=()):
    storage.put_frames(aws.s3, aws.bucket, job_id, frames_zip(frames, words))


def set_report(pg, job_id, state=None, *, tries=0, error=None, started_minutes_ago=None):
    if started_minutes_ago is None:
        pg.conn.execute(
            "UPDATE jobs SET report_state = %s, report_tries = %s, report_started_at = NULL, "
            "report_error = %s WHERE job_id = %s::uuid",
            (state, tries, error, job_id),
        )
    else:
        pg.conn.execute(
            "UPDATE jobs SET report_state = %s, report_tries = %s, "
            "report_started_at = now() - make_interval(mins => %s), report_error = %s "
            "WHERE job_id = %s::uuid",
            (state, tries, started_minutes_ago, error, job_id),
        )


def object_bytes(aws, key):
    return aws.s3.get_object(Bucket=aws.bucket, Key=key)["Body"].read()


def object_exists(aws, key):
    try:
        aws.s3.head_object(Bucket=aws.bucket, Key=key)
    except ClientError as err:
        assert err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound")
        return False
    return True


# ---------------------------------------------------------------- the report Lambda


class FakeUsage:
    def __init__(self, input_tokens=11, output_tokens=22):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    def model_dump(self):
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


class FakeClient:
    def __init__(self, response=None, error=None, on_create=None):
        self.response = response
        self.error = error
        self.on_create = on_create
        self.calls = []
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.on_create is not None:
            self.on_create()
        if self.error is not None:
            raise self.error
        return self.response


def text_response(text, *, stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
        usage=FakeUsage(),
    )


def answer(report, *, stop_reason="end_turn"):
    return text_response(json.dumps(report), stop_reason=stop_reason)


def valid_report(duration=3):
    return {
        "headline": "A short headline",
        "summary": "A short summary.",
        "moments": [
            {
                "start": 0,
                "end": 1,
                "what_happens": "Someone enters.",
                "brain_response": "Visual areas rise.",
                "suggestion": "Keep the opening.",
            }
        ],
        "strengths": ["Clear opening"],
        "improvements": ["Tighten the end"],
    }


def writing_job(aws, db, pg, *, with_result=True, with_frames=True):
    job_id, _ = done_job(db)
    if with_result:
        put_result(aws, job_id, frames=3)
    if with_frames:
        put_frames(aws, job_id, [b"\xff\xd8zero", b"\xff\xd8one", b"\xff\xd8two"])
    set_report(pg, job_id, "writing", tries=1, started_minutes_ago=0)
    return job_id


def write_report(aws, db, job_id, client, cfg):
    from neurolens.lambdas import report

    report.write_report(job_id, db, aws.s3, aws.bucket, client, cfg)


def report_job(pg, job_id):
    return pg.job(job_id)


def test_write_report_stores_the_answer_then_marks_it_ready(aws, db, pg, make_cfg):
    job_id = writing_job(aws, db, pg)
    client = FakeClient(answer(valid_report()))
    cfg = make_cfg(report=report_cfg())

    write_report(aws, db, job_id, client, cfg)

    assert report_job(pg, job_id)["report_state"] == "ready"
    assert not report_job(pg, job_id)["report_error"]
    stored = json.loads(object_bytes(aws, REPORT_KEY.format(job_id=job_id)))
    assert stored["headline"] == "A short headline"
    assert stored["summary"] == "A short summary."
    assert stored["moments"] == valid_report()["moments"]
    assert stored["model"] == cfg["report"]["model"]
    assert stored["created_at"].endswith("Z")
    assert stored["usage"]["input_tokens"] == 11
    assert stored["usage"]["output_tokens"] == 22
    assert client.calls[0]["model"] == cfg["report"]["model"]
    assert client.calls[0]["max_tokens"] == cfg["report"]["max_tokens"]


@pytest.mark.parametrize("state", [None, "ready", "failed"])
def test_write_report_does_nothing_unless_the_report_is_writing(aws, db, pg, make_cfg, state):
    job_id, _ = done_job(db)
    put_result(aws, job_id, frames=3)
    put_frames(aws, job_id, [b"\xff\xd8zero"])
    set_report(pg, job_id, state, tries=1)
    client = FakeClient(answer(valid_report()))

    write_report(aws, db, job_id, client, make_cfg(report=report_cfg()))

    assert client.calls == []
    assert report_job(pg, job_id)["report_state"] == state
    assert not object_exists(aws, REPORT_KEY.format(job_id=job_id))


@pytest.mark.parametrize("missing", ["result", "frames"])
def test_a_missing_input_fails_without_calling_bedrock(aws, db, pg, make_cfg, missing):
    job_id = writing_job(
        aws, db, pg, with_result=missing != "result", with_frames=missing != "frames"
    )
    client = FakeClient(answer(valid_report()))

    write_report(aws, db, job_id, client, make_cfg(report=report_cfg()))

    job = report_job(pg, job_id)
    assert (job["report_state"], job["report_error"]) == ("failed", "missing_input")
    assert client.calls == []
    assert not object_exists(aws, REPORT_KEY.format(job_id=job_id))


def bad_answer(kind):
    if kind == "bad_json":
        return text_response("this is not JSON")
    report = valid_report()
    if kind == "out_of_range":
        report["moments"][0]["start"] = 0
        report["moments"][0]["end"] = 3
    return answer(report)


def test_a_long_answer_is_still_stored(aws, db, pg, make_cfg):
    """Lengths and counts are the prompt's guidance only: a long report is still a report."""
    job_id = writing_job(aws, db, pg)
    report = valid_report()
    report["headline"] = "h" * 500
    report["summary"] = "s" * 5000
    report["moments"] = [valid_report()["moments"][0] for _ in range(12)]
    report["strengths"] = ["a strength"] * 9
    client = FakeClient(answer(report))

    write_report(aws, db, job_id, client, make_cfg(report=report_cfg()))

    assert report_job(pg, job_id)["report_state"] == "ready"
    assert object_exists(aws, REPORT_KEY.format(job_id=job_id))


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("refusal", "refused"),
        ("truncated", "truncated"),
        ("bad_json", "bad_answer"),
        ("out_of_range", "bad_answer"),
    ],
)
def test_a_bad_answer_fails_with_its_code_and_stores_nothing(aws, db, pg, make_cfg, kind, expected):
    job_id = writing_job(aws, db, pg)
    if kind == "refusal":
        response = answer(valid_report(), stop_reason="refusal")
    elif kind == "truncated":
        response = answer(valid_report(), stop_reason="max_tokens")
    else:
        response = bad_answer(kind)
    client = FakeClient(response)

    write_report(aws, db, job_id, client, make_cfg(report=report_cfg()))

    job = report_job(pg, job_id)
    assert (job["report_state"], job["report_error"]) == ("failed", expected)
    assert not object_exists(aws, REPORT_KEY.format(job_id=job_id))


def test_a_bedrock_error_fails_with_bedrock_error(aws, db, pg, make_cfg):
    job_id = writing_job(aws, db, pg)
    client = FakeClient(error=RuntimeError("bedrock is down"))

    write_report(aws, db, job_id, client, make_cfg(report=report_cfg()))

    job = report_job(pg, job_id)
    assert (job["report_state"], job["report_error"]) == ("failed", "bedrock_error")
    assert not object_exists(aws, REPORT_KEY.format(job_id=job_id))


def test_a_job_deleted_during_the_call_loses_the_report_object(aws, db, pg, make_cfg):
    job_id = writing_job(aws, db, pg)

    def delete_job():
        pg.conn.execute("UPDATE jobs SET deleted_at = now() WHERE job_id = %s::uuid", (job_id,))

    client = FakeClient(answer(valid_report()), on_create=delete_job)

    write_report(aws, db, job_id, client, make_cfg(report=report_cfg()))

    assert not object_exists(aws, REPORT_KEY.format(job_id=job_id))


# ---------------------------------------------------------------- report_prompt.build_request


def _walk(value):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _json_blocks(content):
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            try:
                yield json.loads(block["text"])
            except (TypeError, ValueError):
                continue


def _numeric_lists(value):
    return [
        item
        for item in _walk(value)
        if isinstance(item, list)
        and item
        and all(isinstance(entry, int | float) and not isinstance(entry, bool) for entry in item)
    ]


def test_build_request_puts_each_image_after_its_second_label():
    from neurolens.report_prompt import build_request

    result = make_result(duration=3)
    frames = [b"\xff\xd8zero", b"\xff\xd8one", b"\xff\xd8two"]
    words = [{"start": 1.25, "text": "hello"}]

    system, content = build_request(result, frames, words)

    assert isinstance(system, str) and system
    image_indices = [
        index
        for index, block in enumerate(content)
        if isinstance(block, dict) and block.get("type") == "image"
    ]
    assert len(image_indices) == len(frames)
    for second, index in enumerate(image_indices):
        label = content[index - 1]
        assert label["type"] == "text"
        assert label["text"] == f"t = {second} s"
        source = content[index]["source"]
        assert source["type"] == "base64"
        assert source["media_type"] == "image/jpeg"
        assert base64.b64decode(source["data"]) == frames[second]


def test_build_request_carries_the_results_numbers():
    from neurolens.report_prompt import build_request

    result = make_result(duration=3)

    _, content = build_request(result, [b"\xff\xd8zero"], [])

    table = content[0]["text"]
    lines = table.splitlines()
    assert lines[0].startswith("duration_seconds 3, last_second 2.")
    header = lines[1].split()
    assert header == ["t", *results.SERIES, "muted_overall"]
    rows = [line.split() for line in lines[2:]]
    assert [int(row[0]) for row in rows] == [0, 1, 2]
    for second, row in enumerate(rows):
        expected = [result["signals"][name][second] for name in results.SERIES]
        expected.append(result["muted"]["overall"][second])
        assert [float(cell) for cell in row[1:]] == pytest.approx(expected)
    assert "summary" not in table


def test_build_request_includes_the_spoken_words_when_there_are_some():
    from neurolens.report_prompt import build_request

    result = make_result(duration=3)
    words = [{"start": 1.25, "text": "hello"}]

    _, content = build_request(result, [b"\xff\xd8zero"], words)

    text = " ".join(
        block["text"]
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )
    assert "hello" in text
