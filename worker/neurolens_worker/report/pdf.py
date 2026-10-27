"""
Lay out the report as an A4 PDF (ReportLab, pure Python):

  1  headline, summary, overall engagement chart with frame markers, the hook
  2  what's working — each point with its frame
  3  what to improve — priority, frame, issue, recommended edit
  4  all regions + audio contribution charts, audio verdict
  5  key frames with notes, next steps, how to read this report
"""

from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from .moments import Moment
from .schema import ReportContent

NAVY = colors.HexColor("#0F1629")
ACCENT = colors.HexColor("#0D7C8F")
MUTED = colors.HexColor("#64748B")
LINE = colors.HexColor("#E2E8F0")
PANEL = colors.HexColor("#F1F5F9")
PRIORITY_COLOR = {"high": colors.HexColor("#B91C1C"), "medium": colors.HexColor("#B45309"),
                  "low": colors.HexColor("#15803D")}

PAGE_W, PAGE_H = A4
MARGIN = 16 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

_base = getSampleStyleSheet()
S = {
    "title": ParagraphStyle("title", parent=_base["Title"], fontName="Helvetica-Bold", fontSize=20, leading=24,
                            textColor=NAVY, alignment=TA_LEFT, spaceAfter=2),
    "meta": ParagraphStyle("meta", parent=_base["Normal"], fontSize=8.5, textColor=MUTED, leading=12),
    "headline": ParagraphStyle("headline", parent=_base["Normal"], fontName="Helvetica-Bold", fontSize=13.5,
                               leading=18, textColor=NAVY, spaceBefore=8, spaceAfter=6),
    "h2": ParagraphStyle("h2", parent=_base["Heading2"], fontName="Helvetica-Bold", fontSize=13, leading=16,
                         textColor=ACCENT, spaceBefore=10, spaceAfter=6),
    "h3": ParagraphStyle("h3", parent=_base["Normal"], fontName="Helvetica-Bold", fontSize=10.5, leading=14,
                         textColor=NAVY, spaceAfter=2),
    "body": ParagraphStyle("body", parent=_base["Normal"], fontSize=9.5, leading=13.5, textColor=NAVY),
    "small": ParagraphStyle("small", parent=_base["Normal"], fontSize=8.5, leading=12, textColor=MUTED),
    "label": ParagraphStyle("label", parent=_base["Normal"], fontName="Helvetica-Bold", fontSize=7.5,
                            leading=10, textColor=MUTED),
}


def _p(text: str, style: str = "body") -> Paragraph:
    return Paragraph(escape(text or ""), S[style])


def _img(path: Path | None, width: float) -> Image | Paragraph:
    if not path or not Path(path).exists():
        return _p("(frame unavailable)", "small")
    w, h = PILImage.open(path).size
    return Image(str(path), width=width, height=width * h / w)


def _card(left, right_flowables, left_w: float = 66 * mm) -> Table:
    """Frame on the left (60 mm), findings on the right, with a gutter between them."""
    t = Table([[left, right_flowables]], colWidths=[left_w, CONTENT_W - left_w])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, 0), 0), ("RIGHTPADDING", (0, 0), (0, 0), 0),
        ("LEFTPADDING", (1, 0), (1, 0), 4), ("RIGHTPADDING", (1, 0), (1, 0), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, LINE),
    ]))
    return t


def _labelled(label: str, text: str) -> list:
    return [_p(label.upper(), "label"), _p(text), Spacer(1, 3)]


