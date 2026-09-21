"""
The drawing layer.

``DocumentCanvas`` is a thin, deterministic wrapper over PyMuPDF that does one
thing the raw API does not: every time it draws a value it records a
``FieldRecord`` carrying the exact characters drawn, the page, and the bounding
box computed analytically from the font metrics — no round-trip through text
extraction. ``validate`` later re-extracts the text and checks it agrees, so
the two paths stay honest about each other.

Bounding-box maths: PyMuPDF's ``insert_text`` places the *baseline* at the
given point, and the extracted word box for that run spans
``baseline - ascender*size`` to ``baseline - descender*size`` (descender is
negative). That is exactly what ``_text_bbox`` computes, and it reproduces
PyMuPDF's own extracted word boxes to the third decimal.
"""

from __future__ import annotations

import hashlib
import math
import random
import re
from dataclasses import dataclass
from typing import Iterable, Sequence

import pymupdf

from evals.factory.groundtruth import FieldKind, FieldOrigin, FieldRecord, FieldStatus

#: Logical font name -> PDF base-14 font. Base-14 fonts need no embedded font
#: file, which keeps output byte-stable across machines. "hand" is Times-Italic
#: drawn one glyph at a time with a seeded baseline/advance jitter — see
#: ``hand_text`` — which is how the intake form gets a handwriting-style
#: variant without shipping a TTF.
FONTS: dict[str, str] = {
    "sans": "helv",
    "sans_bold": "hebo",
    "sans_italic": "heit",
    "serif": "tiro",
    "serif_bold": "tibo",
    "serif_italic": "tiit",
    "mono": "cour",
    "mono_bold": "cobo",
    "hand": "tiit",
}

_FONT_CACHE: dict[str, pymupdf.Font] = {}

#: The base-14 fonts are WinAnsi-encoded, and a few common typographic
#: characters do not survive the round trip through PyMuPDF's text extraction
#: (U+2014 comes back as a middle dot). Chart data can contain any of them, so
#: every drawn string passes through here first; ``rendered_text`` records the
#: substituted form, which is what is actually on the page, while ``value``
#: keeps the original.
_TRANSLITERATE = str.maketrans({
    "\u2014": "-", "\u2013": "-", "\u2012": "-", "\u2212": "-",
    "\u2018": "'", "\u2019": "'", "\u201a": ",",
    "\u201c": '"', "\u201d": '"', "\u201e": '"',
    "\u2026": "...", "\u2022": "*", "\u00d7": "x", "\u00a0": " ",
    "\u2009": " ", "\u202f": " ", "\ufeff": "",
})


def drawable(text: str) -> str:
    """The form of ``text`` that is actually drawn on the page."""
    return text.translate(_TRANSLITERATE)


BLACK = (0.0, 0.0, 0.0)
INK = (0.08, 0.11, 0.26)
GREY = (0.45, 0.45, 0.45)
RULE = (0.25, 0.25, 0.25)


def _font(logical: str) -> pymupdf.Font:
    name = FONTS[logical]
    if name not in _FONT_CACHE:
        _FONT_CACHE[name] = pymupdf.Font(name)
    return _FONT_CACHE[name]


def text_width(text: str, logical: str, size: float) -> float:
    return pymupdf.get_text_length(drawable(text), fontname=FONTS[logical], fontsize=size)


def _text_bbox(x: float, baseline: float, text: str, logical: str, size: float) -> pymupdf.Rect:
    font = _font(logical)
    return pymupdf.Rect(
        x,
        baseline - font.ascender * size,
        x + text_width(text, logical, size),
        baseline - font.descender * size,
    )


def truncate(text: str, logical: str, size: float, max_width: float) -> str:
    """Shorten with an ellipsis so a value never runs into the next column."""
    text = drawable(text)
    if text_width(text, logical, size) <= max_width:
        return text
    ellipsis = "..."
    budget = max_width - text_width(ellipsis, logical, size)
    if budget <= 0:
        return ""
    out = ""
    for char in text:
        if text_width(out + char, logical, size) > budget:
            break
        out += char
    return (out.rstrip() + ellipsis) if out.strip() else ""


