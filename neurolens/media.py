"""Video measurement with ffprobe (docs/M3e_spec.md §3).

Standard library only, so the dispatcher Lambda can import it without numpy or the model code.
"""

import json
import math
import subprocess


class UnreadableVideo(ValueError):
    """ffprobe could not produce a duration: not a video, corrupt, or no duration in the header."""


def probe_duration(path, timeout_s=30):
    """Video length in seconds, measured with ffprobe from the file's packets, not the length its
    header declares (M1 §1: whoever made the file wrote the header, so it could understate the
    length). Raises UnreadableVideo if unreadable."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
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
        raise UnreadableVideo(f"ffprobe timed out on {path}") from err
    try:
        info = json.loads(result.stdout)
        has_video = any(s.get("codec_type") == "video" for s in info.get("streams", []))
        starts, ends = [], []
        for packet in info.get("packets", []):
            if "pts_time" not in packet:
                continue  # no timestamp (a raw stream's packets, for example)
            start = float(packet["pts_time"])
            starts.append(start)
            ends.append(start + float(packet.get("duration_time", 0)))
    except (ValueError, KeyError, TypeError, AttributeError) as err:  # JSONDecodeError too
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
