"""A stored result's summary and CSV, and the response config check (M3d §6-§7). Pure Python:
the web tier imports it."""

import csv
import io
import json
import math
import re
from urllib.parse import parse_qs, urlsplit

SERIES = [
    "faces",
    "bodies",
    "scenes",
    "visual",
    "sound",
    "speech",
    "social",
    "see",
    "hear",
    "understand",
    "overall",
]
GROUPS = ["see", "hear", "understand"]
COLUMNS = ["t", *SERIES, *(f"muted_{name}" for name in SERIES)]


def _cell(value):
    """Exactly the number as stored in the result JSON; an empty cell for None."""
    return "" if value is None else json.dumps(value)


def _number(value):
    """A finite number; bool is an int in Python but not a number here."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def validate_response_config(cfg):
    """Raise ValueError unless cfg is a usable `response` block (M3d §6)."""
    if not isinstance(cfg, dict):
        raise ValueError("The response config must be an object.")
    if "colour_range" not in cfg:
        raise ValueError("Missing response setting: colour_range")
    if not _number(cfg["colour_range"]) or cfg["colour_range"] <= 0:
        raise ValueError("response.colour_range must be a finite number greater than 0.")


def _audio_dependence(overall, muted):
    length = min(len(overall), len(muted))
    start = 0 if len(overall) == 1 else 1
    if start >= length:
        return None
    denominator = sum(max(value, 0.0) for value in overall[start:length])
    if denominator < 1e-6:
        return None
    numerator = sum(max(value, 0.0) for value in muted[start:length])
    return round(min(max(1 - numerator / denominator, 0.0), 1.0), 2)


def summarise(result):
    """The audio dependence served with a result (M3d §6), computed at serve time."""
    overall = result["signals"]["overall"]
    muted = result["muted"]["overall"]
    return {"audio_dependence": _audio_dependence(overall, muted)}


RETENTION_COLUMNS = ("elapsedVideoTimeRatio", "audienceWatchRatio")


def parse_retention_report(text):
    """The (elapsed_ratio, watch_ratio) rows of a YouTube Analytics `reports.query` response.

    The text is a JSON object whose `columnHeaders` name both columns (other columns are
    ignored); `rows` holds 2 to 1000 rows. elapsedVideoTimeRatio is in 0-1 and strictly
    increasing; audienceWatchRatio is finite and >= 0. A broken rule raises ValueError naming
    the 1-based row at fault.
    """
    try:
        report = json.loads(text)
    except (TypeError, ValueError):
        raise ValueError("The report is not valid JSON.") from None
    if not isinstance(report, dict):
        raise ValueError("The report must be a JSON object.")

    headers = report.get("columnHeaders")
    names = (
        [header.get("name") for header in headers if isinstance(header, dict)]
        if isinstance(headers, list)
        else []
    )
    for name in RETENTION_COLUMNS:
        if name not in names:
            raise ValueError(f"The report has no {name} column.")
    elapsed_index = names.index("elapsedVideoTimeRatio")
    watch_index = names.index("audienceWatchRatio")

    rows = report.get("rows")
    if rows is None:
        raise ValueError("YouTube had no data for this video: the report has no rows.")
    if not isinstance(rows, list):
        raise ValueError("The report's rows must be a list.")
    if len(rows) < 2:
        raise ValueError("The report needs at least 2 rows.")
    if len(rows) > 1000:
        raise ValueError("row 1001: the report has more than 1000 rows.")

    pairs = []
    previous = None
    for number, row in enumerate(rows, start=1):
        if not isinstance(row, list) or len(row) != len(names):
            raise ValueError(f"row {number}: expected {len(names)} values")
        elapsed, watch = row[elapsed_index], row[watch_index]
        if not _number(elapsed):
            raise ValueError(f"row {number}: elapsedVideoTimeRatio must be a finite number")
        if not 0 <= elapsed <= 1:
            raise ValueError(f"row {number}: elapsedVideoTimeRatio must be between 0 and 1")
        if previous is not None and elapsed <= previous:
            raise ValueError(f"row {number}: elapsedVideoTimeRatio must be strictly increasing")
        if not _number(watch):
            raise ValueError(f"row {number}: audienceWatchRatio must be a finite number")
        if watch < 0:
            raise ValueError(f"row {number}: audienceWatchRatio must be at least 0")
        pairs.append((elapsed, watch))
        previous = elapsed
    return pairs


VIDEO_LINK_MAX_CHARS = 300
VIDEO_LINK_MESSAGE = "Paste the video's YouTube or Facebook link."
FACEBOOK_REELS_MESSAGE = "Facebook Reels aren't supported yet."
FACEBOOK_SHORT_LINK_MESSAGE = (
    "Open the link and paste the full facebook.com address from the browser bar."
)
FACEBOOK_REPORT_SHAPE_MESSAGE = (
    "This Facebook report isn't in the expected shape. Please send it to the NeuroLens team."
)
FACEBOOK_REPORT_VIDEO_MESSAGE = "This report is for a different video than the link."

_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
_YOUTUBE_SHORT_HOSTS = {"youtu.be", "www.youtu.be"}
_FACEBOOK_HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com"}
_YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_DIGITS = re.compile(r"[0-9]+")
_YOUTUBE_PATH_KINDS = {"shorts", "live", "embed"}


def _host_and_parts(text):
    """The lower-case host and non-empty path parts of a link, with or without a scheme."""
    raw = text.strip()
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    path = [part for part in parts.path.split("/") if part]
    return host, path, parse_qs(parts.query)


def _youtube_id(host, path, query):
    if host in _YOUTUBE_SHORT_HOSTS:
        return path[0] if len(path) == 1 else None
    if host not in _YOUTUBE_HOSTS:
        return None
    if path == ["watch"]:
        values = query.get("v")
        return values[0] if values else None
    if len(path) == 2 and path[0] in _YOUTUBE_PATH_KINDS:
        return path[1]
    return None


def _facebook_id(host, path, query):
    if host not in _FACEBOOK_HOSTS:
        return None
    if path == ["watch"]:
        values = query.get("v")
        return values[0] if values else None
    if path == ["video.php"]:
        values = query.get("v")
        return values[0] if values else None
    if "videos" in path:
        index = path.index("videos")
        rest = path[index + 1 :]
        if len(rest) == 1:
            return rest[0]
        if len(rest) == 2:
            return rest[1]
    return None


def parse_video_link(text):
    """The (platform, video_id) of a published video's YouTube or Facebook link (M3i §3).

    The form's link is accepted with or without `https://`, `www.` or `m.`; a refusal raises
    ValueError with the sentence the form shows. Only the documented YouTube and Facebook page
    forms are accepted; Reels and the short links that need a lookup name their own refusal.
    """
    if not isinstance(text, str):
        raise ValueError(VIDEO_LINK_MESSAGE)
    text = text.strip()
    if not text or len(text) > VIDEO_LINK_MAX_CHARS:
        raise ValueError(VIDEO_LINK_MESSAGE)
    host, path, query = _host_and_parts(text)

    if host in {"fb.watch", "www.fb.watch"} or (
        host in _FACEBOOK_HOSTS and len(path) >= 2 and path[:2] == ["share", "v"]
    ):
        raise ValueError(FACEBOOK_SHORT_LINK_MESSAGE)
    if (
        host in _FACEBOOK_HOSTS
        and path
        and (path[0] == "reel" or (len(path) >= 2 and path[:2] == ["share", "r"]))
    ):
        raise ValueError(FACEBOOK_REELS_MESSAGE)

    video_id = _youtube_id(host, path, query)
    platform = "youtube"
    if video_id is None:
        video_id = _facebook_id(host, path, query)
        platform = "facebook"
    if video_id is None:
        raise ValueError(VIDEO_LINK_MESSAGE)
    if platform == "youtube":
        valid = _YOUTUBE_ID.fullmatch(video_id) is not None
    else:
        valid = _DIGITS.fullmatch(video_id) is not None
    if not valid:
        raise ValueError(VIDEO_LINK_MESSAGE)
    return platform, video_id


def parse_facebook_retention(text, video_id):
    """The (elapsed_ratio, watch_ratio) rows of Meta's documented retention reply (M3i §4).

    The reply is `GET /<video_id>/video_insights?metric=total_video_retention_graph`: one
    lifetime metric whose id starts with the linked video's id and whose last value holds the
    40 equal intervals `"0"`-`"39"` as fractions in 0-1. Anything else raises ValueError; the
    shape message asks the user to send the file to the team because it is not stored.
    """
    try:
        report = json.loads(text)
    except (TypeError, ValueError):
        raise ValueError(FACEBOOK_REPORT_SHAPE_MESSAGE) from None

    data = report.get("data") if isinstance(report, dict) else None
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise ValueError(FACEBOOK_REPORT_SHAPE_MESSAGE)
    item = data[0]
    if item.get("name") != "total_video_retention_graph" or item.get("period") != "lifetime":
        raise ValueError(FACEBOOK_REPORT_SHAPE_MESSAGE)
    if not str(item.get("id", "")).startswith(f"{video_id}/"):
        raise ValueError(FACEBOOK_REPORT_VIDEO_MESSAGE)

    values = item.get("values")
    if not isinstance(values, list) or not values:
        raise ValueError(FACEBOOK_REPORT_SHAPE_MESSAGE)
    last = values[-1]
    intervals = last.get("value") if isinstance(last, dict) else None
    expected = {str(number) for number in range(40)}
    if not isinstance(intervals, dict) or set(intervals) != expected:
        raise ValueError(FACEBOOK_REPORT_SHAPE_MESSAGE)
    for key in expected:
        value = intervals[key]
        if not _number(value) or not 0 <= value <= 1:
            raise ValueError(FACEBOOK_REPORT_SHAPE_MESSAGE)
    return [(number / 40, intervals[str(number)]) for number in range(40)]


def retention_csv(pairs):
    """The stored two-column CSV for parsed retention pairs (M3c §3c)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["elapsed_ratio", "watch_ratio"])
    writer.writerows(pairs)
    return out.getvalue()


