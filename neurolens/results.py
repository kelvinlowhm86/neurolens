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
RESPONSE_KEYS = (
    "well_above",
    "above",
    "below",
    "dead_threshold",
    "dead_min_seconds",
    "colour_range",
)


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
    """Raise ValueError unless cfg is a complete, usable `response` block (M3d §6)."""
    if not isinstance(cfg, dict):
        raise ValueError("The response config must be an object.")
    for key in RESPONSE_KEYS:
        if key not in cfg:
            raise ValueError(f"Missing response setting: {key}")
        if not _number(cfg[key]):
            raise ValueError(f"response.{key} must be a finite number.")
    if not cfg["well_above"] > cfg["above"] > cfg["below"]:
        raise ValueError("response thresholds must satisfy well_above > above > below.")
    if cfg["dead_threshold"] <= 0:
        raise ValueError("response.dead_threshold must be positive.")
    if (
        isinstance(cfg["dead_min_seconds"], bool)
        or not isinstance(cfg["dead_min_seconds"], int)
        or cfg["dead_min_seconds"] < 1
    ):
        raise ValueError("response.dead_min_seconds must be an integer of at least 1.")
    if cfg["colour_range"] <= 0:
        raise ValueError("response.colour_range must be positive.")


def _level(value, cfg):
    if value >= cfg["well_above"]:
        return "well above typical"
    if value >= cfg["above"]:
        return "above typical"
    if value <= cfg["below"]:
        return "below typical"
    return "typical"


def _mean(values):
    return sum(values) / len(values) if values else 0.0


def _dead_stretches(overall, threshold, minimum):
    stretches = []
    start = None
    for t, value in enumerate(overall):
        if value < -threshold:
            if start is None:
                start = t
        elif start is not None:
            if t - start >= minimum:
                stretches.append({"start": start, "end": t})
            start = None
    if start is not None and len(overall) - start >= minimum:
        stretches.append({"start": start, "end": len(overall)})
    return stretches


def _audio_dependence(overall, muted):
    length = min(len(overall), len(muted))
    denominator = sum(max(value, 0.0) for value in overall[:length])
    if denominator < 1e-6:
        return None
    numerator = sum(max(value, 0.0) for value in muted[:length])
    return round(min(max(1 - numerator / denominator, 0.0), 1.0), 2)


def summarise(result, response_cfg):
    """The cards served with a result, computed each time so a threshold change needs no rerun."""
    signals = result["signals"]
    muted = result["muted"]
    overall = signals["overall"]
    length = len(overall)

    hook_length = min(3, length)
    hook_group = max(
        GROUPS,
        key=lambda group: _mean(signals[group][:hook_length]),
    )
    hook_value = round(_mean(signals[hook_group][:hook_length]), 3)

    if length == 1:
        peak_t = 0
    elif length > 1:
        peak_t = max(range(1, length), key=lambda t: overall[t])
    else:
        peak_t = None
    peak = (
        None
        if peak_t is None
        else {"t": peak_t, "value": overall[peak_t], "level": _level(overall[peak_t], response_cfg)}
    )

    return {
        "hook": {
            "group": hook_group,
            "value": hook_value,
            "level": _level(hook_value, response_cfg),
        },
        "peak": peak,
        "dead": _dead_stretches(
            overall, response_cfg["dead_threshold"], response_cfg["dead_min_seconds"]
        ),
        "audio_dependence": _audio_dependence(overall, muted["overall"]),
    }


RETENTION_COLUMNS = ("elapsedVideoTimeRatio", "audienceWatchRatio")


def _retention_number(value):
    """A JSON number that is finite; bool is an int in Python but not a number here."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # an integer too large for a float
        return False


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
        if not _retention_number(elapsed):
            raise ValueError(f"row {number}: elapsedVideoTimeRatio must be a finite number")
        if not 0 <= elapsed <= 1:
            raise ValueError(f"row {number}: elapsedVideoTimeRatio must be between 0 and 1")
        if previous is not None and elapsed <= previous:
            raise ValueError(f"row {number}: elapsedVideoTimeRatio must be strictly increasing")
        if not _retention_number(watch):
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
