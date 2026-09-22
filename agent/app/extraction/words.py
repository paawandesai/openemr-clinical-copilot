"""
Stage 1 of D3: a page's words and where they are.

D3 makes document extraction three stages — text layer with word boxes,
vision LLM into a strict schema, deterministic locate. This module is the
first stage, and `locate.py` is the third. Neither calls a model, opens a
socket, or reads anything but the PDF it was handed.

**One output shape for two very different readers.** A digital PDF already
knows where every glyph sits, so PyMuPDF returns the answer exactly. An
image-only page knows nothing, so Tesseract guesses, and its guesses come
with confidences. `PageWords` carries both under the same type, with
`source` saying which reader produced it and `confidence` 1.0 for every
word that came out of a text layer. A caller that wants to treat OCR
output more sceptically than a text layer can; a caller that does not,
still gets boxes in the same coordinate space either way.

**Why Tesseract and not docTR.** Both were considered; Tesseract wins on
the constraints this repository actually has.

* *It is already a dependency of the environment, not of the image.*
  Tesseract is a system binary plus `pytesseract`, a thin subprocess
  wrapper. docTR pulls in PyTorch or TensorFlow — roughly 2 GB in the
  agent image, for a service whose other model calls go out over HTTPS.
* *No weights are fetched at run time.* docTR downloads its detection and
  recognition checkpoints on first use. A unit suite that is documented as
  needing "no network, no credentials" cannot depend on that, and neither
  can an eval gate that has to be reproducible (D6).
* *Word boxes with per-word confidence come out of the box.* Tesseract's
  TSV output gives text, box and confidence per word, which is exactly the
  shape stage 3 consumes.
* *It is CPU-deterministic and fast enough.* Extraction runs
  asynchronously at upload time (D7), so a second per page is not on any
  interactive path.

The honest cost: docTR's deep detector reads degraded scans and handwriting
materially better than Tesseract's classical layout analysis, and the
measurement in `tests/locate_measurement.py` shows exactly where that
hurts — the heavy-scan sample is where located rate falls off. If document
quality in production turns out to look like that sample, revisiting this
with docTR (or a hosted OCR) is the first thing to try, and the
measurement is the evidence to bring. Swapping readers means implementing
`_ocr_page` alone; nothing else in the extraction path knows which one ran.

**Page-segmentation mode is measured, not assumed.** Tesseract's `--psm`
changed the located rate on the factory's scanned samples by more than any
other knob, and no single mode won on both a lab table and an intake form.
`OcrSettings.psm` therefore takes several candidate modes; each is run and
the page keeps the one with the highest confidence-weighted character
count, which is a property of Tesseract's own output and involves no
ground truth. Ties go to the earlier mode in the tuple, so the choice is
deterministic.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from statistics import median

import pymupdf

from app.extraction.geometry import BBox

#: A page with fewer than this many word-shaped tokens in its text layer is
#: treated as image-only. Scanning tools routinely stamp a line or two of
#: text onto an otherwise imaged page ("Scanned by …", a Bates number), and
#: reading that as "this page has a text layer" would skip OCR on a page
#: with no readable content. Four is above any such stamp and below any
#: real page of a lab report or an intake form.
MIN_TEXT_LAYER_WORDS = 4

#: Row splitting tolerance, as a multiple of the page's median word height.
#: Rows in these documents are pitched about one word-height apart, so a
#: little over half a word-height separates "next word" from "next row"
#: without being tripped by the baseline wobble of handwriting.
ROW_GAP_FRACTION = 0.6

#: Baseline-slope search, as dy/dx. +/- 0.061 is +/- 3.5 degrees, which
#: covers the doc-factory's heaviest scan profile (1.7) with room to
#: spare; a page skewed further than this is a rescan, not a locate
#: problem.
_SLOPE_LIMIT = 0.061
_SLOPE_STEPS = 61

#: Projection-profile bin width, as a multiple of the page's median word
#: height. A quarter of a word-height is fine enough to tell two rows
#: apart and coarse enough to keep one row in one bin.
_PROFILE_BIN_FRACTION = 0.25


class TextSource(StrEnum):
    """Which reader produced a page's words."""

    #: PyMuPDF read the PDF's own text layer. Characters are exact.
    TEXT_LAYER = "text_layer"
    #: Tesseract read a rendering of the page. Characters are a guess.
    OCR = "ocr"


class OcrPolicy(StrEnum):
    """When OCR is allowed to run."""

    #: OCR a page only when its text layer is too thin to be real.
    AUTO = "auto"
    #: Never OCR. A page with no text layer comes back with no words.
    NEVER = "never"
    #: OCR every page, whatever its text layer says.
    ALWAYS = "always"


class OcrUnavailableError(RuntimeError):
    """Tesseract (or `pytesseract`) is not installed on this machine."""


