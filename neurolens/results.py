"""A stored result as CSV (M3b §4a). Pure Python: the web tier imports it."""

import csv
import io
import json
import math

REGIONS = ["ffa_faces", "eba_bodies", "ppa_scenes", "sts_social", "auditory"]
COLUMNS = ["t", "engagement_overall", *REGIONS, "auditory_with_audio", "auditory_without_audio"]


def _cell(value):
    """Exactly the number as stored in the result JSON; an empty cell for None."""
    return "" if value is None else json.dumps(value)


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
    """One row per timestep, columns in COLUMNS order."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(COLUMNS)
    for step in result["timesteps"]:
        values = [step["t"], step["engagement_overall"]]
        values += [step["regions"][name] for name in REGIONS]
        values += [step["auditory_with_audio"], step["auditory_without_audio"]]
        writer.writerow([_cell(v) for v in values])
    return out.getvalue()
