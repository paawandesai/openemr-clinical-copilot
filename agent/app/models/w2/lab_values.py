"""
Lab values, reference ranges, and the deterministic abnormal-flag rule.

Split out of lab_pdf.py because this is the part with no I/O, no model and
no document in it: pure functions over small value types, which is what
makes it testable to the density a safety check needs
(tests/test_w2_abnormal_flag.py).

---

**Decision: a lab value is a tagged union, not `float | str`** (D9).

A lab result's value is a number, or a number with a comparator (`<5`), or
a qualitative term (`positive`). `float | str` can hold all three, and that
is the problem: `"<5"` arrives as a string and every consumer that wants to
compare it to a reference range has to re-parse it, each in its own way,
each getting the censoring wrong differently. Worse, under `strict=True` a
`float | str` union quietly accepts `"89"` as a qualitative value when the
extractor forgets to parse.

So: `NumericLabValue` carries a float and an optional `Comparator`;
`QualitativeLabValue` carries text from a closed vocabulary check. The
discriminator is `kind`. FHIR draws the line in the same place
(`valueQuantity.comparator` versus `valueString`), which matters because
the write-back path lands in `procedure_result` and comes back out as a
FHIR Observation.

---

**Decision: censored values are compared as intervals, and refuse to guess.**

`<5` does not mean 5. It means the true value lies in (-inf, 5). So
`compute_abnormal_flag` turns every value into a closed-or-open interval
and compares *interval containment*, not a number:

* the value's interval entirely inside the reference interval -> `normal`
* entirely above `high` -> `high`;  entirely below `low` -> `low`
* overlapping the boundary -> `indeterminate`

Two adjustments keep that from over-reporting `indeterminate` on ordinary
data. An absent reference bound constrains nothing, so `89` against
`< 200` is normal rather than undecidable. And a reference range whose
floor is at or above zero describes a non-negative quantity, so `<0.01`
against `0.00 - 0.04` is `[0, 0.01)` and normal — while a range that admits
negatives, such as a base excess of `-2 - 2`, gets no such assumption.

A plain number is the degenerate interval `[v, v]`, so one rule covers both
cases. `indeterminate` is a real outcome, not an error: a result of `<5`
against a reference range of `0 - 3` genuinely does not determine whether
the patient is in range, and the contract says so rather than picking the
answer that happens to be reassuring.

---

**Decision: a blank flag column means `normal`.**

The doc-factory renders an empty `flag` for every in-range row and `H`/`L`
for the abnormal ones, which is how lab reports are actually printed. So
`parse_reported_flag("")` is `normal`, not "no flag reported". The
consequence is deliberate: a report that prints nothing where a flag
belongs is *asserting* normality, and the cross-check below will catch it
disagreeing with the computed flag. `None` is reserved for a glyph we do
not recognise, which is a different failure and must not be read as an
assertion of anything.
"""

from __future__ import annotations

import math
import re
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from app.models.w2.base import W2Contract


class Comparator(StrEnum):
    """
    The censoring a lab report prints in front of a number. Values match
    FHIR's `Quantity.comparator` value set, because a written-back result
    surfaces as `Observation.valueQuantity` (D1, spike §5).
    """

    LESS_THAN = "<"
    LESS_OR_EQUAL = "<="
    GREATER_THAN = ">"
    GREATER_OR_EQUAL = ">="


class LabValueKind(StrEnum):
    NUMERIC = "numeric"
    QUALITATIVE = "qualitative"


class NumericLabValue(W2Contract):
    """A number, possibly censored: `89`, `<5`, `>1000`."""

    kind: Literal[LabValueKind.NUMERIC] = LabValueKind.NUMERIC
    value: float
    comparator: Comparator | None = Field(
        default=None,
        description="None for an exact reading; set for a censored one such as `<5`.",
    )

    @model_validator(mode="after")
    def _finite(self) -> Self:
        if not math.isfinite(self.value):
            raise ValueError("a lab value is a finite number")
        return self

    def as_text(self) -> str:
        """How the value is written on a report, for a citation's quote."""
        number = f"{self.value:g}"
        return f"{self.comparator.value}{number}" if self.comparator else number


