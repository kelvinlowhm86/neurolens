"""FAKE_INFERENCE_SECONDS: a slow fake job for the CPU rehearsals. Written from docs/M2b_spec.md
§1a (neurolens.inference fake mode) and §11.

Runs in a subprocess so the environment is read fresh whichever way the module reads it.
Timings use whole seconds and generous bounds.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SCRIPT = r"""
import json, os, sys, time
from neurolens import inference
os.environ["FAKE_INFERENCE"] = "1"  # fake events are built in fake mode
events = inference.build_events(sys.argv[1])
if sys.argv[2] == "off":
    del os.environ["FAKE_INFERENCE"]
times = []
for ev in (events, inference.without_audio(events)):
    t0 = time.monotonic()
    preds = inference.predict(ev, 3.0)
    times.append(time.monotonic() - t0)
print(json.dumps({"times": times, "rows": int(preds.shape[0])}))
"""


def run_predict(clip_path, mode, seconds):
    env = {k: v for k, v in os.environ.items() if k not in ("FAKE_INFERENCE",)}
    env.pop("FAKE_INFERENCE_SECONDS", None)
    if mode == "on":
        env["FAKE_INFERENCE"] = "1"
    if seconds is not None:
        env["FAKE_INFERENCE_SECONDS"] = seconds
    proc = subprocess.run(
        [sys.executable, "-c", SCRIPT, str(clip_path), mode],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_fake_inference_seconds_makes_each_fake_predict_take_that_long(clip_path):
    out = run_predict(clip_path, "on", "1")
    assert len(out["times"]) == 2
    for t in out["times"]:
        assert 0.9 <= t < 5
    assert out["rows"] == 3  # still the normal fake output


def test_fake_predict_is_quick_by_default(clip_path):
    out = run_predict(clip_path, "on", None)
    assert all(t < 0.5 for t in out["times"])


def test_fake_inference_seconds_is_ignored_without_fake_inference(clip_path):
    out = run_predict(clip_path, "off", "1")
    assert all(t < 0.5 for t in out["times"])
