"""ffprobe / ffmpeg helpers."""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import InvalidMedia


@dataclass(frozen=True)
class Probe:
    duration_s: float
    has_video: bool
    has_audio: bool
    width: int | None
    height: int | None


def probe(path: Path) -> Probe:
    """Read duration and streams. Raises InvalidMedia if it is not a readable video."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise InvalidMedia(f"ffprobe could not read the file: {result.stderr.strip()[:200]}")
    info = json.loads(result.stdout or "{}")
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise InvalidMedia("The file has no video stream")
    try:
        duration = float(info["format"]["duration"])
    except (KeyError, ValueError):
        raise InvalidMedia("Could not determine the video duration") from None
    if duration <= 0:
        raise InvalidMedia("The video has zero length")
    return Probe(
        duration_s=duration,
        has_video=True,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        width=video.get("width"),
        height=video.get("height"),
    )


def strip_audio(src: Path, dst: Path) -> None:
    """Copy the video stream only (no re-encode) — the silent-baseline input."""
    result = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-an", "-c:v", "copy", str(dst)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed to strip audio: {result.stderr.strip()[:300]}")


def make_test_clip(dst: Path, seconds: float = 4, audio: bool = True) -> Path:
    """Synthetic clip (moving test pattern + tone). Used for warm-up and tests."""
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=25:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(dst)]
    subprocess.run(cmd, check=True, capture_output=True)
    return dst
