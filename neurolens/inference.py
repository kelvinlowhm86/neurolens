"""Model + atlas loading and inference. Heavy libraries are imported inside functions only."""

import logging
import math
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import numpy as np

from neurolens import settings
from neurolens.media import UnreadableVideo as UnreadableVideo
from neurolens.media import probe_duration as probe_duration
from neurolens.response import (
    HCP_MMP_FILES,
    build_masks,
    require_nonempty_masks,
    verify_sha256,
)

logger = logging.getLogger("neurolens")

MAP_DOWNLOAD_TIMEOUT_SECONDS = 60  # each HCP-MMP1 file is ~1.3 MB

N_VERTICES = 20484

# Filled by load_model().
model = None
_roi_masks = None


def fake_mode():
    """True when FAKE_INFERENCE is 1/true/yes. Read from the environment at call time.

    An environment variable only (no config field), so a stale config file can never leave a
    real deployment in fake mode.
    """
    return os.environ.get("FAKE_INFERENCE", "").strip().lower() in ("1", "true", "yes")


def roi_masks():
    """The ROI masks built by load_model()."""
    return _roi_masks


def read_hcp_annot(path):
    """The (labels, names) of a Glasser HCP-MMP1 annot file, read with nibabel."""
    import nibabel.freesurfer as freesurfer

    labels, _, names = freesurfer.read_annot(str(path))
    return labels, names


def load_model(cfg=None, feature_cache=None):
    """Load the TRIBE v2 model (skipped in fake mode) and the Glasser HCP-MMP1 maps, build masks.

    `feature_cache` is where tribev2 writes each job's extracted features. It defaults to the
    models folder; the container passes a temporary folder so the downloaded weights stay untouched.
    """
    global model, _roi_masks

    root = settings.get_root()
    if cfg is None:
        cfg = settings.load_settings(root)
    paths = settings.resolve_paths(cfg, root)
    settings.ensure_dirs(paths)
    # HF env vars must be set before anything imports huggingface_hub.
    settings.configure_env(cfg, paths)

    if fake_mode():
        logger.warning("FAKE_INFERENCE is ON — results are not real model output")
    else:
        logger.info("Loading TRIBE v2 model...")
        from tribev2.demo_utils import TribeModel

        model = TribeModel.from_pretrained(
            cfg["model"]["repo_id"],
            cache_folder=str(feature_cache if feature_cache is not None else paths["models"]),
        )
        logger.info("TRIBE v2 loaded.")

    logger.info("Loading Glasser HCP-MMP1 maps...")
    maps_dir = Path(paths["data"]) / "hcp_mmp"
    maps_dir.mkdir(parents=True, exist_ok=True)
    for side in ("lh", "rh"):
        filename, url, expected = HCP_MMP_FILES[side]
        path = maps_dir / filename
        if not path.exists():
            logger.info(f"Downloading {filename}...")
            fd, temporary = tempfile.mkstemp(dir=maps_dir, prefix=f"{filename}.", suffix=".tmp")
            os.close(fd)
            temporary = Path(temporary)
            replaced = False
            try:
                with urllib.request.urlopen(url, timeout=MAP_DOWNLOAD_TIMEOUT_SECONDS) as response:
                    with temporary.open("wb") as handle:
                        shutil.copyfileobj(response, handle)
                verify_sha256(temporary, expected)
                temporary.replace(path)
                replaced = True
            finally:
                if not replaced:
                    temporary.unlink(missing_ok=True)
        verify_sha256(path, expected)

    lh_labels, lh_names = read_hcp_annot(maps_dir / HCP_MMP_FILES["lh"][0])
    rh_labels, rh_names = read_hcp_annot(maps_dir / HCP_MMP_FILES["rh"][0])
    _roi_masks = build_masks(lh_labels, lh_names, rh_labels, rh_names)
    require_nonempty_masks(_roi_masks)
    logger.info("HCP-MMP1 atlas and ROI masks ready.")


class ThumbnailFailed(RuntimeError):
    """ffmpeg could not make a thumbnail: the file is not a video, it timed out, or it wrote
    nothing."""


