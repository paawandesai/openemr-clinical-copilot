"""
The `intake_form` extraction schema, and the `Discrepancy` that is the only
thing intake data is ever turned into.

**D2 is the whole design of this module.** "Intake-form medications and
allergies are stored as document-derived facts, in their own space, and
surfaced to the clinician as discrepancies against the chart. They are
never merged into the patient's medication or allergy list, not even when
they match, and not behind a confidence threshold."

So this module has no function that returns a FHIR resource, no `merge`,
no `apply_to_chart`, no `reconcile`. `app/models/w2/writeback.py` — the
only module in Week 2 that writes anything into OpenEMR — accepts
`DocumentKind.LAB_PDF` and rejects `INTAKE_FORM` in a validator, so the
prohibition is enforced by a type rather than by everyone remembering it.
tests/test_w2_intake_form.py asserts both: that no merge-shaped symbol
exists here, and that the write-back request refuses an intake document.

A form is what a patient remembered in a waiting room; the chart is what a
clinician recorded. The co-pilot's job is to show where the two differ.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from app.models.w2.base import W2Contract
from app.models.w2.citations import Citation
from app.models.w2.documents import DocumentKind, DocumentPatientHeader, SourceDocument
from app.models.w2.extraction import ExtractedField
from app.models.w2.source_types import SourceType

INTAKE_FORM_SCHEMA_VERSION = "w2-contracts/intake_form/1.0.0"


class IntakeMedication(W2Contract):
    """
    One medication row as the patient wrote it.

    `route` has no counterpart in the doc-factory's forms and is optional
    here; it is in the schema because real intake forms ask for it and a
    contract that could not hold it would force the first branch that met
    one to change the schema. See `factory_mapping.SCHEMA_FIELDS_WITHOUT_
    FACTORY_PATH`.

    Note what is *not* here: no RxNorm code, no normalised drug name, no
    match to a chart medication. Those are reconciliation, and
    reconciliation is a discrepancy, not a field on the document.
    """

    row_index: int = Field(ge=0, description="Which row of the form this is.")
    name: ExtractedField[str]
    dose: ExtractedField[str] = Field(
        default_factory=lambda: ExtractedField[str].missing(),
        description="As written — `10 MG`, `half a tablet`. Not parsed into a quantity.",
    )
    frequency: ExtractedField[str] = Field(
        default_factory=lambda: ExtractedField[str].missing(),
        description="As written — `once daily`, `prn`.",
    )
    route: ExtractedField[str] = Field(default_factory=lambda: ExtractedField[str].missing())


class IntakeAllergy(W2Contract):
    """One allergy row. `substance` and `reaction`, both as written."""

    row_index: int = Field(ge=0)
    substance: ExtractedField[str]
    reaction: ExtractedField[str] = Field(
        default_factory=lambda: ExtractedField[str].missing(),
        description=(
            "What the patient wrote in the reaction column. On the local "
            "Synthea data this column is severity (`Mild`, `Severe`) rather "
            "than a reaction; the field keeps whatever the page says and "
            "does not reinterpret it."
        ),
    )


class FamilyHistoryEntry(W2Contract):
    """
    One condition on the family-history grid: a checkbox, and the relation
    written beside it when the box is ticked.

    `present` is `ExtractedField[bool]` rather than a bare bool because an
    unticked box and an unreadable box are different things, and a scanned
    form gives you both.
    """

    condition: str = Field(
        min_length=1,
        description="The condition the row is for, e.g. `diabetes` — the form's own label.",
    )
    present: ExtractedField[bool]
    relation: ExtractedField[str] = Field(default_factory=lambda: ExtractedField[str].missing())


class IntakeFormExtraction(W2Contract):
    """A whole intake form, extracted. Nothing here reaches the chart (D2)."""

    schema_version: Literal[INTAKE_FORM_SCHEMA_VERSION] = INTAKE_FORM_SCHEMA_VERSION
    extraction_id: str
    document: SourceDocument
    demographics: DocumentPatientHeader
    visit_date: ExtractedField[date] = Field(default_factory=lambda: ExtractedField[date].missing())
    chief_concern: ExtractedField[str] = Field(
        default_factory=lambda: ExtractedField[str].missing()
    )
    medications: list[IntakeMedication] = Field(default_factory=list)
    no_known_drug_allergies: ExtractedField[bool] = Field(
        default_factory=lambda: ExtractedField[bool].missing(),
        description=(
            "The 'no known drug allergies' box. Its own field rather than an "
            "empty allergy list, because a ticked box is an assertion and an "
            "empty list is an absence, and a clinician needs to tell them apart."
        ),
    )
    allergies: list[IntakeAllergy] = Field(default_factory=list)
    family_history: list[FamilyHistoryEntry] = Field(default_factory=list)
    signature_name: ExtractedField[str] = Field(
        default_factory=lambda: ExtractedField[str].missing()
    )
    signature_date: ExtractedField[date] = Field(
        default_factory=lambda: ExtractedField[date].missing()
    )

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.document.kind is not DocumentKind.INTAKE_FORM:
            raise ValueError(
                f"an intake_form extraction describes a "
                f"{DocumentKind.INTAKE_FORM.value} document, not {self.document.kind.value}"
            )
        for label, rows in (
            ("medications", self.medications),
            ("allergies", self.allergies),
        ):
            indices = [row.row_index for row in rows]
            if len(set(indices)) != len(indices):
                raise ValueError(f"{label} row_index values must be unique")
        conditions = [entry.condition for entry in self.family_history]
        if len(set(conditions)) != len(conditions):
            raise ValueError("family_history has one entry per condition")
        return self


# --------------------------------------------------------------------------
# Discrepancies: the only way intake content reaches a clinician.
# --------------------------------------------------------------------------


class DiscrepancyKind(StrEnum):
    """How the document and the chart differ about one thing."""

    #: On the form, not in the chart. The patient reports something the
    #: chart does not record.
    ADDED = "added"
    #: In the chart, not on the form. The factory's
    #: `intake-missing-allergy-006` is exactly this: the chart records an
    #: allergy to fish and the form omits it.
    OMITTED = "omitted"
    #: Both have it and they disagree — the factory's `med_discrepancy`
    #: perturbation, where the form says 10 MG and the chart product is
    #: 2.5 MG.
    CHANGED = "changed"


class DiscrepancyCategory(StrEnum):
    """What kind of thing the two sources disagree about."""

    MEDICATION = "medication"
    ALLERGY = "allergy"
    DEMOGRAPHIC = "demographic"
    FAMILY_HISTORY = "family_history"
    OTHER = "other"


class Discrepancy(W2Contract):
    """
    One difference between what a document says and what the chart says.

    Both sides are cited, and the citations are type-checked against their
    sides: the document side must be a `document` citation and the chart
    side a `chart` one. That is CLAUDE.md invariant 3 applied at the point
    where the two are most likely to be confused — a discrepancy is
    literally a place where two sources are held next to each other — and it
    makes a guideline citation unusable here, which it should be: a
    guideline has no opinion about what this patient's chart records.

    Nothing about this type resolves the difference. There is no `winner`,
    no `resolution`, no `confidence` that something might cross. D2: "The
    co-pilot's job is to show the clinician where the two disagree, not to
    pick a winner."
    """

    category: DiscrepancyCategory
    kind: DiscrepancyKind
    subject: str = Field(
        min_length=1,
        description=(
            "What the disagreement is about, in the clinician's terms — the "
            "medication or substance name, the demographic field. Rendered, "
            "never logged: CLAUDE.md invariant 1 keeps chart content out of "
            "logs, and this is chart content."
        ),
    )
    document_value: str | None = Field(
        default=None,
        description="What the document says. None for `omitted`.",
    )
    chart_value: str | None = Field(
        default=None,
        description="What the chart says. None for `added`.",
    )
    document_citation: Citation | None = Field(
        default=None,
        description="Where on the page. Required except for `omitted`, where there is no page.",
    )
    chart_citation: Citation | None = Field(
        default=None,
        description="Which chart record. Required except for `added`, where there is none.",
    )

    @model_validator(mode="after")
    def _sides_match_kind(self) -> Self:
        if self.document_citation is not None:
            if self.document_citation.source_type is not SourceType.DOCUMENT:
                raise ValueError(
                    "document_citation must be a document citation "
                    f"(got {self.document_citation.source_type.value}); invariant 3"
                )
        if self.chart_citation is not None:
            if self.chart_citation.source_type is not SourceType.CHART:
                raise ValueError(
                    "chart_citation must be a chart citation "
                    f"(got {self.chart_citation.source_type.value}); invariant 3"
                )

        match self.kind:
            case DiscrepancyKind.ADDED:
                if self.document_value is None:
                    raise ValueError("`added` means the document says something")
                if self.chart_value is not None or self.chart_citation is not None:
                    raise ValueError("`added` means the chart says nothing to cite")
                if self.document_citation is None:
                    raise ValueError("`added` requires the document citation")
            case DiscrepancyKind.OMITTED:
                if self.chart_value is None:
                    raise ValueError("`omitted` means the chart says something")
                if self.document_value is not None or self.document_citation is not None:
                    raise ValueError(
                        "`omitted` means the document says nothing; a citation "
                        "here would point at a value that is not on the page"
                    )
                if self.chart_citation is None:
                    raise ValueError("`omitted` requires the chart citation")
            case DiscrepancyKind.CHANGED:
                if self.document_value is None or self.chart_value is None:
                    raise ValueError("`changed` means both sources say something")
                if self.document_value == self.chart_value:
                    raise ValueError(
                        "`changed` means they differ; equal values are not a discrepancy"
                    )
                if self.document_citation is None or self.chart_citation is None:
                    raise ValueError("`changed` requires both citations")
        return self
