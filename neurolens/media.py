"""Video measurement with ffprobe (docs/M3e_spec.md §3).

Standard library only, so the dispatcher Lambda can import it without numpy or the model code.
"""

import json
import math
import subprocess

INVALID_DATA = "Invalid data found when processing input"


class UnreadableVideo(ValueError):
    """ffprobe read the file and found no usable video: not a video, corrupt, or no duration."""


class ProbeFailed(RuntimeError):
    """ffprobe could not fetch or finish the file: retry it rather than blame the video."""


def _stderr_text(err):
    text = err.stderr
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode(errors="replace")
    return text.strip()


def _probe_failure(message, stderr):
    return f"{message}: {stderr}" if stderr else message


def probe_duration(path, timeout_s=30):
    """Video length in seconds, measured with ffprobe from the file's packets, not the length its
    header declares (M1 §1: whoever made the file wrote the header, so it could understate the
    length). Raises UnreadableVideo when the file itself is at fault, ProbeFailed when ffprobe
    could not fetch or finish it."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_streams",
                "-show_entries",
                "packet=pts_time,duration_time",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as err:
        raise ProbeFailed(
            _probe_failure(f"ffprobe timed out after {timeout_s}s on {path}", _stderr_text(err))
        ) from err
    stderr = (result.stderr or "").strip()
    if result.returncode != 0:
        if INVALID_DATA in stderr:
            raise UnreadableVideo(_probe_failure(f"ffprobe could not read {path}", stderr))
        raise ProbeFailed(
            _probe_failure(
                f"ffprobe exited with status {result.returncode} on {path}",
                stderr or "no error text",
            )
        )
    try:
        info = json.loads(result.stdout)
    except ValueError as err:  # JSONDecodeError too
        raise ProbeFailed(
            _probe_failure(f"ffprobe gave no usable JSON for {path}", stderr)
        ) from err
    if not isinstance(info, dict):
        raise ProbeFailed(_probe_failure(f"ffprobe gave no usable JSON for {path}", stderr))
    try:
        has_video = any(s.get("codec_type") == "video" for s in info.get("streams", []))
        starts, ends = [], []
        for packet in info.get("packets", []):
            if "pts_time" not in packet:
                continue  # no timestamp (a raw stream's packets, for example)
            start = float(packet["pts_time"])
            starts.append(start)
            ends.append(start + float(packet.get("duration_time", 0)))
    except (ValueError, KeyError, TypeError, AttributeError) as err:
        raise UnreadableVideo(f"ffprobe could not read {path}") from err
    if not has_video:
        # e.g. an audio-only .mp4: it would pass the length check, then fail in the model
        raise UnreadableVideo(f"{path} has no video stream")
    if not starts:
        raise UnreadableVideo(f"ffprobe found no timestamped packets in {path}")
    # A negative start (AAC encoder priming) counts as 0, so an honest file measures what its
    # header says.
    duration = max(ends) - max(min(starts), 0.0)
    if not math.isfinite(duration):
        raise UnreadableVideo(f"ffprobe gave a non-finite duration for {path}")
    return duration
