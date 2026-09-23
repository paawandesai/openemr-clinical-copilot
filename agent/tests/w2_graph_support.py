"""
Test support for the Week 2 supervisor graph. Not a test module.

Everything the graph touches that would leave the process is replaced, and
nothing else is: the chart is a fixed set of Week 1 records served through
patched FHIR readers; the document is a real doc-factory PDF run through the
real extractor with the ground-truth transcriber
(tests/intake_extractor_support.py); retrieval is the real committed corpus
with BM25 and the deterministic fake embedder from the retrieval tests; the
drafting call is a scripted function that cites whatever it is told to.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pytest

from app.fhir import client as fhir_client
from app.models.claims import Claim
from app.models.conversation import ConversationGap
from app.models.fhir_resources import (
    AllergyIntolerance,
    Condition,
    FhirResourceType,
    MedicationRequest,
    Observation,
)
from app.models.w2.documents import DocumentKind
from app.models.w2.workers import EvidenceChunk
from app.reasoning.draft_answer import AnswerDraft
from app.reasoning.draft_follow_up import Coverage
from app.retrieval.build import load_index
from app.retrieval.corpus_meta import load_registry
from app.retrieval.retriever import GuidelineRetriever
from app.supervisor.facts import DocumentFact
from app.verification.w2_checker import DraftedDocumentFact, DraftedGuideline
from app.workers.document_store import DocumentStore
from tests.intake_extractor_support import chart_for, ground_truth_vision, patient_uuid, pdf_bytes
from tests.test_retrieval_corpus import FakeEmbedder

LAB_SAMPLE = "lab-clean-001"
INTAKE_SAMPLE = "intake-missing-allergy-006"
PATIENT = patient_uuid(LAB_SAMPLE)
OTHER_PATIENT = "00000000-0000-4000-8000-000000000000"
TOKEN = "SENTINEL-TOKEN-6f1c"
USER = "sentinel-user"

#: Distinctive chart values, so a test can search logs and traces for them.
CHART_MARKER = "SENTINEL-CONDITION-Hyperlipidemia-91c2"
MEDICATION_MARKER = "SENTINEL-MEDICATION-Atorvastatin-77ab"


def chart_records() -> dict[FhirResourceType, list[Any]]:
    return {
        FhirResourceType.CONDITION: [
            Condition(source_id="cond-1", display=CHART_MARKER, clinical_status="active")
        ],
        FhirResourceType.MEDICATION_REQUEST: [
            MedicationRequest(
                source_id="med-1",
                display=MEDICATION_MARKER,
                status="active",
                authored_date=datetime(2024, 2, 1),
            )
        ],
        FhirResourceType.ALLERGY_INTOLERANCE: [
            AllergyIntolerance(
                source_id="allergy-1", display="Penicillin", clinical_status="active"
            )
        ],
        FhirResourceType.OBSERVATION: [
            Observation(
                source_id="obs-1",
                value=5.9,
                unit="%",
                loinc_code="4548-4",
                effective_date=datetime(2026, 5, 1),
            )
        ],
    }


@dataclass
class FakeChart:
    """Patched Week 1 FHIR readers. Counts calls; can be told to fail."""

    patients: tuple[str, ...] = (PATIENT, OTHER_PATIENT)
    fail_with: Exception | None = None
    calls: list[str] = field(default_factory=list)
    #: Replaces the default Observation set when given.
    observations: list[Observation] | None = None

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        records = chart_records()
        if self.observations is not None:
            records[FhirResourceType.OBSERVATION] = list(self.observations)

        async def ensure(token: str, patient_id: str) -> None:
            from app.fhir.exceptions import PatientNotFoundError

            self.calls.append("Patient")
            if patient_id not in self.patients:
                raise PatientNotFoundError(patient_id)

        def reader(resource_type: FhirResourceType) -> Callable[..., Any]:
            async def read(token: str, patient_id: str, *args: Any, **kwargs: Any) -> list[Any]:
                self.calls.append(resource_type.value)
                if self.fail_with is not None:
                    raise self.fail_with
                return list(records.get(resource_type, []))

            return read

        monkeypatch.setattr(fhir_client, "ensure_patient_exists", ensure)
        for name, resource_type in (
            ("get_conditions", FhirResourceType.CONDITION),
            ("get_medication_requests", FhirResourceType.MEDICATION_REQUEST),
            ("get_allergy_intolerances", FhirResourceType.ALLERGY_INTOLERANCE),
            ("get_observations", FhirResourceType.OBSERVATION),
            ("get_encounters", FhirResourceType.ENCOUNTER),
        ):
            monkeypatch.setattr(fhir_client, name, reader(resource_type))

    @property
    def chart_reads(self) -> list[str]:
        return [call for call in self.calls if call != "Patient"]


_RETRIEVER: list[GuidelineRetriever] = []


def offline_retriever() -> GuidelineRetriever:
    """The committed corpus, BM25 plus a deterministic fake embedder, no reranker."""
    if not _RETRIEVER:
        _RETRIEVER.append(
            GuidelineRetriever(
                load_index(),
                load_registry(),
                embedder=FakeEmbedder(),
                reranker=None,
                cache_dir=None,
            )
        )
    return _RETRIEVER[0]


async def stored_document(
    documents: DocumentStore,
    sample: str = LAB_SAMPLE,
    patient: str = PATIENT,
    *,
    chart_sample: str | None = None,
) -> str:
    """Upload a factory sample for `patient` and wait for its extraction (tests only)."""

    async def read(uuid: str):  # noqa: ANN202 - a ChartReader
        return chart_for(chart_sample or sample)

    kind = DocumentKind.LAB_PDF if sample.startswith("lab") else DocumentKind.INTAKE_FORM
    stored, _, _ = documents.accept(patient, pdf_bytes(sample), kind, chart_reader=read)
    await documents.wait(stored.document.document_id)
    return stored.document.document_id


def lab_documents() -> DocumentStore:
    return DocumentStore(vision=ground_truth_vision(LAB_SAMPLE))


@dataclass
class ScriptedDrafter:
    """
    Stands in for the drafting call. `script` receives what the model would
    see and returns the citations to emit; the default cites the first chart
    condition, the first located document value, and the first eight words
    of the first chunk. Records every call.
    """

    script: Callable[..., AnswerDraft] | None = None
    fail_with: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(
        self,
        chart: dict[FhirResourceType, list[Any]],
        documents: dict[str, list[DocumentFact]],
        chunks: list[EvidenceChunk],
        question: str,
        context: tuple[Any, ...],
        *,
        patient_session_key: str,
        withheld_rows: int = 0,
    ) -> AnswerDraft:
        self.calls.append(
            {
                "chart": chart,
                "documents": documents,
                "chunks": chunks,
                "context": context,
                "withheld_rows": withheld_rows,
            }
        )
        if self.fail_with is not None:
            raise self.fail_with
        if self.script is not None:
            return self.script(chart, documents, chunks)
        return default_draft(chart, documents, chunks)


def default_draft(
    chart: dict[FhirResourceType, list[Any]],
    documents: dict[str, list[DocumentFact]],
    chunks: list[EvidenceChunk],
) -> AnswerDraft:
    condition = chart[FhirResourceType.CONDITION][0]
    chart_claims = [
        Claim(
            source_id=condition.source_id,
            field="display",
            value=condition.display,
            resource_type=FhirResourceType.CONDITION,
        )
    ]
    document_claims = [
        DraftedDocumentFact(
            document_id=fact.document_id, field_path=fact.field_path, value=fact.quote
        )
        for facts in documents.values()
        for fact in facts
        if fact.field_path.startswith("results.cmp.glucose.")
    ]
    guideline_claims = [
        DraftedGuideline(chunk_id=chunk.chunk_id, excerpt=" ".join(chunk.text.split()[:8]))
        for chunk in chunks[:1]
    ]
    return AnswerDraft(
        chart_claims=chart_claims,
        document_claims=document_claims,
        guideline_claims=guideline_claims,
        coverage=Coverage.FULL,
        gaps=[],
    )


def draft_of(
    chart_claims: list[Claim] | None = None,
    document_claims: list[DraftedDocumentFact] | None = None,
    guideline_claims: list[DraftedGuideline] | None = None,
    coverage: Coverage = Coverage.FULL,
    gaps: list[ConversationGap] | None = None,
) -> AnswerDraft:
    return AnswerDraft(
        chart_claims=chart_claims or [],
        document_claims=document_claims or [],
        guideline_claims=guideline_claims or [],
        coverage=coverage,
        gaps=gaps or [],
    )