def wrap(text: str, logical: str, size: float, max_width: float, max_lines: int = 99) -> list[str]:
    """Greedy word wrap using real font metrics."""
    words = re.split(r"\s+", text.strip())
    lines: list[str] = []
    current = ""
    for word in words:
        if not word:
            continue
        candidate = f"{current} {word}".strip()
        if current and text_width(candidate, logical, size) > max_width:
            lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
        else:
            current = candidate
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and lines:
        lines[-1] = truncate(lines[-1], logical, size, max_width)
    return lines


@dataclass(frozen=True)
class PageGeometry:
    width: float = 612.0
    height: float = 792.0
    margin_left: float = 54.0
    margin_right: float = 54.0
    margin_top: float = 54.0
    margin_bottom: float = 54.0

    @property
    def content_width(self) -> float:
        return self.width - self.margin_left - self.margin_right

    @property
    def content_bottom(self) -> float:
        return self.height - self.margin_bottom


class DocumentCanvas:
    """A multi-page PDF under construction, with per-field ground truth."""

    def __init__(self, geometry: PageGeometry, seed: int, paper: tuple[float, float, float] | None = None) -> None:
        self.geometry = geometry
        self.doc = pymupdf.open()
        self.fields: list[FieldRecord] = []
        self._paper = paper
        self._jitter = random.Random(seed)
        self._page: pymupdf.Page | None = None
        self.page_number = 0
        self.y = 0.0
        self.new_page()

    # -- pages -------------------------------------------------------------

    def new_page(self) -> pymupdf.Page:
        page = self.doc.new_page(width=self.geometry.width, height=self.geometry.height)
        if self._paper is not None:
            page.draw_rect(pymupdf.Rect(0, 0, self.geometry.width, self.geometry.height),
                           color=None, fill=self._paper)
        self._page = page
        self.page_number = self.doc.page_count
        self.y = self.geometry.margin_top
        return page

    @property
    def page(self) -> pymupdf.Page:
        assert self._page is not None
        return self._page

    def room_for(self, height: float) -> bool:
        return self.y + height <= self.geometry.content_bottom

    # -- primitives (no ground truth) --------------------------------------

    def label(
        self,
        x: float,
        baseline: float,
        text: str,
        *,
        font: str = "sans",
        size: float = 8.5,
        color: Sequence[float] = BLACK,
    ) -> pymupdf.Rect:
        """Static chrome — headings, column titles, boilerplate."""
        drawn = drawable(text)
        self.page.insert_text((x, baseline), drawn, fontname=FONTS[font], fontsize=size, color=tuple(color))
        return _text_bbox(x, baseline, drawn, font, size)

    def rule(self, x0: float, y: float, x1: float, *, width: float = 0.7, color: Sequence[float] = RULE) -> None:
        self.page.draw_line(pymupdf.Point(x0, y), pymupdf.Point(x1, y), color=tuple(color), width=width)

    def band(self, rect: pymupdf.Rect, fill: Sequence[float]) -> None:
        self.page.draw_rect(rect, color=None, fill=tuple(fill))

    def box(self, rect: pymupdf.Rect, *, width: float = 0.7, color: Sequence[float] = RULE) -> None:
        self.page.draw_rect(rect, color=tuple(color), fill=None, width=width)

    # -- fields (ground truth) ---------------------------------------------

    def field(
        self,
        field_path: str,
        x: float,
        baseline: float,
        text: str,
        *,
        value: str | float | int | bool | None = None,
        unit: str | None = None,
        origin: FieldOrigin = FieldOrigin.CHART,
        font: str = "sans",
        size: float = 9.0,
        color: Sequence[float] = BLACK,
        align: str = "left",
        width: float | None = None,
        note: str | None = None,
    ) -> pymupdf.Rect:
        """Draw a value and record it. ``align`` needs ``width`` for right/center."""
        drawn = drawable(text)
        if align != "left":
            assert width is not None, "align requires width"
            slack = width - text_width(drawn, font, size)
            x = x + slack if align == "right" else x + slack / 2

        if font == "hand":
            rect = self._draw_hand(x, baseline, drawn, size, color)
        else:
            self.page.insert_text((x, baseline), drawn, fontname=FONTS[font], fontsize=size, color=tuple(color))
            rect = _text_bbox(x, baseline, drawn, font, size)

        self.fields.append(
            FieldRecord(
                field_path=field_path,
                status=FieldStatus.PRESENT,
                kind=FieldKind.TEXT,
                origin=origin,
                value=drawn if value is None else value,
                unit=unit,
                page=self.page_number,
                bbox=(rect.x0, rect.y0, rect.x1, rect.y1),
                rendered_text=drawn,
                note=note,
            )
        )
        return rect

    def checkbox(
        self,
        field_path: str,
        x: float,
        top: float,
        *,
        checked: bool,
        origin: FieldOrigin = FieldOrigin.SYNTHETIC,
        side: float = 8.5,
        note: str | None = None,
    ) -> pymupdf.Rect:
        """A ruled square, with a hand-drawn 'X' when checked."""
        rect = pymupdf.Rect(x, top, x + side, top + side)
        self.box(rect, width=0.8, color=BLACK)
        rendered = ""
        if checked:
            size = side * 0.98
            baseline = top + side - side * 0.22
            mark_x = x + side * 0.20
            self._draw_hand(mark_x, baseline, "X", size, INK)
            rendered = "X"
        self.fields.append(
            FieldRecord(
                field_path=field_path,
                status=FieldStatus.PRESENT,
                kind=FieldKind.CHECKBOX,
                origin=origin,
                value=checked,
                page=self.page_number,
                bbox=(rect.x0, rect.y0, rect.x1, rect.y1),
                rendered_text=rendered,
                note=note,
            )
        )
        return rect

    def absent(
        self,
        field_path: str,
        *,
        value: str | float | int | bool | None,
        unit: str | None = None,
        origin: FieldOrigin = FieldOrigin.PERTURBED,
        note: str | None = None,
    ) -> None:
        """Record a field the document does not carry at all."""
        self.fields.append(
            FieldRecord(
                field_path=field_path,
                status=FieldStatus.ABSENT,
                kind=FieldKind.TEXT,
                origin=origin,
                value=value,
                unit=unit,
                page=None,
                bbox=None,
                rendered_text="",
                note=note,
            )
        )

    def illegible(
        self,
        field_path: str,
        x: float,
        baseline: float,
        *,
        value: str | float | int | bool | None,
        width: float,
        unit: str | None = None,
        size: float = 9.0,
        origin: FieldOrigin = FieldOrigin.PERTURBED,
        note: str | None = None,
    ) -> pymupdf.Rect:
        """
        Draw ink where a value should be, but no text.

        The region reads as a smudged, over-inked scribble; nothing lands in the
        text layer, so an extractor must report it as unreadable rather than
        guessing. ``value`` still carries what the chart says.
        """
        rect = pymupdf.Rect(x, baseline - size * 0.82, x + width, baseline + size * 0.22)
        self._draw_scribble(rect)
        self.fields.append(
            FieldRecord(
                field_path=field_path,
                status=FieldStatus.ILLEGIBLE,
                kind=FieldKind.TEXT,
                origin=origin,
                value=value,
                unit=unit,
                page=self.page_number,
                bbox=(rect.x0, rect.y0, rect.x1, rect.y1),
                rendered_text="",
                note=note,
            )
        )
        return rect

    # -- handwriting + scribble --------------------------------------------

    def _draw_hand(self, x: float, baseline: float, text: str, size: float, color: Sequence[float]) -> pymupdf.Rect:
        """
        Glyph-by-glyph Times-Italic with a seeded baseline/advance/size wobble.

        The wobble stays under ~0.4 pt so PyMuPDF still extracts the run as one
        line — ``validate`` compares whitespace-insensitively, which absorbs the
        per-glyph advance jitter.
        """
        font = _font("hand")
        cursor = x
        top = baseline - font.ascender * size
        bottom = baseline - font.descender * size
        for char in text:
            if char == " ":
                cursor += text_width(" ", "hand", size) * self._jitter.uniform(0.9, 1.25)
                continue
            glyph_size = size * self._jitter.uniform(0.965, 1.04)
            dy = self._jitter.uniform(-0.35, 0.35)
            self.page.insert_text(
                (cursor, baseline + dy),
                char,
                fontname=FONTS["hand"],
                fontsize=glyph_size,
                color=tuple(color),
            )
            top = min(top, baseline + dy - font.ascender * glyph_size)
            bottom = max(bottom, baseline + dy - font.descender * glyph_size)
            cursor += text_width(char, "hand", glyph_size) * self._jitter.uniform(0.97, 1.06)
        return pymupdf.Rect(x, top, max(cursor, x + 1.0), bottom)

    def _draw_scribble(self, rect: pymupdf.Rect) -> None:
        """Deterministic over-inked smudge filling ``rect``."""
        steps = max(10, int(rect.width / 3.0))
        for stroke in range(6):
            points: list[pymupdf.Point] = []
            phase = self._jitter.uniform(0.0, math.pi)
            for step in range(steps + 1):
                t = step / steps
                px = rect.x0 + t * rect.width
                wave = math.sin(phase + t * math.pi * (2.5 + stroke * 1.4)) * rect.height * 0.42
                py = (rect.y0 + rect.height * (0.18 + 0.13 * stroke)
                      + wave + self._jitter.uniform(-0.7, 0.7))
                points.append(pymupdf.Point(px, min(max(py, rect.y0), rect.y1)))
            self.page.draw_polyline(points, color=(0.10, 0.10, 0.14), width=1.1 + 0.2 * (stroke % 3))
        # A few cross strokes, the way a pen goes back over a mistake.
        for cross in range(4):
            x0 = rect.x0 + rect.width * self._jitter.uniform(0.02, 0.7)
            x1 = min(x0 + rect.width * self._jitter.uniform(0.2, 0.45), rect.x1)
            self.page.draw_line(
                pymupdf.Point(x0, rect.y1 - 1.0), pymupdf.Point(x1, rect.y0 + 1.0),
                color=(0.10, 0.10, 0.14), width=1.0,
            )

    # -- output ------------------------------------------------------------

    def page_sizes(self) -> list[dict[str, float | int]]:
        return [
            {"number": i + 1, "width": round(self.doc[i].rect.width, 3), "height": round(self.doc[i].rect.height, 3)}
            for i in range(self.doc.page_count)
        ]

    def finish(self, title: str, subject: str) -> bytes:
        set_fixed_metadata(self.doc, title=title, subject=subject)
        return canonical_pdf_bytes(self.doc.tobytes(garbage=4, deflate=True))


