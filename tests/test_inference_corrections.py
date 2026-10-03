"""The pure helpers of the inference corrections. Written from docs/M2a_spec.md sections 7a and 8.

Runs on a laptop: small pandas tables and numpy arrays, no tribev2, torch or GPU.

The spec does not fix the container for attach_chunk_words, so the inputs are pandas
DataFrames (the shape tribev2's events and WhisperX transcripts take), and the output is read
through pd.DataFrame(...), which accepts a DataFrame or a list of dicts alike.
"""

import numpy as np
import pandas as pd
import pytest
from neurolens import inference

# ---------------------------------------------------------------- helpers


def transcript(*words):
    """One audio file's words, times measured from the start of the file (as WhisperX writes).

    Each word is (text, start) and lasts 0.3 s.
    """
    return pd.DataFrame(
        {
            "text": [text for text, _ in words],
            "start": [float(start) for _, start in words],
            "duration": [0.3] * len(words),
        },
        columns=["text", "start", "duration"],
    )


def chunks(*rows):
    """The audio chunks of one file: (start on the video timeline, offset into the file, duration).

    Each chunk also has a `chunk` label and a `timeline` field, which attached words must carry
    over, and the fields tribev2 does not copy (frequency, filepath, type).
    """
    return pd.DataFrame(
        [
            {
                "type": "Audio",
                "filepath": "/tmp/clip_audio.wav",
                "frequency": 16000,
                "start": float(start),
                "offset": float(offset),
                "duration": float(duration),
                "timeline": "clip-timeline",
                "chunk": f"chunk{i}",
            }
            for i, (start, offset, duration) in enumerate(rows)
        ]
    )


def attached(transcript_df, chunks_df):
    """attach_chunk_words as a DataFrame sorted by time."""
    out = pd.DataFrame(inference.attach_chunk_words(transcript_df, chunks_df))
    if len(out) == 0:
        return out
    return out.sort_values("start").reset_index(drop=True)


def placed(out):
    """(text, start) of every attached word, start rounded to 1 ms."""
    return [(row["text"], round(float(row["start"]), 3)) for _, row in out.iterrows()]


# 119 s file split the way tribev2 splits audio longer than 60 s.
CHUNKS_119 = ((0, 0, 60), (60, 60, 59))


# ---------------------------------------------------------------- attach_chunk_words


def test_words_of_a_119_second_file_are_each_attached_once_at_their_true_time():
    out = attached(
        transcript(("one", 1), ("sixtyone", 61), ("hundredten", 110)), chunks(*CHUNKS_119)
    )
    assert placed(out) == [("one", 1.0), ("sixtyone", 61.0), ("hundredten", 110.0)]


def test_each_word_goes_to_the_chunk_that_contains_it():
    out = attached(
        transcript(("one", 1), ("sixtyone", 61), ("hundredten", 110)), chunks(*CHUNKS_119)
    )
    assert list(out["chunk"]) == ["chunk0", "chunk1", "chunk1"]


def test_a_word_exactly_on_a_chunk_boundary_goes_only_to_the_later_chunk():
    out = attached(transcript(("edge", 60)), chunks(*CHUNKS_119))
    assert placed(out) == [("edge", 60.0)]
    assert list(out["chunk"]) == ["chunk1"]


def test_three_chunks_of_a_150_second_file_put_every_word_once_at_its_true_time():
    words = [("a", 0.5), ("b", 59.9), ("c", 60), ("d", 100), ("e", 119.99), ("f", 120), ("g", 149)]
    out = attached(transcript(*words), chunks((0, 0, 60), (60, 60, 60), (120, 120, 30)))
    assert placed(out) == [(text, float(start)) for text, start in words]
    assert list(out["chunk"]) == ["chunk0"] * 2 + ["chunk1"] * 3 + ["chunk2"] * 2


def test_a_chunk_whose_start_differs_from_its_offset_maps_by_start_plus_word_minus_offset():
    # chunk0 covers 0-30 s of the file and sits at 10 s on the video timeline;
    # chunk1 covers 30-60 s of the file and sits at 100 s.
    out = attached(
        transcript(("x", 5), ("y", 31.5), ("z", 59)),
        chunks((10, 0, 30), (100, 30, 30)),
    )
    assert placed(out) == [("x", 15.0), ("y", 101.5), ("z", 129.0)]
    assert list(out["chunk"]) == ["chunk0", "chunk1", "chunk1"]


def test_an_empty_transcript_gives_no_words():
    out = attached(transcript(), chunks(*CHUNKS_119))
    assert len(out) == 0


def test_a_word_past_the_last_chunk_is_dropped():
    out = attached(transcript(("in", 10), ("past", 125)), chunks(*CHUNKS_119))
    assert placed(out) == [("in", 10.0)]