@dataclass(frozen=True, slots=True)
class OcrSettings:
    """
    How to run Tesseract. See the module docstring for why these defaults.

    `psm` is a tuple of candidate page-segmentation modes; see
    `_ocr_page` for the selection rule.
    """

    dpi: int = 300
    psm: tuple[int, ...] = (11, 6, 4)
    lang: str = "eng"
    #: Render greyscale. Colour carries no glyph information here and the
    #: extra two channels only cost Tesseract time.
    grayscale: bool = True
    #: Drop words Tesseract is less sure of than this, 0.0 to 1.0. Zero
    #: keeps everything: a low-confidence word that happens to be the value
    #: is worth having, and `Word.confidence` lets a caller decide later.
    min_confidence: float = 0.0
    timeout_seconds: int = 120

    def __post_init__(self) -> None:
        if not self.psm:
            raise ValueError("OcrSettings.psm needs at least one candidate mode")
        if self.dpi < 72:
            raise ValueError(f"OcrSettings.dpi must be at least 72; got {self.dpi}")
        if not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError(f"min_confidence is a 0-1 fraction; got {self.min_confidence}")


@dataclass(frozen=True, slots=True)
class Word:
    """One word, where it is, and how sure the reader is of it."""

    text: str
    bbox: BBox
    #: 1.0 for a text layer; Tesseract's per-word confidence / 100 for OCR.
    confidence: float
    #: Index of the visual row this word sits on, 0-based down the page.
    row: int
    #: Position in reading order within the page: `(row, x0)` sorted.
    order: int


@dataclass(frozen=True, slots=True)
class PageWords:
    """Every word on one page, in reading order."""

    page: int
    width: float
    height: float
    words: tuple[Word, ...]
    source: TextSource
    #: Wall-clock seconds this page took to read, the metric the work
    #: order asks to compare text layer against OCR.
    seconds: float
    #: The `--psm` Tesseract was run with, for an OCR page; None otherwise.
    ocr_psm: int | None = None
    #: Page skew taken out before rows were assigned, in radians of slope
    #: (dy/dx). Zero on an unskewed page.
    baseline_slope: float = 0.0
    _rows: tuple[tuple[int, ...], ...] = field(default=(), repr=False)

    @property
    def rows(self) -> tuple[tuple[int, ...], ...]:
        """Word indices grouped into visual rows, top to bottom."""
        return self._rows

    def row_words(self, row: int) -> tuple[Word, ...]:
        """The words on one visual row, left to right."""
        if not 0 <= row < len(self._rows):
            return ()
        return tuple(self.words[index] for index in self._rows[row])

    @property
    def mean_confidence(self) -> float:
        if not self.words:
            return 0.0
        return sum(word.confidence for word in self.words) / len(self.words)


@dataclass(frozen=True, slots=True)
class DocumentWords:
    """Every page of one document."""

    pages: tuple[PageWords, ...]

    def page(self, number: int) -> PageWords | None:
        """The 1-based page, or None if the document has no such page."""
        for page in self.pages:
            if page.page == number:
                return page
        return None

    @property
    def seconds(self) -> float:
        return sum(page.seconds for page in self.pages)


def estimate_baseline_slope(boxes: Sequence[BBox]) -> float:
    """
    The page's dominant text slope, as dy/dx.

    A scan is a photograph of a page held slightly crooked, so a printed
    row's word boxes drift downwards as they go right — on the factory's
    heavy profile, by more than a row's height across the width of a lab
    table. Assigning rows on raw `y` would then split one printed row into
    several and put a test name on a different row from its result, which
    is precisely what `locate_in_row` exists to prevent.

    The slope is found by search, scored by the classic deskew criterion:
    project the words' de-skewed centres `y - m*x` onto a fixed-width
    histogram and take the slope whose profile is most concentrated, i.e.
    the largest sum of squared bin counts. At the right slope every word of
    a printed row lands in one bin; at the wrong one each row smears across
    several, and the sum of squares falls.

    Counting *rows* instead would be degenerate — a large enough slope
    smears every row into its neighbours and reports one row for the whole
    page, which scores best on "fewest rows" and is nonsense. Concentration
    has no such maximum to run away to.

    The profile is scored twice, half a bin apart, and the better of the
    two is kept, so a row that happens to straddle a bin boundary does not
    look like two. Ties — which is what an unskewed page produces — go to
    the flattest slope.
    """
    if len(boxes) < 2:
        return 0.0
    heights = [box.height for box in boxes if box.height > 0]
    if not heights:
        return 0.0
    bin_size = max(median(heights) * _PROFILE_BIN_FRACTION, 0.25)
    centres = [((box.x0 + box.x1) / 2.0, box.y_centre) for box in boxes]

    best_slope = 0.0
    best_score = -1.0
    step = (2 * _SLOPE_LIMIT) / (_SLOPE_STEPS - 1)
    for index in range(_SLOPE_STEPS):
        slope = -_SLOPE_LIMIT + index * step
        adjusted = [(y - slope * x) / bin_size for x, y in centres]
        score = max(_profile_concentration(adjusted, 0.0), _profile_concentration(adjusted, 0.5))
        if score > best_score or (score == best_score and abs(slope) < abs(best_slope)):
            best_score, best_slope = score, slope
    return best_slope


