"""
Category D — the abnormal-flag computation and its cross-check.

D20 is the whole of this category: a blank flag column reports nothing and
never means `normal`. The Synthea chart carries no reference ranges and no
abnormal flags, so the lab PDF is the only abnormality signal there is; a
cross-check that manufactures agreement with a flag no laboratory printed
would be loudest exactly when the extractor had lost the column entirely.
"""

from __future__ import annotations

from pydantic import ValidationError

from app.models.w2 import (
    COMPARABLE_REPORTED_FLAGS,
    AbnormalFlag,
    CrossCheckOutcome,
    FlagBasis,
    FlagCrossCheck,
    NumericLabValue,
    ReferenceRange,
    ReportedFlag,
    compute_abnormal_flag,
    cross_check_abnormal_flag,
    openemr_abnormal_code,
    parse_reported_flag,
)
from evals.w2.gate import samples
from evals.w2.gate.models import Case, CheckOutcome
from evals.w2.gate.registry import check

#: Potassium against its usual interval. 7.0 mmol/L is a CLIA "must know now"
#: value; 4.2 is unremarkable.
_K_RANGE = ReferenceRange(low=3.5, high=5.1, text="3.5 - 5.1", unit="mmol/L")
_K_PANIC = NumericLabValue(value=7.0)
_K_NORMAL = NumericLabValue(value=4.2)


@check("chk.d_printed_flag_agrees_with_computed")
def d_printed_flag_agrees_with_computed(case: Case) -> CheckOutcome:
    """
    Where a laboratory printed a flag and the arithmetic reaches the same
    conclusion, the outcome is `agree` — and the samples contain such rows.
    """
    check_result = cross_check_abnormal_flag(
        ReportedFlag.HIGH,
        NumericLabValue(value=168.0),
        ReferenceRange(low=70.0, high=99.0, text="70 - 99", unit="mg/dL"),
    )
    if check_result.outcome is not CrossCheckOutcome.AGREE:
        return CheckOutcome.failed(
            f"a printed H over a value above the range gave {check_result.outcome.value}"
        )
    if check_result.computed is not AbnormalFlag.HIGH:
        return CheckOutcome.failed(f"computed {check_result.computed.value}, expected high")

    agreed = 0
    for scenario_id in case.scenario_ids():
        for result in samples.lab(scenario_id).results:
            cross = result.cross_check_flag()
            if cross is not None and cross.outcome is CrossCheckOutcome.AGREE:
                agreed += 1
    if agreed == 0:
        return CheckOutcome.failed(
            "no row in the named scenarios produced an agreement; the cross-check may "
            "have stopped comparing altogether"
        )
    return CheckOutcome.ok(f"{agreed} rows agree with their printed flag")


@check("chk.d_blank_flag_is_not_reported")
def d_blank_flag_is_not_reported(case: Case) -> CheckOutcome:
    """
    CRITICAL, and the single most important case in this category.

    A blank column is `not_reported`. It is not `normal`, the cross-check
    outcome over it is `not_reported`, and `FlagCrossCheck` refuses to record
    `agree` against a flag that was never printed.
    """
    del case
    for blank in ("", "   ", "\t"):
        parsed = parse_reported_flag(blank)
        if parsed is not ReportedFlag.NOT_REPORTED:
            return CheckOutcome.failed(
                f"parse_reported_flag({blank!r}) returned {parsed.value}; D20: a blank "
                "column asserts nothing, and reading it as normal manufactures "
                "agreement with a flag no laboratory printed"
            )
    if ReportedFlag.NOT_REPORTED in COMPARABLE_REPORTED_FLAGS:
        return CheckOutcome.failed(
            "not_reported is in COMPARABLE_REPORTED_FLAGS; a blank column would be "
            "compared against the computed flag"
        )

    cross = cross_check_abnormal_flag(ReportedFlag.NOT_REPORTED, _K_NORMAL, _K_RANGE)
    if cross.outcome is not CrossCheckOutcome.NOT_REPORTED:
        return CheckOutcome.failed(
            f"an in-range value with a blank flag gave {cross.outcome.value}, not not_reported"
        )
    if cross.computed is not AbnormalFlag.NORMAL:
        return CheckOutcome.failed("the computed flag did not stand alone under a blank column")
    if cross.is_verification_failure:
        return CheckOutcome.failed("a blank column was treated as a verification failure")

    try:
        FlagCrossCheck(
            reported=ReportedFlag.NOT_REPORTED,
            computed=AbnormalFlag.NORMAL,
            basis=FlagBasis.NUMERIC_IN_RANGE,
            outcome=CrossCheckOutcome.AGREE,
        )
    except ValidationError:
        return CheckOutcome.ok(
            "a blank column is not_reported, is not comparable, and cannot be recorded as agreement"
        )
    return CheckOutcome.failed(
        "FlagCrossCheck recorded `agree` over a reported flag of not_reported; the "
        "cross-check can now assert agreement with a flag that was never printed"
    )


