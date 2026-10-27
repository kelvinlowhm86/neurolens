"""
The report LLM, behind one switch (REPORT_LLM):

  openai   local machines: OpenAI API directly, OPENAI_API_KEY + OPENAI_MODEL from .env
  bedrock  AWS: Claude via Amazon Bedrock (AnthropicBedrockMantle) with the worker's IAM role
  mock     fixed, data-driven text — tests and keyless dev runs

Both real providers get the same system instruction, data, images and JSON schema, and must return JSON
that validates as ReportContent.
"""

import base64
import json
import logging
from pathlib import Path

from neurolens_common.config import Settings

from .moments import Moment
from .prompt import SYSTEM_INSTRUCTION, build_user_text
from .schema import REPORT_SCHEMA, ReportContent

logger = logging.getLogger("neurolens.worker.report.llm")


class ReportLLMError(Exception):
    """The LLM could not produce a usable report (refusal, bad JSON, API error)."""


def _b64(path: Path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode()


def _media_type(path: Path) -> str:
    return "image/png" if path.suffix.lower() == ".png" else "image/jpeg"


def write_report(cfg: Settings, payload: dict, images: list[tuple[str, Path]],
                 moments: list[Moment]) -> tuple[ReportContent, str]:
    """Returns (validated report content, model name used)."""
    if cfg.report_llm == "mock":
        return _mock(payload, moments), "mock"
    if cfg.report_llm == "openai":
        text, model = _openai(cfg, payload, images)
    elif cfg.report_llm == "bedrock":
        text, model = _bedrock(cfg, payload, images)
    else:
        raise ReportLLMError(f"REPORT_LLM={cfg.report_llm!r} does not write reports")
    try:
        return ReportContent.model_validate(json.loads(text)), model
    except (json.JSONDecodeError, ValueError) as e:
        raise ReportLLMError(f"{model} returned JSON that does not match the report schema: {e}") from e


# ─── OpenAI (local machines) ─────────────────────────────────────────────────


def _openai(cfg: Settings, payload: dict, images: list[tuple[str, Path]]) -> tuple[str, str]:
    from openai import OpenAI, OpenAIError

    if not cfg.openai_api_key:
        raise ReportLLMError("OPENAI_API_KEY is not set (put it in .env)")
    content: list[dict] = [{"type": "text", "text": build_user_text(payload)}]
    for label, path in images:
        content.append({"type": "text", "text": label})
        content.append({"type": "image_url", "image_url": {
            "url": f"data:{_media_type(path)};base64,{_b64(path)}", "detail": "high"}})
    try:
        resp = OpenAI(api_key=cfg.openai_api_key).chat.completions.create(
            model=cfg.openai_model,
            messages=[{"role": "system", "content": SYSTEM_INSTRUCTION}, {"role": "user", "content": content}],
            response_format={"type": "json_schema", "json_schema": {
                "name": "neurolens_report", "schema": REPORT_SCHEMA, "strict": True}},
        )
    except OpenAIError as e:
        raise ReportLLMError(f"OpenAI request failed: {e}") from e
    msg = resp.choices[0].message
    if getattr(msg, "refusal", None):
        raise ReportLLMError(f"{cfg.openai_model} declined: {msg.refusal}")
    return msg.content or "", cfg.openai_model


# ─── Claude on Amazon Bedrock (AWS) ──────────────────────────────────────────


def _bedrock(cfg: Settings, payload: dict, images: list[tuple[str, Path]]) -> tuple[str, str]:
    import anthropic
    from anthropic import AnthropicBedrockMantle

    content: list[dict] = [{"type": "text", "text": build_user_text(payload)}]
    for label, path in images:
        content.append({"type": "text", "text": label})
        content.append({"type": "image", "source": {"type": "base64", "media_type": _media_type(path),
                                                    "data": _b64(path)}})
    client = AnthropicBedrockMantle(aws_region=cfg.bedrock_region or cfg.aws_region)
    try:
        # Opus 5.5 always thinks; effort sets how hard (its default is "medium", so set it explicitly).
        resp = client.messages.create(
            model=cfg.bedrock_model,
            max_tokens=16000,
            system=SYSTEM_INSTRUCTION,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": cfg.report_effort,
                           "format": {"type": "json_schema", "schema": REPORT_SCHEMA}},
        )
    except anthropic.APIStatusError as e:
        raise ReportLLMError(f"Bedrock request failed ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise ReportLLMError(f"Could not reach Bedrock: {e}") from e
    if resp.stop_reason == "refusal":
        category = resp.stop_details.category if resp.stop_details else None
        raise ReportLLMError(f"{cfg.bedrock_model} declined to write the report (category: {category})")
    if resp.stop_reason == "max_tokens":
        raise ReportLLMError("The report was cut off (max_tokens)")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return text, cfg.bedrock_model


# ─── Mock (tests, no key) ────────────────────────────────────────────────────


def _mock(payload: dict, moments: list[Moment]) -> ReportContent:
    by_reason = {m.reason: m for m in moments}
    first, last = moments[0], moments[-1]
    peak = by_reason.get("overall peak", first)
    low = by_reason.get("overall low point", by_reason.get("sharpest drop", last))
    return ReportContent.model_validate({
        "headline": f"Engagement peaks at {peak.t}s; tighten the stretch around {low.t}s.",
        "summary": ("This is a sample report generated without an AI model (REPORT_LLM=mock). "
                    "It shows the layout: real reports describe what is on screen at each key frame and "
                    "tie it to the predicted brain response."),
        "hook": {"assessment": f"The opening ({first.t}s) is shown for reference.", "frame_id": first.frame_id},
        "strengths": [{"title": "Strongest moment", "seconds": f"{peak.t}s", "frame_id": peak.frame_id,
                       "evidence": "Attention hits its highest point in your video here.",
                       "why_it_works": "Several regions respond at once."}],
        "improvements": [{"title": "Weakest stretch", "seconds": f"{low.t}s", "frame_id": low.frame_id,
                          "issue": "Attention drops to its lowest point in your video here.",
                          "recommendation": "Consider trimming or replacing this shot.", "priority": "medium"}],
        "audio": {"verdict": "neutral", "explanation": "Sample text.", "recommendation": "Sample text."},
        "frame_notes": [{"frame_id": m.frame_id, "on_screen": m.reason, "brain_response": "Sample text."}
                        for m in moments],
        "next_steps": ["Review the weakest stretch", "Re-run after editing"],
    })
