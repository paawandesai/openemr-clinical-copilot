"""
Interim ground-truth format.

NOT the final Phase 2 schema. This is a plain dataclass + JSON representation
the renderers can emit today; ``schema_version`` exists so a later adapter can
convert it to the Pydantic ``lab_pdf`` / ``intake_form`` models without
guessing which shape a file on disk is in.

Coordinate convention (recorded in every file under ``coordinates``):

* Every ``bbox`` is ``[x0, y0, x1, y1]`` in **PDF points on the original clean
  layout** — i.e. on ``<scenario_id>.clean.pdf``, never on the degraded scan.
* Origin is the **top-left** of the page with **y increasing downward**, which
  is PyMuPDF's page space. Multiply y by -1 and add the page height to get
  PDF-native bottom-left coordinates.
* For a scanned variant, ``transform.pdf_points_to_image_px`` maps a point on
  the clean layout to a pixel on the rendered page image.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

GROUND_TRUTH_SCHEMA_VERSION = "doc-factory-groundtruth/0.1.0"


class FieldStatus(str, Enum):
    #: Drawn, legible, and extractable from the clean PDF's text layer.
    PRESENT = "present"
    #: Deliberately not drawn. ``value`` still carries the chart truth.
    ABSENT = "absent"
    #: Drawn as ink but not as text — a smudge/scribble. ``value`` carries the
    #: chart truth, ``rendered_text`` is empty, and the bbox is the ink region.
    ILLEGIBLE = "illegible"


class FieldOrigin(str, Enum):
    #: Copied verbatim from the chart.
    CHART = "chart"
    #: Derived from the chart (e.g. strength parsed out of a product name).
    CHART_DERIVED = "chart_derived"
    #: Invented by the factory; the chart has no counterpart to disagree with.
    SYNTHETIC = "synthetic"
    #: Deliberately made to disagree with the chart by a perturbation.
    PERTURBED = "perturbed"


class FieldKind(str, Enum):
    TEXT = "text"
    CHECKBOX = "checkbox"


@dataclass
class FieldRecord:
    field_path: str
    status: FieldStatus
    kind: FieldKind
    origin: FieldOrigin
    #: The semantic value. For a present text field this is the same thing the
    #: reader should end up with; for absent/illegible it is the chart truth
    #: the document fails to convey.
    value: str | float | int | bool | None
    unit: str | None = None
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    #: Exactly the characters drawn on the page ("" when nothing was drawn).
    rendered_text: str = ""
    note: str | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "field_path": self.field_path,
            "status": self.status.value,
            "kind": self.kind.value,
            "origin": self.origin.value,
            "value": self.value,
            "unit": self.unit,
            "page": self.page,
            "bbox": [round(v, 3) for v in self.bbox] if self.bbox is not None else None,
            "rendered_text": self.rendered_text,
        }
        if self.note is not None:
            out["note"] = self.note
        return out


@dataclass
class PerturbationRecord:
    kind: str
    params: dict[str, Any]
    #: Ground-truth field paths this perturbation touched.
    field_paths: list[str] = field(default_factory=list)
    #: What the chart says, and what the document says instead. Either may be
    #: None (an omission has no document value; an addition has no chart value).
    chart_value: str | None = None
    document_value: str | None = None
    #: One sentence a human (or a judge) can check an extractor's output against.
    expected_discrepancy: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "params": self.params,
            "field_paths": list(self.field_paths),
            "chart_value": self.chart_value,
            "document_value": self.document_value,
            "expected_discrepancy": self.expected_discrepancy,
        }


COORDINATE_CONVENTION = {
    "space": "clean_pdf",
    "units": "pt",
    "origin": "top-left",
    "y_axis": "down",
    "note": (
        "bbox is [x0, y0, x1, y1] on the original clean layout "
        "(<scenario_id>.clean.pdf), in PyMuPDF page space. For scanned "
        "variants map through transform.pdf_points_to_image_px."
    ),
}


@dataclass
class GroundTruth:
    scenario: dict[str, Any]
    document: dict[str, Any]
    pages: list[dict[str, Any]]
    fields: list[FieldRecord]
    perturbations: list[PerturbationRecord]
    chart_source: dict[str, Any]
    clean_reference: dict[str, Any] | None = None
    transform: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": GROUND_TRUTH_SCHEMA_VERSION,
            "scenario": self.scenario,
            "chart_source": self.chart_source,
            "coordinates": COORDINATE_CONVENTION,
            "document": self.document,
            "clean_reference": self.clean_reference,
            "transform": self.transform,
            "pages": self.pages,
            "perturbations": [p.to_json() for p in self.perturbations],
            "fields": [f.to_json() for f in self.fields],
        }