# --------------------------------------------------------------------------
# Deterministic PDF bytes
# --------------------------------------------------------------------------

#: MuPDF stamps a random /ID pair into the trailer on every save; nothing else
#: in a save of identical content varies. Rewriting /ID with a hash of the rest
#: of the file makes the output byte-identical run to run.
#:
#: The two entries are normally hex strings, but roughly one save in fifty
#: MuPDF writes one of them as a PDF literal string instead, because the random
#: bytes happened to be printable — so the array has to be scanned, not matched
#: with a hex-only pattern. Missing one of those leaves a random ID in the file
#: and quietly breaks reproducibility, which is why ``canonical_pdf_bytes``
#: raises rather than returning the bytes unchanged.
_CANONICAL_ID = b"0" * 32


class NonReproduciblePdf(RuntimeError):
    """The saved PDF has no trailer /ID array to canonicalise."""


def _id_array_span(raw: bytes) -> tuple[int, int] | None:
    """Byte span of the trailer's ``/ID [ ... ]`` array value, brackets included."""
    trailer = raw.rfind(b"trailer")
    region_start = trailer if trailer != -1 else max(0, len(raw) - 4096)
    key = raw.rfind(b"/ID", region_start)
    if key == -1:
        return None

    index = key + 3
    while index < len(raw) and raw[index:index + 1].isspace():
        index += 1
    if raw[index:index + 1] != b"[":
        return None

    open_bracket = index
    index += 1
    while index < len(raw):
        char = raw[index:index + 1]
        if char == b"(":
            index += 1
            nesting = 1
            while index < len(raw) and nesting:
                current = raw[index:index + 1]
                if current == b"\\":
                    index += 2
                    continue
                if current == b"(":
                    nesting += 1
                elif current == b")":
                    nesting -= 1
                index += 1
            continue
        if char == b"<":
            close = raw.find(b">", index)
            if close == -1:
                return None
            index = close + 1
            continue
        if char == b"]":
            return open_bracket, index + 1
        index += 1
    return None


