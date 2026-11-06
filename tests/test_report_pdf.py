"""M3h §18f: the PDF module's hard cases.

Light treatment: a PDF starts with %PDF, its text holds the headline and the names, and every
awkward result renders without error. No pixel positions or exact numbers are pinned.
"""

import io

import pytest
from neurolens import report_pdf, results
from PIL import Image
from pypdf import PdfReader


def make_result(duration=4, *, muted=True):
    signals = {
        name: [round(0.08 * (number + 1) + 0.12 * t, 2) for t in range(duration)]
        for number, name in enumerate(results.SERIES)
    }
    result = {"format": 2, "duration_seconds": duration, "signals": signals}
    if muted:
        result["muted"] = {
            name: [round(value / 2, 2) for value in values] for name, values in signals.items()
        }
    return result


def make_report(duration=4, *, moments=1):
    return {
        "headline": "Headline37",
        "summary": "A short summary for the report.",
        "moments": [
            {
                "start": min(index, duration - 1),
                "end": min(index + 1, duration - 1),
                "what_happens": f"Marker{index} something happens on screen.",
                "brain_response": f"The response rises and then settles, tail{index}.",
                "suggestion": None if index % 2 else f"Try this change, tail{index}.",
            }
            for index in range(moments)
        ],
        "strengths": ["Strengths1"],
        "improvements": ["Improvements1"],
        "created_at": "2026-10-31T00:22:47Z",
    }


def make_comparison(duration=4):
    return {
        "headline": "Comparison37",
        "summary": "A short comparison summary.",
        "differences": [
            {
                "a": {"start": 0, "end": 1},
                "b": None,
                "what_differs": "Only A has this passage, difference1.",
                "brain_response": "A rises while B has nothing here, tail9.",
            }
        ],
        "a_does_better": ["A opens faster."],
        "b_does_better": ["B settles more calmly."],
        "suggestions": ["Keep A's opening."],
        "created_at": "2026-10-31T00:23:37Z",
    }


def jpeg(width=32, height=24):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (40, 80, 120)).save(buffer, format="JPEG")
    return buffer.getvalue()


def text_of(pdf):
    reader = PdfReader(io.BytesIO(pdf))
    return [page.extract_text() or "" for page in reader.pages]


def test_a_single_report_starts_with_pdf_and_holds_the_headline_and_name():
    pdf = report_pdf.report_pdf("My Ad.mp4", make_result(5), make_report(5), [])

    assert pdf.startswith(b"%PDF")
    pages = text_of(pdf)
    assert "My Ad.mp4" in "\n".join(pages)
    assert "Headline37" in "\n".join(pages)


def test_a_comparison_starts_with_pdf_and_holds_both_names():
    result = make_result(5)
    pdf = report_pdf.comparison_pdf(
        ("Ad A.mp4", result, []),
        ("Ad B.mp4", result, []),
        make_comparison(5),
    )

    assert pdf.startswith(b"%PDF")
    text = "\n".join(text_of(pdf))
    assert "Ad A.mp4" in text
    assert "Ad B.mp4" in text


@pytest.mark.parametrize("duration", [1, 2])
def test_a_very_short_video_renders(duration):
    pdf = report_pdf.report_pdf("Tiny.mp4", make_result(duration), make_report(duration), [])

    assert pdf.startswith(b"%PDF")


def test_no_muted_series_renders_without_the_sound_off_key():
    result = make_result(4, muted=False)
    pdf = report_pdf.report_pdf("No sound track.mp4", result, make_report(4), [])

    assert pdf.startswith(b"%PDF")
    assert "Sound off" not in "\n".join(text_of(pdf))


def test_a_null_comparison_side_renders():
    pdf = report_pdf.comparison_pdf(
        ("Ad A.mp4", make_result(4), [jpeg()]),
        ("Ad B.mp4", make_result(4), []),
        make_comparison(4),
    )

    assert pdf.startswith(b"%PDF")


def test_a_chinese_japanese_and_korean_name_comes_back_in_the_text():
    # Simplified and traditional Chinese, Japanese and Korean: the trimmed fallbacks cover all four.
    name = "测试视频 我的視頻 テスト動画 한국어 영상.mp4"
    pdf = report_pdf.report_pdf(name, make_result(3), make_report(3), [])

    text = "\n".join(text_of(pdf))
    for part in ("测试视频", "我的視頻", "テスト動画", "한국어", "영상"):
        assert part in text
    assert "?" not in text


def test_a_character_no_font_has_becomes_a_question_mark():
    pdf = report_pdf.report_pdf("ไทย.mp4", make_result(3), make_report(3), [])

    text = "\n".join(text_of(pdf))
    assert "ไทย" not in text
    assert "?" in text


def test_a_two_hundred_character_name_renders():
    name = "a" * 196 + ".mp4"
    pdf = report_pdf.report_pdf(name, make_result(3), make_report(3), [])

    assert pdf.startswith(b"%PDF")
    assert "…" in "\n".join(text_of(pdf))


def test_eight_long_moments_do_not_split_a_row_across_pages():
    long_text = " ".join(["A long description of what appears on screen."] * 8)
    report = make_report(10, moments=8)
    for index, moment in enumerate(report["moments"]):
        moment["what_happens"] = f"Marker{index} {long_text}"
        moment["brain_response"] = f"{long_text} tail{index}."
    pdf = report_pdf.report_pdf("Eight moments.mp4", make_result(10), report, [jpeg()])

    pages = text_of(pdf)
    for index in range(8):
        homes = [page for page in pages if f"Marker{index}" in page]
        assert len(homes) == 1
        assert f"tail{index}" in homes[0]


def test_frames_are_optional_and_keep_the_rows_whole():
    without = report_pdf.report_pdf("No frames.mp4", make_result(4), make_report(4), [])
    with_frames = report_pdf.report_pdf(
        "Frames.mp4", make_result(4), make_report(4), [jpeg(20, 40), jpeg(20, 40)]
    )

    assert without.startswith(b"%PDF")
    assert with_frames.startswith(b"%PDF")
