"""The report's request to Claude (M3g §5b, M3h §5b). Pure Python: the Lambda and tests
import it."""

import base64
import copy

from neurolens import results

# How long the prompt asks the answer to be (M3g §5d, M3h §5d). Guidance only: the report
# Lambda checks that the answer makes sense, never its length or counts.
MAX_HEADLINE = 120
MIN_MOMENTS, MAX_MOMENTS = 1, 8
MAX_DIFFERENCES = 3
MAX_COMPARISON_LISTS = 2
MAX_LISTS = 2

# Values within this of 0 are "near typical", as READING_RULES says.
NEAR = 0.05

# The table's value columns: the three groups, overall, and the 7 sub-signals.
_SHOWN = [*results.GROUPS, "overall"]
_SUB_SIGNALS = [name for name in results.SERIES if name not in _SHOWN]
_TABLE_COLUMNS = [*_SHOWN, "muted_overall", *_SUB_SIGNALS]

SYSTEM_PROMPT = f"""You write a short, perceptive report about a video for the person who made it.
You are given 1-fps pictures from the video, the spoken words, and the predicted second-by-second
human brain response (from Meta's TRIBE v2 fMRI model).

What the data means:
- 0 is typical film or television viewing on the model's scale. Values within about {NEAR} of 0 are
  close to typical (never call them high or low).
- Three main groups: seeing (sub-signals: faces, bodies, scenes, visual), hearing (sound), and
  understanding (speech, social).
- overall is the combined brain response with sound; muted_overall is the same video predicted
  with the sound off.
- The visual response trails the pictures by about 1 to 3 seconds; hearing follows sound almost
  immediately. You see the pictures and read the words but cannot hear the soundtrack: a low
  hearing value does not mean silence, so never claim music, loudness or a sound effect.

How to write a report that gives genuine value:
- Let the video and the numbers explain each other. Watching the video tells you what the maker is
  trying to accomplish (a joke, a story turn, an emotional build, a pitch); the brain signals tell
  you how the viewer's brain actually responded underneath.
- Do not merely narrate the chart in words, and do not point out the obvious (for example, do not
  waste a moment or suggestion complaining that a brief closing logo, title card, or text screen
  has lower visual activity than a human face; every video maker already knows a text card is
  static). A drop in the final 1 to 3 seconds over a closing card or screen is expected and is not
  a weakness unless engagement started falling well before the closing screen appeared, or the
  static card lingers for 4+ seconds.
- Instead, look for what is genuinely revealing or non-obvious when you put the story and the
  signals together:
  * Why did one scene or beat in this video lock the brain in while another scene (even with people
    talking or moving) lost steam?
  * How do seeing, hearing, and understanding (and their sub-signals like faces, bodies, scenes,
    speech, and social engagement) work together or pull apart as the video unfolds, including
    natural pacing where one channel hands the baton to another?
  * Where does the brain response confirm that a creative choice worked, and where does it expose
    a blind spot the maker wouldn't notice just by watching their own edit (such as the story arc
    and the brain arc running in opposite directions, or a stretch that drops off with the sound
    off)?
- Give credit where credit is due: when a video or stretch holds well above typical viewing,
  explain what about the storytelling, framing, or pacing earned that response. Be equally honest
  when a stretch genuinely falls flat or below typical viewing.
- Every claim about levels (peaks, dips, highest, lowest, above/below typical, or with the sound
  off) must hold in the numbers you were given; check each claim against the table before writing.
- Never write raw decimals (no "+0.24" or "-0.12") or internal column names ("muted_overall");
  say "with the sound off" and use plain, natural language. Never claim real-world viewer metrics
  (views, retention, clicks).
- Suggestions should be thoughtful creative/editorial ideas tailored to what this specific scene
  is trying to do (e.g., how to tie a weak CTA back into the story stakes or comedic loop), never
  generic boilerplate. Leave suggestion as null at peaks or scenes that work well.
- Voice and tone: sound like an experienced human video director or film editor giving notes in an
  edit suite, never like an AI or a drama critic:
  * Use plain, grounded editing language ("cut two seconds earlier", "stay on her face through the
    line", "the wide shot gives back the momentum").
  * Do not exaggerate dips with melodramatic language ("everything collapses", "the brain goes
    offline", "completely severs the viewer", "never registers in the brain"). Below typical simply
    means quieter than average film or TV viewing (for instance, sparse dialogue naturally lowers
    speech and understanding while visuals carry the scene).
  * Avoid classic AI writing tells: never use em dashes or en dashes anywhere (use periods, commas,
    colons, or parentheses instead); no "not X, but Y" or "it's not just X, it's Y" rhetorical
    contrasts; no shallow "-ing" riders bolted onto the end of sentences ("..., confirming...",
    "..., proving...", "..., highlighting..."); no one-line dramatic closers ("The story arc and
    brain arc finally align", "This is the single biggest unlock"); and do not force every headline
    into the same "X peaks, but Y collapses" formula.
- Write for fast, effortless scanning so every sentence earns its place:
  * headline: at most {MAX_HEADLINE} characters, stating a clear director's note or takeaway in
    natural language (no em dashes).
  * summary: 2 to 3 crisp sentences (under 65 words total) stating what works, where the edit loses
    steam, and the main fix.
  * moments: pick the 3 to 5 most revealing turning points (within {MIN_MOMENTS} to {MAX_MOMENTS}
    non-overlapping whole-second spans 0 to last_second, start <= end). For each moment:
    - what_happens: 1 short sentence (under 20 words) naming what is on screen.
    - brain_response: 1 to 2 crisp sentences (under 40 words) explaining why the brain responded
      that way in plain language.
    - suggestion: 1 concrete, actionable editing note (under 30 words), or null when the scene works
      well.
  * strengths and improvements: at most {MAX_LISTS} items each, 1 crisp sentence (under 30 words)
    per bullet that adds a broader takeaway without repeating a moment."""