FIXED_PDF_DATE = "D:20260101000000Z"


def set_fixed_metadata(doc: pymupdf.Document, *, title: str, subject: str) -> None:
    doc.set_metadata(
        {
            "title": title,
            "subject": subject,
            "author": "evals.factory (synthetic)",
            "producer": "evals.factory",
            "creator": "evals.factory",
            "keywords": "synthetic",
            "creationDate": FIXED_PDF_DATE,
            "modDate": FIXED_PDF_DATE,
        }
    )


def canonical_pdf_bytes(raw: bytes) -> bytes:
    """Replace the random trailer /ID with one derived from the file's content."""
    span = _id_array_span(raw)
    if span is None:
        raise NonReproduciblePdf(
            "saved PDF has no trailer /ID array; output would differ between runs"
        )
    start, end = span

    def with_id(value: bytes) -> bytes:
        return raw[:start] + b"[<" + value + b"><" + value + b">]" + raw[end:]

    digest = hashlib.sha256(with_id(_CANONICAL_ID)).hexdigest()[:32].upper().encode("ascii")
    return with_id(digest)


def words_in(page: pymupdf.Page, bbox: Iterable[float], tolerance: float = 0.75) -> list[tuple]:
    """Words on ``page`` whose centre falls inside ``bbox`` (grown by tolerance)."""
    x0, y0, x1, y1 = bbox
    clip = pymupdf.Rect(x0 - tolerance, y0 - tolerance, x1 + tolerance, y1 + tolerance)
    hits = []
    for word in page.get_text("words"):
        centre = pymupdf.Point((word[0] + word[2]) / 2, (word[1] + word[3]) / 2)
        if centre in clip:
            hits.append(word)
    # A field bbox always covers a single drawn line, and the handwriting
    # variant jitters each glyph's baseline, so ordering by y first would
    # scramble the run. x alone is the reading order here.
    hits.sort(key=lambda w: w[0])
    return hits


