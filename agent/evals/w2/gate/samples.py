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
from pathlib import Path
from typing import Any

from app.models.w2 import (
    IntakeFormExtraction,
    LabPdfExtraction,
)
from app.models.w2.factory_mapping import GroundTruthConversion, convert_ground_truth
from evals.w2.gate.models import GateError
from evals.w2.gate.paths import FACTORY_SAMPLES_DIR, W2_ROOT

LAB_SCENARIOS = ("lab-clean-001", "lab-scanned-heavy-abnormal-002", "lab-multipage-003")
INTAKE_SCENARIOS = (
    "intake-clean-004",
    "intake-scanned-light-med-005",
    "intake-missing-allergy-006",
)
ALL_SCENARIOS = LAB_SCENARIOS + INTAKE_SCENARIOS


@functools.cache
def ground_truth(scenario_id: str) -> dict[str, Any]:
    path = _sample_dir(scenario_id) / f"{scenario_id}.ground_truth.json"
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


# --------------------------------------------------------------------------
# Recorded live transcriptions: real model output, replayed with no network.
# --------------------------------------------------------------------------
#
# `fixtures/transcriptions/` holds what claude-opus-5 — the system under test,
# not the judge — actually returned for each committed sample, recorded by
# `evals/w2/factory/record_transcriptions.py` and promoted here from
# `staging/` by a W2_GATE_SESSION. Six recordings, about $0.78 of real
# inference, replayed for free and identically on every run.
#
# Why this matters for category B: every B case could be run against
# `ground_truth_vision`, a stub that hands the worker the right answer. That
# proves the worker's plumbing and nothing about extraction. A case that says
# "the model did not invent a value" has to be scored against what a model
# actually returned, and MODE_RECORDED is what makes that possible offline.
#
# The staleness refusal is the point of the fingerprints: a recording made
# against a different PDF, or a different prompt or output schema, is not an
# answer to the question the worker would ask now. It is refused rather than
# replayed, so a stale fixture fails the gate instead of quietly grading an
# old run. Reimplemented here rather than imported from `tests/` because
# tests/ is not gated and this is the copy an implementation session must not
# be able to weaken.
#
# The fingerprint itself is computed HERE too. It used to be imported from
# `factory/record_transcriptions.py`, which is ungated, and a one-line edit
# there (return the fingerprint already on record) switched this check off:
# an extractor prompt edit then replayed old recordings and passed
# (docs/w2/evidence/prompt-change-check.md on chore/w2-prompt-change-check,
# row 5b). The recorder and the unit tests now import it from this file.
#
# A stale recording FAILS the case that replays it; it is not a GateError.
# As a GateError it stopped the run with exit 2 before any case was scored,
# which blocked the push but named nothing. As a failure the category-B cases
# that replay it fail by name, and W2-L1 names the prompt that moved.

TRANSCRIPTIONS_DIR = W2_ROOT / "fixtures" / "transcriptions"


class StaleRecording(Exception):
    """
    A recorded transcription that no longer answers the question the worker
    asks: made against a different PDF, prompt or output schema. Deliberately
    not a GateError — the runner scores it as a failure of the case that
    replayed it.
    """


