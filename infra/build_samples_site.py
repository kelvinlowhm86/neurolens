#!/usr/bin/env python3
"""Build the static, samples-only NeuroLens site for a GitHub Pages project site.

Usage: python infra/build_samples_site.py <out_dir> [--force]

The output is self-contained and uses relative URLs only, so it works under any sub-path.
"""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_ROOT = REPO_ROOT / "static"
DATA_ROOT = REPO_ROOT / "data"
SHIM_SOURCE = Path(__file__).resolve().parent / "static_shim.js"
MAX_FILE_BYTES = 95_000_000

DATA_FIELDS = ("video_file", "thumbnail", "brain_file", "csv_file", "pdf_file")
STATIC_FILES = ("brain_view.js", "post_json.js")
CONTACT_PAGES = ("privacy.html", "retention_guide.html")
STATIC_DIRS = ("brain", "three")
FORBIDDEN = (
    r'href="/',
    r'src="/',
    r"fetch\('/",
    r"fetch\(`/",
    r"import\(\s*['\"`](?!\./|\.\./|https?:)",  # a bare dynamic import fails in the browser
)

_IMPORT_RE = re.compile(r"(?P<pre>\bfrom\s*)(?P<q>[\"'])/static/(?P<rest>[^\"']*)(?P=q)")
_ATTR_URL_RE = re.compile(r'(?P<name>\b(?:href|src|action))=(?P<q>["\'])/(?P<rest>[^"\']*)(?P=q)')
_JS_URL_RE = re.compile(
    r"(?P<q>['\"`])/(?P<path>(?:api|data|static|app|login|logout)"
    r"(?:[/?#][^'\"`]*)?)(?P=q)"
)


def _relative_url(rest, *, base_dir="."):
    """A leading-slash URL without its slash, with page names mapped to the static layout."""
    if rest == "login" or rest.startswith(("login?", "login#", "login/")):
        return "#login"
    if rest == "logout" or rest.startswith(("logout?", "logout#", "logout/")):
        return "#logout"
    if not rest:
        target = "index.html"
    elif rest == "app" or rest.startswith(("app?", "app#", "app/")):
        target = "app.html" + rest[3:]
    else:
        target = rest
    # "./" keeps the URL valid in import() too, where a bare "static/x.js" is a module name.
    return "./" + target if base_dir == "." else posixpath.relpath(target, base_dir)


def _rewrite_text(text, *, module_in_static, pages_in_static=False):
    """Make every root-relative URL relative, keeping the module import specifier bare-free."""
    prefix = "./" if module_in_static else "./static/"
    text = _IMPORT_RE.sub(
        lambda m: f"{m.group('pre')}{m.group('q')}{prefix}{m.group('rest')}{m.group('q')}", text
    )
    attr_base = "static" if pages_in_static else "."
    text = _ATTR_URL_RE.sub(
        lambda m: (
            f"{m.group('name')}={m.group('q')}"
            f"{_relative_url(m.group('rest'), base_dir=attr_base)}{m.group('q')}"
        ),
        text,
    )
    return _JS_URL_RE.sub(
        lambda m: f"{m.group('q')}{_relative_url(m.group('path'))}{m.group('q')}", text
    )


def _inject_shim(text):
    tag = '<script src="static_shim.js"></script>\n'
    if "<head>" not in text:
        raise ValueError("page has no <head> to load static_shim.js first")
    return text.replace("<head>", "<head>\n" + tag, 1)


def _sample_references(data):
    """Every (relative path, where it is referenced) the pages need from data/."""
    if not isinstance(data, dict):
        raise ValueError("samples.json must be an object")
    samples = data.get("samples") or []
    comparisons = data.get("comparisons") or []
    if not isinstance(samples, list) or not isinstance(comparisons, list):
        raise ValueError("samples and comparisons must be lists")
    references = []
    for sample in samples:
        if not isinstance(sample, dict):
            raise ValueError("every sample must be an object")
        for field in DATA_FIELDS:
            value = sample.get(field)
            if value:
                references.append((value, f"sample {sample.get('id')!r} {field}"))
    for comparison in comparisons:
        if not isinstance(comparison, dict):
            raise ValueError("every comparison must be an object")
        value = comparison.get("pdf_file")
        if value:
            name = f"comparison {comparison.get('a')} vs {comparison.get('b')} pdf_file"
            references.append((value, name))
    return references


def _check_references(references):
    missing = []
    for value, origin in references:
        source = (DATA_ROOT / value).resolve()
        if not source.is_relative_to(DATA_ROOT.resolve()) or not source.is_file():
            missing.append((value, origin))
    return missing


def _fallback_limits(cfg):
    """The /api/limits body from the same sources, when create_app cannot be built here."""
    from neurolens import pricing, settings

    response_cfg = cfg.get("response") or {}
    upload_cfg = cfg.get("upload") or {}
    invite_cents = (cfg.get("billing") or {}).get("invite_cents", 0)
    limits = {
        "max_video_duration_seconds": settings.max_duration(cfg),
        "max_upload_bytes": cfg.get("max_upload_bytes"),
        "colour_range": response_cfg.get("colour_range"),
        "invite_seconds": invite_cents * pricing.BLOCK_SECONDS / pricing.CENTS_PER_BLOCK,
        "price": {
            "cents_per_block": pricing.CENTS_PER_BLOCK,
            "block_seconds": pricing.BLOCK_SECONDS,
            "allowance_seconds": pricing.ALLOWANCE_SECONDS,
        },
    }
    max_batch_files = upload_cfg.get("max_batch_files")
    if max_batch_files is not None:
        limits["max_batch_files"] = max_batch_files
    return limits


