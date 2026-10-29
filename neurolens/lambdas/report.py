"""The report Lambda (docs/M3g_spec.md §5, docs/M3h_spec.md §5): write a video's AI report
or a comparison of two videos from Bedrock.

The report or comparison is claimed by the web app before this runs. Bedrock is called only
while the state is `writing`; every failure finishes that state, so an answer is stored at most
once and a broken answer is never stored.
"""

import json
import logging
import os
from datetime import UTC, datetime

from neurolens import billing, lambdas, report_prompt, settings, storage

logger = logging.getLogger(__name__)

BEDROCK_API_VERSION = "bedrock-2023-05-31"
# Sent as the structured-output format; the lengths the prompt asks for are guidance only (M3g §5d).
_TEXT_LIST = {"type": "array", "items": {"type": "string"}}
REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "integer"},
                    "end": {"type": "integer"},
                    "what_happens": {"type": "string"},
                    "brain_response": {"type": "string"},
                    "suggestion": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
                "required": [
                    "start",
                    "end",
                    "what_happens",
                    "brain_response",
                    "suggestion",
                ],
                "additionalProperties": False,
            },
        },
        "strengths": _TEXT_LIST,
        "improvements": _TEXT_LIST,
    },
    "required": ["headline", "summary", "moments", "strengths", "improvements"],
    "additionalProperties": False,
}

_SPAN = {
    "anyOf": [
        {
            "type": "object",
            "properties": {"start": {"type": "integer"}, "end": {"type": "integer"}},
            "required": ["start", "end"],
            "additionalProperties": False,
        },
        {"type": "null"},
    ]
}
COMPARISON_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "differences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "a": _SPAN,
                    "b": _SPAN,
                    "what_differs": {"type": "string"},
                    "brain_response": {"type": "string"},
                },
                "required": ["a", "b", "what_differs", "brain_response"],
                "additionalProperties": False,
            },
        },
        "a_does_better": _TEXT_LIST,
        "b_does_better": _TEXT_LIST,
        "suggestions": _TEXT_LIST,
    },
    "required": [
        "headline",
        "summary",
        "differences",
        "a_does_better",
        "b_does_better",
        "suggestions",
    ],
    "additionalProperties": False,
}


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_text(value):
    return isinstance(value, str)


def _report_problem(answer, duration):
    """Why the answer cannot be shown as a report, or None (M3g §5d). Only what the page relies
    on is checked, never lengths or counts: a long report is still a report."""
    if not isinstance(answer, dict):
        return "not a JSON object"
    for name in ("headline", "summary"):
        if not _is_text(answer.get(name)):
            return f"{name} is not text"
    for name in ("strengths", "improvements", "moments"):
        if not isinstance(answer.get(name), list):
            return f"{name} is not a list"
    for name in ("strengths", "improvements"):
        if not all(_is_text(item) for item in answer[name]):
            return f"{name} holds something other than text"
    for number, moment in enumerate(answer["moments"], 1):
        if not isinstance(moment, dict):
            return f"moment {number} is not an object"
        start, end = moment.get("start"), moment.get("end")
        if not (_is_int(start) and _is_int(end) and 0 <= start <= end < duration):
            return f"moment {number} runs {start!r}-{end!r}, outside 0-{duration - 1} or reversed"
        if not _is_text(moment.get("what_happens")) or not _is_text(moment.get("brain_response")):
            return f"moment {number} lacks its text"
        if moment.get("suggestion") is not None and not _is_text(moment.get("suggestion")):
            return f"moment {number} has a suggestion that is not text"
    return None


def _span_problem(span, duration, name):
    if span is None:
        return None
    if not isinstance(span, dict):
        return f"{name} is not a time span"
    start, end = span.get("start"), span.get("end")
    if not (_is_int(start) and _is_int(end) and 0 <= start <= end < duration):
        return f"{name} runs {start!r}-{end!r}, outside 0-{duration - 1} or reversed"
    return None


