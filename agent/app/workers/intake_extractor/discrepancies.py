"""
Intake form vs chart: discrepancies, and nothing else (D2).

"Intake-form medications and allergies are stored as document-derived
facts ... and surfaced to the clinician as discrepancies against the chart.
They are never merged into the patient's medication or allergy list."

This module reads a finished `IntakeFormExtraction` and a `ChartSnapshot`
and returns `Discrepancy` objects. It has no write path, imports no
write-capable client, and returns nothing shaped like a chart record.

**Only located intake values take part.** A discrepancy is a statement that
the form says something; invariant 4 forbids making that statement about a
value the locate step could not place. So:

* an intake row whose name is located is compared;
* a row whose name is unlocated is not compared, and any chart record it
  *might* have matched (by its unverified reading) goes to `unresolved`
  instead of becoming an `omitted` discrepancy — "the form omits X" is not
  something this check can assert when the form may say X in a place code
  could not verify;
* a row whose name is illegible means no omission of that category can be
  asserted at all: every would-be `omitted` of that category is
  `unresolved`, and `complete` is False for it.

**Derived renderings are not discrepancies.** A patient writes the common
name (`Simvastatin`) where the chart holds the product string
(`Simvastatin 10 MG Oral Tablet`); the ground truth calls that rendering
`derived`. A medication matches a chart record when every word the patient
wrote is a word of the chart's product name, so a short name is the same
medication, and only an unmatched name is `added`. The dose is compared
separately, against the strengths printed in the product name, and only a
dose that matches none of them is `changed`.

**Matching is one-to-one and deterministic**: first every (intake, chart)
pair whose name and dose both agree, then remaining name-only pairs, each
in page / chart order. Two `Acetaminophen` rows at 160 MG and 325 MG pair
with the two chart products of those strengths, not both with the first.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pydantic import Field

from app.models.fhir_resources import AllergyIntolerance, FhirResourceType, MedicationRequest
from app.models.w2.base import W2Contract
from app.models.w2.citations import Citation
from app.models.w2.extraction import FieldStatus
from app.models.w2.intake_form import (
    Discrepancy,
    DiscrepancyCategory,
    DiscrepancyKind,
    IntakeFormExtraction,
)
from app.models.w2.source_types import SourceType
from app.workers.intake_extractor.chart import ChartSnapshot

_WORD = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
_STRENGTH = re.compile(r"(\d+(?:\.\d+)?)\s*([a-z%]+(?:/[a-z]+)?)")
_PARENTHETICAL = re.compile(r"\s*\([^)]*\)\s*$")


class DiscrepancyCheckStatus(StrEnum):
    COMPLETE = "complete"
    #: Not run: the document is not about this chart's patient (identity.py).
    BLOCKED_IDENTITY_MISMATCH = "blocked_identity_mismatch"
    #: Not run: the chart could not be read.
    CHART_UNAVAILABLE = "chart_unavailable"
    #: Not run: no chart reader was supplied (no clinician token).
    NOT_RUN = "not_run"


class UnresolvedComparison(W2Contract):
    """A chart record the check could neither match nor call omitted."""

    category: DiscrepancyCategory
    chart_citation: Citation
    why: str = Field(description="A fixed phrase, never page or chart content.")


class DiscrepancyReport(W2Contract):
    status: DiscrepancyCheckStatus
    discrepancies: list[Discrepancy] = Field(default_factory=list)
    unresolved: list[UnresolvedComparison] = Field(default_factory=list)
    medications_complete: bool = False
    allergies_complete: bool = False
    #: Intake rows left out of the comparison because their name is not a fact.
    intake_rows_not_compared: int = 0

    def counts(self) -> dict[str, int]:
        found = {kind.value: 0 for kind in DiscrepancyKind}
        for discrepancy in self.discrepancies:
            found[discrepancy.kind.value] += 1
        return found


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.casefold())


def _strengths(text: str) -> list[tuple[Decimal, str]]:
    found = []
    for number, unit in _STRENGTH.findall(text.casefold()):
        try:
            found.append((Decimal(number), unit))
        except InvalidOperation:  # pragma: no cover - guarded by the regex
            continue
    return found


def _dose(text: str) -> tuple[Decimal, str] | None:
    strengths = _strengths(text)
    return strengths[0] if len(strengths) == 1 else None


def _dose_consistent(dose: tuple[Decimal, str], chart_display: str) -> bool | None:
    """
    True if the written dose is one of the strengths in the chart's product
    name; False if the product states strengths and this is none of them;
    None if the product states no strength, so nothing can be compared.

    A unit agrees when it is the same, or when the written unit is the
    numerator of the chart's (`0.0272 MG` for `0.0272 MG/MG`, `10 MG` for
    `10 MG/ML`): a patient writes the amount, the product states a ratio.
    """
    strengths = _strengths(chart_display)
    if not strengths:
        return None
    number, unit = dose
    for chart_number, chart_unit in strengths:
        if chart_number != number:
            continue
        if chart_unit == unit or ("/" not in unit and chart_unit.split("/")[0] == unit):
            return True
    return False


def _name_matches(written: str, chart_display: str) -> bool:
    written_tokens = _tokens(written)
    chart_tokens = set(_tokens(chart_display))
    return bool(written_tokens) and all(token in chart_tokens for token in written_tokens)


def _allergen_key(text: str) -> str:
    return " ".join(_tokens(_PARENTHETICAL.sub("", text)))


def _allergen_matches(written: str, chart_display: str) -> bool:
    return _allergen_key(written) == _allergen_key(chart_display) and bool(_allergen_key(written))


def _chart_citation(
    resource_type: FhirResourceType, record: MedicationRequest | AllergyIntolerance
) -> Citation:
    return Citation(
        source_type=SourceType.CHART,
        source_id=record.source_id,
        page_or_section=resource_type.value,
        field_or_chunk_id="display",
        quote_or_value=record.display,
    )


def _medications(
    extraction: IntakeFormExtraction, chart: ChartSnapshot
) -> tuple[list[Discrepancy], list[UnresolvedComparison], bool, int]:
    chart_meds = [med for med in chart.current_medications if med.display]
    unnamed = [med for med in chart.current_medications if not med.display]
    presented = []
    unverified_names: list[str] = []
    unreadable = False
    for row in extraction.medications:
        name = row.name.as_presented_fact()
        if name is not None:
            dose_fact = row.dose.as_presented_fact()
            presented.append((row, name, dose_fact))
        elif row.name.status is FieldStatus.UNLOCATED and row.name.value:
            unverified_names.append(row.name.value)
        elif row.name.status is FieldStatus.ILLEGIBLE:
            unreadable = True

    matched_chart: set[int] = set()
    pairs: dict[int, int] = {}
    # Pass 1: name and dose agree.
    for position, (_, name, dose_fact) in enumerate(presented):
        dose = _dose(dose_fact.value) if dose_fact else None
        if dose is None:
            continue
        for index, med in enumerate(chart_meds):
            if index in matched_chart or not _name_matches(name.value, med.display or ""):
                continue
            if _dose_consistent(dose, med.display or ""):
                pairs[position] = index
                matched_chart.add(index)
                break
    # Pass 2: name only.
    for position, (_, name, _) in enumerate(presented):
        if position in pairs:
            continue
        for index, med in enumerate(chart_meds):
            if index not in matched_chart and _name_matches(name.value, med.display or ""):
                pairs[position] = index
                matched_chart.add(index)
                break

    found: list[Discrepancy] = []
    for position, (_, name, dose_fact) in enumerate(presented):
        if position not in pairs:
            found.append(
                Discrepancy(
                    category=DiscrepancyCategory.MEDICATION,
                    kind=DiscrepancyKind.ADDED,
                    subject=name.value,
                    document_value=(f"{name.value} {dose_fact.value}" if dose_fact else name.value),
                    document_citation=name.citation,
                )
            )
            continue
        med = chart_meds[pairs[position]]
        dose = _dose(dose_fact.value) if dose_fact else None
        if dose is not None and _dose_consistent(dose, med.display or "") is False:
            found.append(
                Discrepancy(
                    category=DiscrepancyCategory.MEDICATION,
                    kind=DiscrepancyKind.CHANGED,
                    subject=name.value,
                    document_value=f"{name.value} {dose_fact.value}",
                    chart_value=med.display,
                    document_citation=dose_fact.citation,
                    chart_citation=_chart_citation(FhirResourceType.MEDICATION_REQUEST, med),
                )
            )

    unresolved: list[UnresolvedComparison] = [
        UnresolvedComparison(
            category=DiscrepancyCategory.MEDICATION,
            chart_citation=_chart_citation(FhirResourceType.MEDICATION_REQUEST, med),
            why="the chart record has no usable name",
        )
        for med in unnamed
    ]
    for index, med in enumerate(chart_meds):
        if index in matched_chart:
            continue
        citation = _chart_citation(FhirResourceType.MEDICATION_REQUEST, med)
        if unreadable:
            unresolved.append(
                UnresolvedComparison(
                    category=DiscrepancyCategory.MEDICATION,
                    chart_citation=citation,
                    why="a medication row on the form is unreadable",
                )
            )
        elif any(_name_matches(name, med.display or "") for name in unverified_names):
            unresolved.append(
                UnresolvedComparison(
                    category=DiscrepancyCategory.MEDICATION,
                    chart_citation=citation,
                    why="a form row may name it but could not be verified on the page",
                )
            )
        else:
            found.append(
                Discrepancy(
                    category=DiscrepancyCategory.MEDICATION,
                    kind=DiscrepancyKind.OMITTED,
                    subject=med.display or "",
                    chart_value=med.display,
                    chart_citation=citation,
                )
            )
    complete = not unreadable and not unverified_names and not unnamed
    return found, unresolved, complete, len(unverified_names)


def _allergies(
    extraction: IntakeFormExtraction, chart: ChartSnapshot
) -> tuple[list[Discrepancy], list[UnresolvedComparison], bool, int]:
    chart_allergies = [a for a in chart.current_allergies if a.display]
    unnamed = [a for a in chart.current_allergies if not a.display]
    presented = []
    unverified: list[str] = []
    unreadable = False
    for row in extraction.allergies:
        substance = row.substance.as_presented_fact()
        if substance is not None:
            presented.append(substance)
        elif row.substance.status is FieldStatus.UNLOCATED and row.substance.value:
            unverified.append(row.substance.value)
        elif row.substance.status is FieldStatus.ILLEGIBLE:
            unreadable = True

    matched: set[int] = set()
    found: list[Discrepancy] = []
    for substance in presented:
        index = next(
            (
                i
                for i, allergy in enumerate(chart_allergies)
                if i not in matched and _allergen_matches(substance.value, allergy.display or "")
            ),
            None,
        )
        if index is None:
            found.append(
                Discrepancy(
                    category=DiscrepancyCategory.ALLERGY,
                    kind=DiscrepancyKind.ADDED,
                    subject=substance.value,
                    document_value=substance.value,
                    document_citation=substance.citation,
                )
            )
        else:
            matched.add(index)

    unresolved = [
        UnresolvedComparison(
            category=DiscrepancyCategory.ALLERGY,
            chart_citation=_chart_citation(FhirResourceType.ALLERGY_INTOLERANCE, allergy),
            why="the chart record has no usable name",
        )
        for allergy in unnamed
    ]
    for index, allergy in enumerate(chart_allergies):
        if index in matched:
            continue
        citation = _chart_citation(FhirResourceType.ALLERGY_INTOLERANCE, allergy)
        if unreadable or any(_allergen_matches(u, allergy.display or "") for u in unverified):
            unresolved.append(
                UnresolvedComparison(
                    category=DiscrepancyCategory.ALLERGY,
                    chart_citation=citation,
                    why=(
                        "an allergy row on the form is unreadable"
                        if unreadable
                        else "a form row may name it but could not be verified on the page"
                    ),
                )
            )
        else:
            found.append(
                Discrepancy(
                    category=DiscrepancyCategory.ALLERGY,
                    kind=DiscrepancyKind.OMITTED,
                    subject=allergy.display or "",
                    chart_value=allergy.display,
                    chart_citation=citation,
                )
            )
    complete = not unreadable and not unverified and not unnamed
    return found, unresolved, complete, len(unverified)


def compare_intake_to_chart(
    extraction: IntakeFormExtraction, chart: ChartSnapshot
) -> DiscrepancyReport:
    """Medication and allergy discrepancies between an intake form and the chart."""
    meds, med_unresolved, meds_complete, meds_skipped = _medications(extraction, chart)
    allergies, allergy_unresolved, allergies_complete, allergies_skipped = _allergies(
        extraction, chart
    )
    return DiscrepancyReport(
        status=DiscrepancyCheckStatus.COMPLETE,
        discrepancies=[*meds, *allergies],
        unresolved=[*med_unresolved, *allergy_unresolved],
        medications_complete=meds_complete,
        allergies_complete=allergies_complete,
        intake_rows_not_compared=meds_skipped + allergies_skipped,
    )