def build_limits():
    """The real Flask route's body, from config.json through neurolens.settings, no AWS."""
    from neurolens import settings

    cfg = settings.load_config(REPO_ROOT)
    # /api/limits needs neither the database nor AWS; dropping them keeps create_app offline.
    cfg.pop("db", None)
    cfg.pop("aws", None)
    cfg.pop("deployed", None)
    try:
        from neurolens.web.app import create_app

        app = create_app(cfg=cfg, data_dir=DATA_ROOT)
        response = app.test_client().get("/api/limits")
        if response.status_code != 200:
            raise RuntimeError(f"GET /api/limits returned {response.status_code}")
        return response.get_json()
    except Exception as error:
        print(
            f"warning: create_app unavailable ({error}); "
            "computing limits.json from config.json + neurolens.pricing",
            file=sys.stderr,
        )
        return _fallback_limits(cfg)


def _clear_directory(path):
    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def _prepare_output(out, force):
    if out.exists():
        if out.is_symlink():
            raise ValueError("output directory must not be a symlink")
        if not out.is_dir():
            raise ValueError(f"{out} exists and is not a directory")
        if any(out.iterdir()):
            if not force:
                raise ValueError(f"{out} exists and is not empty; pass --force to replace it")
            _clear_directory(out)
    else:
        out.mkdir(parents=True)


def _write_processed(source, destination, *, module_in_static=False, pages_in_static=False):
    text = source.read_text(encoding="utf-8")
    text = _rewrite_text(text, module_in_static=module_in_static, pages_in_static=pages_in_static)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")


def _copy_page(source_name, destination, *, inject_shim):
    text = _rewrite_text(
        (STATIC_ROOT / source_name).read_text(encoding="utf-8"), module_in_static=False
    )
    if inject_shim:
        text = _inject_shim(text)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")


def _assert_relative(out):
    for path in out.rglob("*"):
        if path.suffix not in (".html", ".js") or path.name == "static_shim.js":
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN:
            if re.search(pattern, text):
                raise ValueError(f"{path.relative_to(out)} still has a root-relative URL")


def build(out, *, force=False, samples_path=None, allow_inside_repo=False):
    out = Path(out).expanduser().resolve()
    repo = REPO_ROOT.resolve()
    if (out == repo or repo in out.parents) and not allow_inside_repo:
        raise ValueError(
            "refusing to write inside the repository; pass --allow-inside-repo to override"
        )
    if out == Path(out.anchor):
        raise ValueError("refusing to write into a filesystem root")
    if out.exists() and out.is_dir() and any(out.iterdir()) and not force:
        raise ValueError(f"{out} exists and is not empty; pass --force to replace it")

    samples_path = Path(samples_path) if samples_path else DATA_ROOT / "samples.json"
    data = json.loads(Path(samples_path).read_text(encoding="utf-8"))
    references = _sample_references(data)
    missing = _check_references(references)
    if missing:
        for value, origin in missing:
            print(f"missing referenced file: {value} ({origin})", file=sys.stderr)
        raise ValueError(f"{len(missing)} referenced file(s) missing")

    limits = build_limits()
    _prepare_output(out, force)

    from neurolens.web.app import render_contact_page

    static_out = out / "static"
    static_out.mkdir(parents=True, exist_ok=True)
    for name in STATIC_FILES:
        _write_processed(
            STATIC_ROOT / name,
            static_out / name,
            module_in_static=name == "brain_view.js",
        )
    for name in CONTACT_PAGES:
        text = render_contact_page(STATIC_ROOT, name, "")
        text = _rewrite_text(text, module_in_static=False, pages_in_static=True)
        (static_out / name).write_text(text, encoding="utf-8")
    for name in STATIC_DIRS:
        shutil.copytree(
            STATIC_ROOT / name,
            static_out / name,
            ignore=shutil.ignore_patterns(".DS_Store"),
        )
    _copy_page("index.html", out / "index.html", inject_shim=True)
    _copy_page("app.html", out / "app.html", inject_shim=True)

    shutil.copyfile(SHIM_SOURCE, out / "static_shim.js")
    (out / "limits.json").write_text(
        json.dumps(limits, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out / ".nojekyll").write_text("", encoding="utf-8")

    data_out = out / "data"
    data_out.mkdir(exist_ok=True)
    shutil.copyfile(samples_path, data_out / "samples.json")
    for value, _origin in references:
        source = DATA_ROOT / value
        destination = data_out / value
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)

    _assert_relative(out)

    files = [path for path in out.rglob("*") if path.is_file()]
    total = sum(path.stat().st_size for path in files)
    oversized = [
        (path, path.stat().st_size) for path in files if path.stat().st_size > MAX_FILE_BYTES
    ]
    for path, size in oversized:
        print(f"file too large: {path.relative_to(out)} ({size / 1e6:.1f} MB)", file=sys.stderr)
    print(f"Built {len(files)} files, {total / 1e6:.1f} MB into {out}")
    if oversized:
        raise ValueError(f"{len(oversized)} file(s) exceed {MAX_FILE_BYTES // 1_000_000} MB")
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", help="empty directory for the static site")
    parser.add_argument("--force", action="store_true", help="clear <out_dir> first if non-empty")
    parser.add_argument(
        "--samples",
        type=Path,
        help="samples.json to build from (default: data/samples.json)",
    )
    parser.add_argument(
        "--allow-inside-repo",
        action="store_true",
        help="explicitly allow an output directory inside this repository",
    )
    args = parser.parse_args(argv)
    try:
        build(
            args.out_dir,
            force=args.force,
            samples_path=args.samples,
            allow_inside_repo=args.allow_inside_repo,
        )
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
