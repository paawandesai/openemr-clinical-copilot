"""
The deterministic abnormal-flag rule and its cross-check.

This is the module that most resembles Week 1's verifier: pure comparison
code whose whole value is that its answer is not a second opinion. So it is
tested to the density that implies — every branch of the interval rule,
both directions of censoring, the qualitative vocabulary, and the four
cross-check outcomes.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.w2.lab_values import (
    COMPARABLE_REPORTED_FLAGS,
    AbnormalFlag,
    Comparator,
    CrossCheckOutcome,
    FlagBasis,
    FlagCrossCheck,
    NumericLabValue,
    QualitativeLabValue,
    ReferenceRange,
    ReportedFlag,
    as_abnormal_flag,
    compute_abnormal_flag,
    cross_check_abnormal_flag,
    openemr_abnormal_code,
    parse_reference_range_text,
    parse_reported_flag,
)


def numeric(value: float, comparator: Comparator | None = None) -> NumericLabValue:
    return NumericLabValue(value=value, comparator=comparator)


# -- plain numbers ---------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected", "basis"),
    [
        (89.0, AbnormalFlag.NORMAL, FlagBasis.NUMERIC_IN_RANGE),
        (70.0, AbnormalFlag.NORMAL, FlagBasis.NUMERIC_IN_RANGE),  # bounds inclusive
        (99.0, AbnormalFlag.NORMAL, FlagBasis.NUMERIC_IN_RANGE),
        (69.9, AbnormalFlag.LOW, FlagBasis.NUMERIC_BELOW_LOW),
        (99.1, AbnormalFlag.HIGH, FlagBasis.NUMERIC_ABOVE_HIGH),
        (111.0, AbnormalFlag.HIGH, FlagBasis.NUMERIC_ABOVE_HIGH),
    ],
)
def test_numeric_against_a_two_sided_range(
    value: float, expected: AbnormalFlag, basis: FlagBasis
) -> None:
    computation = compute_abnormal_flag(numeric(value), parse_reference_range_text("70 - 99"))
    assert computation.flag is expected
    assert computation.basis is basis


def test_a_missing_bound_constrains_nothing() -> None:
    """`< 200` has no floor, so 4 is normal rather than undecidable."""
    high_only = parse_reference_range_text("< 200")
    assert high_only.low is None and high_only.high == 200.0
    assert compute_abnormal_flag(numeric(4.0), high_only).flag is AbnormalFlag.NORMAL
    assert compute_abnormal_flag(numeric(250.0), high_only).flag is AbnormalFlag.HIGH

    low_only = parse_reference_range_text("> 40")
    assert low_only.low == 40.0 and low_only.high is None
    assert compute_abnormal_flag(numeric(4000.0), low_only).flag is AbnormalFlag.NORMAL
    assert compute_abnormal_flag(numeric(10.0), low_only).flag is AbnormalFlag.LOW


# -- censored values -------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "comparator", "low", "high", "expected"),
    [
        # Below the detection limit, and the limit is inside the range.
        (0.01, Comparator.LESS_THAN, 0.0, 0.04, AbnormalFlag.NORMAL),
        # Below the detection limit, and the whole interval clears the floor.
        (5.0, Comparator.LESS_THAN, 70.0, 99.0, AbnormalFlag.LOW),
        # The interval straddles the ceiling: genuinely undecidable.
        (5.0, Comparator.LESS_THAN, 0.0, 3.0, AbnormalFlag.INDETERMINATE),
        # Above the measuring range, entirely above the ceiling.
        (1000.0, Comparator.GREATER_THAN, 70.0, 99.0, AbnormalFlag.HIGH),
        # Above a floor but the interval reaches past the ceiling too.
        (80.0, Comparator.GREATER_THAN, 70.0, 99.0, AbnormalFlag.INDETERMINATE),
        # `>=` at the ceiling still reaches above it.
        (99.0, Comparator.GREATER_OR_EQUAL, 70.0, 99.0, AbnormalFlag.INDETERMINATE),
        # `<=` exactly at the floor could be the floor itself.
        (70.0, Comparator.LESS_OR_EQUAL, 70.0, 99.0, AbnormalFlag.INDETERMINATE),
        # `<` exactly at the floor is strictly below it.
        (70.0, Comparator.LESS_THAN, 70.0, 99.0, AbnormalFlag.LOW),
    ],
)
def test_censored_values_are_compared_as_intervals(
    value: float,
    comparator: Comparator,
    low: float,
    high: float,
    expected: AbnormalFlag,
) -> None:
    computation = compute_abnormal_flag(
        numeric(value, comparator), ReferenceRange(low=low, high=high)
    )
    assert computation.flag is expected


def test_a_range_that_admits_negatives_gets_no_non_negativity_assumption() -> None:
    """Base excess is `-2 - 2`. `<-3` there is genuinely below the floor;
    `<3` is genuinely undecidable, because the value really could be -50."""
    base_excess = ReferenceRange(low=-2.0, high=2.0)
    assert (
        compute_abnormal_flag(numeric(-3.0, Comparator.LESS_THAN), base_excess).flag
        is AbnormalFlag.LOW
    )
    assert (
        compute_abnormal_flag(numeric(3.0, Comparator.LESS_THAN), base_excess).flag
        is AbnormalFlag.INDETERMINATE
    )


def test_an_undecidable_censored_value_says_why() -> None:
    computation = compute_abnormal_flag(
        numeric(5.0, Comparator.LESS_THAN), ReferenceRange(low=0.0, high=3.0)
    )
    assert computation.basis is FlagBasis.CENSORED_VALUE_AMBIGUOUS


# -- qualitative values ----------------------------------------------------


@pytest.mark.parametrize(
    ("observed", "reference", "expected", "basis"),
    [
        ("Negative", "Negative", AbnormalFlag.NORMAL, FlagBasis.QUALITATIVE_MATCHES_REFERENCE),
        ("Positive", "Negative", AbnormalFlag.ABNORMAL, FlagBasis.QUALITATIVE_OPPOSES_REFERENCE),
        (
            "Not Detected",
            "Not detected",
            AbnormalFlag.NORMAL,
            FlagBasis.QUALITATIVE_MATCHES_REFERENCE,
        ),
        ("Non-reactive", "Negative", AbnormalFlag.NORMAL, FlagBasis.QUALITATIVE_MATCHES_REFERENCE),
        # Outside the closed vocabulary: disclosed, never guessed.
        ("Trace", "Negative", AbnormalFlag.INDETERMINATE, FlagBasis.QUALITATIVE_NOT_COMPARABLE),
        ("Positive", "See note", AbnormalFlag.INDETERMINATE, FlagBasis.QUALITATIVE_NOT_COMPARABLE),
    ],
)
def test_qualitative_polarity(
    observed: str, reference: str, expected: AbnormalFlag, basis: FlagBasis
) -> None:
    computation = compute_abnormal_flag(
        QualitativeLabValue(text=observed), ReferenceRange(text=reference)
    )
    assert computation.flag is expected
    assert computation.basis is basis


def test_qualitative_with_no_reference_text_is_not_comparable() -> None:
    computation = compute_abnormal_flag(
        QualitativeLabValue(text="Positive"), ReferenceRange(low=0.0, high=1.0)
    )
    assert computation.flag is AbnormalFlag.INDETERMINATE
    assert computation.basis is FlagBasis.QUALITATIVE_NOT_COMPARABLE


# -- missing ranges --------------------------------------------------------


def test_no_reference_range_at_all() -> None:
    computation = compute_abnormal_flag(numeric(89.0), None)
    assert computation.flag is AbnormalFlag.INDETERMINATE
    assert computation.basis is FlagBasis.NO_REFERENCE_RANGE


def test_a_text_only_range_has_no_numeric_bounds() -> None:
    computation = compute_abnormal_flag(numeric(89.0), ReferenceRange(text="See note"))
    assert computation.flag is AbnormalFlag.INDETERMINATE
    assert computation.basis is FlagBasis.NO_NUMERIC_BOUNDS


# -- determinism -----------------------------------------------------------


def test_the_rule_is_pure() -> None:
    """Same inputs, same answer, every time — the property the cross-check
    depends on."""
    value, reference = numeric(111.0), parse_reference_range_text("70 - 99")
    answers = {compute_abnormal_flag(value, reference) for _ in range(50)}
    assert len(answers) == 1


def test_the_module_reaches_nothing() -> None:
    """The flag rule is the document analogue of Week 1's verifier, and is
    bound by the same import rule (tests/test_verifier.py enforces the Week
    1 side)."""
    import app.models.w2.lab_values as module

    source = module.__file__
    assert source is not None
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    for forbidden in ("import httpx", "import requests", "anthropic", "langchain", "openai"):
        assert forbidden not in text, f"{forbidden} must not appear in the flag rule"


# -- printed flags ---------------------------------------------------------


@pytest.mark.parametrize(
    ("glyph", "expected"),
    [
        # D20: a blank column is an absence, not an assertion of normality.
        ("", ReportedFlag.NOT_REPORTED),
        ("  ", ReportedFlag.NOT_REPORTED),
        # A printed N or WNL *is* an assertion.
        ("N", ReportedFlag.NORMAL),
        ("wnl", ReportedFlag.NORMAL),
        ("H", ReportedFlag.HIGH),
        ("h", ReportedFlag.HIGH),
        ("HH", ReportedFlag.HIGH),
        ("L", ReportedFlag.LOW),
        ("LL", ReportedFlag.LOW),
        ("A", ReportedFlag.ABNORMAL),
        # Asserted something we cannot read — different from asserting nothing.
        ("?", ReportedFlag.UNRECOGNISED),
        ("critical", ReportedFlag.UNRECOGNISED),
    ],
)
def test_parse_reported_flag(glyph: str, expected: ReportedFlag) -> None:
    assert parse_reported_flag(glyph) is expected


def test_parse_reported_flag_is_total() -> None:
    """Every string maps. There is no None to forget to handle."""
    for glyph in ("", "H", "\u2014", "n/a", "see note", "12"):
        assert isinstance(parse_reported_flag(glyph), ReportedFlag)


def test_a_blank_column_is_never_normal() -> None:
    """
    D20, superseding D12, stated as the single assertion it turns on. Read
    as `normal`, a blank column would confirm the computed flag on every
    in-range row of every report — agreement with a flag no laboratory
    printed — and the cross-check would be loudest exactly when an
    extractor has lost the flag column entirely.
    """
    assert parse_reported_flag("") is not ReportedFlag.NORMAL
    assert parse_reported_flag("") is ReportedFlag.NOT_REPORTED
    assert parse_reported_flag("") not in COMPARABLE_REPORTED_FLAGS


@pytest.mark.parametrize("reported", sorted(COMPARABLE_REPORTED_FLAGS))
def test_every_comparable_flag_names_a_conclusion(reported: ReportedFlag) -> None:
    assert as_abnormal_flag(reported) is not None


@pytest.mark.parametrize("reported", [ReportedFlag.NOT_REPORTED, ReportedFlag.UNRECOGNISED])
def test_a_non_comparable_flag_names_no_conclusion(reported: ReportedFlag) -> None:
    assert reported not in COMPARABLE_REPORTED_FLAGS
    assert as_abnormal_flag(reported) is None


# -- the cross-check -------------------------------------------------------


def test_agreement() -> None:
    check = cross_check_abnormal_flag(
        ReportedFlag.HIGH, numeric(111.0), parse_reference_range_text("70 - 99")
    )
    assert check.outcome is CrossCheckOutcome.AGREE
    assert check.is_verification_failure is False


def test_disagreement_is_a_verification_failure() -> None:
    """The page prints `N` on an out-of-range value. One of the two readings
    is wrong and this contract does not decide which."""
    check = cross_check_abnormal_flag(
        ReportedFlag.NORMAL, numeric(111.0), parse_reference_range_text("70 - 99")
    )
    assert check.outcome is CrossCheckOutcome.DISAGREE
    assert check.is_verification_failure is True


def test_a_blank_column_leaves_the_computed_flag_standing_alone() -> None:
    """
    D20. The computed flag is still computed and still reported; what does
    not happen is a comparison, and what is never recorded is agreement.
    """
    check = cross_check_abnormal_flag(
        parse_reported_flag(""), numeric(111.0), parse_reference_range_text("70 - 99")
    )
    assert check.reported is ReportedFlag.NOT_REPORTED
    assert check.computed is AbnormalFlag.HIGH
    assert check.basis is FlagBasis.NUMERIC_ABOVE_HIGH
    assert check.outcome is CrossCheckOutcome.NOT_REPORTED
    assert check.outcome is not CrossCheckOutcome.AGREE
    assert check.is_verification_failure is False


def test_a_blank_column_on_an_in_range_value_is_still_not_agreement() -> None:
    """The case D12 got wrong, and the common one: nearly every row of a
    normal report has a blank flag column."""
    check = cross_check_abnormal_flag(
        parse_reported_flag(""), numeric(89.0), parse_reference_range_text("70 - 99")
    )
    assert check.computed is AbnormalFlag.NORMAL
    assert check.outcome is CrossCheckOutcome.NOT_REPORTED
    assert check.is_verification_failure is False


def test_an_unreadable_glyph_is_not_a_failure_and_is_distinguishable() -> None:
    check = cross_check_abnormal_flag(
        parse_reported_flag("?"), numeric(111.0), parse_reference_range_text("70 - 99")
    )
    assert check.outcome is CrossCheckOutcome.NOT_REPORTED
    assert check.is_verification_failure is False
    # Same outcome as a blank column, different cause — the cause is on
    # `reported`, so a log line can tell them apart.
    assert check.reported is ReportedFlag.UNRECOGNISED


def test_an_undecidable_value_is_not_a_failure() -> None:
    """A check that cried wolf on correct data would be switched off."""
    check = cross_check_abnormal_flag(
        ReportedFlag.NORMAL, numeric(5.0, Comparator.LESS_THAN), ReferenceRange(low=0.0, high=3.0)
    )
    assert check.outcome is CrossCheckOutcome.NOT_COMPUTABLE
    assert check.is_verification_failure is False


def test_no_printed_flag_outranks_an_undecidable_computation() -> None:
    """
    Both mean "no comparison", but `not_computable` would suggest that
    fixing the reference range would produce one. With nothing printed, it
    would not.
    """
    check = cross_check_abnormal_flag(
        parse_reported_flag(""),
        numeric(5.0, Comparator.LESS_THAN),
        ReferenceRange(low=0.0, high=3.0),
    )
    assert check.computed is AbnormalFlag.INDETERMINATE
    assert check.outcome is CrossCheckOutcome.NOT_REPORTED


def test_the_cross_check_outcome_cannot_be_asserted_by_hand() -> None:
    with pytest.raises(ValidationError, match="does not follow from"):
        FlagCrossCheck(
            reported=ReportedFlag.NORMAL,
            computed=AbnormalFlag.HIGH,
            basis=FlagBasis.NUMERIC_ABOVE_HIGH,
            outcome=CrossCheckOutcome.AGREE,
        )


def test_agreement_cannot_be_asserted_over_a_blank_column() -> None:
    """The specific forgery D20 exists to make impossible."""
    with pytest.raises(ValidationError, match="does not follow from"):
        FlagCrossCheck(
            reported=ReportedFlag.NOT_REPORTED,
            computed=AbnormalFlag.NORMAL,
            basis=FlagBasis.NUMERIC_IN_RANGE,
            outcome=CrossCheckOutcome.AGREE,
        )


# -- OpenEMR column mapping -------------------------------------------------


@pytest.mark.parametrize(
    ("flag", "code"),
    [
        (AbnormalFlag.NORMAL, "no"),
        (AbnormalFlag.HIGH, "high"),
        (AbnormalFlag.LOW, "low"),
        (AbnormalFlag.ABNORMAL, "yes"),
    ],
)
def test_openemr_abnormal_codes_match_the_proc_res_abnormal_list(
    flag: AbnormalFlag, code: str
) -> None:
    assert openemr_abnormal_code(flag) == code


def test_indeterminate_is_never_written_as_normal() -> None:
    with pytest.raises(ValueError, match="not written back as normal"):
        openemr_abnormal_code(AbnormalFlag.INDETERMINATE)


# -- reference-range parsing ------------------------------------------------


@pytest.mark.parametrize(
    ("text", "low", "high"),
    [
        ("70 - 99", 70.0, 99.0),
        ("70-99", 70.0, 99.0),
        ("70 – 99", 70.0, 99.0),  # en dash
        ("3.5 to 5.1", 3.5, 5.1),
        ("-2 - 2", -2.0, 2.0),
        ("< 200", None, 200.0),
        ("<200", None, 200.0),
        ("<=200", None, 200.0),
        ("> 40", 40.0, None),
        (">=40", 40.0, None),
        ("Negative", None, None),
        ("See note", None, None),
    ],
)
def test_parse_reference_range_text(text: str, low: float | None, high: float | None) -> None:
    parsed = parse_reference_range_text(text, unit="mg/dL")
    assert parsed.low == low
    assert parsed.high == high
    assert parsed.text == text.strip()
    assert parsed.unit == "mg/dL"


def test_an_empty_reference_range_is_an_error_not_an_empty_object() -> None:
    with pytest.raises(ValueError, match="is not empty"):
        parse_reference_range_text("   ")
    with pytest.raises(ValidationError, match="states something"):
        ReferenceRange()


def test_an_inverted_reference_range_is_rejected() -> None:
    with pytest.raises(ValidationError, match="is above high"):
        ReferenceRange(low=99.0, high=70.0)


def test_a_censored_value_renders_as_written() -> None:
    assert numeric(5.0, Comparator.LESS_THAN).as_text() == "<5"
    assert numeric(0.84).as_text() == "0.84"
    assert QualitativeLabValue(text="Positive").as_text() == "Positive"
