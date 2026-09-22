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


# --------------------------------------------------------------------------
# Real retrieval output, offline.
# --------------------------------------------------------------------------
#
# The guideline corpus is committed (`app/retrieval/corpus/chunks.json`) and
# BM25 is pure Python, so a real retrieval runs here in well under a second
# with no model download and no network. That matters for D6: these are
# recorded-fixture runs in the sense that counts, no credential, no service.
#
# Dense and rerank are deliberately not used. Both need fastembed and an ONNX
# model fetched at first use, which is a download inside a CI job and a cache
# that would have to be warmed — and neither adds anything to what these
# cases assert, which is the shape of a citation and of a log line. The one
# assertion that genuinely needs a reranked result set builds it from real
# chunks rather than pretending to have run a cross-encoder.

_RETRIEVAL_CONCEPTS = ("haemoglobin A1c target", "type 2 diabetes mellitus")
RETRIEVAL_CORRELATION_ID = "4c547101-195d-47b2-bc4b-0db614573052"


@functools.cache
def retrieval_output() -> object:
    """
    One real lexical retrieval over the committed corpus.

    Cached for the run: the corpus parse dominates and every case that needs
    evidence wants the same result set, so scoring a dozen cases costs one
    retrieval.
    """
    from app.models.w2.workers import (
        ClinicalConcept,
        EvidenceRetrieverInput,
        RetrievalMethod,
    )
    from app.retrieval.retriever import GuidelineRetriever
    from app.retrieval.worker import run_evidence_retriever

    request = EvidenceRetrieverInput(
        concepts=[ClinicalConcept(text=text) for text in _RETRIEVAL_CONCEPTS],
        top_k=5,
        rerank=False,
        method=RetrievalMethod.LEXICAL,
        correlation_id=RETRIEVAL_CORRELATION_ID,
    )
    output = run_evidence_retriever(request, GuidelineRetriever.load())
    if not output.chunks:
        raise GateError(
            "a lexical retrieval for "
            f"{_RETRIEVAL_CONCEPTS} returned nothing; the committed corpus has "
            "changed and every guideline-side case is now asserting over an empty set"
        )
    return output


def retrieved_chunks() -> list[object]:
    return list(retrieval_output().chunks)  # type: ignore[attr-defined]


def retrieval_log_event() -> dict[str, Any]:
    """
    The real RETRIEVAL_EVENT payload, captured rather than reconstructed.

    Runs a second, uncached retrieval with `log_event` intercepted, so what
    W2-J3 scans is the dict the middleware would have shipped — not a copy of
    it written here, which would pass for ever after somebody added a field.
    """
    from unittest.mock import patch

    from app.models.w2.workers import (
        ClinicalConcept,
        EvidenceRetrieverInput,
        RetrievalMethod,
    )
    from app.retrieval.retriever import GuidelineRetriever
    from app.retrieval.worker import run_evidence_retriever

    captured: list[tuple[str, dict[str, Any]]] = []

    def _capture(event_type: str, event_data: dict[str, Any] | None = None, **_: Any) -> None:
        captured.append((event_type, dict(event_data or {})))

    request = EvidenceRetrieverInput(
        concepts=[ClinicalConcept(text=text) for text in _RETRIEVAL_CONCEPTS],
        top_k=5,
        rerank=False,
        method=RetrievalMethod.LEXICAL,
        correlation_id=RETRIEVAL_CORRELATION_ID,
    )
    with patch("app.retrieval.worker.log_event", _capture):
        run_evidence_retriever(request, GuidelineRetriever.load())

    from app.retrieval.worker import RETRIEVAL_EVENT

    for event_type, event_data in captured:
        if event_type == RETRIEVAL_EVENT:
            return event_data
    raise GateError(
        f"a retrieval emitted {[e for e, _ in captured]} and no {RETRIEVAL_EVENT}; the "
        "one line that proves a query left this system carrying no identifier is gone"
    )
