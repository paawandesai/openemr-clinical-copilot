"""
Worker and supervisor contracts — D4, D5 and D7 enforced by the types.

`test_retrieval_input_has_no_patient_identifier_field` and
`test_routing_decision_has_no_free_text_field` are the two that matter: D5
and D4 are both stated as things that must not exist, and an absence is
only enforced if something asserts it.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.w2.documents import (
    DocumentChecksums,
    DocumentKind,
    ExtractionStatus,
    SourceDocument,
)
from app.models.w2.source_types import SourceType
from app.models.w2.workers import (
    ALLOWED_REASONS,
    FORBIDDEN_RETRIEVAL_FIELD_NAMES,
    ClinicalConcept,
    EvidenceChunk,
    EvidenceRetrieverInput,
    EvidenceRetrieverOutput,
    ExtractionFailureReason,
    GuidelineSource,
    IntakeExtractorInput,
    IntakeExtractorOutput,
    RetrievalMethod,
    Route,
    RoutingDecision,
    RoutingReasonCode,
)

CORRELATION_ID = "00000000-0000-4000-8000-000000000000"


def document(kind: DocumentKind = DocumentKind.LAB_PDF) -> SourceDocument:
    return SourceDocument(
        document_id="lab-clean-001",
        kind=kind,
        filename="lab-clean-001.pdf",
        mime_type="application/pdf",
        page_count=1,
        byte_length=31851,
        checksums=DocumentChecksums(sha3_512="a" * 128),
        has_text_layer=True,
    )


def concept(text: str = "type 2 diabetes") -> ClinicalConcept:
    return ClinicalConcept(text=text)


def chunk(chunk_id: str = "chunk-7", rerank: float | None = 0.8) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        text="Screen adults aged 35 to 70 who have overweight or obesity.",
        source=GuidelineSource(
            guideline_id="uspstf-diabetes-2021",
            title="Screening for Prediabetes and Type 2 Diabetes",
            section="Recommendation",
        ),
        retrieval_score=12.4,
        rerank_score=rerank,
        method=RetrievalMethod.HYBRID,
    )


# -- D5: no patient identifiers in a retrieval query -----------------------


def test_retrieval_input_has_no_patient_identifier_field() -> None:
    """
    D5: "A query leaving this system carries clinical concepts, never a
    name, MRN, date of birth, or any other identifier." Asserted against
    the model's real field names, so adding one fails here.
    """
    fields = set(EvidenceRetrieverInput.model_fields)
    offenders = fields & FORBIDDEN_RETRIEVAL_FIELD_NAMES
    assert offenders == set(), f"D5: retrieval input must not carry {offenders}"


@pytest.mark.parametrize(
    "smuggled",
    ["patient_id", "patient_uuid", "mrn", "question", "chart_context"],
)
def test_a_caller_cannot_smuggle_an_identifier_in(smuggled: str) -> None:
    """`extra="forbid"` is what turns "there is no such field" into an error
    rather than a silently ignored keyword."""
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EvidenceRetrieverInput(
            concepts=[concept()],
            correlation_id=CORRELATION_ID,
            **{smuggled: "a2c373ec-57bf-41cb-8767-94410dcf663d"},
        )


@pytest.mark.parametrize(
    "text",
    [
        "patient born 1960-09-25",
        "seen on 09/10/2026",
        "ssn 123-45-6789",
    ],
)
def test_a_concept_rejects_identifier_shaped_text(text: str) -> None:
    """The one place an identifier could still ride along is the concept
    text — typically because the whole question got passed as one."""
    with pytest.raises(ValidationError, match="no dates or identifier-shaped numbers"):
        ClinicalConcept(text=text)


@pytest.mark.parametrize(
    "text", ["vitamin B12 deficiency", "HbA1c above 7%", "type 2 diabetes mellitus", "COVID-19"]
)
def test_ordinary_clinical_concepts_are_not_false_positives(text: str) -> None:
    assert ClinicalConcept(text=text).text == text


def test_a_retrieval_query_needs_at_least_one_concept() -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        EvidenceRetrieverInput(concepts=[], correlation_id=CORRELATION_ID)


def test_a_code_needs_its_system() -> None:
    with pytest.raises(ValidationError, match="given together or not at all"):
        ClinicalConcept(text="type 2 diabetes", code="44054006")


# -- evidence chunks --------------------------------------------------------


def test_a_chunk_cites_a_guideline_and_only_a_guideline() -> None:
    citation = chunk().to_citation()
    assert citation.source_type is SourceType.GUIDELINE
    assert citation.source_id == "uspstf-diabetes-2021"
    assert citation.field_or_chunk_id == "chunk-7"
    assert citation.page_or_section == "Recommendation"
    assert citation.bbox is None


def test_retrieval_and_rerank_scores_stay_apart() -> None:
    """Collapsing them makes "was this the retriever or the reranker?"
    unanswerable the first time a result looks wrong."""
    one = chunk()
    assert one.retrieval_score == 12.4
    assert one.rerank_score == 0.8


def test_not_reranked_means_no_rerank_scores() -> None:
    with pytest.raises(ValidationError, match="marked not reranked"):
        EvidenceRetrieverOutput(
            chunks=[chunk()],
            method=RetrievalMethod.HYBRID,
            reranked=False,
            correlation_id=CORRELATION_ID,
        )


def test_reranked_means_every_chunk_has_a_score() -> None:
    with pytest.raises(ValidationError, match="rerank_score on every chunk"):
        EvidenceRetrieverOutput(
            chunks=[chunk(), chunk("chunk-8", rerank=None)],
            method=RetrievalMethod.HYBRID,
            reranked=True,
            correlation_id=CORRELATION_ID,
        )


def test_an_unreranked_result_set_is_valid() -> None:
    output = EvidenceRetrieverOutput(
        chunks=[chunk("chunk-9", rerank=None)],
        method=RetrievalMethod.LEXICAL,
        reranked=False,
        correlation_id=CORRELATION_ID,
    )
    assert output.chunks[0].rerank_score is None


# -- D7: still processing ---------------------------------------------------


def test_a_pending_extraction_carries_no_payload() -> None:
    output = IntakeExtractorOutput(
        status=ExtractionStatus.PENDING,
        document_id="lab-clean-001",
        kind=DocumentKind.LAB_PDF,
        correlation_id=CORRELATION_ID,
    )
    assert output.is_still_processing is True
    assert output.payload is None


@pytest.mark.parametrize("status", [ExtractionStatus.PENDING, ExtractionStatus.IN_PROGRESS])
def test_a_partial_extraction_has_nowhere_to_be_returned_from(
    status: ExtractionStatus,
) -> None:
    """D7: never a partial extraction presented as complete."""
    from tests.test_w2_lab_pdf import extraction as lab_extraction

    with pytest.raises(ValidationError, match="D7 forbids returning a partial extraction"):
        IntakeExtractorOutput(
            status=status,
            document_id="lab-clean-001",
            kind=DocumentKind.LAB_PDF,
            payload=lab_extraction(),
            correlation_id=CORRELATION_ID,
        )


def test_a_complete_extraction_needs_its_payload() -> None:
    with pytest.raises(ValidationError, match="a complete extraction has a payload"):
        IntakeExtractorOutput(
            status=ExtractionStatus.COMPLETE,
            document_id="lab-clean-001",
            kind=DocumentKind.LAB_PDF,
            extraction_id="w2x-" + "2" * 32,
            correlation_id=CORRELATION_ID,
        )


def test_a_complete_extraction_agrees_with_its_payload() -> None:
    from tests.test_w2_lab_pdf import EXTRACTION_ID
    from tests.test_w2_lab_pdf import extraction as lab_extraction

    complete = IntakeExtractorOutput(
        status=ExtractionStatus.COMPLETE,
        document_id="lab-clean-001",
        kind=DocumentKind.LAB_PDF,
        extraction_id=EXTRACTION_ID,
        payload=lab_extraction(),
        correlation_id=CORRELATION_ID,
    )
    assert complete.is_still_processing is False

    with pytest.raises(ValidationError, match="payload document_id disagrees"):
        IntakeExtractorOutput(
            status=ExtractionStatus.COMPLETE,
            document_id="some-other-document",
            kind=DocumentKind.LAB_PDF,
            extraction_id=EXTRACTION_ID,
            payload=lab_extraction(),
            correlation_id=CORRELATION_ID,
        )


def test_the_payload_type_must_match_the_declared_kind() -> None:
    from tests.test_w2_lab_pdf import EXTRACTION_ID
    from tests.test_w2_lab_pdf import extraction as lab_extraction

    with pytest.raises(ValidationError):
        IntakeExtractorOutput(
            status=ExtractionStatus.COMPLETE,
            document_id="lab-clean-001",
            kind=DocumentKind.INTAKE_FORM,
            extraction_id=EXTRACTION_ID,
            payload=lab_extraction(),
            correlation_id=CORRELATION_ID,
        )


def test_a_failed_extraction_says_why() -> None:
    with pytest.raises(ValidationError, match="a failed extraction says why"):
        IntakeExtractorOutput(
            status=ExtractionStatus.FAILED,
            document_id="lab-clean-001",
            kind=DocumentKind.LAB_PDF,
            correlation_id=CORRELATION_ID,
        )
    failed = IntakeExtractorOutput(
        status=ExtractionStatus.FAILED,
        document_id="lab-clean-001",
        kind=DocumentKind.LAB_PDF,
        failure_reason=ExtractionFailureReason.NO_TEXT_LAYER_AND_OCR_FAILED,
        correlation_id=CORRELATION_ID,
    )
    assert failed.is_still_processing is False


def test_extractor_input_takes_a_document_not_its_contents() -> None:
    """Keeping page text out of the value passed between nodes keeps chart
    content out of traces (invariant 1)."""
    fields = set(IntakeExtractorInput.model_fields)
    assert not fields & {"page_text", "pages", "images", "content", "content_base64"}
    assert (
        IntakeExtractorInput(document=document(), correlation_id=CORRELATION_ID).force_reextract
        is False
    )


# -- D4: routing ------------------------------------------------------------


def test_routing_decision_has_no_free_text_field() -> None:
    """D4 wants every decision logged with its reason; invariant 1 wants
    nothing loggable to be able to carry chart content. A reason code
    satisfies both; a reason string satisfies neither."""
    assert set(RoutingDecision.model_fields) == {"decision", "reason_code", "correlation_id"}


def test_every_reason_code_belongs_to_exactly_one_route() -> None:
    seen: list[RoutingReasonCode] = []
    for reasons in ALLOWED_REASONS.values():
        seen.extend(reasons)
    assert sorted(code.value for code in seen) == sorted(code.value for code in RoutingReasonCode)
    assert len(seen) == len(set(seen))


def test_every_route_has_at_least_one_reason() -> None:
    assert set(ALLOWED_REASONS) == set(Route)
    assert all(reasons for reasons in ALLOWED_REASONS.values())


@pytest.mark.parametrize("route", list(Route))
def test_each_route_accepts_its_own_reasons(route: Route) -> None:
    for reason in ALLOWED_REASONS[route]:
        decision = RoutingDecision(
            decision=route, reason_code=reason, correlation_id=CORRELATION_ID
        )
        assert decision.decision is route


def test_a_mismatched_reason_is_rejected() -> None:
    with pytest.raises(ValidationError, match="is not a reason for routing to"):
        RoutingDecision(
            decision=Route.ANSWER,
            reason_code=RoutingReasonCode.OUT_OF_SCOPE_RESOURCE,
            correlation_id=CORRELATION_ID,
        )


def test_still_processing_routes_to_refuse() -> None:
    """D7's explicit non-answer is a routing outcome, not a wait."""
    assert RoutingReasonCode.DOCUMENT_STILL_PROCESSING in ALLOWED_REASONS[Route.REFUSE]


def test_a_decision_carries_a_correlation_id() -> None:
    with pytest.raises(ValidationError, match="String should match pattern"):
        RoutingDecision(
            decision=Route.ANSWER,
            reason_code=RoutingReasonCode.CHART_ONLY_QUESTION,
            correlation_id="not-a-uuid",
        )
