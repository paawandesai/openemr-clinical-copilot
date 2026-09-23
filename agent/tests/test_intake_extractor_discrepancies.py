"""
Intake vs chart discrepancies (D2): added / omitted / changed, a citation
on each side that exists, derived renderings never a discrepancy, and
nothing written anywhere.
"""

from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path

from app.models.fhir_resources import AllergyIntolerance, MedicationRequest
from app.models.w2.documents import DocumentKind
from app.models.w2.intake_form import DiscrepancyCategory, DiscrepancyKind
from app.models.w2.source_types import SourceType
from app.workers.intake_extractor.assemble import assemble_intake
from app.workers.intake_extractor.chart import ChartSnapshot
from app.workers.intake_extractor.discrepancies import compare_intake_to_chart
from app.workers.intake_extractor.vision_schema import RawAllergy, RawField, RawMedication
from tests.test_intake_extractor_assemble import (
    ABSENT,
    EXTRACTION_ID,
    _intake,
    page_of,
    present,
    source_document,
    words,
)

PACKAGE = Path(__file__).resolve().parents[1] / "app/workers/intake_extractor"


def med(source_id: str, display: str | None, status: str = "active") -> MedicationRequest:
    return MedicationRequest(
        source_id=source_id,
        display=display,
        status=status,
        rxnorm_code=None,
        authored_date=datetime(2020, 1, 1),
    )


def allergy(source_id: str, display: str | None, status: str = "active") -> AllergyIntolerance:
    return AllergyIntolerance(
        source_id=source_id, display=display, clinical_status=status, recorded_date=None
    )


def chart(medications=(), allergies=()) -> ChartSnapshot:  # noqa: ANN001
    return ChartSnapshot(identity=None, medications=list(medications), allergies=list(allergies))


def extract(rows: list[list[str]], medications=(), allergies=()):  # noqa: ANN001, ANN201
    raw = _intake(
        medications=[
            RawMedication(
                name=present(name) if isinstance(name, str) else name,
                dose=present(dose) if dose else ABSENT,
                frequency=ABSENT,
                route=ABSENT,
            )
            for name, dose in medications
        ],
        allergies=[
            RawAllergy(
                substance=present(substance) if isinstance(substance, str) else substance,
                reaction=ABSENT,
            )
            for substance in allergies
        ],
    )
    return assemble_intake(
        raw, words(page_of(rows)), source_document(kind=DocumentKind.INTAKE_FORM), EXTRACTION_ID
    ).extraction


def test_derived_short_name_is_not_a_discrepancy() -> None:
    extraction = extract(
        [["Simvastatin", "10", "MG"], ["Fexofenadine", "hydrochloride", "30", "MG"]],
        medications=[("Simvastatin", "10 MG"), ("Fexofenadine hydrochloride", "30 MG")],
    )
    report = compare_intake_to_chart(
        extraction,
        chart(
            [
                med("m1", "Simvastatin 10 MG Oral Tablet"),
                med("m2", "Fexofenadine hydrochloride 30 MG Oral Tablet"),
            ]
        ),
    )
    assert report.discrepancies == []
    assert report.medications_complete


def test_dose_change_is_changed_with_both_citations() -> None:
    extraction = extract([["amLODIPine", "10", "MG"]], medications=[("amLODIPine", "10 MG")])
    report = compare_intake_to_chart(
        extraction, chart([med("m1", "amLODIPine 2.5 MG Oral Tablet")])
    )
    (found,) = report.discrepancies
    assert found.kind is DiscrepancyKind.CHANGED
    assert found.category is DiscrepancyCategory.MEDICATION
    assert found.document_citation.source_type is SourceType.DOCUMENT
    assert found.document_citation.bbox is not None
    assert found.document_citation.field_or_chunk_id == "medications[0].dose"
    assert found.chart_citation.source_type is SourceType.CHART
    assert found.chart_citation.page_or_section == "MedicationRequest"
    assert found.chart_citation.source_id == "m1"