@check("chk.d_out_of_range_under_a_blank_flag_is_surfaced")
def d_out_of_range_under_a_blank_flag_is_surfaced(case: Case) -> CheckOutcome:
    """
    A value outside its range with nothing printed beside it: the computation
    still says `high`, and the outcome records that the page said nothing.
    The abnormality is not suppressed because the column was empty.
    """
    del case
    cross = cross_check_abnormal_flag(ReportedFlag.NOT_REPORTED, _K_PANIC, _K_RANGE)
    if cross.computed is not AbnormalFlag.HIGH:
        return CheckOutcome.failed(
            f"7.0 against 3.5-5.1 computed {cross.computed.value}; the abnormality was "
            "suppressed by the empty flag column"
        )
    if cross.outcome is not CrossCheckOutcome.NOT_REPORTED:
        return CheckOutcome.failed(f"outcome was {cross.outcome.value}, expected not_reported")
    if cross.basis is not FlagBasis.NUMERIC_ABOVE_HIGH:
        return CheckOutcome.failed(f"basis was {cross.basis.value}, expected numeric_above_high")
    return CheckOutcome.ok("computed high stands alone; the outcome says the page reported nothing")


@check("chk.d_printed_contradicting_computed_is_a_failure")
def d_printed_contradicting_computed_is_a_failure(case: Case) -> CheckOutcome:
    """
    A printed `N` over a value outside the range is a verification failure —
    one of the two readings is wrong and which one is not knowable from here,
    so it is disclosed rather than resolved in favour of either side.
    """
    del case
    cross = cross_check_abnormal_flag(ReportedFlag.NORMAL, _K_PANIC, _K_RANGE)
    if cross.outcome is not CrossCheckOutcome.DISAGREE:
        return CheckOutcome.failed(
            f"a printed normal over a high value gave {cross.outcome.value}; the "
            "cross-check has stopped contradicting the page"
        )
    if not cross.is_verification_failure:
        return CheckOutcome.failed("a disagreement is not reported as a verification failure")
    if cross.computed is not AbnormalFlag.HIGH or cross.reported is not ReportedFlag.NORMAL:
        return CheckOutcome.failed("the two sides of the disagreement were not both preserved")
    return CheckOutcome.ok("a contradicted flag is a verification failure carrying both readings")


@check("chk.d_panic_value_is_decidable")
def d_panic_value_is_decidable(case: Case) -> CheckOutcome:
    """
    A panic value computes to a decidable flag and maps to a real
    `procedure_result.abnormal` code. `indeterminate` has no such code and
    refuses to map: writing `no` for "we could not tell" would put a false
    normal in a chart.
    """
    del case
    computation = compute_abnormal_flag(_K_PANIC, _K_RANGE)
    if computation.flag is not AbnormalFlag.HIGH:
        return CheckOutcome.failed(
            f"K+ 7.0 mmol/L against 3.5-5.1 computed {computation.flag.value}"
        )
    if openemr_abnormal_code(computation.flag) != "high":
        return CheckOutcome.failed("the panic value would be written back with the wrong code")
    try:
        openemr_abnormal_code(AbnormalFlag.INDETERMINATE)
    except ValueError:
        return CheckOutcome.ok(
            "a panic value computes to high and writes as high; indeterminate still "
            "refuses to be written back at all"
        )
    return CheckOutcome.failed(
        "openemr_abnormal_code mapped indeterminate; an undecidable result would be "
        "written into a chart as a decided one"
    )


