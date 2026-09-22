"""
Measure `app.extraction` against the doc-factory's six committed samples.

This is the evidence behind every number in the work-order report, and the
thing `test_locate_measurement.py` asserts thresholds on. Run it directly
to print the tables:

    cd agent && python -m tests.locate_measurement

It is a library module, not a test module — pytest does not collect it —
and it reads the factory's `output/samples/` and nothing else. It writes
nothing, calls no model and opens no socket.

**Why the ground truth has to be moved before it can be compared.** The
factory records every bbox on the *clean* layout, in
`<scenario>.clean.pdf`. For a scanned scenario the document under test is
a skewed photograph of that layout, so a box found by OCR is in the scan's
page space and the two are not the same rectangle. The factory also
records the exact affine it used,
`transform.pages[i].pdf_points_to_image_px`, so `_ground_truth_box` maps
ground truth forward into the scan's space rather than asking the
extractor to know about a clean layout it was never handed.

**IoU measures two things at once, so the tables separate them.** The
factory's boxes come from font metrics and run the full line height,
ascender to descender. Tesseract's boxes are tight around ink, which for a
line of digits is about two thirds of that. Two boxes over exactly the
same value therefore score an IoU near 0.65 with no placement error at
all. `iou_x` — the same overlap measured on the x-axis alone — is free of
that convention difference, and `height ratio` states it outright, so the
tables can say whether a low IoU means "wrong place" or "same place,
tighter box".

**Decoys.** The factory marks only two fields across all six samples as
`absent` or `illegible`, which is too thin a denominator to call a
false-locate rate. So the harness also probes *synthetic negatives*: each
present value is perturbed by one character (a digit shifted, two letters
swapped), kept only if its perturbed form appears nowhere in the clean
document's text layer, and then looked for. Anything found is a false
locate. A one-character difference is the hardest possible case for the
OCR fuzzy tier, by construction, so the decoy rate is an upper bound on
how often locate invents a box — and the `fuzzy off` rows show exactly how
much of it the fuzzy tier is responsible for.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.extraction import (
    BBox,
    FuzzyMode,
    LocateStatus,
    MatchKind,
    OcrPolicy,
    PageIndex,
    TextSource,
    extract_words,
    locate,
    locate_in_row,
    squash,
    tesseract_available,
)

SAMPLES_DIR = Path(__file__).resolve().parents[1] / "evals/w2/factory/output/samples"

#: Ground-truth format this harness understands. The factory promises to
#: bump it if the shape changes, so failing loudly here is better than
#: quietly measuring the wrong fields.
SUPPORTED_SCHEMA = "doc-factory-groundtruth/0.2.0"

#: Below this, a located box is not really on the value — the work order
#: asks for these to be flagged.
IOU_FLOOR = 0.5

QUALITIES = ("clean", "scanned_light", "scanned_heavy")

#: Which sibling field labels a row, per field family. This is how a real
#: caller would ask: a lab result is looked up under its test name, a dose
#: under its drug, a reaction under its allergen.
_ROW_LABELS = {
    ".value": ".name",
    ".unit": ".name",
    ".flag": ".name",
    ".reference_range": ".name",
    ".dose": ".name",
    ".frequency": ".name",
    ".reaction": ".allergen",
}


def classify(field_path: str) -> str:
    """The field type a located rate is broken down by."""
    if field_path.startswith("results."):
        return "lab_" + field_path.rsplit(".", 1)[-1]
    if field_path.startswith(("lab.", "order.")):
        return "report_header"
    if field_path.startswith("patient."):
        return "patient_identity"
    if field_path.startswith("medications["):
        return "medication"
    if field_path.startswith("allergies"):
        return "allergy"
    if field_path.startswith("family_history."):
        return "family_history"
    return "form_field"


def row_label_path(field_path: str) -> str | None:
    """The field path of the label that heads this field's row, if any."""
    for suffix, label_suffix in _ROW_LABELS.items():
        if field_path.endswith(suffix):
            stem = field_path[: -len(suffix)]
            return stem + label_suffix if stem else None
    return None


