"""
Step after TRIBE: an LLM reads the per-second results, the transcript and key frames, and the worker lays
its findings out as a PDF (frames + line charts + what works / what to improve).

    pdf_path, model = generate_report(cfg, result, video_path, transcript, work_dir)
"""

from pathlib import Path

from neurolens_common.config import Settings
from neurolens_common.schemas import REGIONS

from .llm import ReportLLMError, write_report
from .moments import pick_moments
from .pdf import build_pdf
from .visuals import grab_frames, overall_chart, region_charts

__all__ = ["ReportLLMError", "generate_report"]

SHORT = {"ffa_faces": "faces", "eba_bodies": "bodies", "ppa_scenes": "scenes", "sts_social": "social",
         "auditory": "sound"}


def _transcript_lines(words: list[dict], window_s: float = 4.0) -> list[dict]:
    """Words → short timed lines, so the model can line dialogue up with seconds."""
    lines, current, start = [], [], None
    for w in sorted(words, key=lambda w: w["start"]):
        if start is None:
            start = w["start"]
        if w["start"] - start > window_s and current:
            lines.append({"start_s": round(start, 1), "text": " ".join(current)})
            current, start = [], w["start"]
        current.append(str(w["text"]).strip())
    if current:
        lines.append({"start_s": round(start, 1), "text": " ".join(current)})
    return lines


def build_payload(result: dict, transcript: list[dict], moments) -> dict:
    per_second = []
    for i, t in enumerate(result["timesteps"]):
        row = {"t": t["t"], "overall": t["engagement_overall"]}
        row.update({SHORT[r]: t["regions"][r] for r in REGIONS})
        row["audio_on"] = t["auditory_with_audio"]
        row["audio_off"] = t["auditory_without_audio"]
        row["overall_raw"] = result.get("raw", {}).get("engagement_overall", [None] * (i + 1))[i]
        per_second.append(row)
    return {
        "filename": result.get("filename"),
        "duration_s": result["duration_seconds"],
        "per_second": per_second,
        "summary": result.get("summary", {}),
        "transcript": _transcript_lines(transcript) if transcript else "no speech detected",
        "key_frames": [{"frame_id": m.frame_id, "t": m.t, "why_selected": m.reason} for m in moments],
    }


def generate_report(cfg: Settings, result: dict, video: Path, transcript: list[dict],
                    work_dir: Path) -> tuple[Path, str]:
    work_dir.mkdir(parents=True, exist_ok=True)
    moments = pick_moments(result, max_frames=cfg.report_max_frames)
    if not moments:
        raise ReportLLMError("No timesteps to report on")
    frames = grab_frames(video, moments, work_dir / "frames")
    overall_png = overall_chart(result, moments, work_dir / "overall.png")
    regions_png = region_charts(result, work_dir / "regions.png")

    images = [(f"Key frame F{m.frame_id} at {m.t}s ({m.reason})", frames[m.frame_id])
              for m in moments if m.frame_id in frames]
    images.append(("Overall engagement chart (dashed lines = key frames)", overall_png))
    content, model = write_report(cfg, build_payload(result, transcript, moments), images, moments)

    pdf = build_pdf(work_dir / "report.pdf", content=content, filename=result.get("filename") or video.name,
                    duration_s=result["duration_seconds"], moments=moments, frames=frames,
                    overall_png=overall_png, regions_png=regions_png, model=model)
    return pdf, model
