"""A stored result's summary and CSV, and the response config check (M3d §6-§7). Pure Python:
the web tier imports it."""

import csv
import io
import json
import math

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
OPENING_BAND = 0.25
WINDOW_SECONDS = 3
GAP_SECONDS = 2
GROWTH_FRACTION = 2 / 3
MAX_MOMENT_SECONDS = 10


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


def _mean(values):
    return math.fsum(values) / len(values)


def _stdev(values):
    mean = _mean(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def _opening(overall):
    length = len(overall)
    if length == 1:
        value = overall[0]
    else:
        value = _mean(overall[1 : min(3, length - 1) + 1])

    rest = None
    word = None
    remaining = overall[4:]
    if remaining:
        deviation = _stdev(overall[1:])
        if deviation >= 1e-6:
            rest = _mean(remaining)
            difference = value - rest
            if difference > OPENING_BAND * deviation:
                word = "stronger than the rest"
            elif difference < -OPENING_BAND * deviation:
                word = "weaker than the rest"
            else:
                word = "about the same as the rest"
    return {"value": value, "rest": rest, "word": word}


def _candidates(length):
    if length == 1:
        return [(0, 1)]
    if length < 4:
        return [(1, length)]
    return [(start, start + WINDOW_SECONDS) for start in range(1, length - WINDOW_SECONDS + 1)]


def _overlaps(start, end, moment):
    return start < moment["end"] and end > moment["start"]


def _clear_of_picks(start, end, picks):
    for pick in picks:
        if not (start >= pick["end"] + GAP_SECONDS or end + GAP_SECONDS <= pick["start"]):
            return False
    return True


def _may_grow(second, picks, strong_moments, strongest):
    for pick in picks:
        if pick["start"] - GAP_SECONDS <= second <= pick["end"] + GAP_SECONDS - 1:
            return False
    if not strongest:
        for moment in strong_moments:
            if moment["start"] <= second < moment["end"]:
                return False
    return True


def _grow(overall, start, end, *, strongest, picks, strong_moments):
    length = len(overall)
    if length >= 4:
        video_mean = _mean(overall[1:])
        window_mean = _mean(overall[start:end])
        target = video_mean + GROWTH_FRACTION * (window_mean - video_mean)
        while end - start < MAX_MOMENT_SECONDS:
            options = []
            if start - 1 >= 1:
                options.append(start - 1)
            if end < length:
                options.append(end)
            options = [
                second for second in options if _may_grow(second, picks, strong_moments, strongest)
            ]
            if not options:
                break
            if strongest:
                second = max(options, key=lambda value: (overall[value], -value))
                if not overall[second] > target:
                    break
            else:
                second = min(options, key=lambda value: (overall[value], value))
                if not overall[second] < target:
                    break
            if second < start:
                start = second
            else:
                end = second + 1
    return {"start": int(start), "end": int(end), "value": _mean(overall[start:end])}


def _pick_moments(overall, *, strongest, strong_moments=()):
    candidates = _candidates(len(overall))
    picks = []
    for _ in range(2):
        eligible = []
        for start, end in candidates:
            if not _clear_of_picks(start, end, picks):
                continue
            if not strongest and any(_overlaps(start, end, moment) for moment in strong_moments):
                continue
            eligible.append((start, end))
        if not eligible:
            break
        if strongest:
            start, end = min(
                eligible, key=lambda window: (-_mean(overall[window[0] : window[1]]), window[0])
            )
        else:
            start, end = min(
                eligible, key=lambda window: (_mean(overall[window[0] : window[1]]), window[0])
            )
        picks.append(
            _grow(
                overall,
                start,
                end,
                strongest=strongest,
                picks=picks,
                strong_moments=strong_moments,
            )
        )
    moments = [
        {"start": pick["start"], "end": pick["end"], "value": pick["value"]} for pick in picks
    ]
    return sorted(moments, key=lambda moment: moment["start"])


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
    """The four summary parts served with a result (M3d §6), computed at serve time."""
    overall = result["signals"]["overall"]
    muted = result["muted"]["overall"]
    strong_moments = _pick_moments(overall, strongest=True)
    weak_moments = _pick_moments(overall, strongest=False, strong_moments=strong_moments)
    return {
        "opening": _opening(overall),
        "strong_moments": strong_moments,
        "weak_moments": weak_moments,
        "audio_dependence": _audio_dependence(overall, muted),
    }


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


def retention_csv(pairs):
    """The stored two-column CSV for parsed retention pairs (M3c §3c)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["elapsed_ratio", "watch_ratio"])
    writer.writerows(pairs)
    return out.getvalue()


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