def prompt_fingerprint(kind: Any) -> str:
    """
    sha256 of the extractor's vision prompt and `kind`'s output schema, the
    value each recording carries as `prompt_fingerprint`. Keys are sorted, as
    they were when the six committed recordings were made; W2-L1's pin, which
    does not sort, is what catches a reordered schema.
    """
    import hashlib

    from app.workers.intake_extractor.vision_schema import OUTPUT_SCHEMA, SYSTEM_PROMPT

    material = SYSTEM_PROMPT + json.dumps(OUTPUT_SCHEMA[kind], sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


#: Which vision source a case runs against, named in its case file.
MODE_RECORDED = "recorded"  # real model output, replayed
MODE_STUB = "ground_truth"  # the factory's own answer, for plumbing only


def transcription(sample: str) -> dict[str, Any]:
    path = TRANSCRIPTIONS_DIR / f"{sample}.transcription.json"
    if not path.is_file():
        raise GateError(
            f"no recorded transcription for {sample} at {path}. Record one with "
            "`python -m evals.w2.factory.record_transcriptions <sample>` and have a "
            "W2_GATE_SESSION promote it out of staging/."
        )
    return json.loads(path.read_text(encoding="utf-8"))


#: Scenarios the GATE owns, generated from a staged spec and kept here rather
#: than in the factory's committed sample set. `lab-critical-potassium-007` is
#: the first: three ungated tests assert the factory ships exactly six samples
#: (`test_six_sample_scenarios_are_committed`, `test_the_six_samples_are_all_
#: there`, `test_all_six_samples_build`), and adding a seventh there breaks all
#: three for branches that have nothing to do with this one. It also belongs
#: here on the merits — it is a gate fixture, and this directory is gated.
#:
#: Regenerate with:
#:   python -m evals.w2.factory generate \
#:     evals/w2/staging/specs/lab-critical-potassium-007.yaml \
#:     --out evals/w2/fixtures/scenarios
GATE_SCENARIOS_DIR = W2_ROOT / "fixtures" / "scenarios"


def _sample_dir(sample: str) -> Path:
    owned = GATE_SCENARIOS_DIR / sample
    return owned if owned.is_dir() else FACTORY_SAMPLES_DIR / sample


def sample_pdf(sample: str) -> bytes:
    return (_sample_dir(sample) / f"{sample}.pdf").read_bytes()


def sample_kind(sample: str) -> Any:
    from app.models.w2.documents import DocumentKind

    return DocumentKind(ground_truth(sample)["scenario"]["doc_type"])


def recorded_vision(sample: str) -> Any:
    """
    A VisionExtractor replaying `sample`'s recording. StaleRecording when it
    no longer matches the PDF, prompt or schema; GateError when there is none.
    """
    import hashlib

    from app.workers.intake_extractor.vision import VisionResult, VisionUsage
    from app.workers.intake_extractor.vision_schema import RAW_MODEL

    recorded = transcription(sample)
    kind = sample_kind(sample)
    digest = hashlib.sha256(sample_pdf(sample)).hexdigest()
    if recorded["document_sha256"] != digest:
        raise StaleRecording(
            f"{sample}: the recorded transcription was made against a different PDF "
            "(the factory regenerated the sample). Re-record it; replaying it would "
            "grade the model on a page it never saw."
        )
    if recorded["prompt_fingerprint"] != prompt_fingerprint(kind):
        raise StaleRecording(
            f"{sample}: the prompt or the output schema has changed since this "
            "transcription was recorded. Re-record it; the recording answers a "
            "question the worker no longer asks."
        )
    raw = RAW_MODEL[kind].model_validate_json(json.dumps(recorded["raw"]))
    usage = recorded["usage"]

    async def extract(_kind: Any, _pdf: bytes, _pages: int) -> Any:
        return VisionResult(
            raw=raw,
            usage=VisionUsage(
                calls=usage["calls"],
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                latency_ms=usage["latency_ms"],
                served_models=list(recorded["served_models"]),
                cost_usd=usage["cost_usd"],
            ),
        )

    return extract


@functools.cache
def extraction(sample: str, mode: str = MODE_RECORDED) -> Any:
    """
    One sample through the merged intake-extractor, cached for the run.

    `mode` is MODE_RECORDED (real model output) or MODE_STUB (the factory's
    ground truth). Every case file says which it uses, because the two answer
    different questions and a reader must not have to guess.
    """
    import asyncio

    from structlog.testing import capture_logs

    from app.workers.intake_extractor import attach_and_extract

    if mode == MODE_RECORDED:
        vision = recorded_vision(sample)
    elif mode == MODE_STUB:
        from tests.intake_extractor_support import ground_truth_vision

        vision = ground_truth_vision(sample)
    else:
        raise GateError(f"unknown extraction mode {mode!r}")

    # capture_logs keeps the run's structured output out of the gate's stdout;
    # W2-J is what asserts on log content, from its own captures.
    with capture_logs():
        return asyncio.run(
            attach_and_extract(
                ground_truth(sample)["chart_source"]["patient_uuid"],
                sample_pdf(sample),
                sample_kind(sample),
                vision=vision,
            )
        )


def tesseract_is_available() -> bool:
    from app.extraction import tesseract_available

    return bool(tesseract_available())
