"""
The locate step decides what may be presented as fact (D3, D13, D24).

Hand-built pages and transcriptions, no PDFs: each test states the words
on the page and what the model said, and checks the ExtractedField the
worker produced from them.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.extraction import MatchKind, TextSource, build_page_words
from app.extraction.geometry import BBox as GeometryBox
from app.extraction.words import DocumentWords
from app.models.w2.documents import DocumentChecksums, DocumentKind, SourceDocument
from app.models.w2.extraction import FieldStatus, UnsupportedReason
from app.models.w2.lab_values import CrossCheckOutcome, NumericLabValue, ReportedFlag
from app.workers.intake_extractor.assemble import assemble_intake, assemble_lab
from app.workers.intake_extractor.confidence import TIER_WEIGHT, extraction_confidence
from app.workers.intake_extractor.vision_schema import (
    RawAllergy,
    RawCheckbox,
    RawField,
    RawIntakeDocument,
    RawLabDocument,
    RawLabResult,
    RawMedication,
    RawOrder,
    RawPatient,
    RawPerformingLab,
)

EXTRACTION_ID = "w2x-" + "a" * 32
lab_document = SimpleNamespace(extraction_id=EXTRACTION_ID)

ABSENT = RawField(state="absent", text="", page=None)


def present(text: str, page: int = 1) -> RawField:
    return RawField(state="present", text=text, page=page)


def source_document(
    page_count: int = 1, kind: DocumentKind = DocumentKind.LAB_PDF
) -> SourceDocument:
    return SourceDocument(
        document_id="doc-1",
        kind=kind,
        filename="x.pdf",
        mime_type="application/pdf",
        page_count=page_count,
        byte_length=100,
        checksums=DocumentChecksums(sha3_512="b" * 128),
        has_text_layer=True,
    )


def page_of(
    rows: list[list[str]],
    *,
    page: int = 1,
    source: TextSource = TextSource.TEXT_LAYER,
    confidence: float = 1.0,
):
    items = []
    for row_number, texts in enumerate(rows):
        y = 100.0 + 20.0 * row_number
        x = 50.0
        for text in texts:
            width = 7.0 * len(text)
            items.append((text, GeometryBox(page, x, y, x + width, y + 10.0), confidence))
            x += width + 14.0
    return build_page_words(page=page, width=612, height=792, items=items, source=source)


def words(*pages) -> DocumentWords:  # noqa: ANN002
    return DocumentWords(pages=tuple(pages))


def raw_row(
    key: str,
    *,
    name: str,
    value: str,
    unit: str | None = None,
    reference_range: str | None = None,
    flag: str | None = None,
    page: int = 1,
) -> RawLabResult:
    def field(text: str | None) -> RawField:
        return ABSENT if text is None else present(text, page)

    return RawLabResult(
        panel="cmp",
        test_key=key,
        test_name=present(name, page),
        value=present(value, page),
        unit=field(unit),
        reference_range=field(reference_range),
        flag=field(flag),
    )


def raw_patient(**fields: RawField) -> RawPatient:
    names = ("name", "date_of_birth", "sex", "address", "city_state_zip", "mrn")
    return RawPatient(**{name: fields.get(name, ABSENT) for name in names})


def raw_lab(rows: list[RawLabResult], **order: RawField) -> RawLabDocument:
    order_names = (
        "accession",
        "specimen",
        "collected_at",
        "received_at",
        "reported_at",
        "ordering_provider",
    )
    return RawLabDocument(
        patient=raw_patient(),
        performing_lab=RawPerformingLab(name=ABSENT, address=ABSENT, clia=ABSENT, director=ABSENT),
        order=RawOrder(**{name: order.get(name, ABSENT) for name in order_names}),
        results=rows,
    )


def assemble(raw: RawLabDocument, document_words: DocumentWords, pages: int = 1):  # noqa: ANN201
    return assemble_lab(raw, document_words, source_document(pages), EXTRACTION_ID)


# --------------------------------------------------------------------------


def test_located_value_carries_bbox_and_extraction_id() -> None:
    page = page_of([["Glucose", "89", "mg/dL", "70", "-", "99"]])
    raw = raw_lab(
        [raw_row("glucose", name="Glucose", value="89", unit="mg/dL", reference_range="70 - 99")]
    )
    result = assemble(raw, words(page)).extraction.results[0]

    assert result.value.status is FieldStatus.LOCATED
    fact = result.value.require_presented_fact()
    assert fact.value == NumericLabValue(value=89.0)
    assert fact.citation.bbox is not None and fact.citation.bbox.page == 1
    assert fact.citation.extraction_id == EXTRACTION_ID
    assert fact.citation.quote_or_value == "89"
    assert fact.citation.field_or_chunk_id == "results.cmp.glucose.value"
    assert result.reference_range.require_presented_fact().value.text == "70 - 99"
    assert result.value.confidence == 1.0


def test_ambiguous_is_unlocated_not_located_on_page() -> None:
    # "5" twice in the row the label heads, and no other value to narrow it:
    # locate refuses to choose, and the worker maps that onto D13.
    page = page_of([["Potassium", "5", "5"]])
    raw = raw_lab([raw_row("k", name="Potassium", value="5")])
    assembled = assemble(raw, words(page))
    value = assembled.extraction.results[0].value

    assert value.status is FieldStatus.UNLOCATED
    assert value.as_presented_fact() is None
    unsupported = value.as_unsupported("results[0].value")
    assert unsupported is not None
    assert unsupported.reason is UnsupportedReason.NOT_LOCATED_ON_PAGE
    record = next(r for r in assembled.records if r.schema_path == "results[].value")
    assert record.locate_status.value == "ambiguous"
    assert value.confidence == 0.0


def test_not_located_is_unlocated_never_missing_or_illegible() -> None:
    page = page_of([["Sodium", "140"]])
    raw = raw_lab([raw_row("na", name="Sodium", value="141")])
    value = assemble(raw, words(page)).extraction.results[0].value

    assert value.status is FieldStatus.UNLOCATED
    assert value.as_unsupported("x").reason is UnsupportedReason.NOT_LOCATED_ON_PAGE
    assert value.citation is None or value.citation.bbox is None


def test_absent_and_illegible_come_only_from_the_transcription() -> None:
    page = page_of([["Sodium", "140"]])
    row = RawLabResult(
        panel=None,
        test_key="na",
        test_name=present("Sodium"),
        value=RawField(state="illegible", text="", page=1),
        unit=ABSENT,
        reference_range=ABSENT,
        flag=ABSENT,
    )
    result = assemble(raw_lab([row]), words(page)).extraction.results[0]
    assert result.value.status is FieldStatus.ILLEGIBLE
    assert result.value.value is None
    assert result.unit.status is FieldStatus.MISSING


def test_blank_flag_is_missing_and_cross_checks_as_not_reported() -> None:
    page = page_of([["Glucose", "89", "mg/dL", "70", "-", "99"]])
    raw = raw_lab(
        [
            raw_row(
                "glucose",
                name="Glucose",
                value="89",
                unit="mg/dL",
                reference_range="70 - 99",
                flag="",
            )
        ]
    )
    result = assemble(raw, words(page)).extraction.results[0]
    assert result.reported_flag.status is FieldStatus.MISSING
    check = result.cross_check_flag()
    assert check is not None
    assert check.reported is ReportedFlag.NOT_REPORTED
    assert check.outcome is CrossCheckOutcome.NOT_REPORTED  # never `agree` with a blank


def test_printed_flag_is_located_and_compared() -> None:
    page = page_of([["Glucose", "142", "H", "mg/dL", "70", "-", "99"]])
    raw = raw_lab(
        [
            raw_row(
                "glucose",
                name="Glucose",
                value="142",
                unit="mg/dL",
                reference_range="70 - 99",
                flag="H",
            )
        ]
    )
    result = assemble(raw, words(page)).extraction.results[0]
    assert result.reported_flag.require_presented_fact().value is ReportedFlag.HIGH
    assert result.cross_check_flag().outcome is CrossCheckOutcome.AGREE


def test_value_found_only_on_another_page_is_unlocated() -> None:
    # W2-C6: the model says page 1; the value exists only on page 2.
    one = page_of([["Glucose", "70", "-", "99"]], page=1)
    two = page_of([["Glucose", "89"]], page=2)
    raw = raw_lab([raw_row("glucose", name="Glucose", value="89", page=1)])
    value = assemble(raw, words(one, two), pages=2).extraction.results[0].value
    assert value.status is FieldStatus.UNLOCATED


def test_page_outside_the_document_is_unlocated_without_a_citation() -> None:
    page = page_of([["Glucose", "89"]])
    raw = raw_lab([raw_row("glucose", name="Glucose", value="89", page=7)])
    value = assemble(raw, words(page)).extraction.results[0].value
    assert value.status is FieldStatus.UNLOCATED
    assert value.citation is None


def test_row_label_printed_twice_is_resolved_by_the_rows_values() -> None:
    # "Glucose" heads its own row and ends "Estimated Average Glucose".
    page = page_of(
        [
            ["Glucose", "89", "mg/dL"],
            ["Estimated", "Average", "Glucose", "117", "mg/dL"],
        ]
    )
    raw = raw_lab([raw_row("glucose", name="Glucose", value="89", unit="mg/dL")])
    result = assemble(raw, words(page)).extraction.results[0]
    assert result.test_name.status is FieldStatus.LOCATED
    assert result.value.status is FieldStatus.LOCATED
    assert result.unit.status is FieldStatus.LOCATED
    assert result.unit.citation.bbox.y0 == pytest.approx(100.0)


def test_row_label_that_rows_cannot_separate_stays_unlocated() -> None:
    page = page_of([["Glucose", "89", "mg/dL"], ["Glucose", "89", "mg/dL"]])
    raw = raw_lab([raw_row("glucose", name="Glucose", value="89", unit="mg/dL")])
    result = assemble(raw, words(page)).extraction.results[0]
    assert result.test_name.status is FieldStatus.UNLOCATED
    assert result.value.status is FieldStatus.UNLOCATED


def test_unparseable_date_is_illegible_and_listed() -> None:
    page = page_of([["Collected", "3O/O8/2026"]])
    raw = raw_lab([], collected_at=present("3O/O8/2026"))
    assembled = assemble(raw, words(page))
    assert assembled.extraction.order.collected_at.status is FieldStatus.ILLEGIBLE
    assert assembled.parse_failures == ["order.collected_at"]


def test_collection_date_follows_the_located_collection_time() -> None:
    page = page_of([["Collected", "2026-08-30", "07:12"], ["Glucose", "89"]])
    raw = raw_lab(
        [raw_row("glucose", name="Glucose", value="89")],
        collected_at=present("2026-08-30 07:12"),
    )
    extraction = assemble(raw, words(page)).extraction
    assert extraction.order.collected_at.status is FieldStatus.LOCATED
    fact = extraction.results[0].collection_date.require_presented_fact()
    assert fact.value.isoformat() == "2026-08-30"


def test_day_first_only_date_is_not_reinterpreted() -> None:
    page = page_of([["Visit", "25/09/2026"]])
    raw = RawIntakeDocument(
        patient=raw_patient(),
        visit_date=present("25/09/2026"),
        chief_concern=ABSENT,
        medications=[],
        no_known_drug_allergies=RawCheckbox(label="", state="absent", page=None),
        allergies=[],
        family_history=[],
        signature_name=ABSENT,
        signature_date=ABSENT,
    )
    assembled = assemble_intake(
        raw, words(page), source_document(kind=DocumentKind.INTAKE_FORM), EXTRACTION_ID
    )
    assert assembled.extraction.visit_date.status is FieldStatus.ILLEGIBLE
    assert assembled.parse_failures == ["visit_date"]


def test_ocr_confidence_flows_into_field_confidence() -> None:
    page = page_of([["Sodium", "140"]], source=TextSource.OCR, confidence=0.8)
    raw = raw_lab([raw_row("na", name="Sodium", value="140")])
    value = assemble(raw, words(page)).extraction.results[0].value
    assert value.status is FieldStatus.LOCATED
    assert value.confidence == pytest.approx(0.8)


def test_confidence_definition() -> None:
    assert extraction_confidence(1.0, MatchKind.EXACT, 1.0) == 1.0
    assert extraction_confidence(1.0, MatchKind.NUMBER_ONLY, 1.0) == 0.9
    assert extraction_confidence(0.91, MatchKind.FUZZY, 0.88) == pytest.approx(
        0.91 * 0.8 * 0.88, abs=1e-4
    )
    # A fuzzy match can never outrank an exact one read at the same confidence.
    assert max(TIER_WEIGHT.values()) == TIER_WEIGHT[MatchKind.EXACT]
    assert min(TIER_WEIGHT.values()) == TIER_WEIGHT[MatchKind.FUZZY]


def _intake(**overrides) -> RawIntakeDocument:  # noqa: ANN003
    base = {
        "patient": raw_patient(),
        "visit_date": ABSENT,
        "chief_concern": ABSENT,
        "medications": [],
        "no_known_drug_allergies": RawCheckbox(label="", state="absent", page=None),
        "allergies": [],
        "family_history": [],
        "signature_name": ABSENT,
        "signature_date": ABSENT,
    }
    return RawIntakeDocument(**{**base, **overrides})


def test_ticked_checkbox_is_located_on_its_mark_and_unticked_is_not_asserted() -> None:
    page = page_of([["X", "No", "known", "drug", "allergies"]])
    ticked = assemble_intake(
        _intake(
            no_known_drug_allergies=RawCheckbox(
                label="No known drug allergies", state="ticked", page=1
            )
        ),
        words(page),
        source_document(kind=DocumentKind.INTAKE_FORM),
        EXTRACTION_ID,
    ).extraction.no_known_drug_allergies
    assert ticked.require_presented_fact().value is True
    assert ticked.citation.quote_or_value == "X"

    blank = page_of([["No", "known", "drug", "allergies"]])
    unticked = assemble_intake(
        _intake(
            no_known_drug_allergies=RawCheckbox(
                label="No known drug allergies", state="unticked", page=1
            )
        ),
        words(blank),
        source_document(kind=DocumentKind.INTAKE_FORM),
        EXTRACTION_ID,
    ).extraction.no_known_drug_allergies
    assert unticked.status is FieldStatus.UNLOCATED  # no ink to place a False on


def test_model_saying_ticked_without_a_mark_is_not_a_fact() -> None:
    page = page_of([["No", "known", "drug", "allergies"]])
    field = assemble_intake(
        _intake(
            no_known_drug_allergies=RawCheckbox(
                label="No known drug allergies", state="ticked", page=1
            )
        ),
        words(page),
        source_document(kind=DocumentKind.INTAKE_FORM),
        EXTRACTION_ID,
    ).extraction.no_known_drug_allergies
    assert field.status is FieldStatus.UNLOCATED


def test_intake_rows_anchor_their_dose_and_reaction() -> None:
    page = page_of(
        [
            ["Acetaminophen", "160", "MG", "at", "bedtime"],
            ["Acetaminophen", "325", "MG", "three", "times", "daily"],
            ["Latex", "(substance)", "Mild"],
            ["Mold", "(organism)", "Severe"],
        ]
    )
    raw = _intake(
        medications=[
            RawMedication(
                name=present("Acetaminophen"),
                dose=present("325 MG"),
                frequency=present("three times daily"),
                route=ABSENT,
            )
        ],
        allergies=[RawAllergy(substance=present("Mold (organism)"), reaction=present("Severe"))],
    )
    extraction = assemble_intake(
        raw, words(page), source_document(kind=DocumentKind.INTAKE_FORM), EXTRACTION_ID
    ).extraction
    medication = extraction.medications[0]
    assert medication.name.status is FieldStatus.LOCATED
    assert medication.name.citation.bbox.y0 == pytest.approx(120.0)  # the 325 MG row
    assert medication.dose.status is FieldStatus.LOCATED
    assert extraction.allergies[0].reaction.status is FieldStatus.LOCATED
    assert extraction.allergies[0].reaction.citation.bbox.y0 == pytest.approx(160.0)