def spoken_words(events):
    """The spoken words of `build_events`, in time order, as {"start", "text"} dicts (M3g §2b).

    Empty in fake mode (its stand-in has no words) and when the video has no speech.
    """
    if isinstance(events, _FakeEvents):
        return []
    words = events.loc[events.type == "Word"]
    ordered = words.sort_values("start", kind="stable")
    return [
        {"start": round(float(row["start"]), 2), "text": str(row["text"])}
        for _, row in ordered.iterrows()
    ]


def make_thumbnail(path, seconds):
    """A still from `seconds` into the video, 320 px wide, as JPEG bytes (M3d §5b).

    Raises ThumbnailFailed on a non-zero exit, a timeout (30 s) or empty output. It is only a
    picture, so the worker carries on without one.
    """
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                str(max(seconds, 0)),
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-vf",
                "scale=320:-2",
                "-q:v",
                "5",
                "-f",
                "image2pipe",
                "-",
            ],
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as err:
        raise ThumbnailFailed(f"ffmpeg timed out making a thumbnail from {path}") from err
    if result.returncode != 0 or not result.stdout:
        raise ThumbnailFailed(f"ffmpeg could not make a thumbnail from {path}")
    return result.stdout


PLAY_COPY_WAIT_SECONDS = 300  # the analysis waits this long for the phone copy before the result


class PlayCopy:
    """The phone-friendly H.264 copy of the upload, encoded in the background (M3d §5c).

    Some uploads (VP9, HEVC) do not play on phones. The copy caps the long edge at 1920 px and
    keeps both sides even, in an MP4 with the moov atom first so it starts while it loads. Entering
    the block starts ffmpeg at lower priority, so the encode never slows the paid analysis much;
    `wait` gives it up to its timeout and is never a failure of the job: it returns False and logs
    PLAY_COPY_FAILED when the copy could not be made. Leaving the block stops an encode still
    running, including when the analysis raises.
    """

    def __init__(self, src_path, dst_path):
        self.src_path = Path(src_path)
        self.dst_path = Path(dst_path)
        self.process = None

    def __enter__(self):
        try:
            self._start()
        except OSError:
            logger.exception(f"PLAY_COPY_FAILED {self.src_path}: ffmpeg did not start")
        return self

    def _start(self):
        # `nice` rather than preexec_fn: the worker runs a heartbeat thread, and preexec_fn is
        # unsafe in a process with threads. stderr goes to a file, not a pipe nobody reads until
        # the end: a corrupt stream can print an error per frame, fill a pipe and stall the encode.
        self.log_path = self.dst_path.with_suffix(".log")
        with open(self.log_path, "wb") as log:
            self.process = subprocess.Popen(
                [
                    "nice",
                    "-n",
                    "10",
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    str(self.src_path),
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0?",
                    "-vf",
                    "scale='min(iw,1920)':'min(ih,1920)':"
                    "force_original_aspect_ratio=decrease:force_divisible_by=2",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "23",
                    "-pix_fmt",
                    "yuv420p",
                    "-profile:v",
                    "high",
                    "-movflags",
                    "+faststart",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    str(self.dst_path),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log,
            )

    def wait(self, timeout_s=None):
        """True only if ffmpeg finished cleanly and wrote a non-empty file; never raises."""
        if self.process is None:
            return False
        try:
            self.process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            logger.error(f"PLAY_COPY_FAILED {self.src_path}: timed out after {timeout_s}s")
            return False
        if self.process.returncode != 0 or not self._written():
            try:
                text = self.log_path.read_text(errors="replace")
            except OSError:
                text = ""
            detail = text.strip().splitlines()[-5:]
            logger.error(f"PLAY_COPY_FAILED {self.src_path}: {' | '.join(detail)}")
            return False
        return True

    def _written(self):
        try:
            return self.dst_path.is_file() and self.dst_path.stat().st_size > 0
        except OSError:
            return False

    def __exit__(self, exc_type, exc, tb):
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        return False


JPEG_END = b"\xff\xd9"


class FramesFailed(RuntimeError):
    """ffmpeg could not cut the report's frames: the file is not a video, it timed out, or it
    wrote nothing."""


def make_frames(path, seconds):
    """One JPEG per whole second of the video, for the report (M3g §2a).

    `seconds` is the result's duration (its per-second series length), so frame t lines up with
    value t. The long edge is 640 px at most and never upscaled, and the other edge is even.
    Raises FramesFailed on a non-zero exit, a 120 s timeout, or no frames.
    """
    if seconds < 1:
        raise FramesFailed(f"no frames to cut from {path}")
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-vf",
                "fps=1,scale='if(gte(iw,ih),min(iw,640),-2)':'if(gte(iw,ih),-2,min(ih,640))'",
                "-frames:v",
                str(seconds),
                "-q:v",
                "5",
                "-f",
                "image2pipe",
                "-c:v",
                "mjpeg",
                "-",
            ],
            capture_output=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired as err:
        raise FramesFailed(f"ffmpeg timed out cutting frames from {path}") from err
    # Each JPEG ends with the FFD9 marker, which cannot occur inside JPEG data (FF is stuffed).
    frames = [part + JPEG_END for part in result.stdout.split(JPEG_END) if part]
    if result.returncode != 0 or not frames:
        raise FramesFailed(f"ffmpeg could not cut frames from {path}")
    return frames[:seconds]


