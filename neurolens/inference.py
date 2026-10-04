"""Model + atlas loading and inference. Heavy libraries are imported inside functions only."""

import json
import logging
import math
import os
import subprocess
import time

import numpy as np

from neurolens import settings
from neurolens.engagement import build_roi_masks

logger = logging.getLogger("neurolens")

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


def load_model(cfg=None):
    """Load the TRIBE v2 model (skipped in fake mode) and the Destrieux atlas, build ROI masks."""
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
            cache_folder=str(paths["models"]),
        )
        logger.info("TRIBE v2 loaded.")

    logger.info("Loading Destrieux atlas...")
    from nilearn import datasets as nl_datasets

    destrieux = nl_datasets.fetch_atlas_surf_destrieux()
    labels_lh = np.array(destrieux["map_left"])
    labels_rh = np.array(destrieux["map_right"])
    label_names = destrieux["labels"]
    labels_full = np.concatenate([labels_lh, labels_rh])

    _roi_masks = build_roi_masks(labels_full, label_names)
    logger.info("Atlas and ROI masks ready.")


class UnreadableVideo(ValueError):
    """ffprobe could not produce a duration: not a video, corrupt, or no duration in the header."""


def probe_duration(path):
    """Video length in seconds, measured with ffprobe. Raises UnreadableVideo if unreadable."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as err:
        raise UnreadableVideo(f"ffprobe timed out on {path}") from err
    try:
        info = json.loads(result.stdout)
        duration = float(info["format"]["duration"])
        has_video = any(s.get("codec_type") == "video" for s in info.get("streams", []))
    except (ValueError, KeyError, TypeError, AttributeError) as err:  # JSONDecodeError too
        raise UnreadableVideo(f"ffprobe could not read a duration from {path}") from err
    if not has_video:
        # e.g. an audio-only .mp4: it would pass the length check, then fail in the model
        raise UnreadableVideo(f"{path} has no video stream")
    if not math.isfinite(duration):
        raise UnreadableVideo(f"ffprobe gave a non-finite duration for {path}")
    return duration


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


def build_events(video_path):
    """The events tribev2 needs for one video: video, audio and words on one timeline.

    The same steps, parameters and order as tribev2's demo_utils.get_audio_and_text_events at the
    pinned commit af58661 (requirements/model.txt), except that the word step is
    ChunkAwareExtractWordsFromAudio. Re-check this list whenever the pin changes.
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
    steps = [
        ExtractAudioFromVideo(),
        ChunkEvents(event_type_to_chunk="Audio", max_duration=60, min_duration=30),
        ChunkEvents(event_type_to_chunk="Video", max_duration=60, min_duration=30),
        _chunk_aware_word_step()(),
        AddText(),
        AddSentenceToWords(max_unmatched_ratio=0.05),
        AddContextToWords(sentence_only=False, max_context_len=1024, split_field=""),
        RemoveMissing(),
    ]
    video = {
        "type": "Video",
        "filepath": str(video_path),
        "start": 0,
        "timeline": "default",
        "subject": "default",
    }
    events = standardize_events(pd.DataFrame([video]))
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


def predict(events, duration):
    """TRIBE v2 predictions, one row per second of video in time order: array (n, 20484).

    In fake mode: seeded random numbers of shape (ceil(duration), 20484), seeded from the file's
    size (plus 1 for the no-audio pass), so the same video gives the same output. Each fake call
    sleeps FAKE_INFERENCE_SECONDS (default 0; only with FAKE_INFERENCE on), so a CPU rehearsal
    has a job long enough to interrupt.
    """
    if isinstance(events, _FakeEvents):
        if fake_mode():
            time.sleep(float(os.environ.get("FAKE_INFERENCE_SECONDS") or 0))
        rng = np.random.default_rng(events.size + (0 if events.audio else 1))
        return rng.standard_normal((math.ceil(duration), N_VERTICES))

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
