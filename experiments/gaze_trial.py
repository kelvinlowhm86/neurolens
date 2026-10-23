"""Where does an average viewer look? A ViNet-S saliency heatmap for each clip (trial only).

TRIBE v2 tells us which brain areas respond, not where the eyes go. ViNet-S is a video
saliency model trained on DHF1K; its DHF1K checkpoint predicts a heatmap of where an average
viewer would look in each frame. This script runs that model over a handful of ads so the
result can be watched and judged before deciding whether a gaze view is worth building.

    python experiments/gaze_trial.py CLIP [CLIP ...] --out DIR [--vinet DIR]

The model and its code live outside the repo (ViNet-S is CC BY-NC-SA 4.0, so we import its
model class from a clone under --vinet rather than copying it). The Slurm job
experiments/gaze_trial.sbatch fetches the pinned commit and the DHF1K checkpoint.

Each clip gives, in DIR:
  <stem>.<variant>.mp4   the clip with the heatmap blended over it and the centre square drawn
  <stem>.<variant>.csv   per-second outside share, likeness to a centre blob, peak position
  summary.csv            one row per clip+variant
Vertical clips get two variants: "stretch" (what ViNet's own resize does to a portrait frame)
and "pad" (letterboxed to 16:9, then the map cut back to the picture). Landscape clips only get
"stretch". A finished clip+variant (mp4 and csv both present) is skipped on a rerun, so a job
stopped by the time limit can be resumed with the same command.

The preprocessing mirrors ViNet-S's DHF1K test path: frames at 30 fps, resized to 224x384 with
no aspect correction, RGB, ImageNet mean/std, a 32-frame window ending at each frame (the first
frames repeat frame 0), the 224x384 output resized to the frame size and Gaussian-blurred with
an 11x11 kernel. See models/vinet/repo/ViNet_S/ViNet_S_dataloader.py (DHF1KDataset, mode "test"),
ViNet_S_inferences_metrics.py and utils.py at the pinned commit.
"""

import argparse
import csv
import json
import shutil
import subprocess
import sys
import tempfile
import time
from collections import deque
from pathlib import Path

import numpy as np

FPS = 30
MODEL_H, MODEL_W = 224, 384
CLIP_FRAMES = 32
BLUR_KERNEL = 11
MAX_SHORT = 720
ALPHA = 0.45
CHECKPOINT_NAME = "DHF1K_vinet_s_rootgrouped_32_bs8_kld_cc.pt"
CHECKPOINT_GDRIVE_ID = "12UeAsdiD2xPLmoLRDcE_HjAUjxFdmw5N"
VARIANTS = ("stretch", "pad")
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
CSV_COLUMNS = ["second", "outside_share", "centre_cc", "peak_x", "peak_y"]
# Viewers mostly look at the middle, so a plain centre blob is the guess any gaze model must
# beat. Its spread is a quarter of the frame on each axis.
CENTRE_SIGMA = 0.25
SUMMARY_COLUMNS = [
    "clip",
    "variant",
    "width",
    "height",
    "seconds",
    "mean_outside_share",
    "mean_centre_cc",
    "share_of_seconds_over_half",
    "processing_seconds",
    "gpu_peak_mb",
]


def centre_square(width, height):
    """The square TRIBE v2 sees, in the original frame's pixels.

    V-JEPA 2 resizes the short edge to 292 px and keeps the centre 256x256, so the square's
    side is short_edge * 256 / 292, centred in the frame.
    """
    side = min(width, height) * 256 / 292
    x0 = (width - side) / 2
    y0 = (height - side) / 2
    return x0, y0, x0 + side, y0 + side


def _axis_coverage(size, start, end):
    """Fraction of each pixel row/column [i, i+1) that lies inside [start, end)."""
    pixels = np.arange(size, dtype=float)
    overlap = np.minimum(pixels + 1.0, end) - np.maximum(pixels, start)
    return np.clip(overlap, 0.0, 1.0)