class QualitativeLabValue(W2Contract):
    """A term rather than a number: `positive`, `not detected`, `trace`."""

    kind: Literal[LabValueKind.QUALITATIVE] = LabValueKind.QUALITATIVE
    text: str = Field(min_length=1)

    def as_text(self) -> str:
        return self.text


#: A `type` statement rather than a plain assignment so the alias carries its
#: own name: the exported JSON Schema names the `ExtractedField[LabValue]`
#: definition after it, and an unnamed Annotated union would print its repr.
type LabValue = Annotated[NumericLabValue | QualitativeLabValue, Field(discriminator="kind")]


class ReferenceRange(W2Contract):
    """
    The interval a result is judged against.

    `low`/`high` are the machine-comparable form; `text` is what the report
    actually printed, kept verbatim because it is what a citation quotes and
    because plenty of ranges (`Negative`, `See note`) have no numeric form
    at all. At least one of the three is required — an all-None reference
    range is an extraction bug wearing a valid object's clothes.
    """

    low: float | None = Field(default=None, description="Inclusive lower bound.")
    high: float | None = Field(default=None, description="Inclusive upper bound.")
    text: str | None = Field(
        default=None,
        description="The range exactly as printed, e.g. `70 - 99` or `Negative`.",
    )
    unit: str | None = Field(default=None, description="The unit the bounds are in.")

    @model_validator(mode="after")
    def _usable(self) -> Self:
        if self.low is None and self.high is None and self.text is None:
            raise ValueError("a reference range states something: low, high or text")
        for name in ("low", "high"):
            bound = getattr(self, name)
            if bound is not None and not math.isfinite(bound):
                raise ValueError(f"reference range {name} is a finite number")
        if self.low is not None and self.high is not None and self.low > self.high:
            raise ValueError(f"reference range low ({self.low}) is above high ({self.high})")
        return self

    @property
    def has_numeric_bounds(self) -> bool:
        return self.low is not None or self.high is not None


class AbnormalFlag(StrEnum):
    """
    What a result is, relative to its reference range.

    Five members, not four, because `indeterminate` is a real answer — see
    the module docstring on censored values. `openemr_abnormal_code()` maps
    the four decidable ones onto OpenEMR's `proc_res_abnormal` list
    (`no | yes | high | low`, spike §9) and refuses to map the fifth,
    because there is no column value that means "we could not tell" and
    writing `no` would be a lie with a patient in it.
    """

    NORMAL = "normal"
    HIGH = "high"
    LOW = "low"
    ABNORMAL = "abnormal"
    INDETERMINATE = "indeterminate"


DECIDABLE_FLAGS = frozenset(
    {AbnormalFlag.NORMAL, AbnormalFlag.HIGH, AbnormalFlag.LOW, AbnormalFlag.ABNORMAL}
)

_OPENEMR_ABNORMAL_CODE: dict[AbnormalFlag, str] = {
    AbnormalFlag.NORMAL: "no",
    AbnormalFlag.HIGH: "high",
    AbnormalFlag.LOW: "low",
    AbnormalFlag.ABNORMAL: "yes",
}


def openemr_abnormal_code(flag: AbnormalFlag) -> str:
    """
    `procedure_result.abnormal` for a flag. Raises on `indeterminate`: see
    AbnormalFlag's docstring.
    """
    code = _OPENEMR_ABNORMAL_CODE.get(flag)
    if code is None:
        raise ValueError(
            "indeterminate has no procedure_result.abnormal value; an "
            "undecidable result is not written back as normal"
        )
    return code


class FlagBasis(StrEnum):
    """Why `compute_abnormal_flag` returned what it did. A closed set, so a
    disagreement can be explained in a log line without free text."""

    NUMERIC_IN_RANGE = "numeric_in_range"
    NUMERIC_ABOVE_HIGH = "numeric_above_high"
    NUMERIC_BELOW_LOW = "numeric_below_low"
    #: A censored value whose interval straddles a bound (`<5` against `0 - 3`).
    CENSORED_VALUE_AMBIGUOUS = "censored_value_ambiguous"
    QUALITATIVE_MATCHES_REFERENCE = "qualitative_matches_reference"
    QUALITATIVE_OPPOSES_REFERENCE = "qualitative_opposes_reference"
    QUALITATIVE_NOT_COMPARABLE = "qualitative_not_comparable"
    NO_REFERENCE_RANGE = "no_reference_range"
    NO_NUMERIC_BOUNDS = "no_numeric_bounds"