def test_added_and_omitted() -> None:
    extraction = extract(
        [["Ibuprofen", "200", "MG"]],
        medications=[("Ibuprofen", "200 MG")],
        allergies=[],
    )
    report = compare_intake_to_chart(
        extraction,
        chart([med("m1", "lisinopril 10 MG Oral Tablet")], [allergy("a1", "Fish (substance)")]),
    )
    kinds = sorted((d.category.value, d.kind.value) for d in report.discrepancies)
    assert kinds == [("allergy", "omitted"), ("medication", "added"), ("medication", "omitted")]
    added = next(d for d in report.discrepancies if d.kind is DiscrepancyKind.ADDED)
    assert added.document_citation is not None and added.chart_citation is None
    omitted = next(d for d in report.discrepancies if d.category is DiscrepancyCategory.ALLERGY)
    assert omitted.chart_citation.page_or_section == "AllergyIntolerance"
    assert omitted.document_citation is None  # there is no page to point at


def test_two_rows_of_one_drug_pair_with_their_own_strengths() -> None:
    extraction = extract(
        [["Acetaminophen", "160", "MG"], ["Acetaminophen", "325", "MG"]],
        medications=[("Acetaminophen", "325 MG"), ("Acetaminophen", "160 MG")],
    )
    report = compare_intake_to_chart(
        extraction,
        chart(
            [
                med("m1", "Acetaminophen 160 MG Chewable Tablet"),
                med("m2", "Acetaminophen 325 MG Oral Tablet"),
            ]
        ),
    )
    assert report.discrepancies == []


def test_ratio_strength_written_as_an_amount_is_not_changed() -> None:
    extraction = extract(
        [["sodium", "fluoride", "0.0272", "MG"]], medications=[("sodium fluoride", "0.0272 MG")]
    )
    report = compare_intake_to_chart(
        extraction, chart([med("m1", "sodium fluoride 0.0272 MG/MG Oral Gel")])
    )
    assert report.discrepancies == []


def test_allergen_suffix_is_not_a_discrepancy() -> None:
    extraction = extract([["Latex"]], allergies=["Latex"])
    report = compare_intake_to_chart(
        extraction, chart(allergies=[allergy("a1", "Latex (substance)")])
    )
    assert report.discrepancies == []


def test_unlocated_row_makes_the_omission_unresolved_not_asserted() -> None:
    # The model read "Lisinopril" but it is not on the page: the check cannot
    # say the form omits lisinopril, and it cannot say the form lists it.
    extraction = extract([["Metformin"]], medications=[("Lisinopril", None)])
    report = compare_intake_to_chart(extraction, chart([med("m1", "lisinopril 10 MG Oral Tablet")]))
    assert report.discrepancies == []
    assert [u.chart_citation.source_id for u in report.unresolved] == ["m1"]
    assert not report.medications_complete
    assert report.intake_rows_not_compared == 1


def test_illegible_row_makes_every_omission_unresolved() -> None:
    extraction = extract(
        [["Metformin", "500", "MG"]],
        medications=[
            ("Metformin", "500 MG"),
            (RawField(state="illegible", text="", page=1), None),
        ],
    )
    report = compare_intake_to_chart(
        extraction,
        chart([med("m1", "Metformin 500 MG Oral Tablet"), med("m2", "lisinopril 10 MG")]),
    )
    assert report.discrepancies == []
    assert [u.chart_citation.source_id for u in report.unresolved] == ["m2"]


def test_stopped_chart_medications_are_not_expected_on_the_form() -> None:
    extraction = extract([["Metformin"]], medications=[("Metformin", None)])
    report = compare_intake_to_chart(
        extraction,
        chart([med("m1", "Metformin 500 MG"), med("m2", "Warfarin 5 MG", status="stopped")]),
    )
    assert report.discrepancies == []


def test_uncoded_chart_record_is_unresolved_never_unspecified() -> None:
    extraction = extract([["Metformin"]], medications=[("Metformin", None)])
    report = compare_intake_to_chart(
        extraction, chart([med("m1", "Metformin 500 MG"), med("m2", None)])
    )
    assert report.discrepancies == []
    assert [u.why for u in report.unresolved] == ["the chart record has no usable name"]


def test_worker_package_has_no_write_path() -> None:
    # D2 / invariant 5: nothing here writes to OpenEMR. The only FHIR calls
    # are the three Week 1 reads, and nothing imports the write-back module.
    allowed_fhir = {"get_patient_identity", "get_medication_requests", "get_allergy_intolerances"}
    for source in PACKAGE.glob("*.py"):
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module != "app.models.w2.writeback", source.name
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id == "fhir_client":
                    assert node.attr in allowed_fhir, (source.name, node.attr)
            if isinstance(node, ast.Name):
                assert not node.id.lower().startswith(("post_", "put_", "write_")), source.name