def build_pdf(dst: Path, *, content: ReportContent, filename: str, duration_s: int, moments: list[Moment],
              frames: dict[int, Path], overall_png: Path, regions_png: Path, model: str) -> Path:
    when = datetime.now(timezone.utc).strftime("%d %b %Y")
    by_id = {m.frame_id: m for m in moments}

    def frame(fid: int, width: float = 60 * mm) -> list:
        m = by_id.get(fid)
        caption = f"F{fid} · {m.t}s · {m.reason}" if m else f"F{fid}"
        return [_img(frames.get(fid), width), _p(caption, "small")]

    story = [
        _p("NeuroLens video report", "title"),
        _p(f"{filename} · {duration_s}s · {when} · predicted viewer attention (TRIBE v2) · written by {model}", "meta"),
        _p(content.headline, "headline"),
        _p(content.summary),
        Spacer(1, 8),
        _img(overall_png, CONTENT_W),
        _p("How engaged a viewer is, second by second (1 = your video's strongest moment). "
           "The dashed lines mark the frames (F1, F2, …) used in this report.", "small"),
        _p("The opening hook", "h2"),
        _card(frame(content.hook.frame_id), [_p(content.hook.assessment)]),
        PageBreak(),
        _p("What's working", "h2"),
    ]
    for s in content.strengths:
        story.append(KeepTogether(_card(frame(s.frame_id), [
            _p(f"{s.title}  ({s.seconds})", "h3"),
            *_labelled("What the data shows", s.evidence),
            *_labelled("Why it works", s.why_it_works),
        ])))

    story += [Spacer(1, 6), _p("What to improve", "h2")]
    for imp in content.improvements:
        badge = Table([[Paragraph(f"<font color='white'><b>{escape(imp.priority.upper())} PRIORITY</b></font>",
                                  S["label"])]],
                      style=[("BACKGROUND", (0, 0), (-1, -1), PRIORITY_COLOR.get(imp.priority, MUTED)),
                             ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                             ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)],
                      colWidths=[30 * mm], hAlign="LEFT")
        story.append(KeepTogether(_card(frame(imp.frame_id), [
            badge, Spacer(1, 3),
            _p(f"{imp.title}  ({imp.seconds})", "h3"),
            *_labelled("What's happening", imp.issue),
            *_labelled("What to change", imp.recommendation),
        ])))

    story += [
        PageBreak(),
        _p("What grabs attention, second by second", "h2"),
        _img(regions_png, CONTENT_W),
        _p("Each chart runs from your video's weakest moment (0) to its strongest (1). The last chart shows "
           "the sound signal with and without your audio: the shaded gap is what your audio adds.", "small"),
        Spacer(1, 6),
        _p(f"Your audio: {content.audio.verdict}", "h3"),
        _p(content.audio.explanation),
        Spacer(1, 3),
        *_labelled("Tip", content.audio.recommendation),
        PageBreak(),
        _p("Key moments", "h2"),
    ]
    notes = {n.frame_id: n for n in content.frame_notes}
    rows = []
    for m in moments:
        n = notes.get(m.frame_id)
        rows.append([_img(frames.get(m.frame_id), 40 * mm),
                     [_p(f"F{m.frame_id} · {m.t}s · {m.reason}", "h3"),
                      _p(n.on_screen if n else ""), _p(n.brain_response if n else "", "small")]])
    if rows:
        t = Table(rows, colWidths=[44 * mm, CONTENT_W - 44 * mm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.5, LINE),
                               ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                               ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        story.append(t)

    story += [_p("Next steps", "h2")]
    story += [_p(f"{i}. {step}") for i, step in enumerate(content.next_steps, 1)]
    story += [
        Spacer(1, 10),
        Table([[[_p("How to read this report", "h3"), _p(
            "These results come from an AI model (Meta TRIBE v2) that predicts how an average viewer's brain "
            "reacts to your video. They are predictions, not a test with real viewers. Scores compare moments "
            "within this video only, so don't compare them with other videos. Make your edits, then upload "
            "again to see what changed.",
            "small")]]], colWidths=[CONTENT_W],
              style=[("BACKGROUND", (0, 0), (-1, -1), PANEL), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                     ("RIGHTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 6),
                     ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]),
    ]

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, 9 * mm, f"NeuroLens · {filename}")
        canvas.drawRightString(PAGE_W - MARGIN, 9 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(str(dst), pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN,
                            bottomMargin=16 * mm, title=f"NeuroLens report — {filename}", author="NeuroLens")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return dst
