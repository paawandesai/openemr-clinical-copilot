"""
The generate pipeline: spec -> document(s) + ground truth on disk.

    chart (cache or local OpenEMR)
      -> content plan (perturbations applied, each one recorded)
      -> render (clean PDF, one FieldRecord per drawn value)
      -> optional scan degradation (image-only PDF, transform recorded)
      -> <scenario_id>.pdf / .clean.pdf / .ground_truth.json

Output layout, under ``--out`` (default ``evals/factory/output``)::

    <scenario_id>/
        <scenario_id>.pdf                 the document under test
        <scenario_id>.clean.pdf           only for scanned qualities; the
                                          layout every bbox refers to
        <scenario_id>.ground_truth.json

Nothing printed by this module names a patient: the CLI reports scenario ids
and file paths only.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from evals.factory import render_intake, render_lab
from evals.factory.canvas import DocumentCanvas
from evals.factory.chart import ChartRecord, load_chart
from evals.factory.content import plan_intake, plan_lab
from evals.factory.degrade import scan
from evals.factory.groundtruth import (
    FieldOrigin,
    FieldStatus,
    GroundTruth,
    PerturbationRecord,
)
from evals.factory.spec import DocType, PerturbationKind, Quality, ScenarioSpec

PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_ROOT = PACKAGE_ROOT / "output"
DEFAULT_CACHE_DIR = PACKAGE_ROOT / "cache"
DEFAULT_SPEC_DIR = PACKAGE_ROOT / "specs"

#: Scanned pages are drawn on tinted stock so the rasteriser has paper, not
#: pure white, to degrade. The clean variant stays white.
_SCAN_PAPER = {
    Quality.SCANNED_LIGHT: (0.988, 0.984, 0.969),
    Quality.SCANNED_HEAVY: (0.953, 0.937, 0.898),
}


@dataclass
class GenerateResult:
    spec: ScenarioSpec
    output_dir: Path
    document_path: Path
    clean_path: Path
    ground_truth_path: Path
    chart_origin: str
    page_count: int
    field_count: int


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _has_text_layer(pdf: bytes) -> bool:
    document = pymupdf.open(stream=pdf, filetype="pdf")
    return any(document[i].get_text("text").strip() for i in range(document.page_count))


def _perturbation_records_for_fields(canvas: DocumentCanvas) -> list[PerturbationRecord]:
    """
    Turn the field-scoped perturbations back into perturbation records.

    ``missing_field`` and ``illegible_field`` are applied down inside the
    renderers, so this reads them off the field records the renderers produced
    rather than trying to predict them up front.
    """
    records: list[PerturbationRecord] = []
    for record in canvas.fields:
        if record.origin is not FieldOrigin.PERTURBED:
            continue
        if record.status is FieldStatus.ABSENT:
            records.append(
                PerturbationRecord(
                    kind=PerturbationKind.MISSING_FIELD.value,
                    params={"field": record.field_path},
                    field_paths=[record.field_path],
                    chart_value=None if record.value is None else str(record.value),
                    document_value=None,
                    expected_discrepancy=(
                        f"The document does not carry {record.field_path}; the chart value is "
                        f"{record.value!r}. An extractor must report it as absent, not infer it."
                    ),
                )
            )
        elif record.status is FieldStatus.ILLEGIBLE:
            records.append(
                PerturbationRecord(
                    kind=PerturbationKind.ILLEGIBLE_FIELD.value,
                    params={"field": record.field_path},
                    field_paths=[record.field_path],
                    chart_value=None if record.value is None else str(record.value),
                    document_value=None,
                    expected_discrepancy=(
                        f"{record.field_path} is present as ink but unreadable; the chart value is "
                        f"{record.value!r}. An extractor must report it as illegible, not guess."
                    ),
                )
            )
    return records


def generate(
    spec: ScenarioSpec,
    *,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    refresh: bool = False,
    env_path: Path | None = None,
) -> GenerateResult:
    chart, chart_origin = load_chart(spec.patient_ref, cache_dir, refresh=refresh, env_path=env_path)
    paper = _SCAN_PAPER.get(spec.quality)

    if spec.doc_type is DocType.LAB_PDF:
        content = plan_lab(spec, chart)
        canvas = render_lab.render(content, seed=spec.seed, paper=paper)
        title, subject = f"Laboratory Report - {spec.scenario_id}", "synthetic laboratory report"
    else:
        content = plan_intake(spec, chart)
        canvas = render_intake.render(content, seed=spec.seed, paper=paper)
        title, subject = f"Patient Intake Form - {spec.scenario_id}", "synthetic patient intake form"

    content.plan.assert_all_applied(spec.scenario_id, (f.field_path for f in canvas.fields))

    clean_pdf = canvas.finish(title=title, subject=subject)
    pages = canvas.page_sizes()
    perturbations = list(content.perturbations) + _perturbation_records_for_fields(canvas)

    output_dir = output_root / spec.scenario_id
    output_dir.mkdir(parents=True, exist_ok=True)
    document_path = output_dir / f"{spec.scenario_id}.pdf"

    transform: dict | None = None
    if spec.quality.is_scanned:
        clean_path = output_dir / f"{spec.scenario_id}.clean.pdf"
        clean_path.write_bytes(clean_pdf)
        document_pdf, transform = scan(clean_pdf, spec.quality, spec.seed, spec.scenario_id)
        document_path.write_bytes(document_pdf)
    else:
        clean_path = document_path
        document_pdf = clean_pdf
        document_path.write_bytes(document_pdf)

    ground_truth = GroundTruth(
        scenario=spec.as_dict(),
        chart_source={
            "system": "local-openemr",
            "origin": chart_origin,
            "patient_ref": spec.patient_ref,
            "medications_on_chart": len(chart.medications),
            "allergies_on_chart": len(chart.allergies),
            "conditions_on_chart": len(chart.conditions),
        },
        document={
            "path": document_path.name,
            "sha256": _sha256(document_pdf),
            "bytes": len(document_pdf),
            "page_count": len(pages),
            "has_text_layer": _has_text_layer(document_pdf),
        },
        clean_reference={
            "path": clean_path.name,
            "sha256": _sha256(clean_pdf),
            "bytes": len(clean_pdf),
            "page_count": len(pages),
            "has_text_layer": True,
        },
        transform=transform,
        pages=pages,
        fields=canvas.fields,
        perturbations=perturbations,
    )

    ground_truth_path = output_dir / f"{spec.scenario_id}.ground_truth.json"
    ground_truth_path.write_text(
        json.dumps(ground_truth.to_json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    return GenerateResult(
        spec=spec,
        output_dir=output_dir,
        document_path=document_path,
        clean_path=clean_path,
        ground_truth_path=ground_truth_path,
        chart_origin=chart_origin,
        page_count=len(pages),
        field_count=len(canvas.fields),
    )


def ground_truth_for(spec: ScenarioSpec, chart: ChartRecord) -> dict:
    """
    Render in memory and return only the ground truth.

    Used by the determinism test, which needs to compare ground truth across
    runs without touching the filesystem.
    """
    paper = _SCAN_PAPER.get(spec.quality)
    if spec.doc_type is DocType.LAB_PDF:
        content = plan_lab(spec, chart)
        canvas = render_lab.render(content, seed=spec.seed, paper=paper)
    else:
        content = plan_intake(spec, chart)
        canvas = render_intake.render(content, seed=spec.seed, paper=paper)
    content.plan.assert_all_applied(spec.scenario_id, (f.field_path for f in canvas.fields))
    return {
        "scenario": spec.as_dict(),
        "pages": canvas.page_sizes(),
        "fields": [f.to_json() for f in canvas.fields],
        "perturbations": [
            p.to_json() for p in list(content.perturbations) + _perturbation_records_for_fields(canvas)
        ],
    }
