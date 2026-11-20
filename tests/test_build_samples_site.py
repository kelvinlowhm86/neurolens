"""The samples-only build: every referenced file, relative URLs, limits and a loud failure."""

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "infra" / "build_samples_site.py"
DATA_FIELDS = ("video_file", "thumbnail", "brain_file", "csv_file", "pdf_file")
FORBIDDEN = (r'href="/', r'src="/', r"fetch\('/", r"fetch\(`/")
SAMPLES = json.loads((REPO_ROOT / "data" / "samples.json").read_text(encoding="utf-8"))


def build(out, *extra):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(out), *extra],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def referenced_files():
    paths = []
    for sample in SAMPLES["samples"]:
        paths.extend(sample[field] for field in DATA_FIELDS if sample.get(field))
    paths.extend(entry["pdf_file"] for entry in SAMPLES["comparisons"] if entry.get("pdf_file"))
    return paths


def route_limits():
    from neurolens import settings
    from neurolens.web.app import create_app

    cfg = settings.load_config(REPO_ROOT)
    cfg.pop("db", None)
    cfg.pop("aws", None)
    cfg.pop("deployed", None)
    response = create_app(cfg=cfg, data_dir=REPO_ROOT / "data").test_client().get("/api/limits")
    assert response.status_code == 200
    return response.get_json()


def test_build_is_relative_and_serves_the_samples(tmp_path):
    out = tmp_path / "site"
    result = build(out)
    assert result.returncode == 0, result.stderr

    for relative in referenced_files():
        assert (out / "data" / relative).is_file(), relative
    assert (out / "data" / "samples.json").is_file()
    assert (out / ".nojekyll").is_file()
    assert (out / "static_shim.js").is_file()
    assert json.loads((out / "limits.json").read_text(encoding="utf-8")) == route_limits()

    for page in ("index.html", "app.html"):
        text = (out / page).read_text(encoding="utf-8")
        assert text.index("static_shim.js") < text.index("post_json.js")

    for path in out.rglob("*"):
        if path.suffix not in (".html", ".js") or path.name == "static_shim.js":
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN:
            assert not re.search(pattern, text), f"{path.relative_to(out)}: {pattern}"

    for page in ("privacy.html", "retention_guide.html"):
        text = (out / "static" / page).read_text(encoding="utf-8")
        assert "{{CONTACT_EMAIL}}" not in text
        assert "comp.nus" not in text


def test_missing_referenced_file_fails_loudly(tmp_path):
    samples = json.loads(json.dumps(SAMPLES))
    samples["samples"][0]["video_file"] = "videos/__missing_sample__.mp4"
    samples_path = tmp_path / "samples.json"
    samples_path.write_text(json.dumps(samples), encoding="utf-8")

    result = build(tmp_path / "site", "--samples", str(samples_path))

    assert result.returncode != 0
    assert "__missing_sample__.mp4" in result.stdout + result.stderr