def emit_field(
    canvas: DocumentCanvas,
    plan,
    field_path: str,
    x: float,
    baseline: float,
    text: str,
    *,
    value: str | float | int | bool | None = None,
    unit: str | None = None,
    origin: FieldOrigin = FieldOrigin.CHART,
    font: str = "sans",
    size: float = 9.0,
    color: Sequence[float] = BLACK,
    align: str = "left",
    width: float | None = None,
    illegible_width: float | None = None,
    skip_if_blank: bool = False,
    note: str | None = None,
) -> pymupdf.Rect | None:
    """
    Draw one field, honouring any ``missing_field`` / ``illegible_field``
    perturbation naming it.

    ``value`` (defaulting to ``text``) is the chart truth, and survives into the
    ground truth even when the document does not carry it — a perturbed field is
    recorded as explicitly absent or illegible, never silently dropped.
    """
    truth = text if value is None else value
    if skip_if_blank and not text.strip():
        # The chart has nothing here, so the form has a blank line and there is
        # nothing to extract. Recording a zero-width bbox would be noise.
        return None
    if plan.is_missing(field_path):
        canvas.absent(field_path, value=truth, unit=unit, note=note)
        return None
    if plan.is_illegible(field_path):
        ink_width = illegible_width or width or max(text_width(text, font, size), 24.0)
        return canvas.illegible(
            field_path, x, baseline, value=truth, unit=unit, width=ink_width, size=size, note=note
        )
    return canvas.field(
        field_path, x, baseline, text,
        value=truth, unit=unit, origin=origin, font=font, size=size,
        color=color, align=align, width=width, note=note,
    )