class TimelineError(RuntimeError):
    """Predicted rows do not form one row per second of the video: never publish such a result."""


def attach_chunk_words(transcript, chunks):
    """Give each audio chunk only its own words, at their true time on the video timeline.

    `transcript`: one audio file's words, times measured from the start of the file (as WhisperX
    writes them). `chunks`: that file's audio chunks (`start` on the video timeline, `offset` into
    the file, `duration`). A word belongs to the chunk with offset <= word start < offset +
    duration and moves to chunk start + (word start - offset). It also carries the chunk's other
    fields (not frequency, filepath, type, start, duration or offset) and type "Word".

    This replaces tribev2's ExtractWordsFromAudio attachment (commit af58661), which gives every
    chunk the whole transcript shifted by start + offset: each word then reappears in every later
    chunk, 2 * chunk start too late (M2a spec 7a).
    """
    import pandas as pd

    not_copied = {"frequency", "filepath", "type", "start", "duration", "offset"}
    words = pd.DataFrame(transcript).reset_index(drop=True)
    pieces = []
    owner = pd.Series(-1, index=words.index)
    for i, chunk in enumerate(pd.DataFrame(chunks).to_dict("records")):
        lo = float(chunk["offset"])
        inside = (words["start"] >= lo) & (words["start"] < lo + float(chunk["duration"]))
        if (inside & (owner >= 0)).any():
            raise ValueError("audio chunks overlap: a word would be attached twice")
        owner[inside] = i
        if not inside.any():
            continue
        piece = words[inside].copy()
        piece["start"] = float(chunk["start"]) + (piece["start"] - lo)
        for field, value in chunk.items():
            if field not in not_copied and field not in piece.columns:
                piece[field] = value
        piece["type"] = "Word"
        pieces.append(piece)
    dropped = int((owner < 0).sum())
    if dropped:
        logger.warning(f"{dropped} transcribed word(s) fall outside every audio chunk: dropped")
    if not pieces:
        return pd.DataFrame(columns=[*words.columns, "type"])
    return pd.concat(pieces, ignore_index=True)


def order_by_time(preds, starts, duration, tr=1.0):
    """Rows of `preds` sorted by start time, after checking they form one row per `tr` seconds.

    Raises TimelineError unless the starts (rounded to 1 ms) are exactly 0, tr, 2*tr, ... with no
    gap or repeat, the last start is below `duration` but no more than 2*tr before it, and there
    is one start per row. A safety net: a misaligned timeline must fail loudly, never reach a
    result.
    """
    preds = np.asarray(preds)
    starts = np.round(np.asarray(starts, dtype=float), 3)
    if preds.ndim == 0 or len(preds) == 0 or len(starts) == 0:
        raise TimelineError("no predicted rows")
    if len(starts) != len(preds):
        raise TimelineError(f"{len(starts)} start times for {len(preds)} rows")
    order = np.argsort(starts, kind="stable")
    starts = starts[order]
    expected = np.round(np.arange(len(starts)) * tr, 3)
    if not np.array_equal(starts, expected):
        bad = int(np.flatnonzero(starts != expected)[0])
        raise TimelineError(f"row {bad} starts at {starts[bad]} s, expected {expected[bad]} s")
    last, end = starts[-1], round(float(duration), 3)
    if last >= end:
        raise TimelineError(f"a row starts at {last} s, at or after the video's end ({end} s)")
    if last < round(end - 2 * tr, 3):
        raise TimelineError(f"the rows stop at {last} s, well before the video's end ({end} s)")
    return preds[order]


