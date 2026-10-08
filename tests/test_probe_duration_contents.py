"""probe_duration measures a video from its contents, not its header (docs/M1_spec.md §1
`probe_duration` and §6a "Worker handling"): the span from the earliest packet start to the
latest packet end (pts_time + duration_time) over every stream. A header is written by whoever
made the file, so an edited one could understate the length, pass the length limit and be
charged for less than the GPU then analyses.

The falsified clip is built at test time: a normal 4-second MP4 whose duration fields in the
`moov` box (mvhd, and every track's tkhd and mdhd) are rewritten to a quarter of their value.
The samples, their timestamps and the edit lists are untouched, so the contents still last 4 s.
"""

import json
import struct
import subprocess

import pytest
from neurolens import inference

SECONDS = 4.0
TOLERANCE = 0.15  # AAC priming and frame granularity, not product meaning


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True, capture_output=True)


def make_clip(path, video_s, audio_s):
    ffmpeg(
        "-f", "lavfi", "-i", f"testsrc=size=160x120:rate=10:duration={video_s}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={audio_s}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        str(path),
    )  # fmt: skip


def header_duration(path):
    """What the container header declares: ffprobe's format.duration."""
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


# ---------------------------------------------------------------- the MP4 header patch

CONTAINERS = {b"moov", b"trak", b"mdia"}
# Byte offset of the 32-bit duration inside each full box's payload (version 0 layout):
# mvhd/mdhd: version+flags, creation, modification, timescale, duration; tkhd: version+flags,
# creation, modification, track_ID, reserved, duration.
DURATION_OFFSET = {b"mvhd": 16, b"mdhd": 16, b"tkhd": 20}


def understate_mp4_header(src, dst, divisor=4):
    """Copy an MP4, dividing every header duration in moov by `divisor`. Walks the box tree
    (never a raw byte search, which could hit sample data). Returns the boxes patched."""
    data = bytearray(src.read_bytes())
    patched = []

    def walk(start, end):
        pos = start
        while pos + 8 <= end:
            size, kind = struct.unpack(">I4s", data[pos : pos + 8])
            header = 8
            if size == 1:
                size, header = struct.unpack(">Q", data[pos + 8 : pos + 16])[0], 16
            elif size == 0:
                size = end - pos
            assert size >= header, "malformed MP4 box"
            body = pos + header
            if kind in CONTAINERS:
                walk(body, pos + size)
            elif kind in DURATION_OFFSET:
                assert data[body] == 0, f"{kind} version 1 (64-bit) not handled"
                at = body + DURATION_OFFSET[kind]
                (value,) = struct.unpack(">I", data[at : at + 4])
                data[at : at + 4] = struct.pack(">I", value // divisor)
                patched.append(kind)
            pos += size

    walk(0, len(data))
    dst.write_bytes(bytes(data))
    return patched


@pytest.fixture
def understated_clip(tmp_path):
    honest = tmp_path / "honest.mp4"
    make_clip(honest, SECONDS, SECONDS)
    lying = tmp_path / "lying.mp4"
    patched = understate_mp4_header(honest, lying)
    assert sorted(patched) == sorted([b"mvhd", b"tkhd", b"tkhd", b"mdhd", b"mdhd"])
    return lying


# ---------------------------------------------------------------- tests


def test_a_clip_whose_header_understates_its_length_is_measured_by_its_contents(
    understated_clip,
):
    # Precondition: the header really lies, so this test cannot pass vacuously.
    declared = header_duration(understated_clip)
    assert declared <= SECONDS / 2, f"the patched header still declares {declared} s"
    assert inference.probe_duration(understated_clip) == pytest.approx(SECONDS, abs=TOLERANCE)


def test_a_clip_with_a_longer_audio_track_measures_to_the_later_end(tmp_path):
    """Streams of slightly different lengths: the result is the end of the longer one."""
    clip = tmp_path / "longer_audio.mp4"
    make_clip(clip, video_s=3.0, audio_s=3.5)
    assert inference.probe_duration(clip) == pytest.approx(3.5, abs=TOLERANCE)


def test_a_clip_with_a_longer_video_track_measures_to_the_later_end(tmp_path):
    clip = tmp_path / "longer_video.mp4"
    make_clip(clip, video_s=3.5, audio_s=3.0)
    assert inference.probe_duration(clip) == pytest.approx(3.5, abs=TOLERANCE)


def test_a_video_whose_packets_have_no_timestamps_is_unreadable(tmp_path):
    """A raw H.264 stream (saved as .mp4) has a video stream whose packets carry no
    presentation time, so there is no span to measure."""
    raw = tmp_path / "raw.mp4"
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-f", "h264", str(raw),
    )  # fmt: skip
    # Precondition: a video stream with packets, none of them timestamped.
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams",
         "-show_entries", "packet=pts_time", str(raw)],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    info = json.loads(out)
    assert [s["codec_type"] for s in info["streams"]] == ["video"]
    assert info["packets"], "the raw stream should still have packets"
    assert all("pts_time" not in p for p in info["packets"])
    with pytest.raises(inference.UnreadableVideo):
        inference.probe_duration(raw)
