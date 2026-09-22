"""
The Week 2 citation contract, and the Week 1 bridge.

The bridge is the part that matters most: the work order requires that
"Week 1 citations must still validate or map losslessly", and Week 1 has no
`Citation` type to validate. What it has is `Claim` (app/models/claims.py),
so the proof is a round-trip over every value shape `Claim.value` admits,
including the `None` that findings.md F-57 requires.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.api import ClaimOutcome
from app.models.claims import Claim
from app.models.fhir_resources import FhirResourceType
from app.models.w2.citations import (
    CHART_SECTIONS,
    BBox,
    Citation,
    citation_from_claim,
    citation_from_claim_outcome,
    claim_from_citation,
)
from app.models.w2.source_types import SourceType

EXTRACTION_ID = "w2x-" + "0" * 32


def _bbox(page: int = 1) -> BBox:
    return BBox(page=page, x0=54.0, y0=58.34, x1=326.115, y1=79.115)


# -- bbox -------------------------------------------------------------------


def test_bbox_allows_a_zero_width_box() -> None:
    """
    The doc-factory draws an empty flag column as a zero-width box
    ([306.0, 256.398, 306.0, 268.24]). Rejecting it would make the contract
    unable to describe a normal lab row.
    """
    box = BBox(page=1, x0=306.0, y0=256.398, x1=306.0, y1=268.24)
    assert box.width == 0.0
    assert box.height == pytest.approx(11.842)


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"page": 0, "x0": 0, "y0": 0, "x1": 1, "y1": 1}, "greater than or equal to 1"),
        ({"page": 1, "x0": 5, "y0": 0, "x1": 1, "y1": 1}, "left of"),
        ({"page": 1, "x0": 0, "y0": 5, "x1": 1, "y1": 1}, "above"),
        ({"page": 1, "x0": -1, "y0": 0, "x1": 1, "y1": 1}, "greater than or equal to 0"),
    ],
)
def test_bbox_rejects_impossible_rectangles(kwargs: dict[str, float], fragment: str) -> None:
    with pytest.raises(ValidationError, match=fragment):
        BBox(**kwargs)


# -- source-type rules ------------------------------------------------------


def test_document_citation_requires_extraction_id() -> None:
    with pytest.raises(ValidationError, match="requires extraction_id"):
        Citation(
            source_type=SourceType.DOCUMENT,
            source_id="lab-clean-001",
            page_or_section="1",
            field_or_chunk_id="results.cmp.glucose.value",
            quote_or_value="89",
            bbox=_bbox(),
        )


@pytest.mark.parametrize("source_type", [SourceType.CHART, SourceType.GUIDELINE])
def test_bbox_is_document_only(source_type: SourceType) -> None:
    """D3: the locate step is the only source of a bbox, and it only runs on
    documents. A chart or guideline citation with coordinates means a
    coordinate came from somewhere with no pages."""
    section = "Condition" if source_type is SourceType.CHART else "§4 Screening"
    with pytest.raises(ValidationError, match="bbox is document-only"):
        Citation(
            source_type=source_type,
            source_id="x",
            page_or_section=section,
            field_or_chunk_id="display",
            quote_or_value="Type 2 diabetes",
            bbox=_bbox(),
        )


@pytest.mark.parametrize("source_type", [SourceType.CHART, SourceType.GUIDELINE])
def test_extraction_id_is_document_only(source_type: SourceType) -> None:
    section = "Condition" if source_type is SourceType.CHART else "§4 Screening"
    with pytest.raises(ValidationError, match="extraction_id is document-only"):
        Citation(
            source_type=source_type,
            source_id="x",
            page_or_section=section,
            field_or_chunk_id="display",
            quote_or_value="Type 2 diabetes",
            extraction_id=EXTRACTION_ID,
        )


def test_chart_section_must_be_a_tool_scope_resource_type() -> None:
    assert CHART_SECTIONS == {member.value for member in FhirResourceType}
    with pytest.raises(ValidationError, match="page_or_section must be one of"):
        Citation(
            source_type=SourceType.CHART,
            source_id="x",
            page_or_section="Vitals",
            field_or_chunk_id="display",
            quote_or_value="BP 120/80",
        )


def test_document_page_and_bbox_page_must_agree() -> None:
    with pytest.raises(ValidationError, match="says page 2 but bbox is on page 1"):
        Citation(
            source_type=SourceType.DOCUMENT,
            source_id="lab-clean-001",
            page_or_section="2",
            field_or_chunk_id="results.cmp.glucose.value",
            quote_or_value="89",
            bbox=_bbox(page=1),
            extraction_id=EXTRACTION_ID,
        )


def test_only_a_chart_citation_may_quote_nothing() -> None:
    """findings.md F-57: a chart record with no usable name is cited as
    nameless. A document or guideline citation that quotes nothing cites
    nothing."""
    nameless = Citation(
        source_type=SourceType.CHART,
        source_id="cond-1",
        page_or_section="Condition",
        field_or_chunk_id="display",
        quote_or_value=None,
    )
    assert nameless.quote_or_value is None

    with pytest.raises(ValidationError, match="requires quote_or_value"):
        Citation(
            source_type=SourceType.GUIDELINE,
            source_id="g-1",
            page_or_section="§4",
            field_or_chunk_id="chunk-7",
            quote_or_value=None,
        )


def test_extra_fields_are_refused() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Citation(
            source_type=SourceType.CHART,
            source_id="cond-1",
            page_or_section="Condition",
            field_or_chunk_id="display",
            quote_or_value="x",
            patient_id="a2c373ec-57bf-41cb-8767-94410dcf663d",
        )


# -- constructors -----------------------------------------------------------


def test_located_document_constructor_derives_the_page_from_the_bbox() -> None:
    citation = Citation.located_document(
        source_id="lab-clean-001",
        field_path="results.cmp.glucose.value",
        quote="89",
        bbox=_bbox(page=3),
        extraction_id=EXTRACTION_ID,
    )
    assert citation.page_or_section == "3"
    assert citation.is_located_document is True


def test_unlocated_document_constructor_cannot_carry_a_bbox() -> None:
    citation = Citation.unlocated_document(
        source_id="lab-clean-001",
        field_path="results.cmp.glucose.value",
        quote="89",
        page=1,
        extraction_id=EXTRACTION_ID,
    )
    assert citation.bbox is None
    assert citation.is_located_document is False


# -- the Week 1 bridge ------------------------------------------------------

WEEK1_CLAIMS = [
    Claim(
        source_id="cond-1",
        field="display",
        value="Type 2 diabetes mellitus",
        resource_type=FhirResourceType.CONDITION,
    ),
    # F-57: a record with no usable name.
    Claim(
        source_id="cond-2",
        field="display",
        value=None,
        resource_type=FhirResourceType.CONDITION,
    ),
    Claim(
        source_id="obs-1",
        field="value_quantity",
        value=6.9,
        resource_type=FhirResourceType.OBSERVATION,
    ),
    Claim(
        source_id="enc-1",
        field="count",
        value=3,
        resource_type=FhirResourceType.ENCOUNTER,
    ),
    Claim(
        source_id="med-1",
        field="status",
        value="active",
        resource_type=FhirResourceType.MEDICATION_REQUEST,
    ),
    Claim(
        source_id="alg-1",
        field="criticality",
        value="high",
        resource_type=FhirResourceType.ALLERGY_INTOLERANCE,
    ),
]


@pytest.mark.parametrize("claim", WEEK1_CLAIMS, ids=lambda c: f"{c.resource_type.value}.{c.field}")
def test_week1_claim_round_trips_through_citation_losslessly(claim: Claim) -> None:
    citation = citation_from_claim(claim)
    assert citation.source_type is SourceType.CHART
    assert claim_from_citation(citation) == claim


def test_every_tool_scope_resource_type_round_trips() -> None:
    """Not just the ones the fixture happens to name."""
    for resource_type in FhirResourceType:
        claim = Claim(source_id="id-1", field="display", value="x", resource_type=resource_type)
        assert claim_from_citation(citation_from_claim(claim)) == claim


def test_value_types_survive_the_round_trip_unchanged() -> None:
    """A float must not come back an int, and an int must not come back a
    string — strict mode is what keeps the bridge lossless rather than
    merely equal-ish."""
    claim = Claim(
        source_id="obs-1", field="v", value=6.0, resource_type=FhirResourceType.OBSERVATION
    )
    recovered = claim_from_citation(citation_from_claim(claim))
    assert isinstance(recovered.value, float)
    assert recovered.value == 6.0


def test_non_chart_citations_have_no_week1_claim() -> None:
    guideline = Citation(
        source_type=SourceType.GUIDELINE,
        source_id="g-1",
        page_or_section="§4",
        field_or_chunk_id="chunk-7",
        quote_or_value="Screen adults aged 35 to 70 ...",
    )
    with pytest.raises(ValueError, match="invariant 3"):
        claim_from_citation(guideline)


def test_a_boolean_document_quote_is_not_a_week1_claim() -> None:
    """bool is an int subclass, so this would otherwise pass validation and
    silently become 0/1."""
    checkbox = Citation(
        source_type=SourceType.CHART,
        source_id="cond-1",
        page_or_section="Condition",
        field_or_chunk_id="flag",
        quote_or_value=True,
    )
    with pytest.raises(ValueError, match="no boolean form"):
        claim_from_citation(checkbox)


def test_claim_outcome_projects_one_way() -> None:
    outcome = ClaimOutcome(
        resource_type=FhirResourceType.CONDITION,
        source_id="cond-1",
        field="display",
        value="Type 2 diabetes mellitus",
        verified=False,
        unverified_reason="value mismatch",
    )
    citation = citation_from_claim_outcome(outcome)
    assert citation.source_id == outcome.source_id
    assert citation.field_or_chunk_id == outcome.field
    assert citation.quote_or_value == outcome.value
    # Verification state deliberately does not survive: a citation says
    # where a value came from, never whether anyone confirmed it.
    assert "verified" not in citation.model_dump()