class _FakeEvents:
    """Fake mode's stand-in for an events table: which file, its size, and whether audio is kept."""

    def __init__(self, path, size, audio):
        self.path, self.size, self.audio = str(path), size, audio


_word_step_class = None


def _chunk_aware_word_step():
    """tribev2's ExtractWordsFromAudio with only the word attachment replaced (built once)."""
    global _word_step_class
    if _word_step_class is not None:
        return _word_step_class

    from pathlib import Path

    import pandas as pd
    from tribev2.eventstransforms import ExtractWordsFromAudio

    class ChunkAwareExtractWordsFromAudio(ExtractWordsFromAudio):
        def _run(self, events):
            if "Word" in events.type.unique():
                logger.warning("Words already present in the events, skipping")
                return events
            audio = events.loc[events.type == "Audio"]
            attached = []
            for wav in audio.filepath.unique():
                # Same transcription and .tsv cache as tribev2's step.
                tsv = Path(wav).with_suffix(".tsv")
                if tsv.exists() and not self.overwrite:
                    try:
                        transcript = pd.read_csv(tsv, sep="\t")
                    except pd.errors.EmptyDataError:
                        transcript = pd.DataFrame()
                else:
                    transcript = self._get_transcript_from_audio(Path(wav), self.language)
                    transcript.to_csv(tsv, sep="\t", index=False)
                    logger.info(f"Wrote transcript to {tsv}")
                if len(transcript) == 0:
                    continue
                words = attach_chunk_words(transcript, audio.loc[audio.filepath == wav])
                if len(words):
                    words["language"] = self.language
                    attached.append(words)
            if not attached:
                logger.warning("No transcripts found, skipping")
                return events
            return pd.concat([events, *attached], ignore_index=True)

    _word_step_class = ChunkAwareExtractWordsFromAudio
    return _word_step_class


def audio_piece_length(duration, max_piece=60.0):
    """The equal piece length that splits `duration` into pieces of at most `max_piece` seconds.

    The audio model's GPU memory grows fast with piece length, so an audio track longer than
    60 s is split into `ceil(duration / max_piece)` equal pieces. Equal pieces keep every piece
    <= max_piece and, above 60 s, at least 30 s, so no cut is dropped.
    """
    if duration <= 0:
        raise ValueError(f"audio duration must be positive, got {duration}")
    return duration / math.ceil(duration / max_piece)


def build_events(video_path):
    """The events tribev2 needs for one video: video, audio and words on one timeline.

    The same steps, parameters and order as tribev2's demo_utils.get_audio_and_text_events at the
    pinned commit af58661 (requirements/model.txt), except for two changes: the word step is
    ChunkAwareExtractWordsFromAudio, and audio is split into equal pieces of at most 60 s (see
    audio_piece_length) so a long track never becomes one memory-hungry piece. Re-check this list
    whenever the pin changes.
    In fake mode: a stand-in that needs no model.
    """
    if fake_mode():
        return _FakeEvents(video_path, os.path.getsize(video_path), audio=True)

    import pandas as pd
    from neuralset.events.transforms import (
        AddContextToWords,
        AddSentenceToWords,
        AddText,
        ChunkEvents,
        ExtractAudioFromVideo,
        RemoveMissing,
    )
    from neuralset.events.utils import standardize_events

    logger.info(f"Building events from {video_path}...")
    video = {
        "type": "Video",
        "filepath": str(video_path),
        "start": 0,
        "timeline": "default",
        "subject": "default",
    }
    events = standardize_events(pd.DataFrame([video]))
    events = ExtractAudioFromVideo()(events)
    audio = events.loc[events.type == "Audio"]
    if len(audio) > 1:
        raise RuntimeError(f"expected at most one Audio event from one video, got {len(audio)}")
    steps = []
    if len(audio) == 1:  # a video with no sound track has no Audio event
        piece = audio_piece_length(float(audio.iloc[0]["duration"]))
        steps.append(
            ChunkEvents(event_type_to_chunk="Audio", max_duration=piece, min_duration=piece / 2)
        )
    steps += [
        ChunkEvents(event_type_to_chunk="Video", max_duration=60, min_duration=30),
        _chunk_aware_word_step()(),
        AddText(),
        AddSentenceToWords(max_unmatched_ratio=0.05),
        AddContextToWords(sentence_only=False, max_context_len=1024, split_field=""),
        RemoveMissing(),
    ]
    for step in steps:
        events = step(events)
    return standardize_events(events)


