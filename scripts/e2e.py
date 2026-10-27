"""
End-to-end check against a running stack (`make dev` or `make dev-mock`), driven like the browser:

  dev sign-in → create job (credits reserved, upload URL) → presigned POST upload to S3
  → mark uploaded → poll until SUCCEEDED → fetch result + CSV → credits charged for the real length

    python scripts/e2e.py [--base URL (default: http://$DEV_PUBLIC_HOST:$DEV_WEB_PORT)] [--video path.mp4] [--timeout 900]

Without --video a 6-second synthetic clip is generated with ffmpeg.
"""

import argparse
import math
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import requests


def make_clip(path: Path, seconds: int = 6) -> Path:
    subprocess.run(["ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=25:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


def duration_of(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def step(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("PUBLIC_BASE_URL") or
                    f"http://{os.environ.get('DEV_PUBLIC_HOST') or 'localhost'}:{os.environ.get('DEV_WEB_PORT') or '8000'}")
    ap.add_argument("--video", type=Path)
    ap.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args()

    video = a.video or make_clip(Path(tempfile.mkdtemp()) / "e2e.mp4")
    dur = duration_of(video)
    s = requests.Session()

    email = f"e2e-{uuid.uuid4().hex[:8]}@neurolens.local"
    r = s.post(f"{a.base}/auth/dev-login", json={"email": email})
    r.raise_for_status()
    me = s.get(f"{a.base}/api/me").json()
    start_credits = me["user"]["credits_available"]
    step(f"signed in as {email} with {start_credits} credits; video {video.name} ({dur:.1f}s)")

    r = s.post(f"{a.base}/api/jobs", json={"filename": video.name, "content_type": "video/mp4",
                                            "size_bytes": video.stat().st_size, "est_duration_s": dur})
    if r.status_code != 201:
        print("create job failed:", r.status_code, r.text)
        return 1
    job = r.json()
    job_id = job["job_id"]
    step(f"job {job_id}: reserved {job['credits_reserved']} credits, pre-warm={job['prewarm']}")

    t0 = time.monotonic()
    with video.open("rb") as f:
        up = requests.post(job["upload"]["url"], data=job["upload"]["fields"], files={"file": (video.name, f, "video/mp4")})
    if up.status_code not in (200, 201, 204):
        print("upload failed:", up.status_code, up.text[:300])
        return 1
    s.post(f"{a.base}/api/jobs/{job_id}/uploaded")
    step(f"uploaded in {time.monotonic() - t0:.1f}s")

    last = None
    deadline = time.monotonic() + a.timeout
    while time.monotonic() < deadline:
        try:
            st = s.get(f"{a.base}/api/jobs/{job_id}").json()
        except requests.ConnectionError:      # a dropped keep-alive connection; poll again
            time.sleep(2)
            continue
        now = (st["status"], st["progress_stage"])
        if now != last:
            step(f"status {st['status']} {st['progress_stage'] or ''} {st['progress_pct'] or ''}".rstrip())
            last = now
        if st["status"] == "SUCCEEDED":
            break
        if st["status"] in ("FAILED", "EXPIRED", "CANCELLED"):
            print("job ended badly:", st["error"])
            return 1
        time.sleep(2)
    else:
        print(f"timed out after {a.timeout}s")
        return 1

    res = s.get(f"{a.base}/api/jobs/{job_id}/result").json()
    result = res["result"]
    csv_lines = s.get(f"{a.base}/api/jobs/{job_id}/export.csv").text.strip().splitlines()
    after = s.get(f"{a.base}/api/me").json()["user"]
    expected_charge = max(5, math.ceil(dur))
    checks = {
        "result has one timestep per second": len(result["timesteps"]) == result["duration_seconds"],
        "result schema v2": result.get("schema_version") == 2,
        "CSV rows match timesteps": len(csv_lines) == len(result["timesteps"]) + 1,
        "video URL returned": bool(res["video_url"]),
        f"charged {expected_charge} credits": start_credits - after["credits_available"] == expected_charge,
        "no credits left on hold": after["credits_reserved"] == 0,
    }
    s.post(f"{a.base}/api/jobs/{job_id}/metrics", json={"client_render_ms": 1})
    timings = st["timings"]
    step(f"done: backend={result['processing']['backend']} full={result['processing']['pass_full_s']}s "
         f"silent={result['processing']['pass_silent_s']}s gpu={result['processing']['gpu']}")
    for k in ("s3_event_at", "claimed_at", "pass_full_done_at", "completed_at"):
        print(f"    {k:<18} {timings.get(k)}")
    for name, ok in checks.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