@dataclass(frozen=True)
class Sample:
    scenario_id: str
    doc_type: str
    quality: str
    pdf: Path
    clean_pdf: Path
    ground_truth: dict

    @property
    def is_scan(self) -> bool:
        return self.pdf != self.clean_pdf


def discover_samples(directory: Path = SAMPLES_DIR) -> list[Sample]:
    """Every committed sample, in scenario-id order."""
    samples: list[Sample] = []
    for path in sorted(directory.glob("*/*.ground_truth.json")):
        truth = json.loads(path.read_text())
        if truth.get("schema_version") != SUPPORTED_SCHEMA:
            raise ValueError(
                f"{path.name} is {truth.get('schema_version')!r}; this harness "
                f"reads {SUPPORTED_SCHEMA!r}. Update it deliberately; do not "
                f"measure a format you have not read."
            )
        scenario = truth["scenario"]
        clean = truth.get("clean_reference") or truth["document"]
        samples.append(
            Sample(
                scenario_id=scenario["scenario_id"],
                doc_type=scenario["doc_type"],
                quality=scenario["quality"],
                pdf=path.parent / truth["document"]["path"],
                clean_pdf=path.parent / clean["path"],
                ground_truth=truth,
            )
        )
    return samples


def _page_transforms(truth: dict) -> dict[int, tuple[float, ...]]:
    """
    Per page, the affine taking a clean-layout point to a point in the
    document under test. Empty for an undegraded scenario.
    """
    transform = truth.get("transform")
    if not transform:
        return {}
    mapping: dict[int, tuple[float, ...]] = {}
    for page in transform["pages"]:
        a, c, e, b, d, f = page["pdf_points_to_image_px"]
        width_px, height_px = page["image_size_px"]
        width_pt, height_pt = page["page_size_pt"]
        sx, sy = width_pt / width_px, height_pt / height_px
        mapping[page["page"]] = (a * sx, c * sx, e * sx, b * sy, d * sy, f * sy)
    return mapping


def _ground_truth_box(truth_field: dict, transforms: dict[int, tuple[float, ...]]) -> BBox | None:
    """The field's bbox, in the page space of the document under test."""
    raw = truth_field.get("bbox")
    page = truth_field.get("page")
    if raw is None or page is None:
        return None
    x0, y0, x1, y1 = raw
    affine = transforms.get(page)
    if affine is None:
        return BBox(page=page, x0=x0, y0=y0, x1=x1, y1=y1)
    a, c, e, b, d, f = affine
    corners = [
        (a * x + c * y + e, b * x + d * y + f) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))
    ]
    xs = [point[0] for point in corners]
    ys = [point[1] for point in corners]
    return BBox(page=page, x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys))


def _iou_x(left: BBox, right: BBox) -> float | None:
    """Overlap on the x-axis alone, free of the box-height convention."""
    overlap = min(left.x1, right.x1) - max(left.x0, right.x0)
    union = max(left.x1, right.x1) - min(left.x0, right.x0)
    if union <= 0:
        return None
    return max(0.0, overlap) / union


def _decoy(text: str) -> str | None:
    """
    A one-character perturbation of a drawn value: a value that is *not* on
    the page. Returns None when the text is too short to perturb.
    """
    characters = list(text)
    for index in range(len(characters) - 1, -1, -1):
        if characters[index].isdigit():
            characters[index] = str((int(characters[index]) + 5) % 10)
            return "".join(characters)
    letters = [index for index, character in enumerate(characters) if character.isalpha()]
    if len(letters) < 4:
        return None
    first, second = letters[1], letters[2]
    if characters[first].casefold() == characters[second].casefold():
        return None
    characters[first], characters[second] = characters[second], characters[first]
    return "".join(characters)


@dataclass
class Outcome:
    """One locate call, and how it went."""

    scenario_id: str
    quality: str
    doc_type: str
    field_path: str
    field_type: str
    #: The ground truth's `kind`: "text" or "checkbox". A checkbox's
    #: ground-truth box is the empty square it is drawn in, while a located
    #: box is the mark inside it, so checkbox IoU is not comparable with
    #: text IoU and the thresholds keep them apart.
    field_kind: str
    page: int
    probe: str
    status: LocateStatus
    kind: MatchKind | None
    iou: float | None = None
    iou_x: float | None = None
    height_ratio: float | None = None