COMPARISON_PROMPT = f"""You compare two videos, [A] and [B], for the person who made them. For each
you are given 1-fps pictures, spoken words, and the predicted second-by-second human brain
response side by side (columns A_ and B_). They may be two cuts of one campaign or two different
videos in a similar space: work out which from the pictures and words, and say so.

What the data means:
- 0 is typical film or television viewing on the model's scale. Values within about {NEAR} of 0 are
  close to typical.
- Three main groups: seeing (faces, bodies, scenes, visual), hearing (sound), and understanding
  (speech, social), plus overall and muted_overall (with the sound off).
- The visual response trails the pictures by 1 to 3 seconds; hearing follows sound immediately.

How to write a comparison that gives genuine value:
- Let the videos and the numbers explain each other. When [A] and [B] are two cuts of one campaign,
  match shared scenes by what is on screen (not by the clock, since the same shot can sit at
  different seconds in [A] and [B]). Explain what one cut added, cut, trimmed, or reordered, and
  how the viewer's brain responded to that editorial choice.
- When [A] and [B] are two different videos (such as two brand skits), compare their structural
  storytelling: how each hooks the viewer, how each transitions from entertainment/story into the
  brand pitch or call-to-action, and why one holds the brain where the other loses steam.
- When one video is longer than the other, past the shorter video's last_second the table has only
  one value per pair of columns (the longer video's B_ or A_ columns, in order: see, hear,
  understand, overall, muted_overall, faces, bodies, scenes, visual, sound, speech, social).
  Evaluate those extra unshared seconds purely on their own merits against typical viewing (0),
  paying close attention to the signs (+ or -) of each column in that row.
- Every claim about levels against 0 or between [A] and [B] must hold in the table at those exact
  seconds; check the numbers for that specific time span before writing! Never claim a stretch is
  above typical if the table shows values near or below 0 in those seconds.
- Judge which video is stronger overall from the shared-seconds counts in the first line of the
  table and the peaks (they are close if neither leads in at least 6 of 10 shared seconds), but
  never quote the raw counts line (never write "A higher in 13, B higher in 14").
- Name the videos ONLY as [A] and [B] (brackets included) in every field, including the headline
  and summary, never a bare A or B, and never by their titles without [A] or [B]. Always cite times
  with the video label ([A] 12 s, [B] 20-24 s).
- NEVER write a decimal number from the table anywhere in the output (no "+0.13", "-0.22", "0.25",
  or "(+0.31 vs +0.08)"): express every comparison in words against typical viewing or against the
  other video ("well above typical", "clearly higher than [A]", "below typical"). Never write
  internal column names ("muted_overall" -> "with the sound off").
- Voice and tone: sound like an experienced human video director or film editor comparing two cuts
  in an edit suite, never like an AI:
  * Use plain, grounded editing language. Do not exaggerate dips ("everything collapses", "the
    brain goes offline"); below typical simply means quieter than average film or TV viewing.
  * Avoid classic AI writing tells: never use em dashes or en dashes anywhere (use periods, commas,
    colons, or parentheses instead); no "not X, but Y" rhetorical contrasts; no shallow "-ing"
    riders ("..., confirming...", "..., proving..."); and no dramatic one-line closers.
- Write for fast, effortless scanning so every sentence earns its place:
  * headline: at most {MAX_HEADLINE} characters, naming [A] and [B] and the core takeaway in plain
    language (no em dashes).
  * summary: 2 to 3 crisp sentences (under 75 words total) stating how [A] and [B] relate and why
    one pulls ahead or where they trade leads.
  * differences: 1 to {MAX_DIFFERENCES} non-overlapping differences (start/end whole seconds
    within each video's 0..last_second, or null when a scene exists only in the other cut). For
    each difference:
    - what_differs: 1 short sentence (under 25 words) naming what differs on screen.
    - brain_response: 1 to 2 crisp sentences (under 40 words) explaining how and why the brain
      response differs.
  * a_does_better, b_does_better, and suggestions: at most {MAX_COMPARISON_LISTS} items each,
    1 crisp sentence (under 30 words) per bullet."""


