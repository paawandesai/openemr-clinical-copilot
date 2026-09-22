"""
The final answer contract — what the co-pilot is allowed to hand a clinician.

Two rules, both from CLAUDE.md invariant 3 ("Patient-record facts and
guideline evidence are distinct source_types. Never let a guideline
citation stand in for a chart fact, or the reverse"):

**Every claim carries at least one citation.** `min_length=1` on both claim
types. An uncited sentence is not a claim this contract can express, which
means the render step has nothing to render one from.

**Patient facts and guideline evidence live in separate lists, and a single
claim cannot mix them.** `PatientFactClaim` accepts `chart` and `document`
citations; `GuidelineClaim` accepts `guideline` and nothing else.

**Decision: a patient-fact claim's citations must all share one source
type.** The work order requires that a claim cannot mix patient facts with
guideline evidence; this goes one step further and forbids mixing *chart*
with *document* inside a single claim. A sentence supported partly by the
chart and partly by an uploaded PDF reads as one fact with one provenance,
and D2 is the reason that matters: an intake form's medication list and the
chart's medication list are exactly the two things that must never blur
together. Two sources means two claims, or a `Discrepancy`
(app/models/w2/intake_form.py) when the point is that they differ.

Unsupported fields ride along in their own list. Invariant 4: a value that
could not be located is "presented as unsupported ... never rendered as
fact and never counted as verified" — so it appears in the answer, in a
list nothing counts as a claim, exactly as Week 1 discloses a failed claim
rather than dropping it (app/verification/disclosure.py).
"""

from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from app.models.w2.base import CORRELATION_ID_PATTERN, W2Contract
from app.models.w2.citations import Citation
from app.models.w2.extraction import UnsupportedField
from app.models.w2.source_types import PATIENT_SOURCE_TYPES, SourceType


class PatientFactClaim(W2Contract):
    """A statement about this patient, from the chart or from a document."""

    statement: str = Field(min_length=1, description="The claim, as the clinician reads it.")
    citations: list[Citation] = Field(min_length=1)

    @model_validator(mode="after")
    def _one_patient_source_type(self) -> Self:
        kinds = {citation.source_type for citation in self.citations}
        outside = kinds - PATIENT_SOURCE_TYPES
        if outside:
            raise ValueError(
                f"a patient fact is not supported by {sorted(k.value for k in outside)} "
                "evidence; guideline citations belong in guideline_evidence (invariant 3)"
            )
        if len(kinds) > 1:
            raise ValueError(
                "a single claim draws on one source type; a statement backed by "
                "both the chart and a document is two claims, or a Discrepancy "
                "when the point is that they differ (D2)"
            )
        return self

    @property
    def source_type(self) -> SourceType:
        """Where this claim comes from. Single-valued by the validator above."""
        return self.citations[0].source_type


class GuidelineClaim(W2Contract):
    """
    A statement drawn from guideline evidence. Not about this patient.

    Kept in its own type rather than as a flag on `PatientFactClaim`
    because the difference is the thing invariant 3 protects, and a boolean
    on a shared type is one `if` away from being ignored.
    """

    statement: str = Field(min_length=1)
    citations: list[Citation] = Field(min_length=1)

    @model_validator(mode="after")
    def _guideline_only(self) -> Self:
        wrong = {
            citation.source_type
            for citation in self.citations
            if citation.source_type is not SourceType.GUIDELINE
        }
        if wrong:
            raise ValueError(
                f"guideline evidence is not cited from {sorted(k.value for k in wrong)} "
                "sources; a chart or document fact belongs in patient_facts (invariant 3)"
            )
        return self


class FinalAnswer(W2Contract):
    """
    One rendered answer, with its claims separated by provenance and its
    unsupported values disclosed.

    `answer` is the prose the clinician reads; the lists are what it is
    accountable to. Nothing here records whether a claim passed
    verification — that is `VerificationResult` (app/models/claims.py),
    produced by a separate step, and keeping it out means an answer cannot
    assert its own correctness.
    """

    answer: str = Field(min_length=1)
    patient_facts: list[PatientFactClaim] = Field(default_factory=list)
    guideline_evidence: list[GuidelineClaim] = Field(default_factory=list)
    unsupported: list[UnsupportedField] = Field(
        default_factory=list,
        description=(
            "Values that could not be located on their source page, "
            "disclosed rather than dropped (invariant 4). Never counted as "
            "claims and never counted as verified."
        ),
    )
    correlation_id: str = Field(pattern=CORRELATION_ID_PATTERN)

    @property
    def chart_facts(self) -> list[PatientFactClaim]:
        return [c for c in self.patient_facts if c.source_type is SourceType.CHART]

    @property
    def document_facts(self) -> list[PatientFactClaim]:
        return [c for c in self.patient_facts if c.source_type is SourceType.DOCUMENT]

    def all_citations(self) -> list[Citation]:
        """Every citation the answer rests on, claims only."""
        found: list[Citation] = []
        for claim in self.patient_facts:
            found.extend(claim.citations)
        for claim in self.guideline_evidence:
            found.extend(claim.citations)
        return found