def without_audio(events):
    """The events of the no-audio pass: the video rows only.

    Same video file as the with-audio pass, so tribev2 reuses the video features it has already
    computed instead of encoding the video again. The video model does not use the soundtrack.
    """
    if isinstance(events, _FakeEvents):
        return _FakeEvents(events.path, events.size, audio=False)
    return events.loc[events.type == "Video"].copy()


def _fake_predictions(events, rows):
    """Smooth stand-in output, so local previews look like real predictions: 24 overlapping blobs
    on the fsaverage5 sphere (bundled with nilearn, no download) of mixed size and strength, whose
    strength drifts slowly, plus a little noise. Peaks reach about twice the default colour_range,
    like a calibrated map: most of a hot area shows red to yellow, with small bright cores. Seeded
    from the file's size, so the same video gives the same output; the no-audio pass is the same
    blobs at 60% strength with its own noise (seed size + 1).
    """
    from nilearn import datasets

    sphere = datasets.load_fsaverage("fsaverage5")["sphere"].parts
    xyz = np.vstack([sphere["left"].coordinates, sphere["right"].coordinates])
    xyz /= np.linalg.norm(xyz, axis=1, keepdims=True)
    rng = np.random.default_rng(events.size)
    blobs = 24
    picks = rng.integers(0, N_VERTICES, blobs)
    width = rng.uniform(0.02, 0.07, blobs)  # 1 - cos(angle): about 11 to 21 degrees across
    spread = np.exp(-(((1 - xyz @ xyz[picks].T) / width) ** 2))  # (20484, blobs)
    left = np.arange(N_VERTICES) < N_VERTICES // 2
    spread[left[:, None] != left[picks][None, :]] = 0  # each blob stays on its own hemisphere
    t = np.arange(rows)[:, None]
    period, phase = rng.uniform(12, 30, blobs), rng.uniform(0, 2 * np.pi, blobs)
    amplitude = rng.uniform(0.15, 0.5, blobs)
    strength = amplitude * np.sin(2 * np.pi * t / period + phase)  # (rows, blobs)
    field = strength @ spread.T
    if not events.audio:
        field *= 0.6
    noise = np.random.default_rng(events.size + (0 if events.audio else 1))
    return field + 0.01 * noise.standard_normal((rows, N_VERTICES))  # below the map's fade-in


def predict(events, duration):
    """TRIBE v2 predictions, one row per second of video in time order: array (n, 20484).

    In fake mode: shape (ceil(duration), 20484), see _fake_predictions. Each fake call sleeps
    FAKE_INFERENCE_SECONDS (default 0; only with FAKE_INFERENCE on), so a CPU rehearsal has a job
    long enough to interrupt.
    """
    if isinstance(events, _FakeEvents):
        if fake_mode():
            time.sleep(float(os.environ.get("FAKE_INFERENCE_SECONDS") or 0))
        return _fake_predictions(events, math.ceil(duration))

    logger.info("Running predict()...")
    preds, segments = model.predict(events=events)
    return order_by_time(preds, [s.start for s in segments], duration, tr=model.data.TR)


def reset_gpu_peak():
    """Start a new peak-memory count, so gpu_info() reports one job's peak. No-op without a GPU."""
    try:
        import torch
    except ImportError:  # laptop / fake mode: torch is not installed
        return
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats(0)


def gpu_info():
    """GPU name and peak memory use since reset_gpu_peak(), or None when there is no CUDA GPU."""
    try:
        import torch
    except ImportError:  # laptop / fake mode: torch is not installed
        return None

    if not torch.cuda.is_available():
        return None
    return {
        "device": torch.cuda.get_device_name(0),
        "peak_vram_gb": round(torch.cuda.max_memory_allocated(0) / 1e9, 2),
    }
