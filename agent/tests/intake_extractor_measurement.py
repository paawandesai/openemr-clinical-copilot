"""
The intake-extractor measured over the six committed doc-factory samples.

    cd agent && python -m tests.intake_extractor_measurement

No model call and no network: the vision step is the ground-truth stub
(`intake_extractor_support.ground_truth_vision`), so this measures the
worker *after* the model — stage 1 (text layer / Tesseract), the locate
step, the flag cross-check and the discrepancy check — against a perfect
transcriber. The scanned samples still go through real OCR, so their
located rate is the OCR path's, not the ground truth's.

Reported per sample and in total:

* **located rate** — located / fields the transcription gave a value
  (located + unlocated). `missing` and `illegible` fields are the document's
  own gaps and are not in the denominator; a blank flag column is `missing`.
* **located on the right ink** — of located fields on a text-layer page, the
  share whose bbox overlaps the ground-truth bbox at IoU >= 0.5. (Scanned
  pages are skewed images of the layout the ground truth is recorded on, so
  the comparison there needs the factory's transform; it is left to eval
  case W2-C2 rather than approximated here.)
* **cross-check agreement** — agree / comparable, with the denominator (D20).
* **discrepancy precision / recall** — against the discrepancies the
  factory declares through its `med_discrepancy` / `allergy_discrepancy`
  perturbations.
* **false-discrepancy rate on derived fields** — discrepancies caused by a
  field whose ground truth `rendering` is `derived`, over the number of
  derived fields. Must be 0.
* **fuzzy on exact-only** — located fields of a numeric / unit / date /
  flag class whose tier is `fuzzy`. Must be 0 (D24).
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from typing import Any

from app.extraction.geometry import BBox as GeometryBox
from app.models.w2.intake_form import Discrepancy, DiscrepancyKind
from app.workers.intake_extractor import attach_and_extract
from app.workers.intake_extractor.report import ExtractionReport
from tests.intake_extractor_support import (
    FAMILY_LABELS,
    SAMPLES,
    chart_for,
    doc_kind,
    ground_truth,
    ground_truth_vision,
    patient_uuid,
    pdf_bytes,
)

EXACT_ONLY = {"numeric", "unit", "date", "flag"}


@dataclass
class SampleMeasurement:
    sample: str
    report: ExtractionReport
    located: int = 0
    unlocated: int = 0
    missing: int = 0
    illegible: int = 0
    iou_checked: int = 0
    iou_ok: int = 0
    fuzzy_on_exact_only: int = 0
    expected_discrepancies: int = 0
    found_discrepancies: int = 0
    true_positives: int = 0
    derived_fields: int = 0
    derived_false_discrepancies: int = 0
    perturbation_notes: list[str] = field(default_factory=list)

    @property
    def located_rate(self) -> float | None:
        base = self.located + self.unlocated
        return self.located / base if base else None


def _gt_by_cite_path(sample: str) -> dict[str, dict[str, Any]]:
    """Ground truth keyed by the citation path the worker emits."""
    by_path: dict[str, dict[str, Any]] = {}
    for record in ground_truth(sample)["fields"]:
        path = record["field_path"]
        for key, label in FAMILY_LABELS.items():
            slug = label.lower().replace(" ", "_")
            if path == f"family_history.{key}":
                path = f"family_history.{slug}"
            elif path == f"family_history.{key}.relation":
                path = f"family_history.{slug}.relation"
        by_path[path] = record
    return by_path


def _expected(sample: str) -> list[dict[str, Any]]:
    expected = []
    for perturbation in ground_truth(sample).get("perturbations", []):
        kind, params = perturbation["kind"], perturbation.get("params", {})
        if kind == "med_discrepancy":
            mode = {"add": "added", "omit": "omitted", "dose_change": "changed"}[params["mode"]]
            expected.append({**perturbation, "category": "medication", "kind": mode})
        elif kind == "allergy_discrepancy":
            mode = {"add": "added", "omit": "omitted"}[params["mode"]]
            expected.append({**perturbation, "category": "allergy", "kind": mode})
    return expected


def _matches(found: Discrepancy, expected: dict[str, Any]) -> bool:
    if found.category.value != expected["category"] or found.kind.value != expected["kind"]:
        return False
    if found.kind is DiscrepancyKind.OMITTED:
        chart_value = expected.get("chart_value") or ""
        return bool(found.chart_value) and chart_value.startswith(found.chart_value or "")
    cited = found.document_citation.field_or_chunk_id if found.document_citation else None
    paths = expected.get("field_paths") or []
    if found.kind is DiscrepancyKind.CHANGED:
        return cited in paths
    # added: the discrepancy cites the row's name; the perturbation names the row.
    return cited is not None and any(cited.split(".")[0] == p.split(".")[0] for p in paths)


async def measure_sample(sample: str) -> SampleMeasurement:
    async def chart_reader(_uuid: str):  # noqa: ANN202 - a ChartReader
        return chart_for(sample)

    report = await attach_and_extract(
        patient_uuid(sample),
        pdf_bytes(sample),
        doc_kind(sample),
        vision=ground_truth_vision(sample),
        chart_reader=chart_reader,
        document_id=sample,
    )
    measured = SampleMeasurement(sample=sample, report=report)
    gt = _gt_by_cite_path(sample)
    text_layer = bool(report.has_text_layer)
    for entry in report.fields:
        outcome = entry.outcome
        if outcome == "located":
            measured.located += 1
            if entry.value_class in EXACT_ONLY and entry.match_tier == "fuzzy":
                measured.fuzzy_on_exact_only += 1
        elif outcome == "unlocated":
            measured.unlocated += 1
        elif outcome == "missing":
            measured.missing += 1
        else:
            measured.illegible += 1

    payload = report.output.payload
    if text_layer and payload is not None:
        boxes = _located_boxes(payload)
        for path, box in boxes.items():
            record = gt.get(path)
            if record is None or record.get("bbox") is None:
                continue
            truth = GeometryBox(record["page"], *record["bbox"])
            measured.iou_checked += 1
            if box.iou(truth) >= 0.5:
                measured.iou_ok += 1

    derived = [r for r in ground_truth(sample)["fields"] if r.get("rendering") == "derived"]
    measured.derived_fields = len(derived)
    expected = _expected(sample)
    measured.expected_discrepancies = len(expected)
    if report.discrepancies is not None:
        found = report.discrepancies.discrepancies
        measured.found_discrepancies = len(found)
        unmatched = list(expected)
        for discrepancy in found:
            hit = next((e for e in unmatched if _matches(discrepancy, e)), None)
            if hit is not None:
                unmatched.remove(hit)
                measured.true_positives += 1
            elif _caused_by_derived(discrepancy, derived):
                measured.derived_false_discrepancies += 1
    return measured


def _caused_by_derived(discrepancy: Discrepancy, derived: list[dict[str, Any]]) -> bool:
    cited = (
        discrepancy.document_citation.field_or_chunk_id if discrepancy.document_citation else None
    )
    derived_paths = {record["field_path"] for record in derived}
    derived_chart_values = {str(record.get("value")) for record in derived}
    if cited in derived_paths:
        return True
    return discrepancy.chart_value is not None and discrepancy.chart_value in derived_chart_values


def _located_boxes(payload: Any) -> dict[str, GeometryBox]:
    from app.models.w2.extraction import ExtractedField

    boxes: dict[str, GeometryBox] = {}

    def visit(value: Any) -> None:
        if isinstance(value, ExtractedField):
            if value.is_presentable and value.citation and value.citation.bbox:
                box = value.citation.bbox
                boxes[value.citation.field_or_chunk_id] = GeometryBox(
                    box.page, box.x0, box.y0, box.x1, box.y1
                )
            return
        if hasattr(type(value), "model_fields"):
            for name in type(value).model_fields:
                if name != "collection_date":
                    visit(getattr(value, name))
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return boxes


async def measure_all() -> list[SampleMeasurement]:
    return [await measure_sample(sample) for sample in SAMPLES]


def _pct(numerator: int, denominator: int) -> str:
    return (
        f"{numerator / denominator:.1%} ({numerator}/{denominator})" if denominator else "n/a (0/0)"
    )


def format_table(rows: list[SampleMeasurement]) -> str:
    header = (
        "| sample | status | located rate | right ink (IoU>=0.5, text layer) | "
        "mean conf | x-check agree / comparable | discrepancies found / expected / TP | "
        "derived false disc. | fuzzy on exact-only |"
    )
    lines = [header, "|" + "---|" * 9]
    for row in rows:
        check = row.report.flag_cross_check
        xcheck = (
            f"{check.agree}/{check.comparable} (not_reported {check.not_reported}, "
            f"not_computable {check.not_computable}, disagree {check.disagree})"
            if check
            else "—"
        )
        disc = (
            f"{row.found_discrepancies} / {row.expected_discrepancies} / {row.true_positives}"
            if row.report.discrepancies is not None
            else "—"
        )
        lines.append(
            f"| {row.sample} | {row.report.output.status.value} | "
            f"{_pct(row.located, row.located + row.unlocated)} | "
            f"{_pct(row.iou_ok, row.iou_checked) if row.iou_checked else 'n/a (scan)'} | "
            f"{row.report.mean_confidence} | {xcheck} | {disc} | "
            f"{row.derived_false_discrepancies}/{row.derived_fields} | {row.fuzzy_on_exact_only} |"
        )
    located = sum(r.located for r in rows)
    attempted = sum(r.located + r.unlocated for r in rows)
    agree = sum(r.report.flag_cross_check.agree for r in rows if r.report.flag_cross_check)
    comparable = sum(
        r.report.flag_cross_check.comparable for r in rows if r.report.flag_cross_check
    )
    found = sum(r.found_discrepancies for r in rows)
    expected = sum(r.expected_discrepancies for r in rows)
    tp = sum(r.true_positives for r in rows)
    derived = sum(r.derived_fields for r in rows)
    derived_false = sum(r.derived_false_discrepancies for r in rows)
    lines += [
        "",
        f"- end-to-end located rate: {_pct(located, attempted)}",
        f"- located on the right ink (text-layer pages): "
        f"{_pct(sum(r.iou_ok for r in rows), sum(r.iou_checked for r in rows))}",
        f"- cross-check agreement over comparable flags: {_pct(agree, comparable)}",
        f"- discrepancy precision: {_pct(tp, found)}; recall: {_pct(tp, expected)}",
        f"- false-discrepancy rate on derived fields: {_pct(derived_false, derived)}",
        f"- fuzzy tier on numeric/unit/date/flag fields: {sum(r.fuzzy_on_exact_only for r in rows)}",
    ]
    return "\n".join(lines)


def main() -> int:
    rows = asyncio.run(measure_all())
    print(format_table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
