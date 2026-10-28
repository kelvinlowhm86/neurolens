"""The report Lambda (docs/M3g_spec.md §5): write one video's AI report from Bedrock.

The job's report is claimed by the web app before this runs. Bedrock is called only while the
job's state is `writing`; every failure finishes that state, so a report is stored at most once
and a broken answer is never stored.
"""

import json
import logging
import os
from datetime import UTC, datetime

from neurolens import billing, lambdas, report_prompt, results, settings, storage
from neurolens.report_prompt import MAX_HEADLINE, MAX_LISTS, MAX_MOMENTS, MAX_SUMMARY, MIN_MOMENTS

logger = logging.getLogger(__name__)

KINDS = ("strong", "weak")
BEDROCK_API_VERSION = "bedrock-2023-05-31"
# Sent as the structured-output format. Bedrock's schema subset has no length or count limits, so
# those live in the prompt and in _valid_report (M3g §5d).
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
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "what_happens": {"type": "string"},
                    "brain_response": {"type": "string"},
                    "suggestion": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
                "required": [
                    "start",
                    "end",
                    "kind",
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


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_text(value, limit=None):
    return isinstance(value, str) and (limit is None or len(value) <= limit)


def _valid_report(answer, duration):
    """True only for a complete answer inside the schema's limits (M3g §5d)."""
    if not isinstance(answer, dict):
        return False
    if not _is_text(answer.get("headline"), MAX_HEADLINE):
        return False
    if not _is_text(answer.get("summary"), MAX_SUMMARY):
        return False
    for name in ("strengths", "improvements"):
        items = answer.get(name)
        if not isinstance(items, list) or len(items) > MAX_LISTS:
            return False
        if not all(isinstance(item, str) for item in items):
            return False
    moments = answer.get("moments")
    if not isinstance(moments, list) or not MIN_MOMENTS <= len(moments) <= MAX_MOMENTS:
        return False
    for moment in moments:
        if not isinstance(moment, dict):
            return False
        start, end = moment.get("start"), moment.get("end")
        if not (_is_int(start) and _is_int(end) and 0 <= start <= end < duration):
            return False
        if moment.get("kind") not in KINDS:
            return False
        if not _is_text(moment.get("what_happens")) or not _is_text(moment.get("brain_response")):
            return False
        if moment.get("suggestion") is not None and not _is_text(moment.get("suggestion")):
            return False
    return True


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
        try:
            frames, words = storage.get_frames(s3, bucket, job_id) if result else ([], [])
        except Exception:
            logger.exception(f"{job_id}: could not read the frames")
            frames = None
        if result is None or frames is None:
            billing.finish_report(db, job_id, "failed", "missing_input")
            logger.warning(f"{job_id}: report failed: missing input")
            return

        system, content = report_prompt.build_request(
            result, frames, words, results.summarise(result)
        )
        report_cfg = cfg["report"]
        try:
            response = client.messages.create(
                model=report_cfg["model"],
                max_tokens=report_cfg["max_tokens"],
                system=system,
                messages=[{"role": "user", "content": content}],
                output_config={"format": {"type": "json_schema", "schema": REPORT_SCHEMA}},
                # The SDK's default API version is refused by InvokeModel models (M3g §5c).
                extra_body={"anthropic_version": BEDROCK_API_VERSION},
            )
        except Exception:
            logger.exception(f"{job_id}: Bedrock failed")
            billing.finish_report(db, job_id, "failed", "bedrock_error")
            return

        if response.stop_reason == "refusal":
            billing.finish_report(db, job_id, "failed", "refused")
            logger.warning(f"{job_id}: report refused")
            return
        if response.stop_reason == "max_tokens":
            billing.finish_report(db, job_id, "failed", "truncated")
            logger.warning(f"{job_id}: report truncated")
            return
        text = _text_of(response)
        try:
            answer = json.loads(text) if text is not None else None
        except (TypeError, ValueError):
            answer = None
        if not _valid_report(answer, result["duration_seconds"]):
            billing.finish_report(db, job_id, "failed", "bad_answer")
            logger.warning(f"{job_id}: report was not a usable answer")
            return

        stored = {
            **answer,
            "model": report_cfg["model"],
            "created_at": storage.utc_text(datetime.now(UTC)),
            "usage": _usage(response),
        }
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


def handler(event, context):
    lambdas.setup_logging()
    cfg = settings.load_settings()
    write_report(
        event["job_id"],
        lambdas.database(),
        lambdas.s3_client(),
        os.environ["NEUROLENS_S3_BUCKET"],
        _bedrock_client(cfg["aws"]["region"]),
        cfg,
    )
