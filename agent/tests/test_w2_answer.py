"""The final answer contract — invariant 3 and invariant 4 at the output."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.w2.answer import FinalAnswer, GuidelineClaim, PatientFactClaim
from app.models.w2.citations import BBox, Citation
from app.models.w2.extraction import ExtractedField, UnsupportedReason
from app.models.w2.source_types import SourceType

EXTRACTION_ID = "w2x-" + "5" * 32
CORRELATION_ID = "00000000-0000-4000-8000-000000000000"


def chart(source_id: str = "cond-1") -> Citation:
    return Citation(
        source_type=SourceType.CHART,
        source_id=source_id,
        page_or_section="Condition",
        field_or_chunk_id="display",
        quote_or_value="Type 2 diabetes mellitus",
    )


def document() -> Citation:
    return Citation.located_document(
        source_id="lab-clean-001",
        field_path="results.a1c.hba1c.value",
        quote="7.4",
        bbox=BBox(page=1, x0=270.0, y0=256.0, x1=280.0, y1=268.0),
        extraction_id=EXTRACTION_ID,
    )


def guideline(chunk_id: str = "chunk-7") -> Citation:
    return Citation(
        source_type=SourceType.GUIDELINE,
        source_id="uspstf-diabetes-2021",
        page_or_section="Recommendation",
        field_or_chunk_id=chunk_id,
        quote_or_value="Screen adults aged 35 to 70 ...",
    )


# -- every claim is cited ---------------------------------------------------


@pytest.mark.parametrize("claim_type", [PatientFactClaim, GuidelineClaim])
def test_a_claim_without_a_citation_cannot_exist(claim_type: type) -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        claim_type(statement="The patient has diabetes.", citations=[])


# -- the two sides never cross ----------------------------------------------


def test_a_patient_fact_cannot_be_supported_by_a_guideline() -> None:
    with pytest.raises(ValidationError, match="belong in guideline_evidence"):
        PatientFactClaim(statement="The patient has diabetes.", citations=[guideline()])


def test_guideline_evidence_cannot_be_supported_by_the_chart() -> None:
    with pytest.raises(ValidationError, match="belongs in patient_facts"):
        GuidelineClaim(statement="Screen adults aged 35 to 70.", citations=[chart()])


def test_a_patient_fact_cannot_mix_a_guideline_in_among_chart_citations() -> None:
    with pytest.raises(ValidationError, match="belong in guideline_evidence"):
        PatientFactClaim(statement="The patient has diabetes.", citations=[chart(), guideline()])


def test_a_single_claim_does_not_mix_chart_and_document() -> None:
    """A sentence backed partly by the chart and partly by an uploaded PDF
    reads as one fact with one provenance, and D2 is why that must not
    happen: the intake list and the chart list are exactly the two things
    that must never blur."""
    with pytest.raises(ValidationError, match="a single claim draws on one source type"):
        PatientFactClaim(statement="On lisinopril.", citations=[chart(), document()])


def test_chart_and_document_facts_are_both_patient_facts_and_stay_distinguishable() -> None:
    answer = FinalAnswer(
        answer="...",
        patient_facts=[
            PatientFactClaim(statement="Has diabetes.", citations=[chart()]),
            PatientFactClaim(statement="HbA1c 7.4% on the 30 Aug report.", citations=[document()]),
        ],
        correlation_id=CORRELATION_ID,
    )
    assert [c.statement for c in answer.chart_facts] == ["Has diabetes."]
    assert [c.source_type for c in answer.document_facts] == [SourceType.DOCUMENT]


def test_several_citations_of_one_kind_are_fine() -> None:
    claim = PatientFactClaim(
        statement="Two active problems.", citations=[chart("cond-1"), chart("cond-2")]
    )
    assert claim.source_type is SourceType.CHART


# -- the whole answer -------------------------------------------------------


def test_an_answer_keeps_the_two_kinds_in_separate_lists() -> None:
    answer = FinalAnswer(
        answer="Has diabetes; guidelines recommend screening.",
        patient_facts=[PatientFactClaim(statement="Has diabetes.", citations=[chart()])],
        guideline_evidence=[
            GuidelineClaim(statement="Screen adults 35 to 70.", citations=[guideline()])
        ],
        correlation_id=CORRELATION_ID,
    )
    assert len(answer.all_citations()) == 2
    assert {c.source_type for c in answer.patient_facts[0].citations} == {SourceType.CHART}
    assert {c.source_type for c in answer.guideline_evidence[0].citations} == {SourceType.GUIDELINE}


def test_unsupported_values_ride_along_and_are_not_claims() -> None:
    """Invariant 4: disclosed, never rendered as fact, never counted as
    verified — so they are in the answer, in a list nothing counts as a
    claim."""
    disclosure = ExtractedField[str].illegible().as_unsupported("patient.city_state_zip")
    assert disclosure is not None
    answer = FinalAnswer(answer="...", unsupported=[disclosure], correlation_id=CORRELATION_ID)
    assert answer.patient_facts == []
    assert answer.all_citations() == []
    assert answer.unsupported[0].reason is UnsupportedReason.ILLEGIBLE_ON_PAGE


def test_an_answer_does_not_assert_its_own_verification() -> None:
    """That is VerificationResult's job, produced by a separate step."""
    fields = set(FinalAnswer.model_fields)
    assert not fields & {"verified", "passed", "verification", "confidence"}


def test_an_answer_needs_a_correlation_id() -> None:
    with pytest.raises(ValidationError, match="Field required"):
        FinalAnswer(answer="...")