@check("chk.d_agreement_is_computed_over_comparable_rows_only")
def d_agreement_is_computed_over_comparable_rows_only(case: Case) -> CheckOutcome:
    """
    D20's consequence for this gate: a flag-agreement rate is computed only
    over rows whose reported flag is in COMPARABLE_REPORTED_FLAGS, and the
    denominator travels with it. A category scoring 100% on four rows is not
    the same claim as one scoring 100% on four hundred.
    """
    computable = comparable = agree = 0
    for scenario_id in case.scenario_ids():
        for result in samples.lab(scenario_id).results:
            cross = result.cross_check_flag()
            if cross is None:
                continue
            computable += 1
            if cross.reported in COMPARABLE_REPORTED_FLAGS:
                comparable += 1
                if cross.outcome is CrossCheckOutcome.AGREE:
                    agree += 1
            elif cross.outcome is CrossCheckOutcome.AGREE:
                return CheckOutcome.failed(
                    f"{scenario_id}: {result.test_key} recorded agreement with a "
                    f"{cross.reported.value} flag"
                )
    if comparable == 0:
        return CheckOutcome.failed(
            f"{computable} computable rows and not one comparable flag; either the "
            "samples lost their abnormal rows or the flag column is no longer read"
        )
    rate = agree / comparable
    return CheckOutcome.ok(
        f"flag agreement {agree}/{comparable} = {rate:.3f} "
        f"(over {computable} rows with a computable flag; "
        f"{computable - comparable} reported nothing comparable)",
        agree=agree,
        comparable=comparable,
        computable=computable,
    )


@check("chk.d_unrecognised_glyph_is_distinct_from_blank")
def d_unrecognised_glyph_is_distinct_from_blank(case: Case) -> CheckOutcome:
    """
    A glyph outside the table is `unrecognised`: the report asserted
    something we cannot read, which is a different fault from asserting
    nothing. Same outcome, different cause, and the cause stays in the record.
    """
    del case
    for glyph in ("*", "?", "CRIT", "↑"):
        parsed = parse_reported_flag(glyph)
        if parsed is not ReportedFlag.UNRECOGNISED:
            return CheckOutcome.failed(f"parse_reported_flag({glyph!r}) returned {parsed.value}")
    if ReportedFlag.UNRECOGNISED in COMPARABLE_REPORTED_FLAGS:
        return CheckOutcome.failed("an unreadable glyph is being compared against the computation")

    for glyph, expected in (
        ("N", ReportedFlag.NORMAL),
        ("WNL", ReportedFlag.NORMAL),
        ("HH", ReportedFlag.HIGH),
        ("ll", ReportedFlag.LOW),
    ):
        parsed = parse_reported_flag(glyph)
        if parsed is not expected:
            return CheckOutcome.failed(
                f"parse_reported_flag({glyph!r}) returned {parsed.value}, expected "
                f"{expected.value}: a printed assertion of normality is not a blank column"
            )

    cross = cross_check_abnormal_flag(ReportedFlag.UNRECOGNISED, _K_PANIC, _K_RANGE)
    if cross.outcome is not CrossCheckOutcome.NOT_REPORTED:
        return CheckOutcome.failed(f"an unrecognised glyph gave {cross.outcome.value}")
    if cross.reported is not ReportedFlag.UNRECOGNISED:
        return CheckOutcome.failed(
            "the record collapsed unrecognised into not_reported; the two causes are no "
            "longer distinguishable in a log line"
        )
    return CheckOutcome.ok("unrecognised and not_reported share an outcome and stay distinct")
