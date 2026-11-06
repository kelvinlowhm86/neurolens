"""M3h §2, §5d, §13: the shown order and the model's [A]/[B] markers.

`orient_comparison` turns a stored answer for the video the person opened, without ever
changing the stored object; `_comparison_problem` rejects an answer that names a video
outside the markers.
"""

import copy

import pytest
from neurolens import report_prompt
from neurolens.lambdas import report


def stored_comparison():
    return {
        "headline": "[A] beats [B]",
        "summary": "[A] draws the stronger response; [B] falls away.",
        "differences": [
            {
                "a": {"start": 0, "end": 1},
                "b": {"start": 2, "end": 3},
                "what_differs": "[A] opens on a face.",
                "brain_response": "[A]'s response rises above [B]'s.",
            }
        ],
        "a_does_better": ["[A] 0-1 s"],
        "b_does_better": ["[B] 2-3 s"],
        "suggestions": ["Keep [A]'s opening."],
        "created_at": "2026-10-31T00:00:00Z",
        "model": "test-model",
        "usage": {"input_tokens": 1, "output_tokens": 2},
    }


def test_without_flip_turns_markers_into_a_and_b_and_changes_nothing_else():
    source = stored_comparison()

    oriented = report_prompt.orient_comparison(source, flip=False)

    assert oriented["headline"] == "A beats B"
    assert oriented["summary"] == "A draws the stronger response; B falls away."
    assert oriented["differences"] == [
        {
            "a": {"start": 0, "end": 1},
            "b": {"start": 2, "end": 3},
            "what_differs": "A opens on a face.",
            "brain_response": "A's response rises above B's.",
        }
    ]
    assert oriented["a_does_better"] == ["A 0-1 s"]
    assert oriented["b_does_better"] == ["B 2-3 s"]
    assert oriented["suggestions"] == ["Keep A's opening."]
    for name in ("created_at", "model", "usage"):
        assert oriented[name] == source[name]


def test_with_flip_swaps_every_span_list_and_letter():
    source = stored_comparison()

    oriented = report_prompt.orient_comparison(source, flip=True)

    assert oriented["headline"] == "B beats A"
    assert oriented["summary"] == "B draws the stronger response; A falls away."
    assert oriented["differences"] == [
        {
            "a": {"start": 2, "end": 3},
            "b": {"start": 0, "end": 1},
            "what_differs": "B opens on a face.",
            "brain_response": "B's response rises above A's.",
        }
    ]
    assert oriented["a_does_better"] == ["A 2-3 s"]
    assert oriented["b_does_better"] == ["B 0-1 s"]
    assert oriented["suggestions"] == ["Keep B's opening."]
    for name in ("created_at", "model", "usage"):
        assert oriented[name] == source[name]


@pytest.mark.parametrize("flip", [False, True])
def test_orientation_never_mutates_the_input(flip):
    source = stored_comparison()
    before = copy.deepcopy(source)

    report_prompt.orient_comparison(source, flip=flip)

    assert source == before


@pytest.mark.parametrize("flip", [False, True])
def test_an_answer_with_no_marker_in_headline_or_summary_raises(flip):
    source = stored_comparison()
    source["headline"] = "A beats B"
    source["summary"] = "Neither names a video."

    with pytest.raises(ValueError):
        report_prompt.orient_comparison(source, flip=flip)


def test_a_marker_in_the_summary_alone_is_enough():
    source = stored_comparison()
    source["headline"] = "One video is stronger"

    oriented = report_prompt.orient_comparison(source, flip=False)

    assert oriented["headline"] == "One video is stronger"
    assert oriented["summary"] == "A draws the stronger response; B falls away."


def comparison_answer(**overrides):
    answer = {
        "headline": "[A] beats [B]",
        "summary": "[A] holds attention; [B] falls away.",
        "differences": [],
        "a_does_better": ["[A] opens clearly."],
        "b_does_better": [],
        "suggestions": [],
    }
    answer.update(overrides)
    return answer


def test_an_answer_using_markers_throughout_passes():
    assert report._comparison_problem(comparison_answer(), 3, 3) is None


@pytest.mark.parametrize("name", ["headline", "summary"])
def test_a_missing_marker_in_the_headline_or_summary_is_a_problem(name):
    answer = comparison_answer()
    answer[name] = "Neither names a video."

    assert report._comparison_problem(answer, 3, 3) is not None


@pytest.mark.parametrize(
    "text",
    ["B is stronger.", "At A 12 s the face appears.", "A's opening is strong."],
)
def test_a_marker_missed_outside_the_brackets_is_a_problem(text):
    answer = comparison_answer(summary=f"[A] leads. {text}")

    assert report._comparison_problem(answer, 3, 3) is not None
