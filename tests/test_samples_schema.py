"""data/samples.json entries have the M3d §9 shape the page reads.

The list may be empty until step 3 adds the new open-licensed samples; every entry present must
carry the full entry and a format-2 result, and any brain file must be a whole number of
20484-vertex float16 rows.
"""

import json
from pathlib import Path

import pytest
from neurolens import results

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SAMPLES = json.loads((DATA_DIR / "samples.json").read_text())["samples"]
N_VERTICES = 20484
SERIES = {
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
}
ENTRY_KEYS = {
    "id",
    "title",
    "description",
    "attribution",
    "video_file",
    "thumbnail",
    "brain_file",
    "csv_file",
    "result",
    "summary",
}
SUMMARY_KEYS = {"audio_dependence"}


@pytest.mark.parametrize("sample", SAMPLES, ids=[s.get("id", "?") for s in SAMPLES])
def test_sample_has_the_new_entry_shape(sample):
    # M3h §7: a sample may also carry its stored report.
    assert ENTRY_KEYS <= set(sample) <= ENTRY_KEYS | {"report"}
    assert sample["result"]["format"] == 2
    assert set(sample["result"]["signals"]) == SERIES
    assert set(sample["result"]["muted"]) == SERIES
    assert set(sample["summary"]) == SUMMARY_KEYS


@pytest.mark.parametrize("sample", SAMPLES, ids=[s.get("id", "?") for s in SAMPLES])
def test_a_sample_brain_file_is_a_whole_number_of_vertex_rows(sample):
    brain = DATA_DIR / sample["brain_file"]
    if not brain.exists():
        return
    assert brain.stat().st_size % (N_VERTICES * 2) == 0


@pytest.mark.parametrize("sample", SAMPLES, ids=[s.get("id", "?") for s in SAMPLES])
def test_a_sample_csv_file_matches_its_result(sample):
    csv_file = DATA_DIR / sample["csv_file"]
    if not csv_file.exists():
        return
    assert csv_file.read_text() == results.to_csv(sample["result"])