def _profile_concentration(scaled: Sequence[float], offset: float) -> int:
    """Sum of squared bin counts for one histogram phase."""
    counts: dict[int, int] = {}
    for value in scaled:
        key = int(value + offset)
        counts[key] = counts.get(key, 0) + 1
    return sum(count * count for count in counts.values())


def _assign_rows(words: Sequence[tuple[str, BBox, float]]) -> tuple[list[int], float]:
    """
    Group words into visual rows. Returns a row index per word and the
    slope that was taken out.
    """
    if not words:
        return [], 0.0
    boxes = [box for _, box, _ in words]
    slope = estimate_baseline_slope(boxes)
    heights = [box.height for box in boxes if box.height > 0]
    tolerance = (median(heights) if heights else 1.0) * ROW_GAP_FRACTION

    adjusted = [box.y_centre - slope * ((box.x0 + box.x1) / 2.0) for box in boxes]
    order = sorted(range(len(words)), key=lambda i: (adjusted[i], boxes[i].x0))

    rows = [0] * len(words)
    current = 0
    rows[order[0]] = 0
    for previous, index in zip(order, order[1:], strict=False):
        if (adjusted[index] - adjusted[previous]) > tolerance:
            current += 1
        rows[index] = current
    return rows, slope


def _build_page(
    *,
    page_number: int,
    width: float,
    height: float,
    raw: Sequence[tuple[str, BBox, float]],
    source: TextSource,
    seconds: float,
    ocr_psm: int | None,
) -> PageWords:
    rows, slope = _assign_rows(raw)
    indexed = sorted(range(len(raw)), key=lambda i: (rows[i], raw[i][1].x0, raw[i][1].y0))

    words: list[Word] = []
    grouped: dict[int, list[int]] = {}
    for position, source_index in enumerate(indexed):
        text, box, confidence = raw[source_index]
        words.append(
            Word(text=text, bbox=box, confidence=confidence, row=rows[source_index], order=position)
        )
        grouped.setdefault(rows[source_index], []).append(position)

    return PageWords(
        page=page_number,
        width=width,
        height=height,
        words=tuple(words),
        source=source,
        seconds=seconds,
        ocr_psm=ocr_psm,
        baseline_slope=slope,
        _rows=tuple(tuple(grouped[row]) for row in sorted(grouped)),
    )


def build_page_words(
    *,
    page: int,
    width: float,
    height: float,
    items: Sequence[tuple[str, BBox, float]],
    source: TextSource,
    seconds: float = 0.0,
    ocr_psm: int | None = None,
) -> PageWords:
    """
    Assemble a `PageWords` from `(text, bbox, confidence)` triples.

    This is the seam a different reader plugs into. Everything downstream —
    row assignment, reading order, `locate` — is computed here from the
    boxes alone, so swapping Tesseract for docTR, a hosted OCR, or a
    fixture in a test means producing these triples and nothing else.
    """
    return _build_page(
        page_number=page,
        width=width,
        height=height,
        raw=list(items),
        source=source,
        seconds=seconds,
        ocr_psm=ocr_psm,
    )


def _text_layer_words(page: pymupdf.Page, page_number: int) -> list[tuple[str, BBox, float]]:
    raw: list[tuple[str, BBox, float]] = []
    for x0, y0, x1, y1, text, *_ in page.get_text("words"):
        if not text.strip():
            continue
        raw.append(
            (
                text,
                BBox(
                    page=page_number,
                    x0=float(min(x0, x1)),
                    y0=float(min(y0, y1)),
                    x1=float(max(x0, x1)),
                    y1=float(max(y0, y1)),
                ),
                1.0,
            )
        )
    return raw


def _has_text_layer(raw: Sequence[tuple[str, BBox, float]]) -> bool:
    readable = sum(1 for text, _, _ in raw if any(character.isalnum() for character in text))
    return readable >= MIN_TEXT_LAYER_WORDS


def _require_tesseract():  # noqa: ANN202 - the module object, typed nowhere useful
    try:
        import pytesseract
    except ImportError as error:  # pragma: no cover - environment-dependent
        raise OcrUnavailableError(
            "pytesseract is not installed; an image-only page cannot be read. "
            "Install it with the agent's requirements.txt."
        ) from error
    try:
        pytesseract.get_tesseract_version()
    except Exception as error:  # pragma: no cover - environment-dependent
        raise OcrUnavailableError(
            "the tesseract binary is not on PATH; an image-only page cannot be "
            "read. On macOS: brew install tesseract."
        ) from error
    return pytesseract


