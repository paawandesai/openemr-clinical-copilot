"""
The document-level abnormal-flag cross-check (D11, D20) and the
wrong-patient flag.
"""

from __future__ import annotations

from datetime import date

from app.models.w2.lab_values import AbnormalFlag, ReportedFlag
from app.workers.intake_extractor.assemble import assemble_lab
from app.workers.intake_extractor.chart import PatientIdentity
from app.workers.intake_extractor.checks import cross_check_report
from app.workers.intake_extractor.identity import IdentityComparison, check_identity
from tests.test_intake_extractor_assemble import (
    EXTRACTION_ID,
    page_of,
    present,
    raw_lab,
    raw_patient,
    raw_row,
    source_document,
    words,
)


def _lab(rows_on_page: list[list[str]], raw_rows: list) -> object:  # noqa: ANN001
    return assemble_lab(
        raw_lab(raw_rows), words(page_of(rows_on_page)), source_document(), EXTRACTION_ID
    ).extraction


def test_disagreement_is_a_surfaced_verification_failure() -> None:
    # The page prints H on a value inside its range: one of the two misread
    # the page, and the check says so rather than choosing.
    extraction = _lab(
        [["Glucose", "89", "H", "mg/dL", "70", "-", "99"]],
        [
            raw_row(
                "glucose",
                name="Glucose",
                value="89",
                unit="mg/dL",
                reference_range="70 - 99",
                flag="H",
            )
        ],
    )
    summary = cross_check_report(extraction)
    assert summary.disagree == 1
    assert summary.has_verification_failure
    (failure,) = summary.disagreements
    assert failure.reported is ReportedFlag.HIGH
    assert failure.computed is AbnormalFlag.NORMAL
    assert failure.value_citation is not None and failure.value_citation.bbox is not None
    # Neither side was rewritten: the printed flag is still what the page says.
    assert extraction.results[0].reported_flag.value is ReportedFlag.HIGH


def test_blank_flags_never_count_as_agreement() -> None:
    extraction = _lab(
        [["Sodium", "140", "mmol/L", "135", "-", "145"]],
        [
            raw_row(
                "na",
                name="Sodium",
                value="140",
                unit="mmol/L",
                reference_range="135 - 145",
                flag="",
            )
        ],
    )
    summary = cross_check_report(extraction)
    assert summary.agree == 0
    assert summary.not_reported == 1
    assert summary.comparable == 0
    assert summary.agreement_rate is None  # 0/0 is not 100%


def test_agreement_rate_carries_its_denominator() -> None:
    extraction = _lab(
        [
            ["Glucose", "142", "H", "mg/dL", "70", "-", "99"],
            ["Sodium", "140", "mmol/L", "135", "-", "145"],
        ],
        [
            raw_row(
                "glucose",
                name="Glucose",
                value="142",
                unit="mg/dL",
                reference_range="70 - 99",
                flag="H",
            ),
            raw_row(
                "na",
                name="Sodium",
                value="140",
                unit="mmol/L",
                reference_range="135 - 145",
                flag="",
            ),
        ],
    )
    summary = cross_check_report(extraction)
    assert (summary.agree, summary.comparable, summary.not_reported) == (1, 1, 1)
    assert summary.agreement_rate == 1.0


def test_computed_abnormal_with_no_printed_flag_is_kept() -> None:
    # W2-D3: out of range, flag column blank. Not a disagreement, not lost.
    extraction = _lab(
        [["Potassium", "6.9", "mmol/L", "3.5", "-", "5.1"]],
        [
            raw_row(
                "k",
                name="Potassium",
                value="6.9",
                unit="mmol/L",
                reference_range="3.5 - 5.1",
                flag="",
            )
        ],
    )
    summary = cross_check_report(extraction)
    assert summary.disagree == 0
    assert [(u.test_key, u.computed) for u in summary.computed_abnormal_unflagged] == [
        ("k", AbnormalFlag.HIGH)
    ]


def test_unlocated_value_confirms_nothing() -> None:
    extraction = _lab(
        [["Glucose", "H", "mg/dL", "70", "-", "99"]],  # the value is not on the page
        [
            raw_row(
                "glucose",
                name="Glucose",
                value="142",
                unit="mg/dL",
                reference_range="70 - 99",
                flag="H",
            )
        ],
    )
    summary = cross_check_report(extraction)
    assert summary.checked == 0 and summary.agree == 0


# -- identity ------------------------------------------------------------------


def _header(name: str, dob: str | None = None):  # noqa: ANN202
    row = ["Name", *name.split()]
    rows = [row] + ([["DOB", dob]] if dob else [])
    raw = raw_lab([])
    raw = raw.model_copy(
        update={
            "patient": raw_patient(
                name=present(name), **({"date_of_birth": present(dob)} if dob else {})
            )
        }
    )
    return assemble_lab(raw, words(page_of(rows)), source_document(), EXTRACTION_ID).extraction


CHART = PatientIdentity(
    source_id="p1",
    given=("Merlyn225", "Sanda877"),
    family="Mueller846",
    birth_date=date(1960, 9, 25),
)


def test_same_patient_matches_in_any_word_order() -> None:
    check = check_identity(_header("Mueller846, Merlyn225", "1960-09-25").patient_header, CHART)
    assert check.name is IdentityComparison.MATCH
    assert check.date_of_birth is IdentityComparison.MATCH
    assert not check.hard_flag


def test_wrong_patient_name_is_a_hard_flag() -> None:
    check = check_identity(_header("Dana Whitfield").patient_header, CHART)
    assert check.name is IdentityComparison.MISMATCH
    assert check.hard_flag
    assert check.document_name_citation is not None


def test_wrong_birth_date_is_a_hard_flag() -> None:
    check = check_identity(_header("Merlyn225 Mueller846", "1961-09-25").patient_header, CHART)
    assert check.date_of_birth is IdentityComparison.MISMATCH
    assert check.hard_flag


def test_unlocated_name_is_not_comparable_never_a_match() -> None:
    raw = raw_lab([]).model_copy(update={"patient": raw_patient(name=present("Dana Whitfield"))})
    header = assemble_lab(
        raw, words(page_of([["Name", "Someone", "Else"]])), source_document(), EXTRACTION_ID
    ).extraction.patient_header
    check = check_identity(header, CHART)
    assert check.name is IdentityComparison.NOT_COMPARABLE
    assert not check.hard_flag


def test_no_chart_is_not_comparable() -> None:
    check = check_identity(_header("Dana Whitfield").patient_header, None)
    assert check.name is IdentityComparison.NOT_COMPARABLE
    assert not check.hard_flag
