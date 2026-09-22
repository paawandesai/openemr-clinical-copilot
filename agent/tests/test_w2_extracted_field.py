"""
`ExtractedField` — CLAUDE.md invariant 4 enforced by the type system.

The interesting assertions are the ones about what cannot be constructed:
a located field without a bbox, an illegible field carrying the value it
could not read, a `PresentedFact` built by hand out of an unlocated
citation. Each of those is a way invariant 4 could be broken quietly, and
each raises here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.w2.citations import BBox, Citation
from app.models.w2.extraction import (
    PRESENTABLE_STATUSES,
    ExtractedField,
    FieldStatus,
    PresentedFact,
    UnsupportedField,
    UnsupportedFieldError,
    UnsupportedReason,
)
from app.models.w2.source_types import SourceType

EXTRACTION_ID = "w2x-" + "1" * 32


def located_citation(quote: str = "89") -> Citation:
    return Citation.located_document(
        source_id="lab-clean-001",
        field_path="results.cmp.glucose.value",
        quote=quote,
        bbox=BBox(page=1, x0=270.437, y0=256.355, x1=280.0, y1=268.171),
        extraction_id=EXTRACTION_ID,
    )


def unlocated_citation(quote: str = "89") -> Citation:
    return Citation.unlocated_document(
        source_id="lab-clean-001",
        field_path="results.cmp.glucose.value",
        quote=quote,
        page=1,
        extraction_id=EXTRACTION_ID,
    )


# -- only `located` is a fact ----------------------------------------------


def test_only_located_is_presentable() -> None:
    assert PRESENTABLE_STATUSES == {FieldStatus.LOCATED}


def test_located_yields_a_presented_fact() -> None:
    field = ExtractedField[float].located(89.0, located_citation())
    fact = field.as_presented_fact()
    assert fact is not None
    assert fact.value == 89.0
    assert fact.citation.bbox is not None


@pytest.mark.parametrize(
    "field",
    [
        ExtractedField[float].unlocated(89.0),
        ExtractedField[float].missing(),
        ExtractedField[float].illegible(),
    ],
    ids=["unlocated", "missing", "illegible"],
)
def test_nothing_but_located_yields_a_fact(field: ExtractedField[float]) -> None:
    assert field.is_presentable is False
    assert field.as_presented_fact() is None
    with pytest.raises(UnsupportedFieldError, match="invariant 4"):
        field.require_presented_fact()


def test_exactly_one_of_fact_and_disclosure_is_produced() -> None:
    """A field is either shown as a fact or disclosed as unsupported. It can
    never be silently neither — that is the Week 1 disclosure rule
    (ARCHITECTURE.md §5) applied to documents."""
    fields = [
        ExtractedField[float].located(89.0, located_citation()),
        ExtractedField[float].unlocated(89.0),
        ExtractedField[float].missing(),
        ExtractedField[float].illegible(),
    ]
    for field in fields:
        fact = field.as_presented_fact()
        disclosure = field.as_unsupported("results.cmp.glucose.value")
        assert (fact is None) != (disclosure is None)


# -- the states the validator refuses --------------------------------------


def test_located_requires_a_bbox() -> None:
    with pytest.raises(ValidationError, match="requires a document citation with a bbox"):
        ExtractedField[float](status=FieldStatus.LOCATED, value=89.0, citation=unlocated_citation())


def test_located_requires_a_citation() -> None:
    with pytest.raises(ValidationError, match="requires a citation"):
        ExtractedField[float](status=FieldStatus.LOCATED, value=89.0)


def test_located_requires_a_value() -> None:
    with pytest.raises(ValidationError, match="located field has a value"):
        ExtractedField[float](status=FieldStatus.LOCATED, citation=located_citation())


def test_unlocated_cannot_carry_a_bbox() -> None:
    """A bbox is what located means."""
    with pytest.raises(ValidationError, match="cannot carry a bbox"):
        ExtractedField[float](status=FieldStatus.UNLOCATED, value=89.0, citation=located_citation())


def test_unlocated_requires_a_value() -> None:
    with pytest.raises(ValidationError, match="unlocated means the model read a value"):
        ExtractedField[float](status=FieldStatus.UNLOCATED)


@pytest.mark.parametrize("status", [FieldStatus.MISSING, FieldStatus.ILLEGIBLE])
def test_missing_and_illegible_cannot_carry_a_value(status: FieldStatus) -> None:
    """
    The doc-factory's own expectation, for both cases: "An extractor must
    report it as absent, not infer it" / "must report it as illegible, not
    guess". The ground truth's `value` for these fields is the *chart*
    truth, and putting it here would present chart truth as document
    content.
    """
    with pytest.raises(ValidationError, match="Putting the chart's value here"):
        ExtractedField[str](status=status, value="Everett, Massachusetts 02149")


@pytest.mark.parametrize("status", [FieldStatus.MISSING, FieldStatus.ILLEGIBLE])
def test_no_confidence_without_a_value(status: FieldStatus) -> None:
    with pytest.raises(ValidationError, match="no value to be confident about"):
        ExtractedField[str](status=status, confidence=0.9)


def test_missing_has_nothing_to_cite() -> None:
    with pytest.raises(ValidationError, match="nothing to cite"):
        ExtractedField[str](status=FieldStatus.MISSING, citation=located_citation())


def test_illegible_may_cite_the_ink_region() -> None:
    """The bbox of an illegible field is evidence that something is there,
    which is different from the value being located."""
    citation = Citation.located_document(
        source_id="intake-missing-allergy-006",
        field_path="patient.city_state_zip",
        quote="",
        bbox=BBox(page=1, x0=124.704, y0=176.79, x1=220.704, y1=187.71),
        extraction_id=EXTRACTION_ID,
    )
    field = ExtractedField[str].illegible(citation=citation)
    assert field.is_presentable is False
    assert field.citation is not None and field.citation.bbox is not None


def test_an_extracted_field_cites_a_document_not_the_chart() -> None:
    chart = Citation(
        source_type=SourceType.CHART,
        source_id="cond-1",
        page_or_section="Condition",
        field_or_chunk_id="display",
        quote_or_value="x",
    )
    with pytest.raises(ValidationError, match="cites a document"):
        ExtractedField[str](status=FieldStatus.UNLOCATED, value="x", citation=chart)


def test_generic_parameter_is_enforced() -> None:
    with pytest.raises(ValidationError):
        ExtractedField[float].located("eighty-nine", located_citation())


def test_field_is_frozen() -> None:
    """A mutable field could be moved to `located` after validation, without
    ever passing the validator that requires a bbox."""
    field = ExtractedField[float].unlocated(89.0)
    with pytest.raises(ValidationError):
        field.status = FieldStatus.LOCATED


# -- PresentedFact cannot be forged ----------------------------------------


def test_presented_fact_rejects_an_unlocated_citation() -> None:
    with pytest.raises(ValidationError, match="located document citation"):
        PresentedFact(value=89.0, citation=unlocated_citation())


def test_presented_fact_rejects_a_chart_citation() -> None:
    chart = Citation(
        source_type=SourceType.CHART,
        source_id="cond-1",
        page_or_section="Condition",
        field_or_chunk_id="display",
        quote_or_value="x",
    )
    with pytest.raises(ValidationError, match="located document citation"):
        PresentedFact(value="x", citation=chart)


# -- disclosure ------------------------------------------------------------


def test_unlocated_disclosure_keeps_the_value_under_a_name_that_says_so() -> None:
    disclosure = ExtractedField[float].unlocated(89.0).as_unsupported("results.cmp.glucose.value")
    assert disclosure is not None
    assert disclosure.reason is UnsupportedReason.NOT_LOCATED_ON_PAGE
    assert disclosure.unverified_value == "89.0"
    assert "value" not in {name for name in type(disclosure).model_fields if name == "value"}


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        (ExtractedField[str].missing(), UnsupportedReason.NOT_PRESENT_IN_DOCUMENT),
        (ExtractedField[str].illegible(), UnsupportedReason.ILLEGIBLE_ON_PAGE),
    ],
)
def test_disclosure_of_an_unread_field_carries_no_guess(
    field: ExtractedField[str], reason: UnsupportedReason
) -> None:
    disclosure = field.as_unsupported("patient.dob")
    assert disclosure is not None
    assert disclosure.reason is reason
    assert disclosure.unverified_value is None


def test_unsupported_field_refuses_a_guess_on_an_unread_field() -> None:
    with pytest.raises(ValidationError, match="rather than a guess"):
        UnsupportedField(
            field_path="patient.dob",
            reason=UnsupportedReason.NOT_PRESENT_IN_DOCUMENT,
            unverified_value="2014-01-24",
        )


def test_unsupported_not_located_cannot_carry_a_bbox() -> None:
    with pytest.raises(ValidationError, match="cannot carry a bbox"):
        UnsupportedField(
            field_path="results.cmp.glucose.value",
            reason=UnsupportedReason.NOT_LOCATED_ON_PAGE,
            unverified_value="89",
            citation=located_citation(),
        )
