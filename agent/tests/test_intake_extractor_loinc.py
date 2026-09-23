"""
The committed LOINC table and its matching rules (app/workers/intake_extractor/loinc.py).
"""

from __future__ import annotations

import pytest

from app.models.w2.citations import BBox, Citation
from app.models.w2.extraction import ExtractedField
from app.workers.intake_extractor.loinc import (
    DELIBERATELY_UNMAPPED,
    LOINC_TABLE,
    LoincOutcome,
    assign_loinc,
    normalise_test_name,
    normalise_unit,
)
from tests.intake_extractor_support import LAB_SAMPLES, ground_truth

EXTRACTION_ID = "w2x-" + "c" * 32


def _located(text: str) -> ExtractedField[str]:
    return ExtractedField[str].located(
        text,
        Citation.located_document(
            source_id="d",
            field_path="results.x.y.name",
            quote=text,
            bbox=BBox(page=1, x0=1, y0=1, x1=2, y1=2),
            extraction_id=EXTRACTION_ID,
        ),
    )


def _unlocated(text: str) -> ExtractedField[str]:
    return ExtractedField[str].unlocated(text)


MISSING = ExtractedField[str].missing()


def loinc_check_digit(body: str) -> int:
    """LOINC's published mod-10 algorithm over the digits before the hyphen."""
    digits = body[::-1]
    odd = "".join(digits[0::2])  # positions 1, 3, 5 ... from the right
    even = "".join(digits[1::2])
    combined = even[::-1] + str(int(odd[::-1]) * 2)
    total = sum(int(d) for d in combined)
    return (10 - total % 10) % 10


def test_every_code_has_a_valid_check_digit() -> None:
    # A transposed or mistyped code fails here rather than in a chart.
    for entry in LOINC_TABLE:
        body, _, check = entry.code.partition("-")
        assert body.isdigit() and check.isdigit(), entry
        assert loinc_check_digit(body) == int(check), entry


def test_the_check_digit_implementation_catches_a_typo() -> None:
    assert loinc_check_digit("2345") == 7  # glucose, 2345-7
    assert loinc_check_digit("2354") != 7  # a transposition does not pass


def test_name_and_unit_pairs_are_unique() -> None:
    keys = [(normalise_test_name(e.name), normalise_unit(e.unit)) for e in LOINC_TABLE]
    assert len(keys) == len(set(keys))


def test_every_factory_analyte_is_coded_or_deliberately_refused() -> None:
    printed: set[tuple[str, str]] = set()
    for sample in LAB_SAMPLES:
        records = {f["field_path"]: f for f in ground_truth(sample)["fields"]}
        for path, record in records.items():
            if path.startswith("results.") and path.endswith(".name"):
                unit = records.get(path[: -len("name")] + "unit", {}).get("rendered_text", "")
                printed.add((record["rendered_text"], unit))
    uncovered = sorted(
        (name, unit)
        for name, unit in printed
        if assign_loinc(_located(name), _located(unit)).outcome is not LoincOutcome.MAPPED
    )
    assert uncovered == [("eGFR", "mL/min/1.73")]
    assert set(DELIBERATELY_UNMAPPED) == {"eGFR"}


def test_normalisation_folds_case_punctuation_and_space_only() -> None:
    assert assign_loinc(_located("protein  total"), _located("g/dl")).code == "2885-2"
    assert assign_loinc(_located("PROTEIN, TOTAL"), MISSING).code == "2885-2"


@pytest.mark.parametrize("near_miss", ["Glucos", "Glucose Fasting", "Gluc", "Blood Glucose"])
def test_no_fuzzy_matching(near_miss: str) -> None:
    assert assign_loinc(_located(near_miss), _located("mg/dL")).outcome is (
        LoincOutcome.NAME_NOT_IN_TABLE
    )


def test_unit_breaks_a_tie() -> None:
    assert assign_loinc(_located("Glucose"), _located("mg/dL")).code == "2345-7"
    assert assign_loinc(_located("Glucose"), _located("mmol/L")).code == "14749-6"
    assert assign_loinc(_located("Hemoglobin A1c"), _located("mmol/mol")).code == "59261-8"


def test_ambiguous_name_without_a_unit_is_not_guessed() -> None:
    result = assign_loinc(_located("Glucose"), MISSING)
    assert result.code is None and result.outcome is LoincOutcome.AMBIGUOUS_WITHOUT_UNIT


def test_unique_name_without_a_unit_maps() -> None:
    assert assign_loinc(_located("Sodium"), MISSING).code == "2951-2"


def test_a_unit_the_table_does_not_have_for_the_name_vetoes_the_code() -> None:
    # Sodium reported in mg/dL is not the mmol/L measurement 2951-2 names.
    result = assign_loinc(_located("Sodium"), _located("mg/dL"))
    assert result.code is None and result.outcome is LoincOutcome.UNIT_MISMATCH


def test_an_unlocated_unit_still_vetoes() -> None:
    # The page could not confirm the unit, but the model read one that
    # disagrees: a wrong code is worse than none.
    result = assign_loinc(_located("Glucose"), _unlocated("mmol/mol"))
    assert result.code is None and result.outcome is LoincOutcome.UNIT_MISMATCH


def test_an_unlocated_name_codes_nothing() -> None:
    result = assign_loinc(_unlocated("Glucose"), _located("mg/dL"))
    assert result.code is None and result.outcome is LoincOutcome.NO_NAME


def test_egfr_is_refused_on_purpose() -> None:
    result = assign_loinc(_located("eGFR"), _located("mL/min/1.73"))
    assert result.code is None and result.outcome is LoincOutcome.NAME_NOT_IN_TABLE