@dataclass
class PageTiming:
    scenario_id: str
    page: int
    source: TextSource
    seconds: float
    words: int
    psm: int | None
    skew_degrees: float


@dataclass
class Measurement:
    outcomes: list[Outcome] = field(default_factory=list)
    timings: list[PageTiming] = field(default_factory=list)
    #: Samples whose OCR pages could not be read here (no Tesseract).
    skipped_scans: list[str] = field(default_factory=list)

    def extend(self, other: Measurement) -> None:
        self.outcomes.extend(other.outcomes)
        self.timings.extend(other.timings)
        self.skipped_scans.extend(other.skipped_scans)

    def by_probe(self, probe: str, quality: str | None = None) -> list[Outcome]:
        return [
            outcome
            for outcome in self.outcomes
            if outcome.probe == probe and (quality is None or outcome.quality == quality)
        ]


def _clean_stream(sample: Sample) -> str:
    """
    Every word of the undegraded document, squashed — the arbiter of
    whether a decoy really is absent from the page.
    """
    document = extract_words(sample.clean_pdf, policy=OcrPolicy.NEVER)
    return "".join(squash(word.text) for page in document.pages for word in page.words)


def measure_sample(sample: Sample, *, allow_ocr: bool = True) -> Measurement:
    """Run every probe over one sample."""
    measurement = Measurement()
    truth = sample.ground_truth
    transforms = _page_transforms(truth)

    policy = OcrPolicy.AUTO if allow_ocr else OcrPolicy.NEVER
    document = extract_words(sample.pdf, policy=policy)
    indexes = {page.page: PageIndex.for_page(page) for page in document.pages}

    for page in document.pages:
        measurement.timings.append(
            PageTiming(
                scenario_id=sample.scenario_id,
                page=page.page,
                source=page.source,
                seconds=page.seconds,
                words=len(page.words),
                psm=page.ocr_psm,
                skew_degrees=math.degrees(math.atan(page.baseline_slope)),
            )
        )
    if sample.is_scan and any(not page.words for page in document.pages):
        measurement.skipped_scans.append(sample.scenario_id)
        return measurement

    fields = truth["fields"]
    by_path = {entry["field_path"]: entry for entry in fields}
    clean_stream = _clean_stream(sample)
    scanned = sample.is_scan

    def record(entry, probe, status, kind, box) -> None:
        expected = _ground_truth_box(entry, transforms)
        iou = iou_x = height_ratio = None
        if box is not None and expected is not None:
            iou = box.iou(expected)
            iou_x = _iou_x(box, expected)
            if expected.height > 0:
                height_ratio = box.height / expected.height
        measurement.outcomes.append(
            Outcome(
                scenario_id=sample.scenario_id,
                quality=sample.quality,
                doc_type=sample.doc_type,
                field_path=entry["field_path"],
                field_type=classify(entry["field_path"]),
                field_kind=entry.get("kind", "text"),
                page=entry.get("page") or 0,
                probe=probe,
                status=status,
                kind=kind,
                iou=iou,
                iou_x=iou_x,
                height_ratio=height_ratio,
            )
        )

    for entry in fields:
        page_number = entry.get("page")
        index = indexes.get(page_number) if page_number else None
        present = entry["status"] == "present" and index is not None
        drawn = entry["rendered_text"].strip()

        # 1. What the page draws. `rendered_text` is the whole of it: for a
        #    `rendering: derived` field the chart's wording is not on the
        #    page, and looking for it would measure the wrong thing.
        if present and drawn:
            result = locate(entry["rendered_text"], index)
            record(entry, "rendered", result.status, result.kind, result.bbox)
            if scanned:
                strict = locate(entry["rendered_text"], index, fuzzy=FuzzyMode.NEVER)
                record(entry, "rendered_no_fuzzy", strict.status, strict.kind, strict.bbox)

        # 2. The number as a model would emit it: the ground truth holds
        #    89.0 where the page draws "89".
        value = entry["value"]
        if present and isinstance(value, (int, float)) and not isinstance(value, bool):
            result = locate(str(value), index)
            record(entry, "numeric_value", result.status, result.kind, result.bbox)

        # 3. Row-anchored: the value looked up under the label beside it.
        label_path = row_label_path(entry["field_path"]) if present and drawn else None
        label = by_path.get(label_path) if label_path else None
        if (
            label is not None
            and label["status"] == "present"
            and label.get("page") == page_number
            and label["rendered_text"].strip()
        ):
            row = locate_in_row(label["rendered_text"], entry["rendered_text"], index)
            record(entry, "row_anchored", row.status, row.value.kind, row.bbox)

        # 4. The fields the factory says the document fails to convey.
        #    Anything found anywhere in the document is a false locate.
        if entry["status"] in {"absent", "illegible"} and entry["value"] is not None:
            for candidate_page in indexes.values():
                result = locate(str(entry["value"]), candidate_page)
                record(entry, "missing_field", result.status, result.kind, None)

        # 5. Synthetic negatives.
        if present and drawn:
            decoy = _decoy(entry["rendered_text"])
            if decoy is not None and squash(decoy) and squash(decoy) not in clean_stream:
                result = locate(decoy, index)
                record(entry, "decoy", result.status, result.kind, None)
                if scanned:
                    strict = locate(decoy, index, fuzzy=FuzzyMode.NEVER)
                    record(entry, "decoy_no_fuzzy", strict.status, strict.kind, None)

    return measurement