def _cell(values, second):
    return (
        "" if second >= len(values) or values[second] is None else f"{float(values[second]):+.2f}"
    )


def _tallies(values):
    """(above, near, below) seconds for a series as the table shows it: each value rounded to
    2 decimals, with None and seconds past the end skipped."""
    above = near = below = 0
    for value in values:
        if value is None:
            continue
        value = round(float(value), 2)
        if value > NEAR:
            above += 1
        elif value < -NEAR:
            below += 1
        else:
            near += 1
    return above, near, below


def _series_counts(result):
    counts = []
    for name in _SHOWN:
        above, near, below = _tallies(result["signals"][name])
        counts.append(f"{name} {above}/{near}/{below}")
    return ", ".join(counts)


def _shared_comparison(a, b):
    """(shared seconds, A higher, B higher, within) over the seconds both videos share,
    comparing overall as the table shows it."""
    shared = min(a["duration_seconds"], b["duration_seconds"])
    overall_a, overall_b = a["signals"]["overall"], b["signals"]["overall"]
    a_higher = b_higher = within = 0
    for second in range(shared):
        value_a = overall_a[second] if second < len(overall_a) else None
        value_b = overall_b[second] if second < len(overall_b) else None
        if value_a is None or value_b is None:
            continue
        difference = round(round(float(value_a), 2) - round(float(value_b), 2), 2)
        if difference > NEAR:
            a_higher += 1
        elif difference < -NEAR:
            b_higher += 1
        else:
            within += 1
    return shared, a_higher, b_higher, within


def series_table(result):
    """One row per second: t, the three groups, overall, overall with the sound off, and the 7
    sub-signals so the writer can see both the group curves and the underlying drivers."""
    duration = result["duration_seconds"]
    lines = [
        f"duration_seconds {duration}, last_second {duration - 1}. One row per second. "
        f"Seconds above / near / below typical: {_series_counts(result)}.",
        " ".join(["t", *_TABLE_COLUMNS]),
    ]
    signals, muted = result["signals"], result["muted"]["overall"]
    for second in range(duration):
        cells = [_cell(signals[name], second) for name in _SHOWN]
        cells.append(_cell(muted, second))
        cells.extend(_cell(signals[name], second) for name in _SUB_SIGNALS)
        lines.append(" ".join([f"{second:>3}", *cells]))
    return "\n".join(lines)


def comparison_table(a, b):
    """One row per second for both videos side by side, a blank past the shorter one, so a
    second can be compared with the same second of the other video (M3h §5b)."""
    names = [(label, name) for name in [*_SHOWN, "muted_overall"] for label in "AB"]
    seconds = max(a["duration_seconds"], b["duration_seconds"])
    a_above, a_near, a_below = _tallies(a["signals"]["overall"])
    b_above, b_near, b_below = _tallies(b["signals"]["overall"])
    shared, a_higher, b_higher, within = _shared_comparison(a, b)
    lines = [
        f"Video A: duration_seconds {a['duration_seconds']}, last_second "
        f"{a['duration_seconds'] - 1}. Video B: duration_seconds {b['duration_seconds']}, "
        f"last_second {b['duration_seconds'] - 1}. One row per second. "
        f"Seconds above / near / below typical: A overall {a_above}/{a_near}/{a_below}, "
        f"B overall {b_above}/{b_near}/{b_below}. Over the shared seconds 0-{shared - 1}: "
        f"A higher in {a_higher}, B higher in {b_higher}, within 0.05 in {within}.",
        " ".join(["t", *(f"{label}_{name}" for label, name in names)]),
    ]
    for second in range(seconds):
        cells = []
        for label, name in names:
            result = a if label == "A" else b
            series = (
                result["muted"]["overall"] if name == "muted_overall" else result["signals"][name]
            )
            cells.append(_cell(series, second) if second < result["duration_seconds"] else "")
        lines.append(" ".join([f"{second:>3}", *cells]))
    return "\n".join(lines)