def outside_share(saliency_map, box):
    """Share of the map's total mass that lies outside `box` (x0, y0, x1, y1) in pixels.

    The map may be at any non-negative scale. A map that sums to zero gives 0.0, never NaN.
    Pixels are weighted by their fractional overlap with the box, so a uniform map gives exactly
    one minus the box's area fraction even when the box edges fall between pixels.
    """
    saliency = np.asarray(saliency_map, dtype=float)
    if saliency.ndim != 2:
        raise ValueError(f"saliency_map must be 2-D, got shape {saliency.shape}")
    total = float(saliency.sum())
    if not np.isfinite(total) or total <= 0.0:
        return 0.0
    x0, y0, x1, y1 = box
    inside = np.outer(
        _axis_coverage(saliency.shape[0], y0, y1),
        _axis_coverage(saliency.shape[1], x0, x1),
    )
    return float(np.sum(saliency * (1.0 - inside)) / total)


def centre_blob(height, width):
    """A Gaussian at the frame centre, spread CENTRE_SIGMA of each side."""
    ys = (np.arange(height) + 0.5) / height - 0.5
    xs = (np.arange(width) + 0.5) / width - 0.5
    return np.exp(-(ys[:, None] ** 2 + xs[None, :] ** 2) / (2 * CENTRE_SIGMA**2))


def correlation(a, b):
    """Pearson correlation of two maps (the saliency CC metric); 0.0 when either is flat."""
    a = np.asarray(a, dtype=float).ravel()
    b = np.asarray(b, dtype=float).ravel()
    a, b = a - a.mean(), b - b.mean()
    scale = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / scale) if scale > 0 else 0.0


def peak_position(saliency_map):
    """(x, y) of the map's maximum as fractions of width and height, pixel centres as 0.5."""
    row, column = np.unravel_index(int(np.argmax(saliency_map)), saliency_map.shape)
    return (column + 0.5) / saliency_map.shape[1], (row + 0.5) / saliency_map.shape[0]


def display_size(width, height):
    """The rendered frame size: the source scaled down so its short edge is at most 720 px."""
    scale = min(1.0, MAX_SHORT / min(width, height))
    return _even(round(width * scale)), _even(round(height * scale))


def _even(value):
    """Nearest even integer at least 2 (libx264 needs even frame dimensions)."""
    value = max(2, int(value))
    return value - value % 2


def probe_clip(path):
    """(width, height, duration_seconds) of the clip's first video stream."""
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,duration:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(out.stdout)
    streams = data.get("streams") or []
    if not streams:
        raise ValueError(f"{path}: no video stream")
    stream = streams[0]
    duration = data.get("format", {}).get("duration") or stream.get("duration")
    if duration is None:
        raise ValueError(f"{path}: ffprobe reported no duration")
    return int(stream["width"]), int(stream["height"]), float(duration)


def decode_frames(path, width, height):
    """Yield the clip's frames as BGR arrays at 30 fps, scaled to (width, height)."""
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(path),
        "-vf",
        f"fps={FPS},scale={width}:{height}:flags=bicubic",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "pipe:1",
    ]
    frame_bytes = width * height * 3
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors)
        finished = False
        try:
            while True:
                raw = process.stdout.read(frame_bytes)
                if not raw:
                    finished = True
                    break
                if len(raw) != frame_bytes:
                    raise RuntimeError(
                        f"ffmpeg gave a short frame from {path} ({len(raw)} of {frame_bytes} bytes)"
                    )
                yield np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3).copy()
        finally:
            if process.poll() is None:
                process.terminate()
            process.stdout.close()
            code = process.wait()
            if finished and code != 0:
                errors.seek(0)
                detail = errors.read().decode(errors="replace").strip()
                raise RuntimeError(f"ffmpeg failed to decode {path}: {detail}")


