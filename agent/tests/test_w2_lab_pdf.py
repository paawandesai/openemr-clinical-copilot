"""The `lab_pdf` schema: structure, self-consistency, and the per-row cross-check."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from app.models.w2.citations import BBox, Citation
from app.models.w2.documents import (
    DocumentChecksums,
    DocumentKind,
    DocumentPatientHeader,
    SourceDocument,
)
from app.models.w2.extraction import ExtractedField
from app.models.w2.lab_pdf import LabOrderHeader, LabPdfExtraction, LabResult, PerformingLab
from app.models.w2.lab_values import (
    AbnormalFlag,
    CrossCheckOutcome,
    NumericLabValue,
    ReferenceRange,
    ReportedFlag,
    parse_reference_range_text,
)

EXTRACTION_ID = "w2x-" + "2" * 32
OTHER_EXTRACTION_ID = "w2x-" + "3" * 32


def citation(field_path: str, quote: str, page: int = 1) -> Citation:
    return Citation.located_document(
        source_id="lab-clean-001",
        field_path=field_path,
        quote=quote,
        bbox=BBox(page=page, x0=270.0, y0=256.0, x1=280.0, y1=268.0),
        extraction_id=EXTRACTION_ID,
    )


def document(pages: int = 1) -> SourceDocument:
    return SourceDocument(
        document_id="lab-clean-001",
        kind=DocumentKind.LAB_PDF,
        filename="lab-clean-001.pdf",
        mime_type="application/pdf",
        page_count=pages,
        byte_length=31851,
        checksums=DocumentChecksums(sha256="c" * 64),
        has_text_layer=True,
    )


def header() -> DocumentPatientHeader:
    return DocumentPatientHeader(
        name=ExtractedField[str].located("Merlyn225 Mueller846", citation("patient.name", "M")),
        date_of_birth=ExtractedField[str].missing(),
        sex=ExtractedField[str].missing(),
        address=ExtractedField[str].missing(),
        city_state_zip=ExtractedField[str].missing(),
    )


def glucose(value: float = 89.0, flag: ReportedFlag = ReportedFlag.NOT_REPORTED) -> LabResult:
    return LabResult(
        panel="cmp",
        test_key="glucose",
        test_name=ExtractedField[str].located("Glucose", citation("...name", "Glucose")),
        value=ExtractedField[NumericLabValue].located(
            NumericLabValue(value=value), citation("...value", str(value))
        ),
        unit=ExtractedField[str].located("mg/dL", citation("...unit", "mg/dL")),
        reference_range=ExtractedField[ReferenceRange].located(
            parse_reference_range_text("70 - 99", unit="mg/dL"), citation("...range", "70 - 99")
        ),
        collection_date=ExtractedField[date].located(
            date(2026, 8, 30), citation("order.collected", "2026-08-30 07:12")
        ),
        reported_flag=ExtractedField[ReportedFlag].located(flag, citation("...flag", "")),
    )


def extraction(results: list[LabResult] | None = None, pages: int = 1) -> LabPdfExtraction:
    return LabPdfExtraction(
        extraction_id=EXTRACTION_ID,
        document=document(pages),
        patient_header=header(),
        results=results if results is not None else [glucose()],
    )


# -- structure --------------------------------------------------------------


def test_a_lab_extraction_describes_a_lab_document() -> None:
    with pytest.raises(ValidationError, match="describes a lab_pdf document"):
        LabPdfExtraction(
            extraction_id=EXTRACTION_ID,
            document=document().model_copy(update={"kind": DocumentKind.INTAKE_FORM}),
            patient_header=header(),
        )


def test_test_key_is_unique_within_a_document() -> None:
    with pytest.raises(ValidationError, match="repeated: \\['glucose'\\]"):
        extraction([glucose(), glucose(value=91.0)])


def test_every_citation_belongs_to_this_extraction() -> None:
    stray = LabResult(
        panel="cmp",
        test_key="bun",
        test_name=ExtractedField[str].located(
            "BUN",
            Citation.located_document(
                source_id="lab-clean-001",
                field_path="...name",
                quote="BUN",
                bbox=BBox(page=1, x0=1, y0=1, x1=2, y1=2),
                extraction_id=OTHER_EXTRACTION_ID,
            ),
        ),
        value=ExtractedField[NumericLabValue].missing(),
    )
    with pytest.raises(ValidationError, match="does not belong to this extraction"):
        extraction([stray])


def test_a_citation_cannot_point_past_the_last_page() -> None:
    off_page = LabResult(
        panel="cmp",
        test_key="bun",
        test_name=ExtractedField[str].located("BUN", citation("...name", "BUN", page=4)),
        value=ExtractedField[NumericLabValue].missing(),
    )
    with pytest.raises(ValidationError, match="page 4 of a 1-page document"):
        extraction([off_page])


def test_a_multipage_document_accepts_citations_on_later_pages() -> None:
    later = LabResult(
        panel="cbc",
        test_key="wbc",
        test_name=ExtractedField[str].located("WBC", citation("...name", "WBC", page=2)),
        value=ExtractedField[NumericLabValue].missing(),
    )
    assert len(extraction([later], pages=2).results) == 1


def test_an_extraction_that_found_nothing_is_representable() -> None:
    """A scan too degraded to read is a real outcome with zero presentable
    facts, not an object that refuses to exist."""
    empty = extraction([])
    assert empty.results == []
    assert empty.presentable_results() == []
    assert empty.flag_verification_failures() == []


def test_the_order_header_defaults_to_missing_not_absent() -> None:
    order = LabOrderHeader()
    assert order.collected_at.as_presented_fact() is None
    assert order.accession.as_presented_fact() is None


def test_performing_lab_requires_only_a_name() -> None:
    lab = PerformingLab(name=ExtractedField[str].located("Bay State", citation("lab.name", "B")))
    assert lab.clia.as_presented_fact() is None


# -- the row's own citation -------------------------------------------------


def test_a_rows_citation_is_the_one_on_its_value() -> None:
    row = glucose()
    assert row.citation is row.value.citation
    assert row.citation is not None
    assert row.citation.field_or_chunk_id == "...value"


def test_an_unlocated_value_leaves_the_row_with_no_citation_and_no_fact() -> None:
    row = LabResult(
        test_key="glucose",
        test_name=ExtractedField[str].missing(),
        value=ExtractedField[NumericLabValue].unlocated(NumericLabValue(value=89.0)),
    )
    assert row.citation is None
    assert row.presented_value() is None


# -- the cross-check --------------------------------------------------------


def test_a_row_cross_checks_its_own_flag() -> None:
    agreeing = glucose(value=89.0, flag=ReportedFlag.NORMAL)
    check = agreeing.cross_check_flag()
    assert check is not None
    assert check.outcome is CrossCheckOutcome.AGREE
    assert check.is_verification_failure is False


def test_a_misprinted_flag_is_a_verification_failure() -> None:
    wrong = glucose(value=111.0, flag=ReportedFlag.NORMAL)
    check = wrong.cross_check_flag()
    assert check is not None
    assert check.computed is AbnormalFlag.HIGH
    assert check.reported is ReportedFlag.NORMAL
    assert check.is_verification_failure is True

    failures = extraction([wrong]).flag_verification_failures()
    assert [row.test_key for row, _ in failures] == ["glucose"]


def test_a_blank_flag_column_leaves_the_row_uncontradicted() -> None:
    """D20: the computed flag stands alone, and a blank column is never
    counted as the report agreeing with it."""
    blank = glucose(value=111.0, flag=ReportedFlag.NOT_REPORTED)
    check = blank.cross_check_flag()
    assert check is not None
    assert check.computed is AbnormalFlag.HIGH
    assert check.outcome is CrossCheckOutcome.NOT_REPORTED
    assert check.is_verification_failure is False
    assert extraction([blank]).flag_verification_failures() == []


def test_an_unlocatable_flag_is_not_treated_as_something_the_report_said() -> None:
    """
    A flag the locate step could not place is not an observation about the
    document (invariant 4). The row still cross-checks — the computed flag
    stands alone — but nothing is compared against a value nobody found.
    """
    row = LabResult(
        panel="cmp",
        test_key="glucose",
        test_name=ExtractedField[str].missing(),
        value=ExtractedField[NumericLabValue].located(
            NumericLabValue(value=111.0), citation("...value", "111")
        ),
        reference_range=ExtractedField[ReferenceRange].located(
            parse_reference_range_text("70 - 99"), citation("...range", "70 - 99")
        ),
        reported_flag=ExtractedField[ReportedFlag].unlocated(ReportedFlag.NORMAL),
    )
    check = row.cross_check_flag()
    assert check is not None
    assert check.reported is ReportedFlag.NOT_REPORTED
    assert check.computed is AbnormalFlag.HIGH
    assert check.outcome is CrossCheckOutcome.NOT_REPORTED
    assert check.is_verification_failure is False


def test_a_row_with_no_locatable_value_is_not_cross_checked() -> None:
    """It is already disclosed as unsupported; a second failure for one
    underlying fault would just be noise."""
    row = LabResult(
        test_key="glucose",
        test_name=ExtractedField[str].missing(),
        value=ExtractedField[NumericLabValue].unlocated(NumericLabValue(value=111.0)),
        reference_range=ExtractedField[ReferenceRange].located(
            parse_reference_range_text("70 - 99"), citation("...range", "70 - 99")
        ),
        reported_flag=ExtractedField[ReportedFlag].located(
            ReportedFlag.NORMAL, citation("...flag", "N")
        ),
    )
    assert row.cross_check_flag() is None
    assert extraction([row]).flag_verification_failures() == []


def test_a_row_with_no_reference_range_is_not_computable_and_not_a_failure() -> None:
    row = LabResult(
        test_key="glucose",
        test_name=ExtractedField[str].missing(),
        value=ExtractedField[NumericLabValue].located(
            NumericLabValue(value=111.0), citation("...value", "111")
        ),
        reported_flag=ExtractedField[ReportedFlag].located(
            ReportedFlag.NORMAL, citation("...flag", "N")
        ),
    )
    check = row.cross_check_flag()
    assert check is not None
    assert check.computed is AbnormalFlag.INDETERMINATE
    assert check.is_verification_failure is False


def test_presentable_results_filters_on_the_value() -> None:
    located = glucose()
    unlocated = LabResult(
        test_key="bun",
        test_name=ExtractedField[str].missing(),
        value=ExtractedField[NumericLabValue].unlocated(NumericLabValue(value=15.0)),
    )
    assert [row.test_key for row in extraction([located, unlocated]).presentable_results()] == [
        "glucose"
    ]


def test_order_timestamps_are_typed() -> None:
    order = LabOrderHeader(
        collected_at=ExtractedField[datetime].located(
            datetime(2026, 8, 30, 7, 12), citation("order.collected", "2026-08-30 07:12")
        )
    )
    fact = order.collected_at.as_presented_fact()
    assert fact is not None and fact.value == datetime(2026, 8, 30, 7, 12)


def test_the_field_type_argument_is_enforced_at_the_row_boundary() -> None:
    """
    `ExtractedField[T]` is re-validated where it lands, not merely accepted
    because it is an ExtractedField. Without this, `ExtractedField[T]` would
    be decoration: a caller could hand a row's `value` a string field.
    """
    with pytest.raises(ValidationError):
        LabResult(
            test_key="glucose",
            test_name=ExtractedField[str].missing(),
            value=ExtractedField[str].unlocated("not a lab value"),
        )
