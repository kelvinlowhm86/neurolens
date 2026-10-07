"""A stored result as CSV (M3b §4a). Pure Python: the web tier imports it."""

import csv
import io
import json

REGIONS = ["ffa_faces", "eba_bodies", "ppa_scenes", "sts_social", "auditory"]
COLUMNS = ["t", "engagement_overall", *REGIONS, "auditory_with_audio", "auditory_without_audio"]


def _cell(value):
    """Exactly the number as stored in the result JSON; an empty cell for None."""
    return "" if value is None else json.dumps(value)


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