def _comparison_problem(answer, duration_a, duration_b):
    """Why the answer cannot be shown as a comparison, or None (M3h §5d). Each span is checked
    against its own video, and only what the page relies on is checked."""
    if not isinstance(answer, dict):
        return "not a JSON object"
    for name in ("headline", "summary"):
        if not _is_text(answer.get(name)):
            return f"{name} is not text"
    for name in ("differences", "a_does_better", "b_does_better", "suggestions"):
        if not isinstance(answer.get(name), list):
            return f"{name} is not a list"
    for name in ("a_does_better", "b_does_better", "suggestions"):
        if not all(_is_text(item) for item in answer[name]):
            return f"{name} holds something other than text"
    for number, difference in enumerate(answer["differences"], 1):
        if not isinstance(difference, dict):
            return f"difference {number} is not an object"
        if "a" not in difference or "b" not in difference:
            return f"difference {number} lacks a time span"
        left, right = difference["a"], difference["b"]
        if left is None and right is None:
            return f"difference {number} has no time in either video"
        problem = _span_problem(left, duration_a, f"difference {number} in A")
        if problem:
            return problem
        problem = _span_problem(right, duration_b, f"difference {number} in B")
        if problem:
            return problem
        if not _is_text(difference.get("what_differs")) or not _is_text(
            difference.get("brain_response")
        ):
            return f"difference {number} lacks its text"
    return None


def _text_of(response):
    """The first text block of the answer, or None."""
    for block in response.content or []:
        if getattr(block, "type", None) == "text":
            return getattr(block, "text", None)
    return None


def _usage(response):
    usage = response.usage
    return usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)


def _bedrock_client(region):
    """The Anthropic SDK's Bedrock client; imported here so importers need no SDK (M3g §5c)."""
    from anthropic import AnthropicBedrock

    return AnthropicBedrock(aws_region=region, timeout=240, max_retries=2)


def _call_model(client, cfg, system, content, schema):
    """Ask Bedrock, parse the JSON and build the stored extras, shared by both paths (M3h §5a).

    Returns (answer, extras, error); error is one of the M3g failure codes, or None.
    """
    report_cfg = cfg["report"]
    try:
        response = client.messages.create(
            model=report_cfg["model"],
            max_tokens=report_cfg["max_tokens"],
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
            # The SDK's default API version is refused by InvokeModel models (M3g §5c).
            extra_body={"anthropic_version": BEDROCK_API_VERSION},
        )
    except Exception:
        logger.exception("Bedrock failed")
        return None, None, "bedrock_error"

    if response.stop_reason == "refusal":
        return None, None, "refused"
    if response.stop_reason == "max_tokens":
        return None, None, "truncated"
    text = _text_of(response)
    try:
        answer = json.loads(text) if text is not None else None
    except (TypeError, ValueError):
        answer = None
    if answer is None:
        return None, None, "bad_answer"
    extras = {
        "model": report_cfg["model"],
        "created_at": storage.utc_text(datetime.now(UTC)),
        "usage": _usage(response),
    }
    return answer, extras, None


def _read_frames(s3, bucket, job_id):
    try:
        return storage.get_frames(s3, bucket, job_id)
    except Exception:
        logger.exception(f"{job_id}: could not read the frames")
        return None, None


