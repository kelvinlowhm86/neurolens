"""The PDF of a written report and of a written comparison (M3h §18a).

Pure layout: no AWS, no database, no model. `fpdf2` and `fontTools` are imported inside the
functions that need them, so the web function's cold start is unchanged until a PDF is asked
for. The page has the same look: light paper, Space Grotesk and JetBrains Mono, orange for the
video and blue for its comparison partner.
"""

import io
import math
from datetime import datetime
from functools import lru_cache
from pathlib import Path, PurePosixPath

FONTS = Path(__file__).with_name("fonts")
NOTE = (
    "Written by Claude from predicted brain responses (TRIBE v2), not viewer data. "
    "0 = typical viewing."
)
GROUPS = (("see", "Seeing"), ("hear", "Hearing"), ("understand", "Understanding"))
PALETTE = {
    "ink": (22, 24, 28),
    "ink2": (78, 84, 94),
    "ink3": (128, 134, 144),
    "line": (222, 225, 230),
    "well": (246, 247, 249),
    "accent": (232, 116, 20),
    "blue": (52, 116, 214),
    "cool": (111, 126, 153),
}
FAMILY_FONTS = {
    "sg": ("SpaceGrotesk-Regular.ttf", "SpaceGrotesk-Bold.ttf", "SpaceGrotesk-Medium.ttf"),
    "sgm": ("SpaceGrotesk-Medium.ttf", "SpaceGrotesk-Medium.ttf"),
    "mono": ("JetBrainsMono-Regular.ttf",),
    "monom": ("JetBrainsMono-Medium.ttf",),
}
FALLBACK_FONTS = ("NotoSansSC.ttf", "NotoSansKR.ttf")
PAGE_BOTTOM = 20


def _mmss(value):
    """Whole seconds as m:ss, floored like the page's fmtRange (M3h §18a)."""
    try:
        seconds = max(0, int(math.floor(float(value))))
    except (TypeError, ValueError):
        seconds = 0
    return f"{seconds // 60}:{seconds % 60:02d}"


def _date(value):
    """An ISO timestamp as "31 Oct 2026", or "" when the report has none."""
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    return f"{moment.day} {moment:%b %Y}"


@lru_cache(maxsize=None)
def _font_chars(filename):
    from fontTools.ttLib import TTFont

    font = TTFont(str(FONTS / filename), fontNumber=0, lazy=True)
    chars = set()
    for table in font["cmap"].tables:
        chars.update(table.cmap)
    return frozenset(chars)


@lru_cache(maxsize=None)
def _main_chars(family):
    chars = set()
    for filename in FAMILY_FONTS.get(family, FAMILY_FONTS["sg"]):
        chars.update(_font_chars(filename))
    return frozenset(chars)


@lru_cache(maxsize=None)
def _fallback_chars():
    chars = set()
    for filename in FALLBACK_FONTS:
        chars.update(_font_chars(filename))
    return frozenset(chars)


def _coverage(family):
    return _main_chars(family) | _fallback_chars()


def _needs_fallback(strings):
    main = set()
    for family in FAMILY_FONTS:
        main.update(_main_chars(family))
    fallback = _fallback_chars()
    for text in strings:
        for char in str(text or ""):
            if ord(char) not in main and ord(char) in fallback:
                return True
    return False


def _all_strings(report, values=()):
    """Every string that can be drawn, for the one fallback-font decision."""
    found = []

    def walk(value):
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list | tuple):
            for item in value:
                walk(item)

    walk(report)
    walk(list(values))
    return found