def measure_all(samples: Sequence[Sample] | None = None, *, allow_ocr: bool = True) -> Measurement:
    total = Measurement()
    for sample in samples if samples is not None else discover_samples():
        total.extend(measure_sample(sample, allow_ocr=allow_ocr))
    return total


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------


def _count(outcomes: Iterable[Outcome], status: LocateStatus) -> tuple[int, int]:
    outcomes = list(outcomes)
    return sum(1 for outcome in outcomes if outcome.status is status), len(outcomes)


def rate(outcomes: Iterable[Outcome], status: LocateStatus = LocateStatus.LOCATED) -> float:
    """Fraction of `outcomes` with `status`, 0.0 when there are none."""
    hit, total = _count(outcomes, status)
    return hit / total if total else 0.0


def _percent(hit: int, total: int) -> str:
    return "     -" if not total else f"{hit / total * 100:5.1f}%"


def _table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    widths = [
        max([len(str(headers[column]))] + [len(str(row[column])) for row in rows])
        for column in range(len(headers))
    ]
    head = "  ".join(str(headers[i]).ljust(widths[i]) for i in range(len(headers)))
    rule = "  ".join("-" * widths[i] for i in range(len(headers)))
    body = ["  ".join(str(row[i]).ljust(widths[i]) for i in range(len(headers))) for row in rows]
    return "\n".join([head, rule, *body])


def _status_row(label, outcomes: Sequence[Outcome]) -> tuple[object, ...]:
    located, total = _count(outcomes, LocateStatus.LOCATED)
    ambiguous, _ = _count(outcomes, LocateStatus.AMBIGUOUS)
    missed, _ = _count(outcomes, LocateStatus.NOT_LOCATED)
    return (
        *(label if isinstance(label, tuple) else (label,)),
        total,
        _percent(located, total),
        _percent(ambiguous, total),
        _percent(missed, total),
    )