def parse_retention_csv(text):
    """The (elapsed_ratio, watch_ratio) pairs of a stored retention CSV (M3l §4)."""
    reader = csv.DictReader(io.StringIO(text))
    return [(float(row["elapsed_ratio"]), float(row["watch_ratio"])) for row in reader]


def retention_points(pairs, duration_s):
    """A stored curve as [seconds, percent] points for the page's Real viewers strip (M3l §4)."""
    return [[ratio * duration_s, watch * 100] for ratio, watch in pairs]


MIN_CURVE_POINTS = 20
MIN_WATCH_SPREAD = 0.02
MAX_WATCH_RATIO = 10


def curve_quality(pairs):
    """The failed checks of an audience-retention curve, empty when it is valid (M3f §5d).

    A full curve has at least 20 points, a watch-ratio spread of at least 0.02, ends no higher
    than it started, and has no value above 10.
    """
    failed = []
    if len(pairs) < MIN_CURVE_POINTS:
        failed.append("too_few_points")
    if not pairs:
        return failed
    watches = [watch for _, watch in pairs]
    if max(watches) - min(watches) < MIN_WATCH_SPREAD:
        failed.append("flat_curve")
    if watches[-1] > watches[0]:
        failed.append("rising_curve")
    if max(watches) > MAX_WATCH_RATIO:
        failed.append("watch_ratio_too_high")
    return failed


def to_csv(result):
    """One row per second, columns in COLUMNS order; muted cells beyond the shared length empty."""
    signals = result["signals"]
    muted = result["muted"]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(COLUMNS)
    for t in range(result["duration_seconds"]):
        values = [t]
        values += [signals[name][t] for name in SERIES]
        values += [muted[name][t] if t < len(muted[name]) else None for name in SERIES]
        writer.writerow([_cell(v) for v in values])
    return out.getvalue()
