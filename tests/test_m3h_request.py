"""M3h §5b, §13: the shared request builders.

The numbers go to the model as one row per second (not one long list per signal), and a
comparison sends A's labelled blocks before B's, each picture after its own second's label.
"""

import base64

import pytest
from neurolens import report_prompt, results


SHOWN = ["see", "hear", "understand", "overall"]


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
    assert header == ["t", *SHOWN, "muted_overall"]
    rows = [line.split() for line in lines[2:]]
    assert len(rows) == 4
    for second, row in enumerate(rows):
        assert int(row[0]) == second
        expected = [result["signals"][name][second] for name in SHOWN]
        expected.append(result["muted"]["overall"][second])
        assert [float(cell) for cell in row[1:]] == pytest.approx(expected)


def test_series_table_leaves_a_missing_muted_value_blank():
    result = make_result(3)
    result["muted"]["overall"] = result["muted"]["overall"][:-1]

    table = report_prompt.series_table(result)
    lines = table.splitlines()

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


def test_comparison_table_puts_both_videos_side_by_side_with_blanks_past_the_shorter():
    a, b = make_result(3), make_result(2)

    _, content = report_prompt.build_comparison_request(
        (a, [b"\xff\xd8a"] * 3, []), (b, [b"\xff\xd8b"] * 2, [])
    )

    lines = content[-1]["text"].splitlines()
    header = lines[1].split()
    assert header[:5] == ["t", "A_see", "B_see", "A_hear", "B_hear"]
    assert header[-2:] == ["A_muted_overall", "B_muted_overall"]
    rows = [dict(zip(header, line.lstrip().split(" "), strict=False)) for line in lines[2:]]
    assert len(rows) == 3
    assert float(rows[1]["A_see"]) == pytest.approx(a["signals"]["see"][1])
    assert float(rows[1]["B_see"]) == pytest.approx(b["signals"]["see"][1])
    last = lines[-1].split()
    assert len(last) == 1 + len(header[1:]) // 2  # only A's cells; B's are blank


def test_the_table_comes_after_the_pictures():
    _, content = report_prompt.build_request(make_result(2), [b"\xff\xd8x"] * 2, [])

    assert content[-1]["text"].startswith("duration_seconds 2")
    assert all(block["type"] == "image" or "t = " in block["text"] for block in content[:-1])
