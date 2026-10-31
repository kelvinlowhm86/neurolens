"""M3h §5, §13: the comparison path of the report Lambda.

The fake Anthropic client stands in for Bedrock; storage is moto S3 and comparison states are
real PostgreSQL rows. No network, Bedrock or paid call is made.
"""

import json
import uuid
import zipfile
from io import BytesIO
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from neurolens import results, storage
from test_m3g_report import done_job


def canonical(x, y):
    return tuple(sorted(str(uuid.UUID(str(value))) for value in (x, y)))


def comparison_key(a, b):
    return f"results/{a}_{b}.report.json"


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
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for number, frame in enumerate(frames):
            archive.writestr(f"{number:04d}.jpg", frame)
        archive.writestr("words.json", json.dumps(list(words)))
    return buffer.getvalue()


def put_result(aws, job_id, duration=3):
    result = make_result(duration, frames=duration)
    assert storage.put_result(aws.s3, aws.bucket, job_id, result) is True
    return result


def put_frames(aws, job_id, count, words=()):
    frames = [b"\xff\xd8" + bytes([number]) for number in range(count)]
    storage.put_frames(aws.s3, aws.bucket, job_id, frames_zip(frames, words))
    return frames


def set_comparison(pg, a, b, state="writing", *, tries=1, error=None):
    pg.conn.execute(
        "INSERT INTO comparisons (job_a, job_b, state, tries, started_at, error) "
        "VALUES (%s::uuid, %s::uuid, %s, %s, now(), %s) "
        "ON CONFLICT (job_a, job_b) DO UPDATE SET state = EXCLUDED.state, "
        "tries = EXCLUDED.tries, started_at = EXCLUDED.started_at, error = EXCLUDED.error",
        (a, b, state, tries, error),
    )


def comparison(pg, a, b):
    return pg.one("SELECT * FROM comparisons WHERE job_a = %s::uuid AND job_b = %s::uuid", a, b)


def writing_pair(aws, db, pg, durations=(3, 3)):
    first, _ = done_job(db)
    second, _ = done_job(db)
    for job_id, duration in ((first, durations[0]), (second, durations[1])):
        put_result(aws, job_id, duration)
        put_frames(aws, job_id, duration)
    a, b = canonical(first, second)
    duration_by_job = {first: durations[0], second: durations[1]}
    set_comparison(pg, a, b, "writing", tries=1)
    return a, b, duration_by_job[a], duration_by_job[b]


def write_comparison(aws, db, a, b, client, cfg):
    from neurolens.lambdas import report

    report.write_comparison(a, b, db, aws.s3, aws.bucket, client, cfg)


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


def answer(value, *, stop_reason="end_turn"):
    return text_response(json.dumps(value), stop_reason=stop_reason)


def valid_comparison(duration_a=3, duration_b=3):
    end_a = min(1, duration_a - 1)
    end_b = min(1, duration_b - 1)
    return {
        "headline": "A draws the stronger response",
        "summary": "A holds attention; B falls away in the middle.",
        "differences": [
            {
                "a": {"start": 0, "end": end_a},
                "b": {"start": 0, "end": end_b},
                "what_differs": "A opens on a face.",
                "brain_response": "A's visual response rises above B's.",
            }
        ],
        "a_does_better": ["A 0-1 s"],
        "b_does_better": ["B 2-3 s"],
        "suggestions": ["Keep A's opening."],
    }


def object_exists(aws, key):
    try:
        aws.s3.head_object(Bucket=aws.bucket, Key=key)
    except ClientError as err:
        assert err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound")
        return False
    return True


def stored(aws, a, b):
    return json.loads(aws.s3.get_object(Bucket=aws.bucket, Key=comparison_key(a, b))["Body"].read())


def test_success_stores_the_comparison_then_marks_it_ready(aws, db, pg, make_cfg):
    a, b, duration_a, duration_b = writing_pair(aws, db, pg)
    client = FakeClient(answer(valid_comparison(duration_a, duration_b)))
    cfg = make_cfg(report={"model": "test-report-model", "max_tokens": 4321, "max_tries": 3})

    write_comparison(aws, db, a, b, client, cfg)

    row = comparison(pg, a, b)
    assert (row["state"], row["error"]) == ("ready", None)
    value = stored(aws, a, b)
    assert value["headline"] == "A draws the stronger response"
    assert value["summary"] == "A holds attention; B falls away in the middle."
    assert value["differences"] == valid_comparison(duration_a, duration_b)["differences"]
    assert value["a_does_better"] == ["A 0-1 s"]
    assert value["b_does_better"] == ["B 2-3 s"]
    assert value["suggestions"] == ["Keep A's opening."]
    assert value["model"] == cfg["report"]["model"]
    assert value["created_at"].endswith("Z")
    assert value["usage"] == {"input_tokens": 11, "output_tokens": 22}
    assert len(client.calls) == 1


