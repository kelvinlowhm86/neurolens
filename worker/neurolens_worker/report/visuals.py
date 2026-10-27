"""Still frames (ffmpeg) and line charts (matplotlib) for the report, in the dashboard's colours."""

import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from neurolens_common.schemas import REGIONS  # noqa: E402

from .moments import Moment  # noqa: E402

NAVY = "#0F1629"
MUTED = "#94A3B8"
GRID = "#E2E8F0"
COLORS = {"engagement_overall": "#EA580C", "ffa_faces": "#DC2626", "eba_bodies": "#2563EB",
          "ppa_scenes": "#16A34A", "sts_social": "#D97706", "auditory": "#9333EA"}
TITLES = {"engagement_overall": "Overall engagement", "ffa_faces": "Faces (FFA)", "eba_bodies": "Bodies (EBA)",
          "ppa_scenes": "Scenes (PPA)", "sts_social": "Social cues (STS)", "auditory": "Auditory cortex"}


def grab_frames(video: Path, moments: list[Moment], out_dir: Path, width: int = 640) -> dict[int, Path]:
    """One JPEG per moment, taken mid-second. Moments whose frame can't be read are skipped."""
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = {}
    for m in moments:
        dst = out_dir / f"frame_{m.frame_id}.jpg"
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{m.t + 0.5:.2f}", "-i", str(video),
                            "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", str(dst)],
                           capture_output=True)
        if r.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
            frames[m.frame_id] = dst
    return frames


def _style(ax, title: str):
    ax.set_title(title, loc="left", fontsize=10, fontweight="bold", color=NAVY)
    ax.set_ylim(0, 1.05)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.tick_params(colors=MUTED, labelsize=8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)


def overall_chart(result: dict, moments: list[Moment], dst: Path) -> Path:
    ts = result["timesteps"]
    x = [t["t"] for t in ts]
    y = [t["engagement_overall"] for t in ts]
    fig, ax = plt.subplots(figsize=(9, 2.8), dpi=150)
    ax.fill_between(x, y, color=COLORS["engagement_overall"], alpha=0.12)
    ax.plot(x, y, color=COLORS["engagement_overall"], linewidth=2)
    _style(ax, TITLES["engagement_overall"])
    for m in moments:
        ax.axvline(m.t, color=NAVY, alpha=0.25, linewidth=1, linestyle="--")
        ax.text(m.t, 1.02, f"F{m.frame_id}", ha="center", va="bottom", fontsize=7, color=NAVY)
    ax.set_xlabel("seconds", fontsize=8, color=MUTED)
    fig.tight_layout()
    fig.savefig(dst)
    plt.close(fig)
    return dst


def region_charts(result: dict, dst: Path) -> Path:
    """Five region panels plus the with/without-audio comparison, as one image."""
    ts = result["timesteps"]
    x = [t["t"] for t in ts]
    fig, axes = plt.subplots(3, 2, figsize=(9, 7.2), dpi=150, sharex=True)
    for ax, region in zip(axes.flat, REGIONS):
        y = [t["regions"][region] for t in ts]
        ax.plot(x, y, color=COLORS[region], linewidth=1.8)
        ax.fill_between(x, y, color=COLORS[region], alpha=0.08)
        _style(ax, TITLES[region])
    ax = axes.flat[5]
    pairs = [(t["t"], t["auditory_with_audio"], t["auditory_without_audio"]) for t in ts
             if t["auditory_with_audio"] is not None]
    if pairs:
        px, pw, po = zip(*pairs)
        ax.plot(px, pw, color=COLORS["auditory"], linewidth=1.8, label="with audio")
        ax.plot(px, po, color=COLORS["auditory"], linewidth=1.3, linestyle="--", alpha=0.6, label="audio removed")
        ax.fill_between(px, po, pw, color=COLORS["auditory"], alpha=0.1)
        ax.legend(fontsize=7, frameon=False, loc="upper right")
    _style(ax, "Audio contribution")
    for ax in axes[-1]:
        ax.set_xlabel("seconds", fontsize=8, color=MUTED)
    fig.tight_layout()
    fig.savefig(dst)
    plt.close(fig)
    return dst
