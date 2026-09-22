"""
The doc-factory samples, converted into Week 2 contract instances.

D6 requires the gate to run on recorded fixtures rather than a live OpenEMR:
the six committed scenarios under `factory/output/samples/` and their ground
truth are the recording. Nothing here renders a PDF or reads a database, so
the deterministic tier stays offline.
"""

from __future__ import annotations

import functools
import json
from typing import Any

from app.models.w2 import (
    IntakeFormExtraction,
    LabPdfExtraction,
)
from app.models.w2.factory_mapping import GroundTruthConversion, convert_ground_truth
from evals.w2.gate.models import GateError
from evals.w2.gate.paths import FACTORY_SAMPLES_DIR

LAB_SCENARIOS = ("lab-clean-001", "lab-scanned-heavy-abnormal-002", "lab-multipage-003")
INTAKE_SCENARIOS = (
    "intake-clean-004",
    "intake-scanned-light-med-005",
    "intake-missing-allergy-006",
)
ALL_SCENARIOS = LAB_SCENARIOS + INTAKE_SCENARIOS


@functools.cache
def ground_truth(scenario_id: str) -> dict[str, Any]:
    path = FACTORY_SAMPLES_DIR / scenario_id / f"{scenario_id}.ground_truth.json"
    if not path.is_file():
        raise GateError(
            f"doc-factory sample {scenario_id} is missing ({path}). Regenerate with "
            "`cd agent && python -m evals.w2.factory generate evals/w2/factory/specs "
            "--out evals/w2/factory/output/samples`."
        )
    return json.loads(path.read_text(encoding="utf-8"))


@functools.cache
def conversion(scenario_id: str) -> GroundTruthConversion:
    """The scenario as a Week 2 schema instance, with its chart-truth notes."""
    return convert_ground_truth(ground_truth(scenario_id))


def lab(scenario_id: str) -> LabPdfExtraction:
    extraction = conversion(scenario_id).extraction
    if not isinstance(extraction, LabPdfExtraction):
        raise GateError(f"{scenario_id} is not a lab_pdf scenario")
    return extraction


def intake(scenario_id: str) -> IntakeFormExtraction:
    extraction = conversion(scenario_id).extraction
    if not isinstance(extraction, IntakeFormExtraction):
        raise GateError(f"{scenario_id} is not an intake_form scenario")
    return extraction


def field_records(scenario_id: str) -> list[dict[str, Any]]:
    """The raw ground-truth `fields` list, for checks that assert on it directly."""
    return list(ground_truth(scenario_id)["fields"])


def record_for(scenario_id: str, field_path: str) -> dict[str, Any]:
    for record in field_records(scenario_id):
        if record["field_path"] == field_path:
            return record
    raise GateError(f"{scenario_id} has no ground-truth field {field_path}")


def page_text(scenario_id: str) -> str:
    """
    Everything the document draws, as one string.

    Built from the ground truth's own `rendered_text` rather than by
    re-extracting the PDF: the point of W2-F6 is that a citation's quote
    matches what is on the page, and the factory's record of what it drew is
    the page for this purpose. Re-extraction is the doc-factory's own
    `validate` command, which already checks the two agree.
    """
    return "\n".join(
        str(record.get("rendered_text", ""))
        for record in field_records(scenario_id)
        if record.get("status") == "present"
    )