@pytest.mark.parametrize("state", [None, "ready", "failed"])
def test_nothing_happens_unless_the_comparison_is_writing(aws, db, pg, make_cfg, state):
    first, _ = done_job(db)
    second, _ = done_job(db)
    for job_id in (first, second):
        put_result(aws, job_id)
        put_frames(aws, job_id, 3)
    a, b = canonical(first, second)
    if state is not None:
        set_comparison(pg, a, b, state, tries=1)
    client = FakeClient(answer(valid_comparison()))

    write_comparison(
        aws, db, a, b, client, make_cfg(report={"model": "m", "max_tokens": 1, "max_tries": 3})
    )

    assert client.calls == []
    assert not object_exists(aws, comparison_key(a, b))
    if state is None:
        assert pg.rows("SELECT * FROM comparisons") == []
    else:
        assert comparison(pg, a, b)["state"] == state


@pytest.mark.parametrize("which", ["a", "b"])
@pytest.mark.parametrize("missing", ["result", "frames"])
def test_either_missing_input_fails_without_calling_bedrock(aws, db, pg, make_cfg, which, missing):
    a, b, duration_a, duration_b = writing_pair(aws, db, pg)
    target = a if which == "a" else b
    key = f"results/{target}.json" if missing == "result" else f"results/{target}.frames"
    aws.s3.delete_object(Bucket=aws.bucket, Key=key)
    client = FakeClient(answer(valid_comparison(duration_a, duration_b)))

    write_comparison(
        aws, db, a, b, client, make_cfg(report={"model": "m", "max_tokens": 1, "max_tries": 3})
    )

    row = comparison(pg, a, b)
    assert (row["state"], row["error"]) == ("failed", "missing_input")
    assert client.calls == []
    assert not object_exists(aws, comparison_key(a, b))


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("refusal", "refused"),
        ("truncated", "truncated"),
        ("bad_json", "bad_answer"),
        ("a_out_of_range", "bad_answer"),
        ("b_out_of_range", "bad_answer"),
        ("both_null", "bad_answer"),
        ("past_the_shorter_end", "bad_answer"),
        ("unfinished_summary", "bad_answer"),
    ],
)
def test_a_bad_answer_fails_with_its_code_and_stores_nothing(aws, db, pg, make_cfg, kind, expected):
    durations = (3, 5) if kind == "past_the_shorter_end" else (3, 3)
    a, b, duration_a, duration_b = writing_pair(aws, db, pg, durations)
    value = valid_comparison(duration_a, duration_b)
    if kind == "refusal":
        response = answer(value, stop_reason="refusal")
    elif kind == "truncated":
        response = answer(value, stop_reason="max_tokens")
    elif kind == "bad_json":
        response = text_response("this is not JSON")
    elif kind == "unfinished_summary":
        value["summary"] = "Sound carries it,"
        response = answer(value)
    else:
        difference = value["differences"][0]
        if kind == "a_out_of_range":
            difference["a"] = {"start": 0, "end": duration_a}
        elif kind == "b_out_of_range":
            difference["b"] = {"start": 0, "end": duration_b}
        elif kind == "both_null":
            difference["a"] = None
            difference["b"] = None
        else:
            shorter, longer = ("a", duration_b) if duration_a < duration_b else ("b", duration_a)
            difference[shorter] = {"start": 0, "end": longer - 1}
        response = answer(value)
    client = FakeClient(response)

    write_comparison(
        aws, db, a, b, client, make_cfg(report={"model": "m", "max_tokens": 1, "max_tries": 3})
    )

    row = comparison(pg, a, b)
    assert (row["state"], row["error"]) == ("failed", expected)
    assert not object_exists(aws, comparison_key(a, b))


def test_a_bedrock_error_fails_with_bedrock_error(aws, db, pg, make_cfg):
    a, b, duration_a, duration_b = writing_pair(aws, db, pg)
    client = FakeClient(error=RuntimeError("bedrock is down"))

    write_comparison(
        aws, db, a, b, client, make_cfg(report={"model": "m", "max_tokens": 1, "max_tries": 3})
    )

    row = comparison(pg, a, b)
    assert (row["state"], row["error"]) == ("failed", "bedrock_error")
    assert not object_exists(aws, comparison_key(a, b))


@pytest.mark.parametrize("deleted", ["a", "b"])
def test_a_job_deleted_during_the_call_loses_the_comparison_object(aws, db, pg, make_cfg, deleted):
    a, b, duration_a, duration_b = writing_pair(aws, db, pg)
    target = a if deleted == "a" else b

    def delete_job():
        pg.conn.execute("UPDATE jobs SET deleted_at = now() WHERE job_id = %s::uuid", (target,))

    client = FakeClient(answer(valid_comparison(duration_a, duration_b)), on_create=delete_job)

    write_comparison(
        aws, db, a, b, client, make_cfg(report={"model": "m", "max_tokens": 1, "max_tries": 3})
    )

    assert not object_exists(aws, comparison_key(a, b))
