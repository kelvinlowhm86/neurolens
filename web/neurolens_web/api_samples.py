"""
Public sample gallery (works signed-out). Pre-computed results ship in samples.json inside the image;
the videos come from data/ locally, or from the bucket's samples/ prefix on AWS.
"""

import json
from functools import lru_cache
from pathlib import Path

from flask import Blueprint, abort, current_app, send_from_directory

bp = Blueprint("samples", __name__)


@lru_cache(maxsize=1)
def _load(path: str) -> list[dict]:
    p = Path(path)
    return json.loads(p.read_text()).get("samples", []) if p.exists() else []


@bp.get("/api/samples")
def samples():
    cfg = current_app.extensions["nl_cfg"]
    items = []
    for s in _load(str(cfg.samples_json.resolve())):
        s = dict(s)
        if cfg.samples_source == "s3":
            storage = current_app.extensions["nl"].storage
            s["video_url"] = storage.presigned_get(f"samples/{s['video_file']}")
            s["thumbnail_url"] = storage.presigned_get(f"samples/{s['thumbnail']}") if s.get("thumbnail") else None
        else:
            s["video_url"] = f"/data/{s['video_file']}"
            s["thumbnail_url"] = f"/data/{s['thumbnail']}" if s.get("thumbnail") else None
        items.append(s)
    return {"samples": items}


@bp.get("/data/<path:filename>")
def local_data(filename):
    cfg = current_app.extensions["nl_cfg"]
    if cfg.samples_source != "local" or not (filename.startswith("videos/") or filename.startswith("output/")):
        abort(404)
    return send_from_directory(cfg.data_dir.resolve(), filename)
