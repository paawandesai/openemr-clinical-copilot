"""
The extracted-field wrapper — CLAUDE.md invariant 4, made structural.

Invariant 4: "An extracted value that cannot be located on its source page
is flagged `unsupported` and presented as unsupported. It is never rendered
as fact and never counted as verified."

D3 says why there are four statuses and not two. Extraction is three
stages: a text layer with word boxes, a vision model that emits schema
fields and no coordinates, and a deterministic locate step that matches
each emitted value back to the word boxes. Each stage fails differently:

* ``located``    — the locate step found the value on the page it came from.
                   The only status that is a fact.
* ``unlocated``  — the model read a value; code could not find it on the
                   page. This is invariant 4's "unsupported": the value is
                   kept and disclosed, never dropped and never asserted.
* ``missing``    — the document does not carry the field at all. Nothing was
                   read, so there is nothing to disclose but the absence.
* ``illegible``  — the field is on the page as ink but not as text. Nothing
                   was read. The doc-factory's own expectation for this case
                   is explicit: "An extractor must report it as illegible,
                   not guess" (intake-missing-allergy-006 ground truth).

**Why `unlocated` rather than `unsupported`.** Invariant 4 names the
*outcome* — the value is presented as unsupported. The status names the
*cause*, because the three non-fact statuses are presented the same way and
debugged completely differently: `unlocated` means the locate step and the
model disagree about the page, `missing` means the document is incomplete,
`illegible` means the scan is. `UnsupportedReason` below carries invariant
4's vocabulary out to the presentation layer, so nothing is lost.

**The type system is the first verifier.** `ExtractedField.value` is
`T | None` and every consumer would have to remember to check `status`
before reading it. So no consumer is given that opportunity: the only way
to obtain a value in a non-optional type is `as_presented_fact()`, which
returns `None` for every status but `located`, or `require_presented_fact()`,
which raises. A render function that annotates its parameter
`PresentedFact[str]` cannot be handed an unlocated one, and
`PresentedFact` re-checks the invariant on construction, so it cannot be
forged by hand either. That is the same move Week 1 made in
`VerificationResult._reject_undeclared_silent_failure`
(app/models/claims.py): make the unsafe state unconstructible rather than
auditing for it later.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from app.models.w2.base import W2Contract
from app.models.w2.citations import Citation
from app.models.w2.source_types import SourceType


class FieldStatus(StrEnum):
    """What the three-stage extraction (D3) concluded about one field."""

    LOCATED = "located"
    UNLOCATED = "unlocated"
    MISSING = "missing"
    ILLEGIBLE = "illegible"


#: The one status that may be presented as fact. A frozenset rather than an
#: `is LOCATED` comparison scattered around the codebase, so that widening
#: it would be one visible edit in one place — and a grep for this name
#: finds every site that depends on it.
PRESENTABLE_STATUSES = frozenset({FieldStatus.LOCATED})


class UnsupportedReason(StrEnum):
    """
    Invariant 4's vocabulary, as the presentation layer needs it. One member
    per non-presentable status; `FieldStatus.LOCATED` has no member because
    a located field is not unsupported.
    """

    #: unlocated: read by the model, not found on the page by the locate step.
    NOT_LOCATED_ON_PAGE = "not_located_on_page"
    #: missing: the document does not carry the field.
    NOT_PRESENT_IN_DOCUMENT = "not_present_in_document"
    #: illegible: present as ink, not as readable text.
    ILLEGIBLE_ON_PAGE = "illegible_on_page"


_REASON_FOR_STATUS: dict[FieldStatus, UnsupportedReason] = {
    FieldStatus.UNLOCATED: UnsupportedReason.NOT_LOCATED_ON_PAGE,
    FieldStatus.MISSING: UnsupportedReason.NOT_PRESENT_IN_DOCUMENT,
    FieldStatus.ILLEGIBLE: UnsupportedReason.ILLEGIBLE_ON_PAGE,
}


class UnsupportedFieldError(RuntimeError):
    """Raised by `require_presented_fact()` on a field that is not a fact."""


class PresentedFact[T](W2Contract):
    """
    A value that may be stated as fact, with the citation that supports it.

    Both fields are non-optional, so holding one of these is the proof: a
    `PresentedFact[float]` is a float and a located document citation, or it
    does not exist. The validator re-checks that the citation is a located
    document citation, which makes the type unforgeable rather than merely
    inconvenient to misuse.
    """

    value: T
    citation: Citation

    @model_validator(mode="after")
    def _really_supported(self) -> Self:
        if self.value is None:
            raise ValueError("a presented fact has a value; None is not a fact")
        if not self.citation.is_located_document:
            raise ValueError(
                "a presented fact is supported by a located document citation "
                "(source_type=document with a bbox); invariant 4"
            )
        return self


class UnsupportedField(W2Contract):
    """
    A field that is not a fact, in the shape the render step discloses it.

    Deliberately has no field called `value`. What the model read, when it
    read anything, is `unverified_value` — named so that a render site
    cannot use it without the name saying what it is. That mirrors Week 1's
    `disclosed_as_unverified` and the UNVERIFIED lines it produces
    (app/verification/disclosure.py): the failure is shown, and shown as a
    failure.
    """

    field_path: str = Field(
        min_length=1,
        description="Which field this is, in the schema's dotted-path vocabulary.",
    )
    reason: UnsupportedReason
    unverified_value: str | None = Field(
        default=None,
        description=(
            "What the model read, stringified, for `not_located_on_page` "
            "only. None for the other two reasons: nothing was read, and a "
            "value here would be a guess (the doc-factory's illegible case "
            "expects exactly that refusal)."
        ),
    )
    citation: Citation | None = Field(
        default=None,
        description=(
            "The document citation, when there is one. Never carries a bbox "
            "for `not_located_on_page` — that is what not located means — "
            "but may for `illegible_on_page`, where the bbox is the ink "
            "region and evidence that something is there."
        ),
    )

    @model_validator(mode="after")
    def _reason_rules(self) -> Self:
        if self.reason is not UnsupportedReason.NOT_LOCATED_ON_PAGE:
            if self.unverified_value is not None:
                raise ValueError(
                    f"{self.reason.value} means nothing was read; "
                    "unverified_value must be None rather than a guess"
                )
        if self.reason is UnsupportedReason.NOT_LOCATED_ON_PAGE:
            if self.citation is not None and self.citation.bbox is not None:
                raise ValueError("not_located_on_page cannot carry a bbox")
        if self.citation is not None and self.citation.source_type is not SourceType.DOCUMENT:
            raise ValueError("an unsupported field's citation is a document citation")
        return self


class ExtractedField[T](W2Contract):
    """
    One field pulled out of a document, with what the locate step concluded.

    Generic over the value type so that a lab result's numeric value, a
    medication's free text and a checkbox's boolean all use the same
    wrapper with the same rules, and `ExtractedField[float]` rejects a
    string at the boundary rather than inside the abnormal-flag
    computation.

    Construct these through `located()`, `unlocated()`, `missing()` and
    `illegible()` where you can: the constructors take exactly the
    arguments their status permits, so the combinations the validator has
    to reject are mostly unspellable.
    """

    status: FieldStatus
    value: T | None = Field(
        default=None,
        description=(
            "What the document says. Non-None for located and unlocated; "
            "None for missing and illegible, where nothing was read. Read "
            "it through as_presented_fact(), not directly."
        ),
    )

    citation: Citation | None = Field(
        default=None,
        description="The document citation supporting `value`; see the status rules.",
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "The extractor's confidence in `value`, when it reports one. "
            "Must be None where `value` is None: a confidence about nothing "
            "is a number that invites being compared to a threshold."
        ),
    )

    @model_validator(mode="after")
    def _status_rules(self) -> Self:
        status = self.status

        if status is FieldStatus.LOCATED:
            if self.value is None:
                raise ValueError("located field has a value")
            if self.citation is None:
                raise ValueError("located field requires a citation")
            if not self.citation.is_located_document:
                raise ValueError(
                    "located field requires a document citation with a bbox "
                    "— D3 makes the locate step the only source of one, and "
                    "invariant 4 makes an unlocatable value unsupported"
                )
            return self

        if status is FieldStatus.UNLOCATED:
            if self.value is None:
                raise ValueError(
                    "unlocated means the model read a value that code could "
                    "not place; a field with no value is missing or illegible"
                )
            if self.citation is not None:
                if self.citation.source_type is not SourceType.DOCUMENT:
                    raise ValueError("an extracted field cites a document")
                if self.citation.bbox is not None:
                    raise ValueError(
                        "unlocated field cannot carry a bbox: a bbox is what located means"
                    )
            return self

        # missing / illegible: nothing was read.
        if self.value is not None:
            raise ValueError(
                f"{status.value} field has no value — the document does not "
                "convey one. Putting the chart's value here would present "
                "chart truth as document content (invariant 4, D2)"
            )
        if self.confidence is not None:
            raise ValueError(f"{status.value} field has no value to be confident about")
        if status is FieldStatus.MISSING and self.citation is not None:
            raise ValueError("a field the document does not carry has nothing to cite")
        if self.citation is not None and self.citation.source_type is not SourceType.DOCUMENT:
            raise ValueError("an extracted field cites a document")
        return self

    # -- construction -----------------------------------------------------

    @classmethod
    def located(
        cls,
        value: T,
        citation: Citation,
        *,
        confidence: float | None = None,
    ) -> Self:
        return cls(
            status=FieldStatus.LOCATED,
            value=value,
            citation=citation,
            confidence=confidence,
        )

    @classmethod
    def unlocated(
        cls,
        value: T,
        *,
        citation: Citation | None = None,
        confidence: float | None = None,
    ) -> Self:
        return cls(
            status=FieldStatus.UNLOCATED,
            value=value,
            citation=citation,
            confidence=confidence,
        )

    @classmethod
    def missing(cls) -> Self:
        return cls(status=FieldStatus.MISSING)

    @classmethod
    def illegible(cls, *, citation: Citation | None = None) -> Self:
        return cls(status=FieldStatus.ILLEGIBLE, citation=citation)

    # -- reading ----------------------------------------------------------

    @property
    def is_presentable(self) -> bool:
        """Whether this field may be stated as fact. See PRESENTABLE_STATUSES."""
        return self.status in PRESENTABLE_STATUSES

    def as_presented_fact(self) -> PresentedFact[T] | None:
        """
        The value as a fact, or None if it is not one.

        The only route from an `ExtractedField` to a non-optional value. A
        caller that ignores the None gets a `None` where it wanted a value
        and fails immediately, rather than rendering an unlocated number.
        """
        if not self.is_presentable:
            return None
        value, citation = self.value, self.citation
        if value is None or citation is None:
            # Unreachable through the validator; kept as a hard stop rather
            # than an assert so that -O does not remove it.
            raise UnsupportedFieldError("located field lost its value or citation")
        return PresentedFact(value=value, citation=citation)

    def require_presented_fact(self) -> PresentedFact[T]:
        """As `as_presented_fact()`, but raises instead of returning None."""
        fact = self.as_presented_fact()
        if fact is None:
            raise UnsupportedFieldError(
                f"field is {self.status.value}, not a fact "
                f"({_REASON_FOR_STATUS[self.status].value}); invariant 4"
            )
        return fact

    def as_unsupported(self, field_path: str) -> UnsupportedField | None:
        """
        The disclosure record for a field that is not a fact, or None when it
        is one. The counterpart to `as_presented_fact()`: exactly one of the
        two returns a value for any field, so nothing can be silently
        neither shown nor disclosed.
        """
        if self.is_presentable:
            return None
        reason = _REASON_FOR_STATUS[self.status]
        return UnsupportedField(
            field_path=field_path,
            reason=reason,
            unverified_value=(
                str(self.value) if reason is UnsupportedReason.NOT_LOCATED_ON_PAGE else None
            ),
            citation=self.citation,
        )
