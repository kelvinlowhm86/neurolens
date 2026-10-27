"""
What the report LLM must return. One JSON Schema serves both providers (OpenAI strict json_schema and
Claude output_config.format), so it sticks to the common subset: objects with every property required,
additionalProperties false, strings/integers/enums/arrays. Counts ("2-4 items") live in the prompt.
"""

from pydantic import BaseModel

AUDIO_VERDICTS = ["carries the video", "supports the visuals", "neutral", "works against the visuals"]
PRIORITIES = ["high", "medium", "low"]


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


STR = {"type": "string"}
INT = {"type": "integer"}

REPORT_SCHEMA = _obj({
    "headline": STR,
    "summary": STR,
    "hook": _obj({"assessment": STR, "frame_id": INT}),
    "strengths": {"type": "array", "items": _obj({
        "title": STR, "seconds": STR, "frame_id": INT, "evidence": STR, "why_it_works": STR})},
    "improvements": {"type": "array", "items": _obj({
        "title": STR, "seconds": STR, "frame_id": INT, "issue": STR, "recommendation": STR,
        "priority": {"type": "string", "enum": PRIORITIES}})},
    "audio": _obj({"verdict": {"type": "string", "enum": AUDIO_VERDICTS}, "explanation": STR,
                   "recommendation": STR}),
    "frame_notes": {"type": "array", "items": _obj({"frame_id": INT, "on_screen": STR, "brain_response": STR})},
    "next_steps": {"type": "array", "items": STR},
})


class Hook(BaseModel):
    assessment: str
    frame_id: int


class Strength(BaseModel):
    title: str
    seconds: str
    frame_id: int
    evidence: str
    why_it_works: str


class Improvement(BaseModel):
    title: str
    seconds: str
    frame_id: int
    issue: str
    recommendation: str
    priority: str


class Audio(BaseModel):
    verdict: str
    explanation: str
    recommendation: str


class FrameNote(BaseModel):
    frame_id: int
    on_screen: str
    brain_response: str


class ReportContent(BaseModel):
    headline: str
    summary: str
    hook: Hook
    strengths: list[Strength]
    improvements: list[Improvement]
    audio: Audio
    frame_notes: list[FrameNote]
    next_steps: list[str]
