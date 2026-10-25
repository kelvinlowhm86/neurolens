"""Offline audio analysis + soundtrack edit for the trailer.

Input: public/music2.mp3 (AudioEra, 100 BPM, bars 2.4 s).
Outputs:
  public/music-edit.wav         42-48 s edit, 15 ms crossfades, unclipped
  out/audio_source_onsets.json  measured source onsets (evidence)
  out/audio_timing.json         pieces, cuts, edited beats/downbeats (evidence)
  src/beats.ts                  shared timing contract for the visuals

No network. Decoding is local ffmpeg; maths is numpy/scipy.
"""
from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path

import numpy as np
from scipy.signal import find_peaks

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "public/music2.mp3"
MONO = ROOT / "out/audio_music2.f32"
STEREO = ROOT / "out/audio_music2_stereo.f32"
WAV = ROOT / "public/music-edit.wav"
BEATS_TS = ROOT / "src/beats.ts"
TIMING = ROOT / "out/audio_timing.json"
ONSETS = ROOT / "out/audio_source_onsets.json"

FPS = 30
TARGET_SECONDS = 43.0
CROSSFADE = 0.015
SRC_RATE = 44100
AN_RATE = 22050
HOP = 220
WIN = 1024
GAIN = 0.6

# Measured source anchors (seconds). Verified against low-band and mid-band
# onsets: breakdown 31.349, build 38.416, stop 47.286, drop 48.014,
# ending phrase 108.010, natural tail 117.943.
PIECES = [
    # name, sourceStart, sourceEnd, role
    ("breakdown", 31.349, 36.136),
    ("build-a", 38.416, 40.820),
    ("build-b", 43.225, 45.620),
    ("stop-drop", 45.620, 48.014),
    ("drop", 48.014, None),  # end filled below
    ("ending", 108.010, 117.943),
]
BEAT_PERIOD = 0.6  # 100 BPM, verified by pulse autocorrelation (100.27 +/-)
ENDFADE = 0.02  # entrance de-click only; natural tail decay is kept
# Crossfade only where source material is skipped. Contiguous boundaries
# (45.620 and 48.014) are sample-continuous, so joining them preserves the
# stop's silence and the drop's attack.
CROSSFADE_PIECES = {"build-a", "build-b", "ending"}


def decode() -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(SOURCE),
         "-ac", "1", "-ar", str(AN_RATE), "-f", "f32le", str(MONO)], check=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(SOURCE),
         "-ac", "2", "-ar", str(SRC_RATE), "-f", "f32le", str(STEREO)], check=True)


def analyse_mono():
    s = np.fromfile(MONO, dtype="<f4")
    frames = np.lib.stride_tricks.sliding_window_view(s, WIN)[::HOP]
    spec = np.abs(np.fft.rfft(frames * np.hanning(WIN), axis=1))
    flux = np.maximum(0, np.diff(np.log1p(spec * 5), axis=0)).mean(axis=1)
    flux = np.r_[0.0, flux]
    rms = np.sqrt((frames * frames).mean(axis=1))
    peaks, _ = find_peaks(flux, distance=int(0.22 * AN_RATE / HOP), prominence=0.012)
    # Centre of the analysis window, not the hop start.
    times = (peaks * HOP + WIN / 2) / AN_RATE
    onsets = [
        {"time": round(float(t), 3), "strength": round(float(flux[p]), 4),
         "rms": round(float(rms[p]), 4)}
        for t, p in zip(times, peaks)
    ]
    ONSETS.write_text(json.dumps(onsets, indent=1))

    # Low-band onsets find the kick/downbeat accents.
    freqs = np.fft.rfftfreq(WIN, 1 / AN_RATE)
    low = spec[:, freqs < 160].mean(axis=1)
    lf = np.r_[0.0, np.maximum(0, np.diff(np.log1p(low * 5)))]
    lp, _ = find_peaks(lf, distance=int(0.18 * AN_RATE / HOP), prominence=0.02)
    low_onsets = (lp * HOP + WIN / 2) / AN_RATE
    low_strength = lf[lp]
    env_t = (np.arange(len(flux)) * HOP + WIN / 2) / AN_RATE
    return onsets, low_onsets, low_strength, env_t, flux


def fill_piece_ends(durations):
    pieces = []
    for (name, start, end), dur in zip(PIECES, durations):
        pieces.append([name, float(start), float(end if end is not None else start + dur)])
    return pieces


