#!/usr/bin/env python3
"""Build readable contact sheets from extracted trailer frames."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
MAX_PER_SHEET = 6


def frame_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("frame_*.jpg"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(ROOT / "out" / "frames"), help="frame directory")
    parser.add_argument("--output", default=str(ROOT / "out"), help="sheet output directory")
    parser.add_argument("--prefix", default="contact", help="sheet filename prefix")
    parser.add_argument(
        "--step", type=int, default=60, help="frame stride used when extracting the frames"
    )
    parser.add_argument(
        "--start-frame", type=int, default=0, help="frame number of the first extracted image"
    )
    parser.add_argument(
        "--fps", type=float, default=30.0, help="video frame rate for the time labels"
    )
    parser.add_argument(
        "--per-sheet",
        type=int,
        default=MAX_PER_SHEET,
        help=f"images per sheet (maximum {MAX_PER_SHEET})",
    )
    parser.add_argument("--width", type=int, default=640, help="thumbnail width in pixels")
    parser.add_argument("--columns", type=int, default=2, help="thumbnail columns per sheet")
    args = parser.parse_args()

    directory = Path(args.input)
    if not directory.is_absolute():
        directory = ROOT / directory
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    frames = frame_files(directory)
    if not frames:
        print(f"No frames in {directory}; no contact sheets written.")
        return 0

    per_sheet = max(1, min(MAX_PER_SHEET, args.per_sheet))
    if per_sheet != args.per_sheet:
        print(f"Using {per_sheet} images per sheet (maximum {MAX_PER_SHEET}).")
    columns = max(1, min(per_sheet, args.columns))
    width = max(160, args.width)
    thumb_height = round(width * 9 / 16)
    label_height = 30
    padding = 16
    gap = 12
    rows = math.ceil(per_sheet / columns)
    sheet_width = padding * 2 + columns * width + (columns - 1) * gap
    sheet_height = padding * 2 + rows * (label_height + thumb_height) + (rows - 1) * gap
    font = ImageFont.load_default(size=20)
    output.mkdir(parents=True, exist_ok=True)

    for start in range(0, len(frames), per_sheet):
        batch = frames[start : start + per_sheet]
        sheet = Image.new("RGB", (sheet_width, sheet_height), "#17181B")
        draw = ImageDraw.Draw(sheet)
        for offset, path in enumerate(batch):
            column = offset % columns
            row = offset // columns
            x = padding + column * (width + gap)
            y = padding + row * (label_height + thumb_height + gap)
            frame_number = args.start_frame + (start + offset) * args.step
            label = f"frame {frame_number}  {frame_number / args.fps:07.2f}s"
            draw.text((x + 2, y + 4), label, font=font, fill="#A0A6AF")
            with Image.open(path) as image:
                sheet.paste(
                    image.convert("RGB").resize((width, thumb_height), Image.Resampling.LANCZOS),
                    (x, y + label_height),
                )
        target = output / f"{args.prefix}_{start // per_sheet + 1}.jpg"
        sheet.save(target, quality=95)
        print(target)

    print(f"{len(frames)} frames, {per_sheet} per sheet, {width}px wide, from {directory}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
