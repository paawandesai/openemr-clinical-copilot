"""
The `intake_form` schema and `Discrepancy` — D2.

The two tests that matter most are structural rather than about a value:
`test_the_module_exposes_no_merge_path` and
`test_intake_documents_cannot_be_written_back`. D2 says intake medications
and allergies are "never merged into the patient's medication or allergy
list, not even when they match, and not behind a confidence threshold", and
those two are what make that a property of the code rather than a promise.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

import app.models.w2.intake_form as intake_module
from app.models.w2.citations import BBox, Citation
from app.models.w2.documents import (
    DocumentChecksums,
    DocumentKind,
    DocumentPatientHeader,
    SourceDocument,
)
from app.models.w2.extraction import ExtractedField
from app.models.w2.intake_form import (
    Discrepancy,
    DiscrepancyCategory,
    DiscrepancyKind,
    FamilyHistoryEntry,
    IntakeAllergy,
    IntakeFormExtraction,
    IntakeMedication,
)
from app.models.w2.source_types import SourceType

EXTRACTION_ID = "w2x-" + "4" * 32


def doc_citation(field_path: str, quote: str | bool) -> Citation:
    return Citation.located_document(
        source_id="intake-clean-004",
        field_path=field_path,
        quote=quote,
        bbox=BBox(page=1, x0=59.0, y0=344.0, x1=206.0, y1=358.0),
        extraction_id=EXTRACTION_ID,
    )


def chart_citation(source_id: str = "med-1") -> Citation:
    return Citation(
        source_type=SourceType.CHART,
        source_id=source_id,
        page_or_section="MedicationRequest",
        field_or_chunk_id="display",
        quote_or_value="amLODIPine 2.5 MG Oral Tablet",
    )


def document() -> SourceDocument:
    return SourceDocument(
        document_id="intake-clean-004",
        kind=DocumentKind.INTAKE_FORM,
        filename="intake-clean-004.pdf",
        mime_type="application/pdf",
        page_count=2,
        byte_length=124606,
        checksums=DocumentChecksums(sha256="8" * 64),
        has_text_layer=True,
    )


def demographics() -> DocumentPatientHeader:
    return DocumentPatientHeader(
        name=ExtractedField[str].located("Jae222 Dach178", doc_citation("patient.name", "Jae222")),
        date_of_birth=ExtractedField[str].missing(),
        sex=ExtractedField[str].missing(),
        address=ExtractedField[str].missing(),
        city_state_zip=ExtractedField[str].missing(),
    )


def extraction(**overrides: object) -> IntakeFormExtraction:
    kwargs: dict[str, object] = {
        "extraction_id": EXTRACTION_ID,
        "document": document(),
        "demographics": demographics(),
    }
    kwargs.update(overrides)
    return IntakeFormExtraction(**kwargs)  # type: ignore[arg-type]


# -- D2, structurally -------------------------------------------------------


def test_the_module_exposes_no_merge_path() -> None:
    """
    D2 forbids intake data reaching the chart. The cheapest way for that to
    erode is a helpful `merge_into_chart()` added by someone who has not
    read D2, so assert the shape of the module's public surface.
    """
    forbidden = ("merge", "apply_to_chart", "reconcile", "to_fhir", "to_chart", "upsert")
    offenders = [
        name
        for name in dir(intake_module)
        if not name.startswith("_") and any(word in name.lower() for word in forbidden)
    ]
    assert offenders == [], f"D2: intake data is never merged into the chart; found {offenders}"


def test_intake_documents_cannot_be_written_back() -> None:
    """
    The other half: the only module that writes to OpenEMR refuses this
    document kind. Built with an otherwise entirely valid request, so the
    refusal is the document kind and not some other field failing first.
    """
    from app.models.w2.lab_values import AbnormalFlag, NumericLabValue
    from app.models.w2.writeback import (
        LabWriteBackRequest,
        LabWriteBackResult,
        lab_result_extraction_id,
        lab_result_idempotency_key,
    )

    digest = "a" * 128
    collected = date(2026, 8, 30)
    result = LabWriteBackResult(
        test_code="2345-7",
        test_name="Glucose",
        value=NumericLabValue(value=89.0),
        collection_date=collected,
        abnormal=AbnormalFlag.NORMAL,
        page=1,
        bbox=BBox(page=1, x0=270.0, y0=256.0, x1=280.0, y1=268.0),
        idempotency_key=lab_result_idempotency_key(
            document_sha3_512=digest, test_code="2345-7", collection_date=collected
        ),
        extraction_id=lab_result_extraction_id(
            document_sha3_512=digest, test_code="2345-7", collection_date=collected
        ),
    )
    payload: dict[str, object] = {
        "patient_uuid": "a2c373d1-c8c6-47f3-b566-e4ceaacdb1c7",
        "document": {
            "filename": "report.pdf",
            "mime_type": "application/pdf",
            "sha3_512": digest,
            "byte_length": 10,
            "content_base64": "eA==",
        },
        "results": [result],
        "extraction_id": EXTRACTION_ID,
        "correlation_id": "00000000-0000-4000-8000-000000000000",
    }
    # Identical payload, lab document: accepted.
    LabWriteBackRequest(**payload)  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="only lab values are written back"):
        LabWriteBackRequest(document_kind=DocumentKind.INTAKE_FORM, **payload)  # type: ignore[arg-type]


# -- structure --------------------------------------------------------------


def test_an_intake_extraction_describes_an_intake_document() -> None:
    with pytest.raises(ValidationError, match="describes a intake_form document"):
        extraction(document=document().model_copy(update={"kind": DocumentKind.LAB_PDF}))


def test_row_indices_are_unique() -> None:
    row = IntakeMedication(row_index=0, name=ExtractedField[str].missing())
    with pytest.raises(ValidationError, match="medications row_index values must be unique"):
        extraction(medications=[row, row])


def test_family_history_has_one_entry_per_condition() -> None:
    entry = FamilyHistoryEntry(
        condition="diabetes",
        present=ExtractedField[bool].located(True, doc_citation("family_history.diabetes", True)),
    )
    with pytest.raises(ValidationError, match="one entry per condition"):
        extraction(family_history=[entry, entry])


def test_no_known_drug_allergies_is_its_own_field() -> None:
    """A ticked box is an assertion; an empty list is an absence. A
    clinician needs to tell them apart."""
    asserted = extraction(
        no_known_drug_allergies=ExtractedField[bool].located(
            True, doc_citation("allergies.none_known", True)
        )
    )
    silent = extraction()
    assert asserted.no_known_drug_allergies.as_presented_fact() is not None
    assert silent.no_known_drug_allergies.as_presented_fact() is None
    assert asserted.allergies == silent.allergies == []


def test_an_unticked_box_is_a_located_false_not_a_missing_field() -> None:
    field = ExtractedField[bool].located(False, doc_citation("family_history.stroke", False))
    fact = field.as_presented_fact()
    assert fact is not None and fact.value is False


def test_route_exists_and_defaults_to_missing() -> None:
    medication = IntakeMedication(row_index=0, name=ExtractedField[str].missing())
    assert medication.route.as_presented_fact() is None


def test_an_allergy_reaction_is_kept_as_written() -> None:
    """On the local Synthea data this column holds severity, not a reaction.
    The field keeps what the page says and does not reinterpret it."""
    allergy = IntakeAllergy(
        row_index=0,
        substance=ExtractedField[str].located("Latex (substance)", doc_citation("a", "Latex")),
        reaction=ExtractedField[str].located("Mild", doc_citation("b", "Mild")),
    )
    fact = allergy.reaction.as_presented_fact()
    assert fact is not None and fact.value == "Mild"


def test_visit_and_signature_dates_are_typed() -> None:
    extracted = extraction(
        visit_date=ExtractedField[date].located(
            date(2026, 9, 10), doc_citation("visit.date", "09/10/2026")
        )
    )
    fact = extracted.visit_date.as_presented_fact()
    assert fact is not None and fact.value == date(2026, 9, 10)


# -- Discrepancy ------------------------------------------------------------


def test_added_has_a_document_side_only() -> None:
    discrepancy = Discrepancy(
        category=DiscrepancyCategory.MEDICATION,
        kind=DiscrepancyKind.ADDED,
        subject="ibuprofen",
        document_value="ibuprofen 200 MG",
        document_citation=doc_citation("medications[4].name", "ibuprofen"),
    )
    assert discrepancy.chart_value is None
    assert discrepancy.chart_citation is None


def test_omitted_has_a_chart_side_only() -> None:
    """The factory's intake-missing-allergy-006: the chart records an
    allergy to fish and the form omits it."""
    discrepancy = Discrepancy(
        category=DiscrepancyCategory.ALLERGY,
        kind=DiscrepancyKind.OMITTED,
        subject="Fish (substance)",
        chart_value="Fish (substance)",
        chart_citation=chart_citation("alg-9"),
    )
    assert discrepancy.document_value is None
    assert discrepancy.document_citation is None


def test_changed_needs_both_sides_and_a_real_difference() -> None:
    """The factory's med_discrepancy: the form says 10 MG, the chart
    product is 2.5 MG."""
    discrepancy = Discrepancy(
        category=DiscrepancyCategory.MEDICATION,
        kind=DiscrepancyKind.CHANGED,
        subject="amLODIPine",
        document_value="amLODIPine 10 MG",
        chart_value="amLODIPine 2.5 MG Oral Tablet",
        document_citation=doc_citation("medications[6].dose", "10 MG"),
        chart_citation=chart_citation(),
    )
    assert discrepancy.kind is DiscrepancyKind.CHANGED


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        (
            {"kind": DiscrepancyKind.ADDED, "document_value": "x", "chart_value": "y"},
            "the chart says nothing to cite",
        ),
        ({"kind": DiscrepancyKind.ADDED}, "the document says something"),
        ({"kind": DiscrepancyKind.OMITTED, "document_value": "x"}, "the chart says something"),
        (
            {"kind": DiscrepancyKind.CHANGED, "document_value": "x"},
            "both sources say something",
        ),
    ],
)
def test_each_kind_requires_its_own_sides(kwargs: dict[str, object], fragment: str) -> None:
    with pytest.raises(ValidationError, match=fragment):
        Discrepancy(category=DiscrepancyCategory.MEDICATION, subject="s", **kwargs)  # type: ignore[arg-type]


def test_equal_values_are_not_a_discrepancy() -> None:
    with pytest.raises(ValidationError, match="equal values are not a discrepancy"):
        Discrepancy(
            category=DiscrepancyCategory.MEDICATION,
            kind=DiscrepancyKind.CHANGED,
            subject="lisinopril",
            document_value="lisinopril 10 MG",
            chart_value="lisinopril 10 MG",
            document_citation=doc_citation("m", "lisinopril"),
            chart_citation=chart_citation(),
        )


def test_the_document_side_must_be_a_document_citation() -> None:
    with pytest.raises(ValidationError, match="must be a document citation"):
        Discrepancy(
            category=DiscrepancyCategory.MEDICATION,
            kind=DiscrepancyKind.ADDED,
            subject="ibuprofen",
            document_value="ibuprofen",
            document_citation=chart_citation(),
        )


def test_the_chart_side_must_be_a_chart_citation() -> None:
    with pytest.raises(ValidationError, match="must be a chart citation"):
        Discrepancy(
            category=DiscrepancyCategory.ALLERGY,
            kind=DiscrepancyKind.OMITTED,
            subject="Fish",
            chart_value="Fish (substance)",
            chart_citation=doc_citation("a", "Fish"),
        )


def test_a_guideline_can_never_be_either_side() -> None:
    """Invariant 3 at the place the two are most likely to be confused."""
    guideline = Citation(
        source_type=SourceType.GUIDELINE,
        source_id="g-1",
        page_or_section="§4",
        field_or_chunk_id="chunk-7",
        quote_or_value="Reconcile medications at every visit.",
    )
    with pytest.raises(ValidationError, match="must be a document citation"):
        Discrepancy(
            category=DiscrepancyCategory.MEDICATION,
            kind=DiscrepancyKind.ADDED,
            subject="x",
            document_value="x",
            document_citation=guideline,
        )
    with pytest.raises(ValidationError, match="must be a chart citation"):
        Discrepancy(
            category=DiscrepancyCategory.MEDICATION,
            kind=DiscrepancyKind.OMITTED,
            subject="x",
            chart_value="x",
            chart_citation=guideline,
        )


def test_a_discrepancy_has_no_resolution_field() -> None:
    """D2: the co-pilot shows where the two disagree; it does not pick a
    winner. There is nowhere to record one."""
    fields = set(Discrepancy.model_fields)
    assert not fields & {"resolution", "winner", "preferred", "confidence", "auto_merge"}
