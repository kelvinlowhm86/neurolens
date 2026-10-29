"""M3h §5b, §13: the shared request builders.

The numbers go to the model as one row per second (not one long list per signal), and a
comparison sends A's labelled blocks before B's, each picture after its own second's label.
"""

import base64

import pytest
from neurolens import report_prompt, results


def make_result(duration=3):
    signals = {
        name: [round(0.01 * (number + 1) + 0.1 * t, 2) for t in range(duration)]
        for number, name in enumerate(results.SERIES)
    }
    muted = {name: [round(value / 2, 2) for value in signals[name]] for name in results.SERIES}
    return {
        "format": 2,
        "duration_seconds": duration,
        "signals": signals,
        "muted": muted,
    }


def test_series_table_has_one_row_per_second_in_header_order():
    result = make_result(4)

    table = report_prompt.series_table(result)
    lines = table.splitlines()

    assert "duration_seconds 4" in lines[0]
    assert "last_second 3" in lines[0]
    header = lines[1].split()
    assert header == ["t", *results.SERIES, "muted_overall"]
    rows = [line.split() for line in lines[2:]]
    assert len(rows) == 4
    for second, row in enumerate(rows):
        assert int(row[0]) == second
        expected = [result["signals"][name][second] for name in results.SERIES]
        expected.append(result["muted"]["overall"][second])
        assert [float(cell) for cell in row[1:]] == pytest.approx(expected)


def test_series_table_labels_the_video_and_leaves_a_missing_muted_value_blank():
    result = make_result(3)
    result["muted"]["overall"] = result["muted"]["overall"][:-1]

    table = report_prompt.series_table(result, label="A")
    lines = table.splitlines()

    assert "A" in lines[0]
    assert "duration_seconds 3" in lines[0]
    assert "last_second 2" in lines[0]
    header = lines[1].split()
    last_row = lines[-1].split()
    assert len(last_row) == len(header) - 1


def test_comparison_request_labels_a_then_b_and_keeps_each_image_with_its_second():
    frames_a = [b"\xff\xd8a0", b"\xff\xd8a1", b"\xff\xd8a2"]
    frames_b = [b"\xff\xd8b0", b"\xff\xd8b1"]
    a = (make_result(3), frames_a, [{"start": 0.5, "text": "hello"}])
    b = (make_result(2), frames_b, [])

    system, content = report_prompt.build_comparison_request(a, b)

    assert isinstance(system, str) and system
    image_indices = [
        index
        for index, block in enumerate(content)
        if isinstance(block, dict) and block.get("type") == "image"
    ]
    assert len(image_indices) == len(frames_a) + len(frames_b)
    labels = [content[index - 1]["text"] for index in image_indices]
    expected_labels = [f"A t = {second} s" for second in range(3)]
    expected_labels += [f"B t = {second} s" for second in range(2)]
    assert labels == expected_labels
    images = [base64.b64decode(content[index]["source"]["data"]) for index in image_indices]
    assert images == [*frames_a, *frames_b]