def write_report(job_id, db, s3, bucket, client, cfg):
    """Write the claimed report for job_id. Never raises: every failure finishes the report."""
    try:
        with db.transaction() as tx:
            rows = tx.execute(
                "SELECT report_state FROM jobs WHERE job_id = CAST(:job_id AS uuid)",
                {"job_id": job_id},
            )
        if not rows or rows[0]["report_state"] != "writing":
            logger.info(f"{job_id}: no report is writing, nothing to do")
            return

        result = storage.get_result(s3, bucket, job_id)
        frames, words = _read_frames(s3, bucket, job_id) if result else (None, None)
        if result is None or frames is None:
            billing.finish_report(db, job_id, "failed", "missing_input")
            logger.warning(f"{job_id}: report failed: missing input")
            return

        system, content = report_prompt.build_request(result, frames, words)
        answer, extras, error = _call_model(client, cfg, system, content, REPORT_SCHEMA)
        if error:
            billing.finish_report(db, job_id, "failed", error)
            logger.warning(f"{job_id}: report failed: {error}")
            return
        problem = _report_problem(answer, result["duration_seconds"])
        if problem:
            billing.finish_report(db, job_id, "failed", "bad_answer")
            logger.warning(f"{job_id}: report was not a usable answer: {problem}")
            return

        stored = {**answer, **extras}
        key = f"results/{job_id}.report.json"
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(stored).encode(),
            ContentType="application/json",
        )
        if not billing.finish_report(db, job_id, "ready"):
            logger.warning(f"{job_id}: the job was deleted meanwhile; removing its report")
            s3.delete_object(Bucket=bucket, Key=key)
    except Exception:
        logger.exception(f"{job_id}: report failed unexpectedly")
        billing.finish_report(db, job_id, "failed", "error")


def write_comparison(job_a, job_b, db, s3, bucket, client, cfg):
    """Write the claimed comparison for the canonical pair. Never raises (M3h §5a)."""
    job_a, job_b = storage.comparison_pair(job_a, job_b)
    try:
        row = billing.get_comparison(db, job_a, job_b)
        if not row or row["state"] != "writing":
            logger.info(f"{job_a}/{job_b}: no comparison is writing, nothing to do")
            return

        result_a = storage.get_result(s3, bucket, job_a)
        result_b = storage.get_result(s3, bucket, job_b)
        frames_a, words_a = _read_frames(s3, bucket, job_a) if result_a else (None, None)
        frames_b, words_b = _read_frames(s3, bucket, job_b) if result_b else (None, None)
        if result_a is None or result_b is None or frames_a is None or frames_b is None:
            billing.finish_comparison(db, job_a, job_b, "failed", "missing_input")
            logger.warning(f"{job_a}/{job_b}: comparison failed: missing input")
            return

        system, content = report_prompt.build_comparison_request(
            (result_a, frames_a, words_a), (result_b, frames_b, words_b)
        )
        answer, extras, error = _call_model(client, cfg, system, content, COMPARISON_SCHEMA)
        if error:
            billing.finish_comparison(db, job_a, job_b, "failed", error)
            logger.warning(f"{job_a}/{job_b}: comparison failed: {error}")
            return
        problem = _comparison_problem(
            answer, result_a["duration_seconds"], result_b["duration_seconds"]
        )
        if problem:
            billing.finish_comparison(db, job_a, job_b, "failed", "bad_answer")
            logger.warning(f"{job_a}/{job_b}: comparison was not a usable answer: {problem}")
            return

        stored = {**answer, **extras}
        key = storage.comparison_key(job_a, job_b)
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(stored).encode(),
            ContentType="application/json",
        )
        if not billing.finish_comparison(db, job_a, job_b, "ready"):
            logger.warning(f"{job_a}/{job_b}: a job was deleted meanwhile; removing its comparison")
            s3.delete_object(Bucket=bucket, Key=key)
    except Exception:
        logger.exception(f"{job_a}/{job_b}: comparison failed unexpectedly")
        billing.finish_comparison(db, job_a, job_b, "failed", "error")


def handler(event, context):
    lambdas.setup_logging()
    cfg = settings.load_settings()
    db = lambdas.database()
    s3 = lambdas.s3_client()
    bucket = os.environ["NEUROLENS_S3_BUCKET"]
    client = _bedrock_client(cfg["aws"]["region"])
    pair = event.get("pair") if isinstance(event, dict) else None
    if pair:
        write_comparison(pair[0], pair[1], db, s3, bucket, client, cfg)
    else:
        write_report(event["job_id"], db, s3, bucket, client, cfg)
