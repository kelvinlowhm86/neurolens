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


def parse_retention_csv(text):
    """The (elapsed_ratio, watch_ratio) rows of a real audience-retention CSV (M3c §3c).

    The header is exactly `elapsed_ratio,watch_ratio`, with 2 to 1000 data rows. elapsed_ratio is
    in 0-1 and strictly increasing; watch_ratio is finite and >= 0. A broken rule raises
    ValueError naming the line.
    """
    header = "elapsed_ratio,watch_ratio"
    lines = [(n, line) for n, line in enumerate(text.splitlines(), start=1) if line.strip()]
    if not lines or lines[0][1] != header:
        line = lines[0][0] if lines else 1
        raise ValueError(f"line {line}: the header must be exactly {header}")
    rows = lines[1:]
    if len(rows) < 2:
        raise ValueError("the CSV needs at least 2 rows after the header")
    if len(rows) > 1000:
        raise ValueError(f"line {rows[1000][0]}: the CSV has more than 1000 rows")

    pairs = []
    previous = None
    for number, line in rows:
        fields = line.split(",")
        if len(fields) != 2:
            raise ValueError(f"line {number}: expected exactly 2 columns")
        try:
            elapsed, watch = float(fields[0]), float(fields[1])
        except ValueError:
            raise ValueError(f"line {number}: both columns must be numbers") from None
        if not math.isfinite(elapsed) or not 0 <= elapsed <= 1:
            raise ValueError(f"line {number}: elapsed_ratio must be between 0 and 1")
        if previous is not None and elapsed <= previous:
            raise ValueError(f"line {number}: elapsed_ratio must be strictly increasing")
        if not math.isfinite(watch) or watch < 0:
            raise ValueError(f"line {number}: watch_ratio must be a finite number >= 0")
        pairs.append((elapsed, watch))
        previous = elapsed
    return pairs


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