def words_block(words, label=None):
    """The spoken words, or None when there are none (M3h §5b)."""
    if not words:
        return None
    header = f"Video {label} spoken words:" if label else "Spoken words:"
    lines = "\n".join(f"{word['start']:.2f} s: {word['text']}" for word in words)
    return f"{header}\n{lines}"


def frame_blocks(frames, seconds, label=None):
    """Each second's label followed by its picture (M3h §5b)."""
    prefix = f"{label} " if label else ""
    content = []
    for second, frame in enumerate(frames[:seconds]):
        content.append({"type": "text", "text": f"{prefix}t = {second} s"})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.b64encode(frame).decode(),
                },
            }
        )
    return content


def _video_blocks(result, frames, words, label=None):
    """The spoken words, then the pictures; the table comes after them (M3g §5b)."""
    content = []
    spoken = words_block(words, label)
    if spoken:
        content.append({"type": "text", "text": spoken})
    content.extend(frame_blocks(frames, result["duration_seconds"], label))
    return content


def build_request(result, frames, words):
    """(system, content) for one video's report (M3g §5b)."""
    content = _video_blocks(result, frames, words)
    content.append({"type": "text", "text": series_table(result)})
    return SYSTEM_PROMPT, content


def build_comparison_request(a, b):
    """(system, content) for a comparison: A's words and frames, B's, then one table (M3h §5b)."""
    result_a, frames_a, words_a = a
    result_b, frames_b, words_b = b
    content = _video_blocks(result_a, frames_a, words_a, "A")
    content.append({"type": "text", "text": f"Video A per-second table:\n{series_table(result_a)}"})
    content.extend(_video_blocks(result_b, frames_b, words_b, "B"))
    content.append({"type": "text", "text": f"Video B per-second table:\n{series_table(result_b)}"})
    content.append({"type": "text", "text": comparison_table(result_a, result_b)})
    return COMPARISON_PROMPT, content


def _plain_letters(text, flip):
    """The [A]/[B] markers turned into the shown letters (M3h §2): with flip the opened video
    (stored B) is A, so [A] becomes B and [B] becomes A; else [A] becomes A and [B] B."""
    first, second = ("B", "A") if flip else ("A", "B")
    out = text.replace("[A]", first).replace("[B]", second)
    if flip:
        out = out.replace("B and A ", "A and B ")
        if ": B " in out and ", A " in out:
            prefix, rest = out.split(": B ", 1)
            b_part, a_part = rest.split(", A ", 1)
            out = f"{prefix}: A {a_part}, B {b_part}"
    return out


def orient_comparison(report, flip):
    """The stored comparison turned for the video the person opened (M3h §2): a copy with the
    markers filled in and, with `flip`, every A and B swapped. Never mutates the input, and
    raises when the answer was written before the markers."""
    if not isinstance(report, dict):
        raise ValueError("the comparison is not a JSON object")
    headline, summary = report.get("headline"), report.get("summary")
    if not (isinstance(headline, str) and isinstance(summary, str)):
        raise ValueError("the comparison has no headline and summary to orient")
    marked = any("[A]" in text or "[B]" in text for text in (headline, summary))
    if not marked:
        raise ValueError(
            "the comparison names no video with [A] or [B]: it was written before the markers"
        )
    oriented = copy.deepcopy(report)
    oriented["headline"] = _plain_letters(headline, flip)
    oriented["summary"] = _plain_letters(summary, flip)
    for difference in oriented.get("differences") or []:
        if flip:
            difference["a"], difference["b"] = difference["b"], difference["a"]
        for name in ("what_differs", "brain_response"):
            difference[name] = _plain_letters(difference[name], flip)
    if flip:
        oriented["a_does_better"], oriented["b_does_better"] = (
            oriented["b_does_better"],
            oriented["a_does_better"],
        )
    for name in ("a_does_better", "b_does_better", "suggestions"):
        oriented[name] = [_plain_letters(item, flip) for item in oriented[name]]
    return oriented