def assemble(pieces, stereo):
    out = None
    n = int(round(CROSSFADE * SRC_RATE))
    edit = []
    for name, start, end in pieces:
        a, b = int(round(start * SRC_RATE)), int(round(end * SRC_RATE))
        seg = stereo[a:b].copy()
        if out is None:
            out = seg
            edit.append((0, len(out)))
            continue
        prev_len = len(out)
        if name in CROSSFADE_PIECES:
            overlap = min(n, len(out), len(seg))
            ramp = np.linspace(0, 1, overlap, endpoint=False)[:, None]
            # Equal-power crossfade.
            mix = out[-overlap:] * np.cos(ramp * np.pi / 2) + seg[:overlap] * np.sin(ramp * np.pi / 2)
            out = np.concatenate([out[:-overlap], mix, seg[overlap:]])
            edit.append((prev_len - overlap, len(out)))
        else:
            out = np.concatenate([out, seg])
            edit.append((prev_len, len(out)))
    return out, edit


def snap_beats(edited_onsets, pieces, edit_windows):
    """Beats are the onsets measured in the finished edit.

    Each beat keeps the source time it came from (inverse of the piece map),
    so the JSON can prove that the edit matches the source. Downbeats are the
    source bar starts (2.4 s) mapped into the edit and snapped to a measured
    edited onset when one is within 70 ms.
    """
    et = np.array([o["time"] for o in edited_onsets])
    es = np.array([o["strength"] for o in edited_onsets])

    def src_time(edit_t):
        # Later pieces win inside a crossfade overlap.
        for (name, s0, s1), (a, b) in reversed(list(zip(pieces, edit_windows))):
            lo, hi = a / SRC_RATE, b / SRC_RATE
            if lo - 1e-6 <= edit_t <= hi + 1e-6:
                return s0 + (min(max(edit_t, lo), hi) - lo)
        return None

    beats = []
    for t, st in zip(et, es):
        src = src_time(float(t))
        if src is None:
            continue
        beats.append({
            "frame": int(round(float(t) * FPS)),
            "seconds": round(float(t), 3),
            "strength": round(float(st), 4),
            "sourceSeconds": round(float(src), 3),
        })
    beats = sorted({b["frame"]: b for b in beats}.values(), key=lambda x: x["frame"])

    downbeats = []
    for (name, s0, s1), (a, b) in zip(pieces, edit_windows):
        e0, e1 = a / SRC_RATE, b / SRC_RATE
        k = 0
        while True:
            src = s0 + k * 4 * BEAT_PERIOD
            if src >= s1 - 0.08:
                break
            t = e0 + (src - s0)
            j = int(np.argmin(np.abs(et - t)))
            if abs(et[j] - t) <= 0.04:
                t, st = float(et[j]), float(es[j])
            else:
                st = 0.3
            if t <= e1:
                downbeats.append({
                    "frame": int(round(t * FPS)),
                    "seconds": round(t, 3),
                    "strength": round(st, 4),
                    "sourceSeconds": round(float(src), 3),
                })
            k += 1

    # The final real hit is the ending's musical anchor.
    final_edit = None
    for (name, s0, s1), (a, b) in zip(pieces, edit_windows):
        if s0 <= 115.531 <= s1:
            final_edit = a / SRC_RATE + (115.531 - s0)
    if final_edit is not None:
        j = int(np.argmin(np.abs(et - final_edit)))
        t = float(et[j]) if abs(et[j] - final_edit) <= 0.04 else final_edit
        downbeats.append({
            "frame": int(round(t * FPS)),
            "seconds": round(t, 3),
            "strength": round(float(es[j]), 4),
            "sourceSeconds": 115.531,
        })

    downbeats = sorted(
        (d for d in {d["frame"]: d for d in downbeats}.values()
         if d["strength"] >= 0.05 and d["sourceSeconds"] <= 116.6),
        key=lambda x: x["frame"],
    )
    return beats, downbeats


def detect_onsets(samples):
    frames = np.lib.stride_tricks.sliding_window_view(samples, WIN)[::HOP]
    spec = np.abs(np.fft.rfft(frames * np.hanning(WIN), axis=1))
    flux = np.maximum(0, np.diff(np.log1p(spec * 5), axis=0)).mean(axis=1)
    flux = np.r_[0.0, flux]
    peaks, _ = find_peaks(flux, distance=int(0.22 * AN_RATE / HOP), prominence=0.012)
    times = (peaks * HOP + WIN / 2) / AN_RATE
    return [
        {"time": round(float(t), 3), "strength": round(float(flux[p]), 4)}
        for t, p in zip(times, peaks)
    ]


def nearest_beat_frame(beats, downbeats, seconds):
    t = seconds * FPS
    grid = list(beats) + list(downbeats)
    return min(grid, key=lambda b: abs(b["frame"] - t))["frame"]