class AbnormalFlagComputation(W2Contract):
    """What the pure rule concluded, and on what basis."""

    flag: AbnormalFlag
    basis: FlagBasis


#: Qualitative terms that mean "nothing found". Lowercase, punctuation
#: stripped. Deliberately small: a term not in either set yields
#: `indeterminate`, which is disclosed, rather than a guess that renders.
_QUALITATIVE_NEGATIVE = frozenset(
    {
        "negative",
        "non reactive",
        "nonreactive",
        "not detected",
        "none detected",
        "no growth",
        "absent",
        "normal",
    }
)

#: Qualitative terms that mean "something found".
_QUALITATIVE_POSITIVE = frozenset(
    {
        "positive",
        "reactive",
        "detected",
        "present",
        "abnormal",
    }
)


def _normalise_term(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.strip().lower()).strip()


def _interval(value: NumericLabValue) -> tuple[float, bool, float, bool]:
    """
    The value as (low, low_inclusive, high, high_inclusive).

    An exact reading is the point interval [v, v]. `<5` is (-inf, 5),
    `<=5` is (-inf, 5], and so on for the other direction.
    """
    v = value.value
    match value.comparator:
        case None:
            return (v, True, v, True)
        case Comparator.LESS_THAN:
            return (-math.inf, False, v, False)
        case Comparator.LESS_OR_EQUAL:
            return (-math.inf, False, v, True)
        case Comparator.GREATER_THAN:
            return (v, False, math.inf, False)
        case Comparator.GREATER_OR_EQUAL:
            return (v, True, math.inf, False)


def compute_abnormal_flag(
    value: NumericLabValue | QualitativeLabValue,
    reference_range: ReferenceRange | None,
) -> AbnormalFlagComputation:
    """
    The abnormal flag a value implies, from the value and its range alone.

    Pure: no I/O, no model, no clock, no global state. The same inputs give
    the same answer on any machine, which is what lets it be used as the
    independent half of a cross-check against what the vision model read off
    the page (`cross_check_abnormal_flag`). It is the document analogue of
    Week 1's deterministic verifier (app/verification/checker.py) and is
    bound by the same rule: it never issues a second opinion, only a
    comparison.
    """
    if reference_range is None:
        return AbnormalFlagComputation(
            flag=AbnormalFlag.INDETERMINATE, basis=FlagBasis.NO_REFERENCE_RANGE
        )

    if isinstance(value, QualitativeLabValue):
        return _qualitative_flag(value, reference_range)

    if not reference_range.has_numeric_bounds:
        return AbnormalFlagComputation(
            flag=AbnormalFlag.INDETERMINATE, basis=FlagBasis.NO_NUMERIC_BOUNDS
        )

    lo = reference_range.low if reference_range.low is not None else -math.inf
    hi = reference_range.high if reference_range.high is not None else math.inf
    v_lo, v_lo_inc, v_hi, v_hi_inc = _interval(value)

    # A range whose floor sits at or above zero describes a quantity that
    # cannot be negative — a concentration, a count, an activity. For such an
    # analyte, `<5` means [0, 5), not (-inf, 5), and `troponin <0.01` against
    # `0.00 - 0.04` is normal rather than undecidable. Ranges that admit
    # negative values (base excess, `-2 - 2`) get no such clamp, so a `<-3`
    # there is still read as genuinely unbounded below.
    if reference_range.low is not None and reference_range.low >= 0 and v_lo == -math.inf:
        v_lo, v_lo_inc = 0.0, True

    # Entirely inside [lo, hi]? An absent bound constrains nothing, so a
    # one-sided range (`< 200`) does not make every value undecidable.
    inside_low = lo == -math.inf or v_lo > lo or (v_lo == lo and v_lo_inc)
    inside_high = hi == math.inf or v_hi < hi or (v_hi == hi and v_hi_inc)
    if inside_low and inside_high:
        return AbnormalFlagComputation(flag=AbnormalFlag.NORMAL, basis=FlagBasis.NUMERIC_IN_RANGE)

    # Entirely above hi? The interval's lower end must clear hi.
    if v_lo > hi or (v_lo == hi and not v_lo_inc):
        return AbnormalFlagComputation(flag=AbnormalFlag.HIGH, basis=FlagBasis.NUMERIC_ABOVE_HIGH)

    # Entirely below lo?
    if v_hi < lo or (v_hi == lo and not v_hi_inc):
        return AbnormalFlagComputation(flag=AbnormalFlag.LOW, basis=FlagBasis.NUMERIC_BELOW_LOW)

    return AbnormalFlagComputation(
        flag=AbnormalFlag.INDETERMINATE, basis=FlagBasis.CENSORED_VALUE_AMBIGUOUS
    )