def test_attached_words_are_type_word_and_carry_the_chunk_fields():
    out = attached(transcript(("one", 1), ("sixtyone", 61)), chunks(*CHUNKS_119))
    assert list(out["type"]) == ["Word", "Word"]
    assert list(out["timeline"]) == ["clip-timeline", "clip-timeline"]
    assert list(out["chunk"]) == ["chunk0", "chunk1"]
    assert list(out["text"]) == ["one", "sixtyone"]


def test_attached_words_keep_their_own_duration():
    out = attached(transcript(("one", 1), ("sixtyone", 61)), chunks(*CHUNKS_119))
    assert list(out["duration"]) == pytest.approx([0.3, 0.3])


# ---------------------------------------------------------------- order_by_time


def tagged(starts, width=4):
    """Rows whose every value is the row's start time, so a row can be traced after sorting."""
    return np.repeat(np.asarray(starts, dtype=float)[:, None], width, axis=1)


def test_timeline_error_is_a_runtime_error():
    assert issubclass(inference.TimelineError, RuntimeError)


def test_order_by_time_sorts_shuffled_rows_and_rows_move_with_their_starts():
    starts = [3.0, 0.0, 4.0, 1.0, 2.0]
    out = np.asarray(inference.order_by_time(tagged(starts), starts, 5.0))
    np.testing.assert_array_equal(out, tagged([0.0, 1.0, 2.0, 3.0, 4.0]))


def test_order_by_time_leaves_already_ordered_rows_alone():
    starts = [0.0, 1.0, 2.0]
    preds = np.arange(3 * 5, dtype=float).reshape(3, 5)
    out = np.asarray(inference.order_by_time(preds, starts, 3.0))
    np.testing.assert_array_equal(out, preds)


@pytest.mark.parametrize(
    "rows,duration",
    [(53, 52.2), (119, 119.01)],
    ids=["trailer_52s", "loop_119s"],
)
def test_order_by_time_accepts_timelines_shaped_like_the_real_runs(rows, duration):
    starts = list(np.arange(rows, dtype=float))
    rng = np.random.default_rng(0)
    order = rng.permutation(rows)
    shuffled = [starts[i] for i in order]
    out = np.asarray(inference.order_by_time(tagged(shuffled), shuffled, duration))
    assert out.shape == (rows, 4)
    np.testing.assert_array_equal(out[:, 0], np.arange(rows, dtype=float))


def test_order_by_time_tolerates_float_noise_below_a_millisecond():
    starts = [0.0, 1.0000000001, 1.9999999999, 3.0]
    out = np.asarray(inference.order_by_time(tagged(starts), starts, 3.5))
    assert out.shape == (4, 4)


def test_order_by_time_accepts_a_timeline_ending_exactly_two_tr_before_the_end():
    starts = [0.0, 1.0, 2.0, 3.0]
    out = np.asarray(inference.order_by_time(tagged(starts), starts, 5.0))
    assert out.shape == (4, 4)


def test_order_by_time_uses_the_given_tr():
    starts = [4.0, 0.0, 2.0]
    out = np.asarray(inference.order_by_time(tagged(starts), starts, 5.0, tr=2.0))
    np.testing.assert_array_equal(out[:, 0], [0.0, 2.0, 4.0])


@pytest.mark.parametrize(
    "starts,duration",
    [
        ([0.0, 1.0, 2.0, 2.0, 3.0], 4.0),  # repeated start
        ([0.0, 1.0, 3.0, 4.0], 4.5),  # gap at 2 s
        ([1.0, 2.0, 3.0], 3.5),  # does not begin at 0
        ([0.0, 1.0, 2.0, 3.0], 3.0),  # last start at the duration
        ([0.0, 1.0, 2.0, 3.0], 2.5),  # last start after the duration
        ([0.0, 1.0, 2.0], 5.5),  # ends more than 2 tr before the duration (cut short)
        ([0.0, 1.0, 2.0, 120.0, 121.0], 121.5),  # ghost rows far past the end
    ],
    ids=[
        "repeated_start",
        "gap",
        "not_from_zero",
        "start_at_duration",
        "start_after_duration",
        "cut_short",
        "ghost_rows",
    ],
)
def test_order_by_time_raises_timeline_error_for_a_misaligned_timeline(starts, duration):
    with pytest.raises(inference.TimelineError):
        inference.order_by_time(tagged(starts), starts, duration)


def test_order_by_time_raises_when_starts_and_rows_differ_in_count():
    with pytest.raises(inference.TimelineError):
        inference.order_by_time(tagged([0.0, 1.0, 2.0, 3.0]), [0.0, 1.0, 2.0], 3.5)


def test_order_by_time_raises_when_there_are_no_rows():
    with pytest.raises(inference.TimelineError):
        inference.order_by_time(np.zeros((0, 4)), [], 3.0)