class _Doc:
    """The shared layout on top of FPDF; the concrete class is made after fpdf2 is imported."""

    kind = ""
    fallback = False

    def normalize_text(self, text):
        family = getattr(self, "font_family", "sg") or "sg"
        coverage = _coverage(family)
        return "".join(char if ord(char) in coverage else "?" for char in str(text))

    def color(self, name, what="text"):
        value = PALETTE[name]
        if what == "draw":
            self.set_draw_color(*value)
        elif what == "fill":
            self.set_fill_color(*value)
        else:
            self.set_text_color(*value)

    def header(self):
        self.set_xy(self.l_margin, 9)
        self.set_font("sg", "B", 9)
        self.color("ink")
        self.cell(0, 4, "NeuroLens", new_x="LMARGIN")
        self.set_font("mono", "", 7.5)
        self.color("ink3")
        self.set_xy(self.l_margin, 9)
        self.cell(0, 4, self.kind, align="R")
        self.color("line", "draw")
        self.set_line_width(0.2)
        self.line(self.l_margin, 15, self.w - self.r_margin, 15)
        self.set_y(21)

    def footer(self):
        self.set_y(-13)
        self.set_font("sg", "", 7.5)
        self.color("ink3")
        self.cell(self.w - self.l_margin - self.r_margin - 14, 4, NOTE)
        self.set_font("mono", "", 7.5)
        self.cell(0, 4, f"{self.page_no()} / {{nb}}", align="R")

    def text_width(self, text):
        return self.get_string_width(text)

    def ellipsis(self, text, width):
        """Shorten a name in the middle, keeping its extension (M3h §18a)."""
        text = str(text or "")
        if self.text_width(text) <= width:
            return text
        suffix = PurePosixPath(text).suffix
        stem = text[: -len(suffix)] if suffix else text
        if self.text_width("…" + suffix) > width:
            return "…"
        for keep in range(len(stem), 0, -1):
            head = (keep + 1) // 2
            tail = keep - head
            candidate = stem[:head] + "…" + (stem[-tail:] if tail else "") + suffix
            if self.text_width(candidate) <= width:
                return candidate
        return "…" + suffix

    def kicker(self, text):
        self.set_font("monom", "", 7.5)
        self.color("ink3")
        self.cell(0, 4, str(text).upper(), new_x="LMARGIN", new_y="NEXT")
        self.ln(1.5)

    def para(self, text, size=10.5, style="", color="ink2", lh=5.2, w=0):
        self.set_font("sg", style, size)
        self.color(color)
        self.multi_cell(w, lh, str(text), align="L", new_x="LMARGIN", new_y="NEXT")

    def section(self, text, reserve=0):
        if self.get_y() + 7 + reserve > self.h - PAGE_BOTTOM:
            self.add_page()
        self.ln(3)
        self.set_font("sg", "B", 12)
        self.color("ink")
        self.cell(0, 6, text, new_x="LMARGIN", new_y="NEXT")
        self.ln(1.5)

    def lines(self, text, width, size=9.4, style=""):
        if not text:
            return 0
        self.set_font("sg", style, size)
        return len(self.multi_cell(width, 4.3, str(text), dry_run=True, output="LINES"))

    def bullets_height(self, items, width):
        total = 0
        for item in items or []:
            total += self.lines(item, width - 4.5, 9.4) * 4.9 + 1.6
        return total

    def bullets(self, items, x, width, mark_color="accent"):
        for item in items or []:
            y = self.get_y()
            self.color(mark_color, "fill")
            self.rect(x, y + 1.9, 1.6, 1.6, style="F")
            self.set_xy(x + 4.5, y)
            self.set_font("sg", "", 9.4)
            self.color("ink2")
            self.multi_cell(width - 4.5, 4.9, str(item), align="L", new_x="LEFT", new_y="NEXT")
            self.ln(1.6)
            self.set_x(x)

    def two_lists(
        self, left_title, left, right_title, right, left_mark="accent", right_mark="accent"
    ):
        width = self.w - self.l_margin - self.r_margin
        column = (width - 8) / 2
        block = 7.5 + max(self.bullets_height(left, column), self.bullets_height(right, column))
        if self.get_y() + block > self.h - PAGE_BOTTOM:
            self.add_page()
        self.ln(3)
        top = self.get_y()
        ends = []
        for index, (title, items, mark) in enumerate(
            ((left_title, left, left_mark), (right_title, right, right_mark))
        ):
            x = self.l_margin + index * (column + 8)
            self.set_xy(x, top)
            self.set_font("sg", "B", 11)
            self.color("ink")
            self.cell(column, 6, title, new_x="LEFT", new_y="NEXT")
            self.ln(1.5)
            self.set_x(x)
            self.bullets(items, x, column, mark)
            ends.append(self.get_y())
        self.set_y(max(ends))

    def legend(self, items, x, y, max_width=None):
        self.set_xy(x, y)
        remaining = max_width or (self.w - self.r_margin - x)
        for text, color, dashed in items:
            self.color(color, "draw")
            self.set_line_width(0.6)
            if dashed:
                self.set_dash_pattern(1.0, 0.9)
            start = self.get_x()
            self.line(start, y + 2, start + 6, y + 2)
            self.set_dash_pattern()
            self.set_x(start + 8)
            self.set_font("sg", "", 8)
            self.color("ink2")
            label = self.ellipsis(text, max(8, remaining - 10))
            self.cell(self.text_width(label) + 6, 4, label)
            remaining -= self.text_width(label) + 14

    def chart(self, series, duration, x, y, w, h, marks=(), muted=None):
        """A chart with level words, moment bands and a width-chosen time axis."""
        values = [value for values, *_ in series for value in values]
        values += list(muted or [])
        low = min([-0.3, *values])
        high = max([0.3, *values])
        if low < -0.3:
            low -= 0.05
        if high > 0.3:
            high += 0.05
        label_w = 24
        px, pw = x + label_w, w - label_w

        def X(t):
            return px + pw * t / max(duration - 1, 1)

        def Y(value):
            return y + h * (high - value) / (high - low)

        self.color("well", "fill")
        self.rect(px, y, pw, h, style="F")
        self.set_font("sg", "", 7.2)
        for value, word in ((0.2, "Well above"), (0.0, "Typical"), (-0.2, "Well below")):
            if not low < value < high:
                continue
            self.color("ink3")
            self.set_xy(x, Y(value) - 2)
            self.cell(label_w - 2, 4, word, align="R")
            self.set_line_width(0.15)
            if value == 0:
                self.set_dash_pattern(1.2, 1.2)
                self.color("ink3", "draw")
            else:
                self.color("line", "draw")
            self.line(px, Y(value), px + pw, Y(value))
            self.set_dash_pattern()

        bands = []
        for start, end in marks:
            end = min(float(end) + 1, duration - 1)
            start = float(start)
            if end <= start:
                end = start + 0.5
            if bands and start <= bands[-1][1]:
                bands[-1][1] = max(bands[-1][0] + 0.2, min(bands[-1][1], start - 0.15))
            bands.append([start, end])
        centers = []
        for start, end in bands:
            left, right = X(start), max(X(end), X(start) + 0.8)
            with self.local_context(fill_opacity=0.12):
                self.color("accent", "fill")
                self.rect(left, y, right - left, h, style="F")
            centers.append((left + right) / 2)
        last_right = -1e9
        for number, center in enumerate(centers, 1):
            center = max(center, last_right + 2.2)
            center = min(center, px + pw - 2)
            last_right = center + 2.2
            self.color("accent", "fill")
            self.circle(center, y - 3, 1.9, style="F")
            self.set_font("monom", "", 6.5)
            self.set_text_color(255, 255, 255)
            self.set_xy(center - 2, y - 5)
            self.cell(4, 4, str(number), align="C")

        if muted:
            self.color("blue", "draw")
            self.set_line_width(0.35)
            self.set_dash_pattern(1.0, 0.9)
            points = [(X(index), Y(value)) for index, value in enumerate(muted)]
            if len(points) == 1:
                self.circle(points[0][0], points[0][1], 0.7, style="F")
            else:
                self.polyline(points)
            self.set_dash_pattern()
        for series_values, color, dashed, fill in series:
            points = [(X(index), Y(value)) for index, value in enumerate(series_values)]
            if fill and len(points) > 1:
                with self.local_context(fill_opacity=0.10):
                    self.color(color, "fill")
                    self.polygon([(points[0][0], Y(0)), *points, (points[-1][0], Y(0))], style="F")
            self.color(color, "draw")
            self.set_line_width(0.55)
            if dashed:
                self.set_dash_pattern(1.0, 0.9)
            if len(points) == 1:
                self.circle(points[0][0], points[0][1], 0.8, style="F")
            else:
                self.polyline(points)
            self.set_dash_pattern()

        self.set_font("mono", "", 6.8)
        label_width = self.text_width("0:00") + 3
        max_ticks = max(1, int(pw // label_width) + 1)
        candidates = (1, 2, 5, 10, 15, 20, 30, 60, 120, 300, 600)
        counts = {
            step: len(range(0, max(1, int(math.ceil(duration))), step)) for step in candidates
        }
        step = next(
            (value for value in candidates if 3 <= counts[value] <= max_ticks),
            None,
        )
        if step is None:
            step = max(
                (value for value in candidates if counts[value] <= max_ticks),
                default=1,
            )
        self.color("ink3")
        for second in range(0, max(1, int(math.ceil(duration))), step):
            self.set_xy(X(second) - 6, y + h + 1)
            self.cell(12, 3.5, _mmss(second), align="C")
        return y + h + 6, low, high

    def strip(self, values, duration, x, y, w, h, label, low, high):
        """One of the three group strips, on the chart's shared scale."""
        label_w = 24
        px, pw = x + label_w, w - label_w

        def X(t):
            return px + pw * t / max(duration - 1, 1)

        def Y(value):
            return y + h * (high - value) / (high - low)

        self.set_font("sgm", "", 8)
        self.color("ink2")
        self.set_xy(x, y + h / 2 - 2)
        self.cell(label_w - 2, 4, label, align="R")
        self.set_line_width(0.15)
        for value in (0.2, -0.2):
            if low < value < high:
                self.color("line", "draw")
                self.set_dash_pattern(1.0, 1.2)
                self.line(px, Y(value), px + pw, Y(value))
        self.set_dash_pattern()
        self.color("line", "draw")
        self.line(px, Y(0), px + pw, Y(0))
        points = [(X(index), Y(value)) for index, value in enumerate(values)]
        for sign, color in ((1, "accent"), (-1, "cool")):
            if len(points) < 2:
                continue
            polygon = [(px, Y(0))]
            polygon += [
                (xx, Y(max(0, value) if sign > 0 else min(0, value)))
                for (xx, _), value in zip(points, values, strict=True)
            ]
            polygon += [(points[-1][0], Y(0))]
            with self.local_context(fill_opacity=0.35):
                self.color(color, "fill")
                self.polygon(polygon, style="F")
        self.color("ink3", "draw")
        self.set_line_width(0.25)
        if len(points) == 1:
            self.circle(points[0][0], points[0][1], 0.6, style="F")
        else:
            self.polyline(points)
        return y + h + 1.5

    def frame_box(self, frame, x, y, w, h):
        """Fit a frame inside the box, and shape the box like the frame itself."""
        from PIL import Image

        with Image.open(io.BytesIO(frame)) as image:
            image_w, image_h = image.size
        if not image_w or not image_h:
            return
        scale = min(w / image_w, h / image_h)
        draw_w, draw_h = image_w * scale, image_h * scale
        left, top = x + (w - draw_w) / 2, y + (h - draw_h) / 2
        self.color("well", "fill")
        self.rect(left, top, draw_w, draw_h, style="F")
        self.image(frame, left, top, draw_w, draw_h)

    def moment_height(self, moment, frame, width):
        frame_w, frame_h = (46, 30) if frame else (0, 0)
        text_w = width - (frame_w + 6 if frame else 0)
        what = self.lines(moment.get("what_happens"), text_w)
        brain = self.lines(moment.get("brain_response"), text_w)
        suggestion = moment.get("suggestion")
        self.set_font("sgm", "", 9.2)
        prefix = self.text_width("Try") + 2.5
        try_lines = self.lines(suggestion, text_w - prefix) if suggestion else 0
        content = 4.5 + 1.0 + what * 4.3
        if brain:
            content += 0.7 + brain * 4.3
        if try_lines:
            content += 0.7 + try_lines * 4.3
        return max(frame_h, content) + 4.8

    def moment_row(self, number, moment, frame, width):
        frame_w, frame_h = (46, 30) if frame else (0, 0)
        text_x = self.l_margin + (frame_w + 6 if frame else 0)
        text_w = width - (frame_w + 6 if frame else 0)
        suggestion = moment.get("suggestion")
        self.set_font("sgm", "", 9.2)
        prefix = self.text_width("Try") + 2.5
        height = self.moment_height(moment, frame, width)
        if self.get_y() + height > self.h - PAGE_BOTTOM:
            self.add_page()
        top = self.get_y()
        if frame:
            self.frame_box(frame, self.l_margin, top, frame_w, frame_h)
        self.color("accent", "fill")
        self.circle(text_x + 1.9, top + 2.2, 1.9, style="F")
        self.set_font("monom", "", 6.5)
        self.set_text_color(255, 255, 255)
        self.set_xy(text_x - 0.1, top + 0.2)
        self.cell(4, 4, str(number), align="C")
        self.set_xy(text_x + 6, top)
        self.set_font("monom", "", 9)
        self.color("accent")
        self.cell(
            0,
            4.5,
            f"{_mmss(moment.get('start'))}–{_mmss(moment.get('end'))}",
            new_x="LEFT",
            new_y="NEXT",
        )
        self.set_x(text_x)
        self.ln(1.0)
        self.set_x(text_x)
        self.set_font("sg", "", 9.2)
        self.color("ink")
        if moment.get("what_happens"):
            self.multi_cell(
                text_w, 4.3, moment["what_happens"], align="L", new_x="LEFT", new_y="NEXT"
            )
        if moment.get("brain_response"):
            self.set_xy(text_x, self.get_y() + 0.7)
            self.color("ink2")
            self.multi_cell(
                text_w, 4.3, moment["brain_response"], align="L", new_x="LEFT", new_y="NEXT"
            )
        if suggestion:
            self.set_xy(text_x, self.get_y() + 0.7)
            self.set_font("sgm", "", 9.2)
            self.color("accent")
            self.cell(prefix, 4.3, "Try ")
            self.set_font("sg", "", 9.2)
            self.color("ink2")
            self.multi_cell(text_w - prefix, 4.3, suggestion, align="L", new_x="LEFT", new_y="NEXT")
        self.set_y(max(self.get_y(), top + frame_h) + 4.8)
        self.color("line", "draw")
        self.set_line_width(0.15)
        self.line(self.l_margin, self.get_y() - 2.5, self.w - self.r_margin, self.get_y() - 2.5)

    def difference_height(self, difference, frames_a, frames_b, width):
        frame_w, frame_h = 38, 25
        show_pictures = bool(frames_a or frames_b)
        text_w = width - (2 * frame_w + 10 if show_pictures else 0)
        what = self.lines(difference.get("what_differs"), text_w)
        brain = self.lines(difference.get("brain_response"), text_w)
        content = (5 + what * 4.3) + (0.7 + brain * 4.3 if brain else 0)
        return max(frame_h + 5 if show_pictures else 4.5, content) + 5.5

    def difference_row(self, difference, frames_a, frames_b, width):
        frame_w, frame_h = 38, 25
        show_pictures = bool(frames_a or frames_b)
        text_x = self.l_margin + (2 * frame_w + 10 if show_pictures else 0)
        text_w = width - (2 * frame_w + 10 if show_pictures else 0)
        height = self.difference_height(difference, frames_a, frames_b, width)
        if self.get_y() + height > self.h - PAGE_BOTTOM:
            self.add_page()
        top = self.get_y()
        if show_pictures:
            for index, (side, frames, color, tag) in enumerate(
                (
                    (difference.get("a"), frames_a, "accent", "A"),
                    (difference.get("b"), frames_b, "blue", "B"),
                )
            ):
                x = self.l_margin + index * (frame_w + 3)
                self.set_font("monom", "", 7.5)
                if side is None:
                    self.color("well", "fill")
                    self.rect(x, top, frame_w, frame_h, style="F")
                    self.set_xy(x, top + frame_h / 2 - 2)
                    self.color("ink3")
                    self.cell(frame_w, 4, f"Not in {tag}", align="C")
                elif frames:
                    middle = int((float(side.get("start", 0)) + float(side.get("end", 0))) // 2)
                    self.frame_box(frames[min(middle, len(frames) - 1)], x, top, frame_w, frame_h)
                else:
                    self.color("well", "fill")
                    self.rect(x, top, frame_w, frame_h, style="F")
                    self.set_xy(x, top + frame_h / 2 - 2)
                    self.color("ink3")
                    self.cell(frame_w, 4, "No picture", align="C")
                self.set_xy(x, top + frame_h + 0.8)
                self.color(color if side else "ink3")
                when = f"{tag}  {_mmss(side.get('start'))}–{_mmss(side.get('end'))}" if side else ""
                self.cell(frame_w, 4, when)
        self.set_xy(text_x, top)
        if not show_pictures:
            self.set_font("monom", "", 8)
            self.color("accent")
            side_a = difference.get("a")
            self.cell(
                0,
                4.5,
                f"A  {_mmss(side_a.get('start'))}–{_mmss(side_a.get('end'))}"
                if side_a
                else "A  not in this video",
                new_x="LEFT",
                new_y="NEXT",
            )
            self.set_x(text_x)
            self.set_font("monom", "", 8)
            self.color("blue")
            side_b = difference.get("b")
            self.cell(
                0,
                4.5,
                f"B  {_mmss(side_b.get('start'))}–{_mmss(side_b.get('end'))}"
                if side_b
                else "B  not in this video",
                new_x="LEFT",
                new_y="NEXT",
            )
            self.set_x(text_x)
            self.ln(0.5)
        self.set_font("sg", "", 9.2)
        self.color("ink")
        if difference.get("what_differs"):
            self.multi_cell(
                text_w, 4.3, difference["what_differs"], align="L", new_x="LEFT", new_y="NEXT"
            )
        if difference.get("brain_response"):
            self.set_xy(text_x, self.get_y() + 0.7)
            self.color("ink2")
            self.multi_cell(
                text_w, 4.3, difference["brain_response"], align="L", new_x="LEFT", new_y="NEXT"
            )
        self.set_y(max(self.get_y(), top + (frame_h + 5 if show_pictures else 0)) + 5.5)
        self.color("line", "draw")
        self.set_line_width(0.15)
        self.line(self.l_margin, self.get_y() - 2.5, self.w - self.r_margin, self.get_y() - 2.5)


def _new_doc(kind, strings):
    from fpdf import FPDF

    fallback = _needs_fallback(strings)

    class Doc(_Doc, FPDF):
        pass

    pdf = Doc(format="A4", unit="mm")
    pdf.kind = kind
    pdf.fallback = fallback
    pdf.set_margins(18, 16, 18)
    pdf.set_auto_page_break(True, margin=PAGE_BOTTOM)
    pdf.add_font("sg", "", FONTS / "SpaceGrotesk-Regular.ttf")
    pdf.add_font("sg", "B", FONTS / "SpaceGrotesk-Bold.ttf")
    pdf.add_font("sgm", "", FONTS / "SpaceGrotesk-Medium.ttf")
    pdf.add_font("mono", "", FONTS / "JetBrainsMono-Regular.ttf")
    pdf.add_font("monom", "", FONTS / "JetBrainsMono-Medium.ttf")
    if fallback:
        pdf.add_font("noto-sc", "", FONTS / "NotoSansSC.ttf")
        pdf.add_font("noto-kr", "", FONTS / "NotoSansKR.ttf")
        pdf.set_fallback_fonts(["noto-sc", "noto-kr"], exact_match=False)
    pdf.alias_nb_pages()
    pdf.set_title(kind)
    pdf.set_creator("NeuroLens")
    return pdf


def report_pdf(name, result, report, frames):
    """The PDF for one written report (M3h §18a)."""
    report = report or {}
    signals = result.get("signals") or {}
    overall = list(signals.get("overall") or [])
    duration = max(1, len(overall))
    muted = list((result.get("muted") or {}).get("overall") or [])
    strings = _all_strings(report, [name, "NeuroLens", "Brain response report", NOTE])
    pdf = _new_doc("Brain response report", strings)
    pdf.add_page()

    width = pdf.w - pdf.l_margin - pdf.r_margin
    date = _date(report.get("created_at"))
    pdf.set_font("sg", "B", 19)
    pdf.color("ink")
    pdf.multi_cell(width, 8.4, pdf.ellipsis(name, width), align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    pdf.set_font("mono", "", 8)
    pdf.color("ink3")
    meta = f"{_mmss(duration)} long" + (f"  ·  {date}" if date else "")
    pdf.cell(0, 4.5, meta, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("sg", "B", 19)
    pdf.color("ink")
    pdf.multi_cell(
        width, 8.4, report.get("headline") or "", align="L", new_x="LMARGIN", new_y="NEXT"
    )
    pdf.ln(3)
    pdf.para(report.get("summary") or "", size=10.8, lh=5.4)
    pdf.ln(6)
    pdf.kicker("Overall response, second by second")
    top = pdf.get_y() + 3
    marks = [
        (moment.get("start", 0), moment.get("end", 0)) for moment in report.get("moments") or []
    ]
    bottom, low, high = pdf.chart(
        [(overall, "accent", False, True)],
        duration,
        pdf.l_margin,
        top,
        width,
        52,
        marks=marks,
        muted=muted[:duration] if muted else None,
    )
    key = [("With sound", "accent", False)]
    if muted:
        key.append(("Sound off: the same video with the sound removed", "blue", True))
    pdf.legend(key, pdf.l_margin + 24, bottom, width - 24)
    pdf.set_y(bottom + 6)
    for key_name, label in GROUPS:
        values = signals.get(key_name) or []
        pdf.set_y(
            pdf.strip(values, duration, pdf.l_margin, pdf.get_y(), width, 8.5, label, low, high)
        )
    pdf.set_font("sg", "", 7.5)
    pdf.color("ink3")
    pdf.cell(
        0,
        4,
        "Orange: above typical   ·   grey: below typical   ·   ±0.2 lines   ·   "
        "same scale as the chart",
        new_x="LMARGIN",
        new_y="NEXT",
    )

    pdf.two_lists(
        "What works", report.get("strengths"), "What to improve", report.get("improvements")
    )
    moments = report.get("moments") or []
    frames_list = frames or []

    def frame_for(moment):
        if not frames_list:
            return None
        middle = int((float(moment.get("start", 0)) + float(moment.get("end", 0))) // 2)
        return frames_list[min(middle, len(frames_list) - 1)]

    reserve = pdf.moment_height(moments[0], frame_for(moments[0]), width) if moments else 0
    pdf.section("Moments", reserve=reserve)
    for number, moment in enumerate(moments, 1):
        pdf.moment_row(number, moment, frame_for(moment), width)
    return bytes(pdf.output())


def comparison_pdf(a, b, report):
    """The PDF for one written comparison (M3h §18a)."""
    report = report or {}
    name_a, result_a, frames_a = a
    name_b, result_b, frames_b = b
    overall_a = list((result_a.get("signals") or {}).get("overall") or [])
    overall_b = list((result_b.get("signals") or {}).get("overall") or [])
    duration_a, duration_b = max(1, len(overall_a)), max(1, len(overall_b))
    strings = _all_strings(report, [name_a, name_b, "NeuroLens", "Comparison report", NOTE])
    pdf = _new_doc("Comparison report", strings)
    pdf.add_page()

    width = pdf.w - pdf.l_margin - pdf.r_margin
    date = _date(report.get("created_at"))
    pdf.set_font("sg", "B", 16)
    pdf.color("accent")
    pdf.multi_cell(
        width,
        7.2,
        "A  " + pdf.ellipsis(name_a, width - 8),
        align="L",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.set_font("sg", "B", 16)
    pdf.color("blue")
    pdf.multi_cell(
        width,
        7.2,
        "B  " + pdf.ellipsis(name_b, width - 8),
        align="L",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(1)
    pdf.set_font("mono", "", 8)
    pdf.color("ink3")
    meta = f"A {_mmss(duration_a)}  ·  B {_mmss(duration_b)}" + (f"  ·  {date}" if date else "")
    pdf.cell(0, 4.5, meta, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("sg", "B", 19)
    pdf.color("ink")
    pdf.multi_cell(
        width, 8.4, report.get("headline") or "", align="L", new_x="LMARGIN", new_y="NEXT"
    )
    pdf.ln(3)
    pdf.para(report.get("summary") or "", size=10.8, lh=5.4)
    pdf.ln(6)
    pdf.kicker("Overall response, second by second")
    top = pdf.get_y() + 2
    bottom, _low, _high = pdf.chart(
        [
            (overall_b, "blue", False, False),
            (overall_a, "accent", False, False),
        ],
        max(duration_a, duration_b),
        pdf.l_margin,
        top,
        width,
        56,
    )
    pdf.legend(
        [(f"A  {name_a}", "accent", False), (f"B  {name_b}", "blue", False)],
        pdf.l_margin + 24,
        bottom,
        width - 24,
    )
    pdf.set_y(bottom + 6)
    pdf.two_lists(
        "A does better",
        report.get("a_does_better"),
        "B does better",
        report.get("b_does_better"),
        "accent",
        "blue",
    )
    differences = report.get("differences") or []
    reserve = (
        pdf.difference_height(differences[0], frames_a or [], frames_b or [], width)
        if differences
        else 0
    )
    pdf.section("Where they differ", reserve=reserve)
    for difference in differences:
        pdf.difference_row(difference, frames_a or [], frames_b or [], width)
    pdf.section("Suggestions", reserve=18)
    pdf.bullets(report.get("suggestions"), pdf.l_margin, width)
    return bytes(pdf.output())