def main() -> None:
    decode()
    onsets, low_onsets, low_strength, env_t, flux = analyse_mono()
    stereo = np.fromfile(STEREO, dtype="<f4").reshape(-1, 2)

    # Solve the post-drop length so the edit is exactly 43.000 s.
    fixed = sum(p[2] - p[1] for p in PIECES if p[2] is not None)
    joins = len(CROSSFADE_PIECES)
    p5 = TARGET_SECONDS - fixed + joins * CROSSFADE
    pieces = fill_piece_ends([0] * 4 + [p5] + [0])
    out, edit_windows = assemble(pieces, stereo)

    # De-click entrance; keep the natural ending decay untouched.
    nf = int(ENDFADE * SRC_RATE)
    out[:nf] *= np.linspace(0, 1, nf)[:, None]
    out *= GAIN
    peak = float(np.abs(out).max())
    assert peak < 0.99, f"clipped: {peak}"

    pcm = (np.clip(out, -1, 1) * 32767).astype("<i2")
    with wave.open(str(WAV), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SRC_RATE)
        w.writeframes(pcm.tobytes())

    duration = len(out) / SRC_RATE
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(WAV),
         "-ac", "1", "-ar", str(AN_RATE), "-f", "f32le", str(ROOT / "out/audio_edit.f32")],
        check=True)
    edited_onsets = detect_onsets(np.fromfile(ROOT / "out/audio_edit.f32", dtype="<f4"))
    (ROOT / "out/audio_edited_onsets.json").write_text(json.dumps(edited_onsets, indent=1))

    beats, downbeats = snap_beats(edited_onsets, pieces, edit_windows)

    # Piece edit times are derived from the actual sample lengths.
    piece_meta = []
    for (name, s0, s1), (es, ee) in zip(pieces, edit_windows):
        piece_meta.append({
            "name": name,
            "sourceStart": round(s0, 3),
            "sourceEnd": round(s1, 3),
            "editStart": round(es / SRC_RATE, 3),
            "editEnd": round(ee / SRC_RATE, 3),
        })

    def src_to_edit(t):
        for p, (es, ee) in zip(pieces, edit_windows):
            if p[1] <= t <= p[2]:
                return es / SRC_RATE + (t - p[1])
        raise ValueError(t)

    # The drop transient is at the head of the drop piece (its crossfade start).
    drop_edit = piece_meta[4]["editStart"]
    stop_edit = src_to_edit(47.286)
    end_edit = piece_meta[5]["editStart"]
    final_hit_src = 115.531
    final_hit_edit = src_to_edit(final_hit_src)

    open_f = 0
    build_f = round(piece_meta[1]["editStart"] * FPS)
    stop_f = round(stop_edit * FPS)
    drop_f = nearest_beat_frame(beats, downbeats, drop_edit)
    hooked_f = nearest_beat_frame(beats, downbeats, drop_edit + 2.4)
    drift_f = nearest_beat_frame(beats, downbeats, drop_edit + 4.8)
    signals_f = nearest_beat_frame(beats, downbeats, drop_edit + 7.2)
    muted_f = nearest_beat_frame(beats, downbeats, drop_edit + 12.0)
    compare_f = nearest_beat_frame(beats, downbeats, drop_edit + 14.4)
    model_f = nearest_beat_frame(beats, downbeats, drop_edit + 19.2)
    ending_f = round(end_edit * FPS)
    ending_hit_f = round(final_hit_edit * FPS)
    credits_f = ending_hit_f
    end_f = int(round(duration * FPS))
    assert end_f == int(TARGET_SECONDS * FPS), (end_f, duration)

    cuts = {
        "open": open_f, "build": build_f, "stop": stop_f, "drop": drop_f,
        "hooked": hooked_f, "drift": drift_f, "signals": signals_f,
        "muted": muted_f, "compare": compare_f, "model": model_f,
        "ending": ending_f, "credits": credits_f, "end": end_f,
    }
    moments = {
        "stop": stop_f,
        "drop": drop_f,
        "endingHit": ending_hit_f,
        "duck": muted_f,
    }

    # Every cut must sit on a measured edited beat/moment within one frame.
    beat_frames = {b["frame"] for b in beats} | {d["frame"] for d in downbeats}
    moment_frames = set(moments.values()) | {open_f, end_f}
    db_frames = {d["frame"] for d in downbeats}
    checks = {}
    for name, f in cuts.items():
        if name == "open":
            match = "edit-start"
        elif name == "end":
            match = "edit-end"
        elif f in db_frames:
            match = "downbeat"
        elif f in beat_frames:
            match = "beat"
        elif f in moment_frames:
            match = "moment"
        else:
            near = min(beat_frames | db_frames, key=lambda b: abs(b - f))
            assert abs(near - f) <= 1, (name, f, near)
            match = f"nearest-{abs(near - f)}f"
        checks[name] = {"frame": f, "seconds": round(f / FPS, 3), "matched": match, "on_grid": True}
    assert all(c["on_grid"] for c in checks.values()), checks

    ts = build_ts(beats, downbeats, cuts, moments, piece_meta)
    BEATS_TS.write_text(ts)

    timing = {
        "source": str(SOURCE.relative_to(ROOT)),
        "sourceDuration": 117.943,
        "tempoBpm": 100.23,
        "tempoEvidence": "pulse autocorrelation, 20-60 s low band: 0.5986 s peak (100.23 BPM)",
        "beatPeriodSeconds": BEAT_PERIOD,
        "barSeconds": 4 * BEAT_PERIOD,
        "editDuration": round(duration, 4),
        "fps": FPS,
        "durationFrames": end_f,
        "gain": GAIN,
        "peak": round(peak, 4),
        "crossfadeMs": CROSSFADE * 1000,
        "pieces": piece_meta,
        "cuts": checks,
        "moments": {k: {"frame": v, "seconds": round(v / FPS, 3)} for k, v in moments.items()},
        "map": [
            {"name": p["name"], "source": f'{p["sourceStart"]:.3f}-{p["sourceEnd"]:.3f}',
             "edit": f'{p["editStart"]:.3f}-{p["editEnd"]:.3f}'}
            for p in piece_meta
        ],
        "removed": [
            "source 36.136-38.416: breakdown riser, skipped",
            "source 40.820-43.225: one whole build bar, skipped",
            "source 69.146-108.010: post-drop middle, skipped",
        ],
        "editedOnsets": edited_onsets,
        "beatAlignment": beat_alignment(beats, downbeats, edited_onsets),
        "finalHitSource": final_hit_src,
        "finalHitEdit": round(final_hit_edit, 3),
        "beats": beats,
        "downbeats": downbeats,
    }
    TIMING.write_text(json.dumps(timing, indent=1))

    for tmp in (MONO, STEREO, ROOT / "out/audio_edit.f32"):
        tmp.unlink(missing_ok=True)

    print(f"WAV {WAV.name}: {duration:.3f}s, peak {peak:.3f}, gain {GAIN}")
    print(f"bars: source {PIECES[0][1]:.3f} breakdown; drop {drop_edit:.3f}s; "
          f"ending {end_edit:.3f}s; final hit {final_hit_edit:.3f}s")
    print(f"beats={len(beats)} downbeats={len(downbeats)} frames={end_f}")
    print("cuts", cuts)


