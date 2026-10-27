"""
python -m neurolens_worker run                                 # the service (default container CMD)
python -m neurolens_worker download-weights --sample ad.mp4    # bake / first local run (needs HF_TOKEN)
python -m neurolens_worker warmup --sample ad.mp4              # offline check after a bake
python -m neurolens_worker bench --out timings.csv a.mp4 b.mp4 # Experiment 3 timings
python -m neurolens_worker silent-check ad.mp4                 # SILENT_MODE=events vs ffmpeg
"""

import argparse
import json
import logging
import os
import socket
import sys
from pathlib import Path

from neurolens_common.config import get_settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="neurolens_worker")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    for name in ("download-weights", "warmup"):
        p = sub.add_parser(name)
        p.add_argument("--sample", type=Path, required=True, help="a short ad with spoken dialogue")
    p = sub.add_parser("bench")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--host", default=socket.gethostname())
    p.add_argument("videos", type=Path, nargs="+")
    p = sub.add_parser("silent-check")
    p.add_argument("video", type=Path)
    args = parser.parse_args()

    cfg = get_settings()
    cfg.scratch_dir.mkdir(parents=True, exist_ok=True)
    _use_pinned_whisperx()

    if args.cmd == "run":
        from .loop import run

        run(cfg)
    elif args.cmd == "download-weights":
        from .weights import download_all

        download_all(cfg, args.sample)
    elif args.cmd == "warmup":
        from .inference.tribe import TribeBackend

        backend = TribeBackend(cfg)
        backend.load()
        backend.warmup(args.sample)
        print("warm-up OK")
    elif args.cmd == "bench":
        from .bench import bench

        bench(cfg, args.videos, args.out, args.host)
    elif args.cmd == "silent-check":
        from .bench import silent_check

        print(json.dumps(silent_check(cfg, args.video), indent=2))


def _use_pinned_whisperx() -> None:
    """Route tribev2's `uvx whisperx` call to the pinned WhisperX in this environment (see bin/uvx)."""
    shim_dir = Path(__file__).resolve().parent / "bin"
    os.environ["PATH"] = f"{shim_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    os.environ["NEUROLENS_PYTHON"] = sys.executable
    # Child processes (WhisperX) must import neurolens_worker too.
    os.environ["PYTHONPATH"] = os.pathsep.join(filter(None, [str(shim_dir.parent.parent),
                                                             os.environ.get("PYTHONPATH")]))


if __name__ == "__main__":
    main()
