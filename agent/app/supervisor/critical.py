"""
Critical-value escalation — a panic value is surfaced, by rule, on every
answer that has it in hand, and the lab-result cap can never drop one.

**The table decides, not the model and not the page.** `CRITICAL_THRESHOLDS`
is a committed table: analyte, LOINC codes, the exact unit strings each
threshold is written in, and a low and/or high critical limit. A value is
critical when it is strictly beyond a limit. Nothing else is consulted: not a
printed flag (a laboratory's `H` is not a panic call, and many reports print
no flag at all — D20), not a reference range, not the drafting model.

**Units are matched exactly or not at all (D24).** A glucose in mmol/L is
never compared with a mg/dL limit; it is evaluated only against the mmol/L
row, and a unit the table does not list for that code is not evaluated —
the value is still shown wherever it is cited, it is just not called
critical by a number that means something else.

**Censored values are intervals (D11).** `<2.0` potassium is entirely below
2.5 and is critical; `<3.0` straddles 2.5 and is not called either way.

**Two strengths of finding.** A *critical value* is one the source states as
fact: a chart Observation, or a document value and unit the locate step
placed on the page. A *possible critical value* is a document value the model
read but the locate step could not place: invariant 4 forbids stating it, so
the finding names the test and the page and says to open the document —
it never states the number. Burying it would be the worse failure.

**These thresholds are a demo configuration, not clinical authority.** They
are typical of published hospital critical-value policies (the commonly
cited adult limits for these analytes), chosen conservatively, and have not
been reviewed for any particular site. A deployment replaces this table with
its own laboratory's list; the code does not change. Recorded in D40 item 9.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.models.fhir_resources import Observation
from app.models.w2.lab_pdf import LabPdfExtraction, LabResult
from app.models.w2.lab_values import NumericLabValue


class Direction(StrEnum):
    HIGH = "high"
    LOW = "low"


@dataclass(frozen=True)
class CriticalThreshold:
    analyte: str
    loinc_codes: frozenset[str]
    #: Exact unit strings (after trimming) this row's limits are written in.
    units: frozenset[str]
    low: float | None = None
    high: float | None = None
    #: Normalised printed names, for a document row with no assigned LOINC code.
    names: frozenset[str] = frozenset()


def _t(analyte, codes, units, low=None, high=None, names=()) -> CriticalThreshold:  # noqa: ANN001
    return CriticalThreshold(
        analyte, frozenset(codes), frozenset(units), low, high, frozenset(names)
    )


_THOUSANDS_PER_UL = ("K/uL", "10*3/uL", "10^3/uL", "x10^3/uL", "x10E3/uL", "thou/uL")

CRITICAL_THRESHOLDS: tuple[CriticalThreshold, ...] = (
    _t("Potassium", ("2823-3", "6298-4"), ("mmol/L", "mEq/L"), 2.5, 6.0, ("potassium", "k")),
    _t("Sodium", ("2951-2", "2947-0"), ("mmol/L", "mEq/L"), 120, 160, ("sodium", "na")),
    _t("Glucose", ("2345-7", "2339-0"), ("mg/dL",), 40, 500, ("glucose",)),
    _t("Glucose", ("14749-6", "15074-8"), ("mmol/L",), 2.2, 27.8, ("glucose",)),
    _t("Calcium", ("17861-6",), ("mg/dL",), 6.0, 13.0, ("calcium",)),
    _t(
        "Bicarbonate (CO2)",
        ("2028-9", "1963-8"),
        ("mmol/L", "mEq/L"),
        10,
        40,
        ("co2 bicarbonate", "co2", "bicarbonate"),
    ),
    _t("Magnesium", ("19123-9", "2601-3"), ("mg/dL",), 1.0, 4.9, ("magnesium",)),
    _t("Phosphorus", ("2777-1",), ("mg/dL",), 1.0, None, ("phosphorus",)),
    _t("Hemoglobin", ("718-7",), ("g/dL",), 7.0, 20.0, ("hemoglobin",)),
    _t("Platelets", ("777-3",), _THOUSANDS_PER_UL, 20, 1000, ("platelets",)),
    _t("WBC", ("6690-2",), _THOUSANDS_PER_UL, 2.0, 30.0, ("wbc",)),
    _t("INR", ("6301-6", "34714-6"), ("", "{INR}", "INR", "ratio"), None, 5.0, ("inr",)),
)

_DECIMAL = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)")


def _normalise_name(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())


def threshold_for(
    *, loinc_code: str | None, unit: str | None, name: str | None = None
) -> CriticalThreshold | None:
    """The row for this code (or, with no code, this printed name) in exactly this unit."""
    unit_text = (unit or "").strip()
    for row in CRITICAL_THRESHOLDS:
        if unit_text not in row.units:
            continue
        if loinc_code is not None:
            if loinc_code in row.loinc_codes:
                return row
        elif name is not None and _normalise_name(name) in row.names:
            return row
    return None


def evaluate(
    row: CriticalThreshold, value: float, comparator: str | None = None
) -> Direction | None:
    """
    HIGH or LOW when the whole interval the value denotes is strictly beyond a
    limit; None otherwise, including when a censored value straddles one.
    """
    if comparator in (None, ">", ">="):
        if row.high is not None and (value > row.high or (comparator == ">" and value >= row.high)):
            return Direction.HIGH
    if comparator in (None, "<", "<="):
        if row.low is not None and (value < row.low or (comparator == "<" and value <= row.low)):
            return Direction.LOW
    return None


def limit(row: CriticalThreshold, direction: Direction) -> float:
    value = row.high if direction is Direction.HIGH else row.low
    assert value is not None
    return value


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and _DECIMAL.fullmatch(value.strip()):
        return float(value.strip())
    return None


# --------------------------------------------------------------------------
# the chart
# --------------------------------------------------------------------------


def observation_direction(observation: Observation) -> tuple[CriticalThreshold, Direction] | None:
    number = _number(observation.value)
    row = threshold_for(loinc_code=observation.loinc_code, unit=observation.unit)
    if number is None or row is None:
        return None
    direction = evaluate(row, number)
    return (row, direction) if direction is not None else None


def keep_critical(
    narrowed: list[Observation], retrieved: list[Observation]
) -> tuple[list[Observation], int]:
    """
    The turn's bounded lab set with every critical result put back that the
    cap dropped, newest first. Returns (set, how many were put back). The cap
    exists to bound a prompt; it is never allowed to be the reason a panic
    value is not in front of the clinician.
    """
    kept_ids = {observation.source_id for observation in narrowed}
    restored = [
        observation
        for observation in retrieved
        if observation.source_id not in kept_ids and observation_direction(observation)
    ]
    if not restored:
        return narrowed, 0

    def key(observation: Observation) -> tuple[datetime, str]:
        return observation.effective_date.replace(tzinfo=None), observation.source_id

    return sorted([*narrowed, *restored], key=key, reverse=True), len(restored)


# --------------------------------------------------------------------------
# documents
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentCritical:
    """One lab row on a document whose value crosses a limit."""

    row: CriticalThreshold
    direction: Direction
    result: LabResult
    #: True when value and unit are located facts; False for a reading the
    #: locate step could not place (a *possible* critical value).
    located: bool
    page: int | None
    #: The unit as read (located or not); one of `row.units` by construction.
    unit: str


def document_criticals(extraction: LabPdfExtraction) -> list[DocumentCritical]:
    found: list[DocumentCritical] = []
    for result in extraction.results:
        value_field, unit_field = result.value, result.unit
        value = value_field.value
        if not isinstance(value, NumericLabValue):
            continue
        unit_fact = unit_field.as_presented_fact()
        unit = unit_fact.value if unit_fact is not None else unit_field.value
        name_fact = result.test_name.as_presented_fact()
        row = threshold_for(
            loinc_code=result.loinc_code,
            unit=unit,
            name=name_fact.value if name_fact is not None else result.test_key,
        )
        if row is None:
            continue
        direction = evaluate(row, value.value, value.comparator.value if value.comparator else None)
        if direction is None:
            continue
        located = value_field.as_presented_fact() is not None and unit_fact is not None
        citation = value_field.citation
        page = (
            citation.bbox.page
            if citation is not None and citation.bbox is not None
            else int(citation.page_or_section)
            if citation is not None and (citation.page_or_section or "").isdigit()
            else None
        )
        found.append(DocumentCritical(row, direction, result, located, page, (unit or "").strip()))
    return found