def tesseract_available() -> bool:
    """Whether OCR can run here. Used to skip OCR tests, never to hide a failure."""
    try:
        _require_tesseract()
    except OcrUnavailableError:
        return False
    return True


def _ocr_page(
    page: pymupdf.Page, page_number: int, settings: OcrSettings
) -> tuple[list[tuple[str, BBox, float]], int]:
    """
    Read one page with Tesseract, returning its words and the `--psm` used.

    Each candidate mode is run and scored by the confidence-weighted count
    of recognised characters — `sum(confidence * len(text))`. That rewards
    a mode that reads more of the page *and* is surer of what it read,
    penalises one that shatters the page into low-confidence fragments, and
    is computed entirely from Tesseract's own output, so nothing about the
    expected answer leaks into the choice.
    """
    from PIL import Image

    pytesseract = _require_tesseract()

    pixmap = page.get_pixmap(dpi=settings.dpi, colorspace=pymupdf.csRGB, alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    if settings.grayscale:
        image = image.convert("L")

    rect = page.rect
    scale_x = rect.width / pixmap.width
    scale_y = rect.height / pixmap.height

    best: list[tuple[str, BBox, float]] = []
    best_psm = settings.psm[0]
    best_score = -1.0

    for psm in settings.psm:
        data = pytesseract.image_to_data(
            image,
            lang=settings.lang,
            config=f"--psm {psm} --dpi {settings.dpi}",
            timeout=settings.timeout_seconds,
            output_type=pytesseract.Output.DICT,
        )
        words: list[tuple[str, BBox, float]] = []
        score = 0.0
        for index in range(len(data["text"])):
            text = data["text"][index]
            if not text.strip():
                continue
            confidence = float(data["conf"][index]) / 100.0
            if confidence < 0:
                continue
            score += confidence * len(text.strip())
            if confidence < settings.min_confidence:
                continue
            left, top = float(data["left"][index]), float(data["top"][index])
            box_width, box_height = float(data["width"][index]), float(data["height"][index])
            words.append(
                (
                    text,
                    BBox(
                        page=page_number,
                        x0=rect.x0 + left * scale_x,
                        y0=rect.y0 + top * scale_y,
                        x1=rect.x0 + (left + box_width) * scale_x,
                        y1=rect.y0 + (top + box_height) * scale_y,
                    ),
                    confidence,
                )
            )
        if score > best_score:
            best, best_psm, best_score = words, psm, score

    return best, best_psm


def extract_page_words(
    page: pymupdf.Page,
    page_number: int,
    *,
    policy: OcrPolicy = OcrPolicy.AUTO,
    ocr: OcrSettings | None = None,
) -> PageWords:
    """Read one already-open PyMuPDF page."""
    settings = ocr or OcrSettings()
    started = time.perf_counter()
    rect = page.rect

    raw: list[tuple[str, BBox, float]] = []
    source = TextSource.TEXT_LAYER
    psm: int | None = None

    if policy is not OcrPolicy.ALWAYS:
        raw = _text_layer_words(page, page_number)

    needs_ocr = policy is OcrPolicy.ALWAYS or (
        policy is OcrPolicy.AUTO and not _has_text_layer(raw)
    )
    if needs_ocr:
        raw, psm = _ocr_page(page, page_number, settings)
        source = TextSource.OCR

    return _build_page(
        page_number=page_number,
        width=float(rect.width),
        height=float(rect.height),
        raw=raw,
        source=source,
        seconds=time.perf_counter() - started,
        ocr_psm=psm,
    )


def extract_words(
    document: bytes | str | Path,
    *,
    policy: OcrPolicy = OcrPolicy.AUTO,
    ocr: OcrSettings | None = None,
    pages: Sequence[int] | None = None,
) -> DocumentWords:
    """
    Read a PDF's words.

    `document` is PDF bytes or a path. `pages` selects 1-based page numbers;
    the default is every page. The result's boxes are in the page space of
    *this* document — for a scan, that is the scan's own space, skew and
    all (see `geometry`).
    """
    if isinstance(document, bytes):
        handle = pymupdf.open(stream=document, filetype="pdf")
    else:
        handle = pymupdf.open(Path(document))

    try:
        wanted = (
            list(range(1, handle.page_count + 1))
            if pages is None
            else [number for number in pages if 1 <= number <= handle.page_count]
        )
        return DocumentWords(
            pages=tuple(
                extract_page_words(handle[number - 1], number, policy=policy, ocr=ocr)
                for number in wanted
            )
        )
    finally:
        handle.close()