class VideoWriter:
    """Pipe BGR frames into ffmpeg and mux the source clip's audio back in."""

    def __init__(self, path, width, height, audio_source):
        self.path = path
        self.errors = tempfile.TemporaryFile()
        command = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{width}x{height}",
            "-r",
            str(FPS),
            "-i",
            "pipe:0",
            "-i",
            str(audio_source),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(path),
        ]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=self.errors)

    def write(self, frame):
        self.process.stdin.write(frame.tobytes())

    def close(self):
        self.process.stdin.close()
        code = self.process.wait()
        self.errors.seek(0)
        detail = self.errors.read().decode(errors="replace").strip()
        self.errors.close()
        if code != 0:
            raise RuntimeError(f"ffmpeg failed to write {self.path}: {detail}")

    def abort(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait()
        try:
            self.process.stdin.close()
        except BrokenPipeError:
            pass
        self.errors.close()


def load_model(vinet_dir):
    """Import ViNet-S's model class from the clone and load the DHF1K checkpoint."""
    try:
        import torch
    except ImportError as err:  # the GPU env has torch; a bare laptop may not
        raise SystemExit(
            "torch is not installed. Install the project's model requirements in this env."
        ) from err

    code_dir = vinet_dir / "repo" / "ViNet_S"
    checkpoint = vinet_dir / CHECKPOINT_NAME
    if not (code_dir / "ViNet_S_model.py").is_file():
        raise SystemExit(
            f"ViNet-S code missing at {code_dir}. Clone the pinned commit there:\n"
            f"  git clone https://github.com/ViNet-Saliency/vinet_v2 {vinet_dir}/repo\n"
            f"  git -C {vinet_dir}/repo checkout d09066b7407d33718800d664a962683d4abd013c"
        )
    if not checkpoint.is_file():
        raise SystemExit(
            f"ViNet-S checkpoint missing at {checkpoint}. Download the public archive and\n"
            f"extract final_models/ViNet_S/vinet_s_visual_dataset_models/vinet_s_dhf1k.pt:\n"
            f"  gdown {CHECKPOINT_GDRIVE_ID} -O /tmp/vinet_models.tar.gz\n"
            "  tar -xzf /tmp/vinet_models.tar.gz -C /tmp "
            "final_models/ViNet_S/vinet_s_visual_dataset_models/vinet_s_dhf1k.pt"
        )

    if str(code_dir) not in sys.path:
        sys.path.insert(0, str(code_dir))
    from ViNet_S_model import VideoSaliencyModel

    model = VideoSaliencyModel(
        use_upsample=True,
        num_hier=3,
        num_clips=CLIP_FRAMES,
        grouped_conv=True,
        root_grouping=True,
        depth=False,
        efficientnet=False,
        BiCubic=False,
    )
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.load_state_dict(torch.load(checkpoint, map_location=device))
    model.to(device).eval()
    print(f"ViNet-S loaded on {device}")
    return model, torch, device


def model_input(frame, cv2):
    """One frame as their dataloader feeds it: 384x224, RGB, ImageNet-normalised, CHW."""
    resized = cv2.resize(frame, (MODEL_W, MODEL_H), interpolation=cv2.INTER_LINEAR)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    normalised = (rgb - MEAN) / STD
    return np.transpose(normalised, (2, 0, 1))


def predict_map(model, torch, device, window):
    """Run the 32-frame window ending at the current frame; return the 224x384 map."""
    frames = list(window)
    if len(frames) < CLIP_FRAMES:  # before a full window, their loader repeats frame 0
        frames = [frames[0]] * (CLIP_FRAMES - len(frames)) + frames
    batch = np.stack(frames)[None]  # 1, T, C, H, W
    batch = np.transpose(batch, (0, 2, 1, 3, 4))
    with torch.no_grad():
        prediction = model(torch.from_numpy(batch).to(device))
    return prediction[0].detach().cpu().numpy()


def blend(frame, saliency, cv2):
    """Jet heatmap at up to 45 % opacity, fading out where saliency is low."""
    low, high = float(saliency.min()), float(saliency.max())
    if high - low < 1e-12:
        return frame
    relative = (saliency - low) / (high - low)
    colour = cv2.applyColorMap(np.uint8(np.clip(relative * 255.0, 0, 255)), cv2.COLORMAP_JET)
    alpha = (ALPHA * relative)[..., None]
    return (frame * (1.0 - alpha) + colour * alpha).astype(np.uint8)


def dashed_rect(frame, box, cv2):
    """Thin white dashed outline of the brain model's centre square (trial marker)."""
    x0, y0, x1, y1 = (int(round(value)) for value in box)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(frame.shape[1] - 1, x1), min(frame.shape[0] - 1, y1)
    if x1 <= x0 or y1 <= y0:
        return
    dash = 8
    for x in range(x0, x1, dash * 2):
        cv2.line(frame, (x, y0), (min(x + dash, x1), y0), (255, 255, 255), 1, cv2.LINE_AA)
        cv2.line(frame, (x, y1), (min(x + dash, x1), y1), (255, 255, 255), 1, cv2.LINE_AA)
    for y in range(y0, y1, dash * 2):
        cv2.line(frame, (x0, y), (x0, min(y + dash, y1)), (255, 255, 255), 1, cv2.LINE_AA)
        cv2.line(frame, (x1, y), (x1, min(y + dash, y1)), (255, 255, 255), 1, cv2.LINE_AA)


def seconds_rows(per_frame):
    """One row per second: mean outside share over its frames, peak on its middle frame."""
    rows = []
    for start in range(0, len(per_frame), FPS):
        chunk = per_frame[start : start + FPS]
        middle = chunk[len(chunk) // 2]
        rows.append(
            {
                "second": start // FPS,
                "outside_share": round(float(np.mean([item[0] for item in chunk])), 4),
                "centre_cc": round(float(np.mean([item[1] for item in chunk])), 4),
                "peak_x": round(middle[2][0], 4),
                "peak_y": round(middle[2][1], 4),
            }
        )
    return rows


def write_csv(path, columns, rows):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def read_csv(path):
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def summary_from_rows(clip, variant, width, height, duration, rows, processing, gpu_peak):
    shares = [float(row["outside_share"]) for row in rows]
    centre = [float(row["centre_cc"]) for row in rows]
    return {
        "clip": clip.stem,
        "variant": variant,
        "width": width,
        "height": height,
        "seconds": round(duration, 3),
        "mean_outside_share": round(float(np.mean(shares)), 4) if shares else "",
        "mean_centre_cc": round(float(np.mean(centre)), 4) if centre else "",
        "share_of_seconds_over_half": (
            round(sum(share > 0.5 for share in shares) / len(shares), 4) if shares else ""
        ),
        "processing_seconds": round(processing, 3) if processing is not None else "",
        "gpu_peak_mb": f"{gpu_peak:.1f}" if gpu_peak is not None else "",
    }


def process_variant(clip, variant, out_dir, model, torch, device, width, height, duration):
    """Run one clip+variant and write its mp4 and csv. Returns its summary row."""
    mp4_path = out_dir / f"{clip.stem}.{variant}.mp4"
    csv_path = out_dir / f"{clip.stem}.{variant}.csv"
    if mp4_path.exists() and csv_path.exists():
        print(f"  {variant}: skipped, {mp4_path.name} and {csv_path.name} already exist")
        return summary_from_rows(
            clip, variant, width, height, duration, read_csv(csv_path), None, None
        )

    import cv2

    started = time.monotonic()
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

    out_w, out_h = display_size(width, height)
    if variant == "pad":
        canvas_w = _even(round(out_h * 16 / 9))
        canvas_h = out_h
        offset_x = (canvas_w - out_w) // 2
    else:
        canvas_w, canvas_h, offset_x = out_w, out_h, 0
    box = centre_square(out_w, out_h)
    blob = centre_blob(out_h, out_w)

    temporary = mp4_path.with_name(f"{clip.stem}.{variant}.tmp.mp4")
    writer = VideoWriter(temporary, out_w, out_h, clip)
    per_frame = []
    window = deque(maxlen=CLIP_FRAMES)
    closed = False
    try:
        for frame in decode_frames(clip, out_w, out_h):
            if variant == "pad":
                canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
                canvas[:, offset_x : offset_x + out_w] = frame
                source = canvas
            else:
                source = frame
            window.append(model_input(source, cv2))

            prediction = predict_map(model, torch, device, window)
            saliency = cv2.resize(prediction, (canvas_w, canvas_h), interpolation=cv2.INTER_LINEAR)
            saliency = cv2.GaussianBlur(saliency, (BLUR_KERNEL, BLUR_KERNEL), 0)
            if variant == "pad":
                saliency = saliency[:, offset_x : offset_x + out_w]

            per_frame.append(
                (outside_share(saliency, box), correlation(saliency, blob), peak_position(saliency))
            )
            rendered = blend(frame, saliency, cv2)
            dashed_rect(rendered, box, cv2)
            writer.write(rendered)
        writer.close()
        closed = True
        if not per_frame:
            raise RuntimeError(f"{clip}: no frames decoded")
        rows = seconds_rows(per_frame)
        temporary.replace(mp4_path)
        write_csv(csv_path, CSV_COLUMNS, rows)
    except BaseException:
        if not closed:
            writer.abort()
        temporary.unlink(missing_ok=True)
        raise

    gpu_peak = None
    if torch.cuda.is_available():
        gpu_peak = torch.cuda.max_memory_allocated() / (1024 * 1024)
    processing = time.monotonic() - started
    print(f"  {variant}: {out_w}x{out_h}, {len(per_frame)} frames, {processing:.1f} s taken")
    return summary_from_rows(clip, variant, width, height, duration, rows, processing, gpu_peak)


def variants_for(width, height):
    """Portrait clips get stretch and pad; landscape (and square) clips only stretch."""
    return VARIANTS if height > width else ("stretch",)


def main():
    parser = argparse.ArgumentParser(
        description="ViNet-S gaze heatmaps and centre-square outside shares for clips."
    )
    parser.add_argument("clips", nargs="+", metavar="CLIP", help="video files to analyse")
    parser.add_argument("--out", required=True, help="directory for mp4s, csvs and summary.csv")
    parser.add_argument(
        "--vinet",
        default="models/vinet",
        help="directory holding repo/ (pinned ViNet-S clone) and the checkpoint",
    )
    args = parser.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} not found on PATH")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    previous = {}
    summary_path = out_dir / "summary.csv"
    if summary_path.exists():
        previous = {(row["clip"], row["variant"]): row for row in read_csv(summary_path)}

    model, torch, device = load_model(Path(args.vinet))

    rows = {}
    failed = []
    for clip in map(Path, args.clips):
        print(f"\n{clip.name}")
        try:
            width, height, duration = probe_clip(clip)
            for variant in variants_for(width, height):
                row = process_variant(
                    clip, variant, out_dir, model, torch, device, width, height, duration
                )
                if row["processing_seconds"] == "" and (clip.stem, variant) in previous:
                    row = previous[(clip.stem, variant)]
                rows[(clip.stem, variant)] = row
        except Exception as err:  # one bad clip must not stop the others
            print(f"\n!!! {clip}: {type(err).__name__}: {err}", file=sys.stderr)
            failed.append(clip)

    for key, row in previous.items():
        rows.setdefault(key, row)
    write_csv(summary_path, SUMMARY_COLUMNS, [rows[key] for key in sorted(rows)])
    print(f"\n{len(rows)} clip+variant rows written to {summary_path}")
    if failed:
        names = ", ".join(clip.name for clip in failed)
        print(f"{len(failed)} clip(s) failed: {names}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