def _qualitative_flag(
    value: QualitativeLabValue, reference_range: ReferenceRange
) -> AbnormalFlagComputation:
    if reference_range.text is None:
        return AbnormalFlagComputation(
            flag=AbnormalFlag.INDETERMINATE, basis=FlagBasis.QUALITATIVE_NOT_COMPARABLE
        )
    observed = _normalise_term(value.text)
    expected = _normalise_term(reference_range.text)

    def polarity(term: str) -> bool | None:
        if term in _QUALITATIVE_NEGATIVE:
            return False
        if term in _QUALITATIVE_POSITIVE:
            return True
        return None

    observed_polarity = polarity(observed)
    expected_polarity = polarity(expected)
    if observed_polarity is None or expected_polarity is None:
        return AbnormalFlagComputation(
            flag=AbnormalFlag.INDETERMINATE, basis=FlagBasis.QUALITATIVE_NOT_COMPARABLE
        )
    if observed_polarity == expected_polarity:
        return AbnormalFlagComputation(
            flag=AbnormalFlag.NORMAL, basis=FlagBasis.QUALITATIVE_MATCHES_REFERENCE
        )
    return AbnormalFlagComputation(
        flag=AbnormalFlag.ABNORMAL, basis=FlagBasis.QUALITATIVE_OPPOSES_REFERENCE
    )


class CrossCheckOutcome(StrEnum):
    """The four ways a reported flag and a computed flag can relate."""

    AGREE = "agree"
    #: The one that is a verification failure. See FlagCrossCheck.
    DISAGREE = "disagree"
    #: The document printed a glyph we do not recognise, so there is nothing
    #: to compare. Not a failure: nothing was asserted that we can dispute.
    NOT_REPORTED = "not_reported"
    #: The value/range pair does not determine a flag (censored value,
    #: missing bounds, uncomparable term). Disclosed, never counted verified.
    NOT_COMPUTABLE = "not_computable"


class FlagCrossCheck(W2Contract):
    """
    What the page said against what the arithmetic says.

    The vision model reads a flag off the page (D3 stage 2); this code
    computes one from the value and the range. When they disagree, one of
    the two misread the document, and which one is not knowable from here —
    so the result is a **verification failure**, handled exactly as Week 1
    handles one: disclosed, never silently resolved in favour of either
    side (ARCHITECTURE.md §5).

    `NOT_COMPUTABLE` is deliberately not a failure. A censored value against
    an overlapping range has no computable flag; treating that as a
    contradiction would make the check cry wolf on correct data, which is
    the fastest way to get a safety check switched off.
    """

    reported: AbnormalFlag | None = Field(
        description="What the document's flag column says; None if the glyph was unrecognised."
    )
    computed: AbnormalFlag = Field(description="What compute_abnormal_flag concluded.")
    basis: FlagBasis
    outcome: CrossCheckOutcome

    @model_validator(mode="after")
    def _outcome_follows_from_inputs(self) -> Self:
        expected = _cross_check_outcome(self.reported, self.computed)
        if expected is not self.outcome:
            raise ValueError(
                f"outcome {self.outcome.value} does not follow from reported="
                f"{self.reported} computed={self.computed.value} (expected "
                f"{expected.value}); the cross-check is not a free-text field"
            )
        return self

    @property
    def is_verification_failure(self) -> bool:
        return self.outcome is CrossCheckOutcome.DISAGREE


