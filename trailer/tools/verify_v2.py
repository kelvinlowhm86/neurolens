#!/usr/bin/env python3
"""Offline timing and data checks for the NeuroLens trailer.

Reads generated timing from src/beats.ts and captions from src/script.ts,
checks the rendered MP4 when it exists, and writes out/verification_v2.json.
No images are inspected automatically.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BEATS = ROOT / "src" / "beats.ts"
SCRIPT = ROOT / "src" / "script.ts"
BUNNY = ROOT / "src" / "bunny.json"
SINTEL = ROOT / "src" / "sintel.json"
BUNNY_SOURCE = ROOT / "public" / "bbb_720p_55s" / "result.json"
SINTEL_SOURCE = ROOT / "public" / "sintel_trailer_720p" / "result.json"
RENDER = ROOT / "out" / "neurolens_trailer_v2.mp4"
SOURCE_CODE = ROOT / "src" / "Trailer.tsx"
OUT = ROOT / "out" / "verification_v2.json"

NODE_LOADER = r"""
const fs = require("fs");
const path = require("path");
const ts = require("typescript");
const cache = new Map();
function load(file) {
  const abs = path.resolve(file);
  if (cache.has(abs)) return cache.get(abs).exports;
  if (abs.endsWith(".json")) {
    const value = JSON.parse(fs.readFileSync(abs, "utf8"));
    cache.set(abs, {exports: value});
    return value;
  }
  const source = fs.readFileSync(abs, "utf8");
  const js = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      esModuleInterop: true,
      resolveJsonModule: true,
    },
  }).outputText;
  const mod = {exports: {}};
  cache.set(abs, mod);
  const req = (spec) => {
    if (spec.startsWith(".")) {
      const base = path.resolve(path.dirname(abs), spec);
      const candidates = [
        base, base + ".ts", base + ".tsx", base + ".js", base + ".json",
        path.join(base, "index.ts"), path.join(base, "index.js"),
      ];
      const hit = candidates.find((p) => fs.existsSync(p) && fs.statSync(p).isFile());
      if (hit) return load(hit);
    }
    throw new Error("Unsupported runtime import " + spec + " from " + abs);
  };
  const fn = new Function("require", "module", "exports", "__filename", "__dirname", js);
  fn(req, mod, mod.exports, abs, path.dirname(abs));
  return mod.exports;
}
const out = {};
for (const file of process.argv.slice(1)) {
  if (!fs.existsSync(file)) {
    out[path.basename(file)] = null;
    continue;
  }
  try {
    out[path.basename(file)] = load(file);
  } catch (error) {
    out[path.basename(file)] = {__error: String((error && error.stack) || error)};
  }
}
process.stdout.write(JSON.stringify(out));
"""


def read_json(path: Path):
    with path.open() as handle:
        return json.load(handle)


def read_result_data(path: Path):
    raw = read_json(path)
    result = {key: raw[key] for key in ("duration_seconds", "signals", "muted") if key in raw}
    result["__path"] = str(path)
    return result


def load_ts_modules(paths: list[Path]) -> dict:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node is required to read the TypeScript timing files")
    command = [node, "-e", NODE_LOADER, *[str(path) for path in paths]]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=90)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"could not parse TypeScript loader output: {error}") from error


def module_key(modules: dict, name: str):
    for key, value in modules.items():
        if key == name:
            return value
    return None


def as_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


def pick_number(mapping: dict, names: tuple[str, ...]):
    for name in names:
        if name in mapping:
            number = as_number(mapping[name])
            if number is not None:
                return number
    return None


def pick_text(mapping: dict):
    preferred = ("text", "caption", "line", "copy", "content", "title", "label")
    for key in preferred:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key, value in mapping.items():
        if key.lower() in {"id", "name", "key"}:
            continue
        if isinstance(value, str) and value.strip() and len(value.strip()) > 1:
            return value.strip()
    return None


def normalize_point(item, fps: float, default_units: str, seconds_to_frames=None):
    if isinstance(item, (int, float)) and not isinstance(item, bool):
        return to_frame(float(item), default_units, fps), "value"
    if isinstance(item, dict):
        frame = pick_number(item, ("frame", "startFrame", "atFrame", "f"))
        if frame is not None:
            return int(round(frame)), "frame"
        seconds = pick_number(item, ("seconds", "second", "time", "sourceSeconds"))
        if seconds is not None:
            return int(round(seconds * fps)), "seconds"
        value = pick_number(item, ("from", "start", "at", "out"))
        if value is not None:
            return to_frame(value, default_units, fps), "inferred"
    return None, None


def to_frame(value: float, units: str, fps: float) -> int:
    if units == "seconds":
        return int(round(value * fps))
    return int(round(value))


def beat_list(raw, fps: float) -> list[dict]:
    if not isinstance(raw, list):
        return []
    output = []
    for index, item in enumerate(raw):
        if isinstance(item, dict):
            frame = pick_number(item, ("frame", "startFrame", "atFrame"))
            seconds = pick_number(item, ("seconds", "second", "time", "sourceSeconds"))
            if frame is None and seconds is not None:
                frame = seconds * fps
            if frame is None:
                continue
            entry = dict(item)
            entry["frame"] = int(round(frame))
            entry["seconds"] = seconds if seconds is not None else frame / fps
            entry.setdefault("index", index)
            output.append(entry)
        else:
            number = as_number(item)
            if number is not None:
                output.append(
                    {"frame": int(round(number * fps)), "seconds": number, "index": index}
                )
    output.sort(key=lambda entry: entry["frame"])
    return output


def cuts_list(raw, fps: float) -> list[dict]:
    output = []
    if isinstance(raw, dict):
        for name, item in raw.items():
            frame, method = normalize_point(item, fps, "frames")
            if frame is None:
                continue
            output.append({"name": name, "frame": frame, "read_as": method})
    elif isinstance(raw, list):
        for index, item in enumerate(raw):
            frame, method = normalize_point(item, fps, "frames")
            if frame is None:
                continue
            name = (
                item.get("name", item.get("id", f"cut{index}"))
                if isinstance(item, dict)
                else f"cut{index}"
            )
            output.append({"name": str(name), "frame": frame, "read_as": method})
    output.sort(key=lambda entry: entry["frame"])
    return output


def moments_map(raw, fps: float) -> dict[str, int]:
    output = {}
    if isinstance(raw, dict):
        for name, item in raw.items():
            frame, _ = normalize_point(item, fps, "frames")
            if frame is not None:
                output[str(name)] = frame
    elif isinstance(raw, list):
        for index, item in enumerate(raw):
            if isinstance(item, dict):
                name = item.get("name", item.get("id", f"moment{index}"))
                frame, _ = normalize_point(item, fps, "frames")
                if frame is not None:
                    output[str(name)] = frame
    return output


def walk_dicts(value, path=(), depth=0):
    if depth > 6:
        return
    if isinstance(value, dict):
        yield path, value
        for key, item in value.items():
            if isinstance(item, (list, dict)):
                yield from walk_dicts(item, path + (str(key),), depth + 1)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, (list, dict)):
                yield from walk_dicts(item, path + (str(index),), depth + 1)


def infer_object_units(item: dict, path, fps: float, duration_seconds: float) -> str:
    lowered = " ".join(path).lower()
    explicit = set()
    for key in item:
        low = key.lower()
        if "frame" in low:
            explicit.add("frames")
        elif "second" in low or low in {"time", "at"}:
            explicit.add("seconds")
    if explicit == {"frames"}:
        return "frames"
    if explicit == {"seconds"}:
        return "seconds"
    if "second" in lowered or "time" in lowered:
        return "seconds"
    raw = []
    for key in ("start", "from", "end", "to", "begin"):
        number = as_number(item.get(key))
        if number is not None:
            raw.append(number)
    if raw and max(raw) > duration_seconds:
        return "frames"
    if raw and any(abs(value - round(value)) > 1e-6 for value in raw):
        return "seconds"
    return "frames"


def extract_captions(script_module, fps: float, duration_seconds: float):
    captions = []
    seen_dicts = set()
    for path, item in walk_dicts(script_module):
        marker = id(item)
        if marker in seen_dicts:
            continue
        seen_dicts.add(marker)
        text = pick_text(item)
        if not text:
            continue
        start_raw = pick_number(
            item, ("start", "from", "startFrame", "fromFrame", "startSeconds", "begin", "in")
        )
        end_raw = pick_number(item, ("end", "to", "endFrame", "toFrame", "endSeconds", "out"))
        if start_raw is None:
            continue
        if end_raw is None:
            length = pick_number(item, ("duration", "durationFrames", "durationSeconds", "length"))
            if length is None:
                continue
            end_raw = start_raw + (length * fps if "durationSeconds" in item else length)
        units = infer_object_units(item, path, fps, duration_seconds)
        start = to_frame(start_raw, units, fps)
        end = to_frame(end_raw, units, fps)
        captions.append(
            {
                "text": text,
                "start": start,
                "end": end,
                "source": ".".join(path),
                "units": units,
            }
        )
    captions.sort(key=lambda item: (item["start"], item["end"], item["text"]))
    deduped = []
    seen = set()
    for caption in captions:
        marker = (caption["text"], caption["start"], caption["end"])
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(caption)
    return deduped


def named_moments(script_module, fps: float, duration_seconds: float) -> dict[str, int]:
    output = {}
    if not isinstance(script_module, dict):
        return output
    for key, value in script_module.items():
        low = key.lower()
        if low not in {"moments", "namedmoments", "marks", "scriptmoments", "momentsframes"}:
            continue
        if isinstance(value, dict):
            for name, item in value.items():
                frame, _ = normalize_point(item, fps, "frames")
                if frame is not None:
                    output[str(name)] = frame
        elif isinstance(value, list):
            output.update(moments_map(value, fps))
    return output


def nearest(target: int, entries: list[dict]):
    if not entries:
        return None, None
    best = min(entries, key=lambda entry: (abs(entry["frame"] - target), entry["frame"]))
    return best, target - best["frame"]


def nearest_named(target: int, moments: dict[str, int]):
    if not moments:
        return None, None
    name = min(moments, key=lambda key: (abs(moments[key] - target), moments[key]))
    return {"name": name, "frame": moments[name]}, target - moments[name]


def within_one(delta) -> bool:
    return delta is not None and abs(delta) <= 1.0001


def analyze_cuts(cuts, beats, downbeats, moments, duration_frames, fps):
    last_beat = max((entry["frame"] for entry in beats), default=None)
    rows = []
    violations = []
    exceptions = []
    for cut in cuts:
        beat, beat_delta = nearest(cut["frame"], beats)
        downbeat, downbeat_delta = nearest(cut["frame"], downbeats)
        moment, moment_delta = nearest_named(cut["frame"], moments)
        aligned = within_one(beat_delta) or within_one(downbeat_delta) or within_one(moment_delta)
        reason = None
        if not aligned:
            if cut["frame"] == duration_frames:
                reason = "end of film"
            elif (
                moment
                and moment["name"] in {"stop", "trueStop", "true_stop"}
                and within_one(moment_delta)
            ):
                reason = "true stop"
            elif (
                last_beat is not None
                and cut["frame"] > last_beat
                and duration_frames - cut["frame"] <= 3 * fps
            ):
                reason = "final decay tail"
            elif (
                cut["name"].lower() in {"credits", "end"}
                and last_beat is not None
                and cut["frame"] >= last_beat
            ):
                reason = "credits/end after final measured beat"
        row = {
            "name": cut["name"],
            "frame": cut["frame"],
            "seconds": round(cut["frame"] / fps, 3),
            "nearest_beat_frame": beat["frame"] if beat else None,
            "nearest_beat_delta": beat_delta,
            "nearest_downbeat_frame": downbeat["frame"] if downbeat else None,
            "nearest_downbeat_delta": downbeat_delta,
            "nearest_moment": moment,
            "nearest_moment_delta": moment_delta,
            "within_1_frame": aligned,
            "exception": reason,
        }
        rows.append(row)
        if not aligned:
            if reason:
                exceptions.append({**row, "reason": reason})
            else:
                violations.append(row)
    return rows, violations, exceptions


def analyze_captions(captions, beats, downbeats, moments, duration_frames, fps):
    last_beat = max((entry["frame"] for entry in beats), default=None)
    rows = []
    violations = []
    exceptions = []
    for caption in captions:
        points = []
        for label, frame in (("start", caption["start"]), ("end", caption["end"])):
            beat, beat_delta = nearest(frame, beats)
            downbeat, downbeat_delta = nearest(frame, downbeats)
            moment, moment_delta = nearest_named(frame, moments)
            aligned = (
                within_one(beat_delta) or within_one(downbeat_delta) or within_one(moment_delta)
            )
            reason = None
            if not aligned:
                if frame == duration_frames:
                    reason = "end of film"
                elif (
                    moment
                    and moment["name"] in {"stop", "trueStop", "true_stop"}
                    and within_one(moment_delta)
                ):
                    reason = "true stop"
                elif (
                    last_beat is not None
                    and frame > last_beat
                    and duration_frames - frame <= 3 * fps
                ):
                    reason = "final decay tail"
                elif frame == 0 and label == "start":
                    reason = "film start"
            points.append(
                {
                    "edge": label,
                    "frame": frame,
                    "seconds": round(frame / fps, 3),
                    "nearest_beat_frame": beat["frame"] if beat else None,
                    "nearest_beat_delta": beat_delta,
                    "nearest_downbeat_frame": downbeat["frame"] if downbeat else None,
                    "nearest_downbeat_delta": downbeat_delta,
                    "nearest_moment": moment,
                    "nearest_moment_delta": moment_delta,
                    "within_1_frame": aligned,
                    "exception": reason,
                }
            )
            if not aligned:
                record = {
                    "text": caption["text"],
                    "source": caption["source"],
                    **{key: value for key, value in points[-1].items() if key != "exception"},
                }
                if reason:
                    exceptions.append({**record, "reason": reason})
                else:
                    violations.append(record)
        rows.append({**caption, "edges": points})
    return rows, violations, exceptions


def compare_series(local: dict, source: dict, key: str, signal: str):
    local_values = local.get(key, {}).get(signal)
    source_values = source.get(key, {}).get(signal)
    if not isinstance(local_values, list) or not isinstance(source_values, list):
        return {"signal": f"{key}.{signal}", "available": False}
    if not local_values or not source_values:
        return {
            "signal": f"{key}.{signal}",
            "available": False,
            "local_length": len(local_values),
            "source_length": len(source_values),
        }
    local_number = [float(value) for value in local_values]
    source_number = [float(value) for value in source_values]
    local_min = min(local_number)
    local_max = max(local_number)
    source_min = min(source_number)
    source_max = max(source_number)
    common = min(len(local_number), len(source_number))
    max_diff = max(abs(local_number[index] - source_number[index]) for index in range(common))
    return {
        "signal": f"{key}.{signal}",
        "available": True,
        "local_length": len(local_number),
        "source_length": len(source_number),
        "local_min": local_min,
        "local_max": local_max,
        "local_min_index": local_number.index(local_min),
        "local_max_index": local_number.index(local_max),
        "source_min": source_min,
        "source_max": source_max,
        "source_min_index": source_number.index(source_min),
        "source_max_index": source_number.index(source_max),
        "min_matches": abs(local_min - source_min) <= 1e-12,
        "max_matches": abs(local_max - source_max) <= 1e-12,
        "indices_match": (
            local_number.index(local_min) == source_number.index(source_min)
            and local_number.index(local_max) == source_number.index(source_max)
        ),
        "max_abs_difference_common_prefix": max_diff,
        "exact_match": len(local_number) == len(source_number) and max_diff <= 1e-12,
    }


def data_check(local, source):
    result = {
        "source_file": str(source.get("__path", "")),
        "local_file": str(local.get("__path", "")),
        "local_duration_seconds": local.get("duration_seconds"),
        "source_duration_seconds": source.get("duration_seconds"),
        "duration_matches": local.get("duration_seconds") == source.get("duration_seconds"),
        "series": [],
    }
    for key in ("signals", "muted"):
        local_group = local.get(key, {})
        source_group = source.get(key, {})
        if not isinstance(local_group, dict) or not isinstance(source_group, dict):
            continue
        for signal in [name for name in local_group if name in source_group]:
            result["series"].append(compare_series(local, source, key, signal))
    overall = [row for row in result["series"] if row.get("signal") == "signals.overall"]
    result["overall"] = overall[0] if overall else None
    result["all_exact"] = bool(result["series"]) and all(
        row.get("exact_match") for row in result["series"]
    )
    return result


def extract_jsx(text: str, name: str):
    pattern = re.compile(r"<" + re.escape(name) + r"\b")
    results = []
    for match in pattern.finditer(text):
        index = match.end()
        depth = 0
        quote = None
        while index < len(text):
            char = text[index]
            if quote:
                if char == "\\":
                    index += 2
                    continue
                if char == quote:
                    quote = None
            elif char in "\"'`":
                quote = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth = max(0, depth - 1)
            elif char == "/" and index + 1 < len(text) and text[index + 1] == ">" and depth == 0:
                index += 2
                break
            index += 1
        results.append((match.start(), index, text[match.start() : index]))
    return results


def prop_expressions(tag: str):
    output = {}
    for match in re.finditer(r"([A-Za-z_$][\w$]*)\s*=\s*", tag):
        start = match.end()
        if start >= len(tag):
            continue
        if tag[start] in "{'\"`":
            opener = tag[start]
            closer = "}" if opener == "{" else opener
            depth = 0
            index = start
            quote = None
            while index < len(tag):
                char = tag[index]
                if quote:
                    if char == "\\":
                        index += 2
                        continue
                    if char == quote:
                        quote = None
                elif char in "\"'`":
                    quote = char
                elif char == "{" if closer == "}" else False:
                    depth += 1
                elif char == "}" if closer == "}" else False:
                    depth -= 1
                    if depth == 0:
                        index += 1
                        break
                index += 1
            output[match.group(1)] = tag[start:index]
    return output


def around(text: str, needle: str, radius: int = 700):
    index = text.find(needle)
    if index < 0:
        return None
    return text[max(0, index - radius) : index + len(needle) + radius]


def source_inspection():
    report = {
        "method": (
            "Static inspection of src/Trailer.tsx and the timing modules; "
            "no rendered image or pixel analysis."
        ),
        "captions_single_source": False,
        "captions_import": None,
        "muted_true_values": False,
        "muted_evidence": [],
        "comparison_common_domain": False,
        "comparison_evidence": [],
        "duck_automation": {
            "audio_volume_callback": False,
            "references_named_duck_moment": False,
            "evidence": [],
        },
        "warnings": [],
    }
    if not SOURCE_CODE.exists():
        report["warnings"].append("src/Trailer.tsx is missing")
        return report
    text = SOURCE_CODE.read_text()
    for match in re.finditer(r"import\s+([^;]+?)\s+from\s+['\"]\./script['\"]", text):
        report["captions_single_source"] = True
        report["captions_import"] = match.group(0).strip()
        break
    if not report["captions_single_source"]:
        report["warnings"].append("Trailer.tsx does not import ./script")

    tags = extract_jsx(text, "Graph") + extract_jsx(text, "Plot")
    muted_tags = [
        tag
        for _, _, tag in tags
        if re.search(r"\bmuted\b", tag) and "comparison" in prop_expressions(tag)
    ]
    for tag in muted_tags:
        props = prop_expressions(tag)
        comparison = props.get("comparison", "")
        values = props.get("values", "")
        if (
            "muted" in comparison
            and "muted" not in values
            and "Math.abs" not in tag
            and ".abs(" not in tag
        ):
            report["muted_true_values"] = True
            report["muted_evidence"].append(
                {
                    "values": values[:240],
                    "comparison": comparison[:240],
                    "note": (
                        "Muted series is passed directly as the comparison; no absolute-value "
                        "or rescaling expression is present in the tag."
                    ),
                }
            )
    if not report["muted_true_values"]:
        report["warnings"].append("No direct muted comparison Plot was found in Trailer.tsx")

    comparison_tags = [
        tag for _, _, tag in tags if "comparison" in prop_expressions(tag) and "muted" not in tag
    ]
    pair_tags = []
    for tag in comparison_tags:
        props = prop_expressions(tag)
        values = props.get("values", "")
        comparison = props.get("comparison", "")
        if values and comparison and values != comparison:
            pair_tags.append(
                {
                    "values": values[:240],
                    "comparison": comparison[:240],
                    "range": props.get("range", "").strip(),
                    "has_comparison_range": "comparisonRange" in props,
                }
            )
    if len(pair_tags) == 1:
        pair = pair_tags[0]
        report["comparison_common_domain"] = not pair["has_comparison_range"]
        report["comparison_evidence"].append(
            {
                **pair,
                "note": (
                    "One graph receives both series. The graph component applies its single "
                    "range to values and comparison; source inspection only."
                ),
            }
        )
    elif len(pair_tags) > 1:
        ranges = {item["range"] for item in pair_tags}
        report["comparison_common_domain"] = len(ranges) == 1 and not any(
            item["has_comparison_range"] for item in pair_tags
        )
        report["comparison_evidence"] = pair_tags
    else:
        report["warnings"].append("No two-series Plot was found in Trailer.tsx")

    audio_tags = extract_jsx(text, "Audio")
    for _, _, tag in audio_tags:
        props = prop_expressions(tag)
        volume = props.get("volume", "")
        if not volume:
            continue
        volume_name = volume.strip().strip("{}").strip()
        definition = None
        if re.fullmatch(r"[A-Za-z_$][\w$]*", volume_name):
            match = re.search(
                r"(?:const|let|var)\s+" + re.escape(volume_name) + r"\s*=([\s\S]{0,1400})", text
            )
            if match:
                definition = match.group(0).split("\n};", 1)[0] + "\n};"
        combined = volume + "\n" + (definition or "")
        callback = bool(
            "=>" in volume
            or "function" in volume
            or (definition and ("=>" in definition or "function" in definition))
        )
        references_duck = (
            bool(re.search(r"\bmoments\s*\.\s*duck\b", combined)) or "duck" in volume_name.lower()
        )
        one_beat = bool(
            references_duck
            and re.search(r"beatGap\s*\(\s*moments\s*\.\s*duck\s*\)", combined)
            and re.search(r"\bd\s*\+\s*beat\b", combined)
        )
        report["duck_automation"]["audio_volume_callback"] = (
            report["duck_automation"]["audio_volume_callback"] or callback
        )
        report["duck_automation"]["references_named_duck_moment"] = (
            report["duck_automation"]["references_named_duck_moment"] or references_duck
        )
        report["duck_automation"]["one_beat_window_source"] = one_beat
        report["duck_automation"]["evidence"].append(
            {
                "volume": volume[:500],
                "definition": definition[:900] if definition else None,
                "audio_tag": tag[:500],
            }
        )
    if not audio_tags:
        report["warnings"].append("No Audio tag was found in Trailer.tsx")

    graph_source = ROOT / "src" / "edit" / "Graph.tsx"
    report["graph_single_range"] = False
    if graph_source.exists():
        graph_text = graph_source.read_text()
        signature = re.search(r"export type GraphProps\s*=\s*\{([\s\S]*?)\};", graph_text)
        props = signature.group(1) if signature else ""
        report["graph_single_range"] = bool(
            re.search(r"\brange\s*:\s*\[number,\s*number\]", props)
            and "comparisonRange" not in props
            and re.search(r"const y = \(value: number\) => [^;]*\brange\b", graph_text)
            and re.search(r"const compare = comparison \? path\(comparison", graph_text)
        )
        report["graph_evidence"] = {
            "range_prop": bool(re.search(r"\brange\s*:\s*\[number,\s*number\]", props)),
            "separate_comparison_range": "comparisonRange" in props,
            "comparison_uses_path": bool(
                re.search(r"const compare = comparison \? path\(comparison", graph_text)
            ),
        }
    else:
        report["warnings"].append("src/edit/Graph.tsx is missing")
    return report


def ffprobe(path: Path):
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration,size:stream=index,codec_type,codec_name,duration,nb_frames,r_frame_rate,avg_frame_rate,width,height,start_time,channels,sample_rate",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=90)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout)


def decode_peak(path: Path):
    import numpy as np

    command = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(path),
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-ar",
        "48000",
        "-f",
        "f32le",
        "pipe:1",
    ]
    result = subprocess.run(command, capture_output=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip())
    samples = np.frombuffer(result.stdout, dtype="<f4")
    if samples.size == 0:
        return {"decoded_samples": 0, "warning": "no audio samples decoded"}
    absolute = np.abs(samples)
    peak_index = int(np.argmax(absolute))
    peak = float(absolute[peak_index])
    return {
        "decoded_samples": int(samples.size),
        "sample_rate": 48000,
        "peak_sample": peak,
        "peak_dbfs": 20 * math.log10(max(peak, 1e-12)),
        "peak_seconds": peak_index / 48000,
    }


def render_check(path: Path, fps: float, duration_frames: int):
    if not path.exists():
        return {
            "present": False,
            "path": str(path),
            "note": "Render not found; duration and audio checks were skipped.",
        }
    info = ffprobe(path)
    expected_seconds = duration_frames / fps
    streams = info.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    format_duration = as_number(info.get("format", {}).get("duration"))
    video_duration = as_number(video.get("duration")) if video else None
    audio_duration = as_number(audio.get("duration")) if audio else None
    nb_frames = as_number(video.get("nb_frames")) if video else None
    result = {
        "present": True,
        "path": str(path),
        "expected_seconds": expected_seconds,
        "expected_frames": duration_frames,
        "format_duration_seconds": format_duration,
        "video": {
            "codec": video.get("codec_name") if video else None,
            "width": video.get("width") if video else None,
            "height": video.get("height") if video else None,
            "duration_seconds": video_duration,
            "nb_frames": int(nb_frames) if nb_frames is not None else None,
            "avg_frame_rate": video.get("avg_frame_rate") if video else None,
        },
        "audio": {
            "codec": audio.get("codec_name") if audio else None,
            "duration_seconds": audio_duration,
            "channels": audio.get("channels") if audio else None,
            "sample_rate": audio.get("sample_rate") if audio else None,
            "duration_delta_seconds": round(audio_duration - expected_seconds, 6)
            if audio_duration is not None
            else None,
        },
        "duration_matches": (
            audio_duration is not None
            and abs(audio_duration - expected_seconds) <= max(0.05, 1 / fps)
        ),
        "frame_count_matches": nb_frames is not None and int(nb_frames) == duration_frames,
    }
    if audio:
        try:
            result["decoded_audio"] = decode_peak(path)
        except Exception as error:  # noqa: BLE001
            result["decoded_audio"] = {"error": str(error)}
    else:
        result["decoded_audio"] = {"error": "no audio stream"}
    return result


def music_piece_check(pieces):
    if not isinstance(pieces, list):
        return None
    output = []
    for index, piece in enumerate(pieces):
        if not isinstance(piece, dict):
            continue
        row = {"index": index, **piece}
        source_start = as_number(piece.get("sourceStart"))
        source_end = as_number(piece.get("sourceEnd"))
        edit_start = as_number(piece.get("editStart"))
        edit_end = as_number(piece.get("editEnd"))
        if None not in (source_start, source_end):
            row["source_length_seconds"] = round(source_end - source_start, 6)
        if None not in (edit_start, edit_end):
            row["edit_length_seconds"] = round(edit_end - edit_start, 6)
        if (
            row.get("source_length_seconds") is not None
            and row.get("edit_length_seconds") is not None
        ):
            row["lengths_match"] = (
                abs(row["source_length_seconds"] - row["edit_length_seconds"]) <= 1e-3
            )
        output.append(row)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", default=str(OUT), help="evidence JSON output path")
    parser.add_argument(
        "--render", default=str(RENDER), help="rendered MP4 to inspect when present"
    )
    parser.add_argument(
        "--no-color", action="store_true", help="accepted for symmetry; output is plain text"
    )
    args = parser.parse_args()

    warnings = []
    errors = []
    paths = [BEATS]
    if SCRIPT.exists():
        paths.append(SCRIPT)
    else:
        warnings.append("src/script.ts is missing; caption checks skipped")
    modules = load_ts_modules(paths)
    beats_module = module_key(modules, "beats.ts")
    script_module = module_key(modules, "script.ts")

    if not isinstance(beats_module, dict) or "__error" in beats_module:
        errors.append(
            "could not read src/beats.ts: " + str((beats_module or {}).get("__error", "missing"))
        )
        evidence = {
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "errors": errors,
            "warnings": warnings,
        }
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(evidence, indent=2) + "\n")
        print("\n".join(errors))
        return 2

    fps = as_number(beats_module.get("FPS")) or 30.0
    duration_frames = as_number(beats_module.get("DURATION"))
    if duration_frames is None:
        errors.append("src/beats.ts does not export DURATION")
        duration_frames = 0
    duration_frames = int(round(duration_frames))
    beats = beat_list(beats_module.get("beats"), fps)
    downbeats = beat_list(beats_module.get("downbeats"), fps)
    if not downbeats:
        warnings.append(
            "src/beats.ts has no downbeats; cuts were compared with beats and named moments only"
        )
    moments = moments_map(beats_module.get("moments"), fps)
    cuts = cuts_list(beats_module.get("cuts"), fps)
    music_pieces = music_piece_check(beats_module.get("musicPieces"))

    if not beats:
        errors.append("src/beats.ts has no usable beats")
    if not cuts:
        errors.append("src/beats.ts has no usable cuts")
    if not moments:
        warnings.append("src/beats.ts has no named moments")

    duration_seconds = duration_frames / fps if fps else 0
    script_captions = []
    script_moments = {}
    if isinstance(script_module, dict) and "__error" not in script_module:
        script_captions = extract_captions(script_module, fps, duration_seconds)
        script_moments = named_moments(script_module, fps, duration_seconds)
        if not script_captions:
            warnings.append("no caption array with text and start/end was found in src/script.ts")
    elif SCRIPT.exists():
        warnings.append(
            "could not read src/script.ts: " + str((script_module or {}).get("__error", "missing"))
        )

    for name, frame in script_moments.items():
        if name not in moments:
            moments[name] = frame

    cut_rows, cut_violations, cut_exceptions = analyze_cuts(
        cuts, beats, downbeats, moments, duration_frames, fps
    )
    caption_rows, caption_violations, caption_exceptions = analyze_captions(
        script_captions, beats, downbeats, moments, duration_frames, fps
    )
    caption_interval_warnings = [
        {
            "text": caption["text"],
            "start": caption["start"],
            "end": caption["end"],
            "frames": caption["end"] - caption["start"],
            "source": caption["source"],
        }
        for caption in script_captions
        if caption["end"] - caption["start"] < 2
    ]
    if caption_interval_warnings:
        warnings.append(
            f"{len(caption_interval_warnings)} caption cue(s) span fewer than 2 frames; "
            "check that each is rendered where intended"
        )

    local_bunny = read_result_data(BUNNY)
    source_bunny = read_result_data(BUNNY_SOURCE)
    bunny_data = data_check(local_bunny, source_bunny)
    data = {"bunny": bunny_data}
    if SINTEL.exists() and SINTEL_SOURCE.exists():
        local_sintel = read_result_data(SINTEL)
        source_sintel = read_result_data(SINTEL_SOURCE)
        data["sintel"] = data_check(local_sintel, source_sintel)

    inspection = source_inspection()
    render = render_check(Path(args.render), fps, duration_frames)

    violations = [{"kind": "cut", **row} for row in cut_violations] + [
        {"kind": "caption", **row} for row in caption_violations
    ]
    if not bunny_data["all_exact"]:
        violations.append(
            {
                "kind": "data",
                "detail": "src/bunny.json does not exactly match the source result fields",
            }
        )
    if not render["present"]:
        warnings.append("render absent; timing was checked against source data only")
    elif not render["duration_matches"]:
        violations.append(
            {
                "kind": "render",
                "detail": "decoded audio duration differs from DURATION/FPS by more than one frame",
            }
        )
    if render["present"] and render.get("video", {}).get("avg_frame_rate") not in {
        None,
        f"{int(fps)}/1",
        f"{int(fps)}/1",
    }:
        warnings.append("render avg_frame_rate is " + str(render["video"].get("avg_frame_rate")))
    if render["present"] and render.get("decoded_audio", {}).get("error"):
        warnings.append(
            "render audio peak could not be decoded: " + str(render["decoded_audio"]["error"])
        )

    evidence = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "inputs": {
            "beats": str(BEATS),
            "script": str(SCRIPT) if SCRIPT.exists() else None,
            "bunny": str(BUNNY),
            "bunny_source": str(BUNNY_SOURCE),
            "sintel": str(SINTEL) if SINTEL.exists() else None,
            "sintel_source": str(SINTEL_SOURCE) if SINTEL_SOURCE.exists() else None,
            "render": str(args.render),
            "trailer_source": str(SOURCE_CODE),
        },
        "timing": {
            "fps": fps,
            "duration_frames": duration_frames,
            "duration_seconds": round(duration_seconds, 6),
            "beat_count": len(beats),
            "first_beat_frame": beats[0]["frame"] if beats else None,
            "last_beat_frame": beats[-1]["frame"] if beats else None,
            "downbeat_count": len(downbeats),
            "moments": moments,
            "music_pieces": music_pieces,
            "cuts": cut_rows,
            "cut_exceptions": cut_exceptions,
            "cut_violations": cut_violations,
            "captions": caption_rows,
            "caption_exceptions": caption_exceptions,
            "caption_violations": caption_violations,
            "short_caption_intervals": caption_interval_warnings,
        },
        "render": render,
        "data": data,
        "source_inspection": inspection,
        "violations": violations,
        "warnings": warnings,
        "errors": errors,
        "notes": [
            "Cuts and caption edges are compared with measured beats, downbeats and named moments.",
            "An end-of-film edge, a named true stop, the final decay tail and the film "
            "start are reported as exceptions rather than timing violations.",
            "Data comparisons use the stored signal arrays only; source job metadata is not read.",
            "Source inspection confirms the code's data plumbing, not the rendered "
            "pixels; no automated image check is claimed.",
        ],
    }

    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps(evidence, indent=2) + "\n")

    print(
        f"Timing: {fps:g} fps, {duration_frames} frames = "
        f"{duration_seconds:.3f}s, {len(beats)} beats, {len(downbeats)} downbeats"
    )
    print("Cuts:")
    print(f"  {'name':<14}{'frame':>7}{'sec':>8}{'beat':>7}{'dF':>6}{'down':>7}{'dD':>6}  moment")
    for row in cut_rows:
        beat = row["nearest_beat_frame"] if row["nearest_beat_frame"] is not None else "-"
        delta = (
            f"{row['nearest_beat_delta']:+.1f}" if row["nearest_beat_delta"] is not None else "-"
        )
        down = row["nearest_downbeat_frame"] if row["nearest_downbeat_frame"] is not None else "-"
        down_delta = (
            f"{row['nearest_downbeat_delta']:+.1f}"
            if row["nearest_downbeat_delta"] is not None
            else "-"
        )
        moment = (
            row["nearest_moment"]["name"] + f" {row['nearest_moment_delta']:+.1f}"
            if row["nearest_moment"]
            else "-"
        )
        flag = "  EXCEPTION: " + row["exception"] if row.get("exception") else ""
        print(
            f"  {row['name']:<14}{row['frame']:>7}{row['seconds']:>8.3f}"
            f"{str(beat):>7}{delta:>6}{str(down):>7}{down_delta:>6}  {moment}{flag}"
        )

    print("Captions:")
    for row in caption_rows:
        edges = {edge["edge"]: edge for edge in row["edges"]}
        start, end = edges.get("start"), edges.get("end")
        text = (row["text"][:54] + "…") if len(row["text"]) > 55 else row["text"]
        start_beat = (
            f"{start['nearest_beat_frame']}{start['nearest_beat_delta']:+.0f}" if start else "-"
        )
        end_beat = f"{end['nearest_beat_frame']}{end['nearest_beat_delta']:+.0f}" if end else "-"
        start_moment = (
            f"{start['nearest_moment']['name']}{start['nearest_moment_delta']:+.0f}"
            if start and start.get("nearest_moment")
            else "-"
        )
        end_moment = (
            f"{end['nearest_moment']['name']}{end['nearest_moment_delta']:+.0f}"
            if end and end.get("nearest_moment")
            else "-"
        )
        flags = []
        for edge in (start, end):
            if edge and edge.get("exception"):
                flags.append(f"{edge['edge']} {edge['exception']}")
            elif edge and not edge["within_1_frame"]:
                flags.append(f"{edge['edge']} OFF GRID")
        suffix = ("  [" + "; ".join(flags) + "]") if flags else ""
        print(
            f"  {row['start']:>4}-{row['end']:<4} beat {start_beat:>7} / {end_beat:>7}  "
            f"moment {start_moment:>15} / {end_moment:>15}  {text}{suffix}"
        )
    if not caption_rows:
        print("  (none)")

    print("Violations:")
    if violations:
        for item in violations:
            if item["kind"] == "caption":
                print(
                    f"  caption {item['edge']} frame {item['frame']} "
                    f"text={item['text'][:48]!r} beat={item['nearest_beat_frame']} "
                    f"delta={item['nearest_beat_delta']}"
                )
            elif item["kind"] == "cut":
                print(
                    f"  cut {item['name']} frame {item['frame']} "
                    f"beat={item['nearest_beat_frame']} "
                    f"delta={item['nearest_beat_delta']}"
                )
            else:
                print(f"  {item['kind']}: {item['detail']}")
    else:
        print("  none")
    if cut_exceptions or caption_exceptions:
        print("Allowed timing exceptions:")
        for item in cut_exceptions:
            print(f"  cut {item['name']} frame {item['frame']}: {item['reason']}")
        for item in caption_exceptions:
            print(
                f"  caption {item['text'][:40]!r} {item['edge']} "
                f"frame {item['frame']}: {item['reason']}"
            )

    print("Render:")
    if render["present"]:
        video = render["video"]
        audio = render["audio"]
        peak = render.get("decoded_audio", {}).get("peak_dbfs")
        print(
            f"  {video['width']}x{video['height']} {video['codec']} "
            f"{video['nb_frames']} frames, video {video['duration_seconds']}s, "
            f"audio {audio['duration_seconds']}s, "
            f"expected {render['expected_seconds']:.3f}s"
        )
        print(
            f"  decoded peak: {peak:.2f} dBFS"
            if peak is not None
            else "  decoded peak: unavailable"
        )
    else:
        print("  not present; skipped")

    print("Data:")
    for label, group in data.items():
        overall = group.get("overall") or {}
        print(
            f"  {label}: duration {group['local_duration_seconds']}s vs source "
            f"{group['source_duration_seconds']}s; exact={group['all_exact']}"
        )
        if overall:
            print(
                f"    overall min {overall['local_min']:+.3f}@"
                f"{overall['local_min_index']} max {overall['local_max']:+.3f}@"
                f"{overall['local_max_index']}"
            )
    print(
        f"  source inspection: muted true values={inspection['muted_true_values']}; "
        f"comparison common domain={inspection['comparison_common_domain']}; "
        f"graph single range={inspection.get('graph_single_range')}; "
        f"duck source={inspection['duck_automation']['references_named_duck_moment']}; "
        f"one-beat duck window={inspection['duck_automation'].get('one_beat_window_source')}"
    )
    if caption_interval_warnings:
        print("  short caption intervals:")
        for item in caption_interval_warnings:
            print(
                f"    {item['start']}-{item['end']} ({item['frames']} frame) {item['text'][:56]!r}"
            )

    if warnings:
        print("Warnings:")
        for warning in warnings:
            print("  " + warning)
    if errors:
        print("Errors:")
        for error in errors:
            print("  " + error)
    print("Wrote " + str(args.json))
    return 2 if errors else (1 if violations else 0)


if __name__ == "__main__":
    sys.exit(main())
