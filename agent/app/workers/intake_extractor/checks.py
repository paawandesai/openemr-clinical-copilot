"""
The abnormal-flag cross-check over a whole lab report (D11, D20).

Per row this is the contract's own `LabResult.cross_check_flag()`: the flag
computed from the value and reference range (`compute_abnormal_flag`), the
printed flag parsed from the flag column (`parse_reported_flag`), both read
through `as_presented_fact()` so an unlocated value can confirm nothing.
This module adds the document-level view:

* **A blank printed flag is `not_reported`, never `normal`** (D20). The
  assembler makes a blank flag column `missing`; the contract reads a
  missing flag as `ReportedFlag.NOT_REPORTED`.
* **Agreement is computed only over comparable flags and always carries its
  denominator.** `comparable` is the number of rows whose printed flag is in
  `COMPARABLE_REPORTED_FLAGS` — the D20 denominator — and `agreement_rate`
  is `agree / comparable`, or None when nothing was comparable. A report
  with no printed flags scores nothing, not 100%.
* **A disagreement is a verification failure, surfaced, not resolved.**
  Every `disagree` row comes back in `disagreements` with both flags and
  the value's citation; the worker reports it to the caller and counts it.
  Neither side is preferred and nothing is rewritten.
"""

from __future__ import annotations

from pydantic import Field

from app.models.w2.base import W2Contract
from app.models.w2.citations import Citation
from app.models.w2.lab_pdf import LabPdfExtraction
from app.models.w2.lab_values import (
    COMPARABLE_REPORTED_FLAGS,
    AbnormalFlag,
    CrossCheckOutcome,
    FlagBasis,
    ReportedFlag,
)


class FlagDisagreement(W2Contract):
    """One row where the page's flag and the arithmetic disagree."""

    test_key: str
    reported: ReportedFlag
    computed: AbnormalFlag
    basis: FlagBasis
    value_citation: Citation | None


class ComputedAbnormal(W2Contract):
    """
    A row the arithmetic calls abnormal while the page printed no flag (eval
    case W2-D3): not a disagreement — nothing was asserted to disagree with —
    but a computed abnormality the caller must not lose.
    """

    test_key: str
    computed: AbnormalFlag
    value_citation: Citation | None


class FlagCrossCheckSummary(W2Contract):
    rows: int = Field(description="Result rows in the extraction.")
    checked: int = Field(description="Rows with a located value, so a cross-check ran.")
    agree: int = 0
    disagree: int = 0
    not_reported: int = 0
    not_computable: int = 0
    comparable: int = Field(
        default=0,
        description="Rows whose printed flag is comparable (D20) — the agreement denominator.",
    )
    agreement_rate: float | None = Field(
        default=None, description="agree / comparable; None when comparable is 0."
    )
    disagreements: list[FlagDisagreement] = Field(default_factory=list)
    computed_abnormal_unflagged: list[ComputedAbnormal] = Field(default_factory=list)

    @property
    def has_verification_failure(self) -> bool:
        return self.disagree > 0


_ABNORMAL = frozenset({AbnormalFlag.HIGH, AbnormalFlag.LOW, AbnormalFlag.ABNORMAL})


def cross_check_report(extraction: LabPdfExtraction) -> FlagCrossCheckSummary:
    counts = {outcome: 0 for outcome in CrossCheckOutcome}
    comparable = 0
    checked = 0
    disagreements: list[FlagDisagreement] = []
    unflagged: list[ComputedAbnormal] = []
    for result in extraction.results:
        check = result.cross_check_flag()
        if check is None:
            continue
        checked += 1
        counts[check.outcome] += 1
        if check.reported in COMPARABLE_REPORTED_FLAGS:
            comparable += 1
        if check.is_verification_failure:
            disagreements.append(
                FlagDisagreement(
                    test_key=result.test_key,
                    reported=check.reported,
                    computed=check.computed,
                    basis=check.basis,
                    value_citation=result.citation,
                )
            )
        elif check.outcome is CrossCheckOutcome.NOT_REPORTED and check.computed in _ABNORMAL:
            unflagged.append(
                ComputedAbnormal(
                    test_key=result.test_key,
                    computed=check.computed,
                    value_citation=result.citation,
                )
            )
    agree = counts[CrossCheckOutcome.AGREE]
    return FlagCrossCheckSummary(
        rows=len(extraction.results),
        checked=checked,
        agree=agree,
        disagree=counts[CrossCheckOutcome.DISAGREE],
        not_reported=counts[CrossCheckOutcome.NOT_REPORTED],
        not_computable=counts[CrossCheckOutcome.NOT_COMPUTABLE],
        comparable=comparable,
        agreement_rate=(agree / comparable) if comparable else None,
        disagreements=disagreements,
        computed_abnormal_unflagged=unflagged,
    )