def _cross_check_outcome(
    reported: AbnormalFlag | None, computed: AbnormalFlag
) -> CrossCheckOutcome:
    if computed is AbnormalFlag.INDETERMINATE:
        return CrossCheckOutcome.NOT_COMPUTABLE
    if reported is None:
        return CrossCheckOutcome.NOT_REPORTED
    return CrossCheckOutcome.AGREE if reported is computed else CrossCheckOutcome.DISAGREE


def cross_check_abnormal_flag(
    reported: AbnormalFlag | None,
    value: NumericLabValue | QualitativeLabValue,
    reference_range: ReferenceRange | None,
) -> FlagCrossCheck:
    """Run the pure rule and compare it with what the page reported."""
    computation = compute_abnormal_flag(value, reference_range)
    return FlagCrossCheck(
        reported=reported,
        computed=computation.flag,
        basis=computation.basis,
        outcome=_cross_check_outcome(reported, computation.flag),
    )


#: How a printed flag glyph maps onto `AbnormalFlag`. `""` is `normal` — see
#: the module docstring. `HH`/`LL` (critical) collapse onto high/low: they
#: say more than our enum does, but nothing it contradicts.
_REPORTED_FLAG_GLYPHS: dict[str, AbnormalFlag] = {
    "": AbnormalFlag.NORMAL,
    "n": AbnormalFlag.NORMAL,
    "wnl": AbnormalFlag.NORMAL,
    "h": AbnormalFlag.HIGH,
    "hh": AbnormalFlag.HIGH,
    "high": AbnormalFlag.HIGH,
    "l": AbnormalFlag.LOW,
    "ll": AbnormalFlag.LOW,
    "low": AbnormalFlag.LOW,
    "a": AbnormalFlag.ABNORMAL,
    "ab": AbnormalFlag.ABNORMAL,
    "abn": AbnormalFlag.ABNORMAL,
    "abnormal": AbnormalFlag.ABNORMAL,
}


def parse_reported_flag(glyph: str) -> AbnormalFlag | None:
    """
    The flag a report printed, or None if the glyph is not one we know.

    None is not "normal". An unrecognised glyph means the document asserted
    something we cannot read, and the cross-check treats it as
    `not_reported` rather than inventing agreement.
    """
    return _REPORTED_FLAG_GLYPHS.get(glyph.strip().lower())


#: `70 - 99`, `70-99`, `70 – 99` (en dash), `70 to 99`.
_RANGE_INTERVAL = re.compile(
    r"^(?P<low>-?\d+(?:\.\d+)?)\s*(?:-|–|—|to)\s*(?P<high>-?\d+(?:\.\d+)?)$"
)
#: `< 200`, `<=200`, `> 40`, `>= 40`.
_RANGE_BOUND = re.compile(r"^(?P<op><=|>=|<|>|≤|≥)\s*(?P<value>-?\d+(?:\.\d+)?)$")


def parse_reference_range_text(text: str, *, unit: str | None = None) -> ReferenceRange:
    """
    A printed reference range -> comparable bounds, keeping the text.

    Pure and total: anything it cannot parse comes back as a text-only
    `ReferenceRange`, which `compute_abnormal_flag` reports as
    `no_numeric_bounds` rather than failing. The original string always
    survives in `text`, because that is what the citation quotes.

    A one-sided printed bound is read as inclusive of the printed number's
    complement — `< 200` becomes `high=200`, meaning 200 itself is the top
    of normal. That is how lab reports are read in practice, and it is the
    conservative direction: it can only ever call a borderline value normal
    when the report meant to, never abnormal when it did not.
    """
    raw = text.strip()
    if not raw:
        raise ValueError("reference range text is not empty")

    interval = _RANGE_INTERVAL.match(raw)
    if interval:
        return ReferenceRange(
            low=float(interval.group("low")),
            high=float(interval.group("high")),
            text=raw,
            unit=unit,
        )

    bound = _RANGE_BOUND.match(raw)
    if bound:
        op = bound.group("op")
        number = float(bound.group("value"))
        if op in ("<", "<=", "≤"):
            return ReferenceRange(high=number, text=raw, unit=unit)
        return ReferenceRange(low=number, text=raw, unit=unit)

    return ReferenceRange(text=raw, unit=unit)