def render_tables(measurement: Measurement) -> str:
    out: list[str] = []

    out.append("A. Pages read")
    out.append(
        _table(
            ("scenario", "page", "reader", "psm", "words", "skew deg", "seconds"),
            [
                (
                    timing.scenario_id,
                    timing.page,
                    timing.source.value,
                    "-" if timing.psm is None else timing.psm,
                    timing.words,
                    f"{timing.skew_degrees:+.2f}",
                    f"{timing.seconds:.3f}",
                )
                for timing in measurement.timings
            ],
        )
    )

    text_layer = [t.seconds for t in measurement.timings if t.source is TextSource.TEXT_LAYER]
    ocr = [t.seconds for t in measurement.timings if t.source is TextSource.OCR]
    out.append("\nB. Time per page, text layer vs OCR")
    out.append(
        _table(
            ("reader", "pages", "mean s", "median s", "max s"),
            [
                (
                    name,
                    len(values),
                    f"{statistics.fmean(values):.3f}" if values else "-",
                    f"{statistics.median(values):.3f}" if values else "-",
                    f"{max(values):.3f}" if values else "-",
                )
                for name, values in (("text_layer", text_layer), ("ocr", ocr))
            ],
        )
    )

    rendered = measurement.by_probe("rendered")
    out.append("\nC. Located rate by quality  (probe: the text the page draws, page-wide search)")
    out.append(
        _table(
            ("quality", "fields", "located", "ambiguous", "not_located"),
            [
                _status_row(quality, [o for o in rendered if o.quality == quality])
                for quality in QUALITIES
            ],
        )
    )

    out.append("\nD. Located rate by field type x quality  (page-wide search)")
    types = sorted({outcome.field_type for outcome in rendered})
    rows = []
    for field_type in types:
        row: list[object] = [field_type]
        for quality in QUALITIES:
            subset = [o for o in rendered if o.field_type == field_type and o.quality == quality]
            located, total = _count(subset, LocateStatus.LOCATED)
            row.append(f"{_percent(located, total)} ({total})")
        rows.append(tuple(row))
    out.append(_table(("field type", *QUALITIES), rows))

    out.append(
        "\nE. bbox agreement with ground truth, located TEXT fields only."
        "\n   Checkboxes are excluded: their ground-truth box is the square and the "
        "located box is\n   the mark drawn inside it, so the two are not the same "
        "rectangle by construction."
    )
    rows = []
    for quality in QUALITIES:
        subset = [
            o
            for o in rendered
            if o.quality == quality and o.iou is not None and o.field_kind == "text"
        ]
        if not subset:
            continue
        ious = sorted(o.iou for o in subset)
        xs = sorted(o.iou_x for o in subset if o.iou_x is not None)
        heights = sorted(o.height_ratio for o in subset if o.height_ratio is not None)
        below = sum(1 for value in ious if value < IOU_FLOOR)
        rows.append(
            (
                quality,
                len(ious),
                f"{statistics.median(ious):.3f}",
                f"{statistics.fmean(ious):.3f}",
                f"{ious[max(0, int(len(ious) * 0.1) - 1)]:.3f}",
                f"{ious[0]:.3f}",
                f"{below} ({_percent(below, len(ious)).strip()})",
                f"{statistics.median(xs):.3f}" if xs else "-",
                f"{statistics.median(heights):.2f}" if heights else "-",
            )
        )
    out.append(
        _table(
            (
                "quality",
                "n",
                "IoU med",
                "IoU mean",
                "IoU p10",
                "IoU min",
                f"IoU < {IOU_FLOOR}",
                "IoU_x med",
                "height ratio",
            ),
            rows,
        )
    )

    out.append("\nF. False locates")
    rows = []
    for probe, label in (
        ("missing_field", "ground truth: absent/illegible"),
        ("decoy", "synthetic negative, 1 char off"),
        ("decoy_no_fuzzy", "same, fuzzy tier off"),
    ):
        for quality in QUALITIES:
            subset = measurement.by_probe(probe, quality)
            located, total = _count(subset, LocateStatus.LOCATED)
            ambiguous, _ = _count(subset, LocateStatus.AMBIGUOUS)
            if total:
                rows.append((label, quality, total, located, _percent(located, total), ambiguous))
    out.append(
        _table(("probe", "quality", "probes", "located", "false-locate rate", "ambiguous"), rows)
    )

    out.append(
        "\nG. Row anchoring vs page-wide search, on the same fields."
        "\n   'on target' is the share of the LOCATED boxes whose IoU clears "
        f"{IOU_FLOOR} — i.e. how\n   often a box that was produced is over the "
        "right ink rather than a lookalike elsewhere."
    )
    row_anchored = measurement.by_probe("row_anchored")
    anchored = {(o.scenario_id, o.field_path) for o in row_anchored}
    page_wide = [o for o in rendered if (o.scenario_id, o.field_path) in anchored]
    rows = []
    for name, subset in (("page-wide locate()", page_wide), ("locate_in_row()", row_anchored)):
        for quality in QUALITIES:
            scoped = [o for o in subset if o.quality == quality]
            if not scoped:
                continue
            hits = [o for o in scoped if o.status is LocateStatus.LOCATED and o.iou is not None]
            on_target = sum(1 for o in hits if o.iou >= IOU_FLOOR)
            rows.append((*_status_row((name, quality), scoped), _percent(on_target, len(hits))))
    out.append(
        _table(
            ("search", "quality", "fields", "located", "ambiguous", "not_located", "on target"),
            rows,
        )
    )

    out.append(
        "\nH. Row anchoring by field type, CLEAN pages only (the search design, without OCR noise)"
    )
    rows = []
    clean_anchored = [o for o in row_anchored if o.quality == "clean"]
    clean_wide = [o for o in page_wide if o.quality == "clean"]
    for field_type in sorted({o.field_type for o in clean_anchored}):
        wide = [o for o in clean_wide if o.field_type == field_type]
        anchored_subset = [o for o in clean_anchored if o.field_type == field_type]
        located_wide, total_wide = _count(wide, LocateStatus.LOCATED)
        located_row, total_row = _count(anchored_subset, LocateStatus.LOCATED)
        rows.append(
            (
                field_type,
                total_row,
                _percent(located_wide, total_wide),
                _percent(located_row, total_row),
            )
        )
    out.append(_table(("field type", "fields", "page-wide", "row-anchored"), rows))

    out.append("\nI. Numeric normalisation: ground truth holds 89.0, the page draws '89'")
    numeric = measurement.by_probe("numeric_value")
    rows = []
    for quality in QUALITIES:
        subset = [o for o in numeric if o.quality == quality]
        if not subset:
            continue
        located, total = _count(subset, LocateStatus.LOCATED)
        ambiguous, _ = _count(subset, LocateStatus.AMBIGUOUS)
        kinds = sorted({o.kind.value for o in subset if o.kind is not None})
        rows.append(
            (
                quality,
                total,
                _percent(located, total),
                _percent(ambiguous, total),
                ", ".join(kinds) or "-",
            )
        )
    out.append(_table(("quality", "fields", "located", "ambiguous", "tiers used"), rows))

    strict = measurement.by_probe("rendered_no_fuzzy")
    if strict:
        out.append("\nJ. What the OCR fuzzy tier buys and costs (scanned samples only)")
        rows = []
        for quality in QUALITIES:
            on = [o for o in rendered if o.quality == quality]
            off = [o for o in strict if o.quality == quality]
            decoy_on = measurement.by_probe("decoy", quality)
            decoy_off = measurement.by_probe("decoy_no_fuzzy", quality)
            if not off:
                continue
            rows.append(
                (
                    quality,
                    len(off),
                    _percent(_count(off, LocateStatus.LOCATED)[0], len(off)),
                    _percent(_count(on, LocateStatus.LOCATED)[0], len(on)),
                    _percent(_count(decoy_off, LocateStatus.LOCATED)[0], len(decoy_off)),
                    _percent(_count(decoy_on, LocateStatus.LOCATED)[0], len(decoy_on)),
                )
            )
        out.append(
            _table(
                (
                    "quality",
                    "fields",
                    "located, fuzzy off",
                    "located, fuzzy on",
                    "false, fuzzy off",
                    "false, fuzzy on",
                ),
                rows,
            )
        )

    if measurement.skipped_scans:
        out.append(f"\nSKIPPED (no Tesseract here): {', '.join(measurement.skipped_scans)}")
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    allow_ocr = "--no-ocr" not in argv
    if allow_ocr and not tesseract_available():
        print("tesseract is not installed; scanned samples will be reported as skipped.\n")
        allow_ocr = False
    samples = discover_samples()
    if not samples:
        print(f"no samples under {SAMPLES_DIR}", file=sys.stderr)
        return 1
    print(render_tables(measure_all(samples, allow_ocr=allow_ocr)))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