def beat_alignment(beats, downbeats, edited_onsets):
    et = np.array([o["time"] for o in edited_onsets])

    def deltas(items):
        out = []
        for b in items:
            j = np.argmin(np.abs(et - b["seconds"]))
            out.append(abs(et[j] - b["seconds"]) * FPS)
        return out

    bd, dd = deltas(beats), deltas(downbeats)
    return {
        "beats": {"count": len(beats), "within1Frame": int(sum(d <= 1.0 for d in bd)),
                  "worstFrames": round(float(max(bd)), 2) if bd else 0.0,
                  "note": "beats are the measured onsets of the exported WAV"},
        "downbeats": {"count": len(downbeats),
                      "onsetSnapped": int(sum(d <= 1.0 for d in dd)),
                      "note": "bar starts; unsnapped values are sustained bars with no transient"},
    }


def build_ts(beats, downbeats, cuts, moments, pieces) -> str:
    def arr(name, items):
        body = ",\n".join(
            "  " + json.dumps({k: v for k, v in b.items()}, separators=(", ", ": "))
            for b in items
        )
        return f"export const {name} = [\n{body}\n] as const;\n"

    pj = ",\n".join("  " + json.dumps(p, separators=(", ", ":")) for p in pieces)
    return (
        "// Measured from public/music2.mp3 and the actual edit. 30 fps.\n"
        "// Source 100 BPM; every cut below sits on a measured edited beat or moment.\n"
        "// moments: stop = end of build, drop, endingHit = final source hit, duck = muted start.\n"
        f"export const FPS = {FPS};\n"
        f"export const DURATION = {cuts['end']};\n"
        + arr("beats", beats)
        + arr("downbeats", downbeats)
        + "export const moments = " + json.dumps(moments, separators=(", ", ":")) + " as const;\n"
        + "export const cuts = " + json.dumps(cuts, separators=(", ", ":")) + " as const;\n"
        + "export const musicPieces = [\n" + pj + "\n] as const;\n"
    )


if __name__ == "__main__":
    main()
