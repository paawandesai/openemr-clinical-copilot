"""
Narrow FHIR resource models — ARCHITECTURE.md §3 (tool scope), §5 (verification).

ARCHITECTURE.md §3: "Tool scope is exactly the five FHIR resource types
USERS.md's three use cases name ... and no resource outside this set is in
scope for v1." These five models exist for exactly those five resource
types and no others.

Each model is deliberately narrow — ARCHITECTURE.md §5: the reasoning model
drafts claims bound to source_id + field + value, never given the full FHIR
resource shape to reason over. Every field below traces to a specific
use-case data requirement cited in ARCHITECTURE.md §3's tool-scope table
(itself a direct mapping from USERS.md §3) or to an explicit ARCHITECTURE.md
§5 instruction. No "for completeness" fields.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

# The three models a brief line names share one rule for `display`.
_NO_NAME = (
    " None when the chart record names nothing usable: no code text, no "
    "non-placeholder coding display, and no narrative the FHIR client can "
    "safely read (app/fhir/client.py _record_name). Never a placeholder "
    "string: a record with no name renders as visibly unnamed and "
    "UNVERIFIED, not under a stand-in value the verifier would confirm "
    "(docs/audit/findings.md F-57)."
)


class FhirResourceType(str, Enum):
    """
    The five, and only five, FHIR resource types in tool scope
    (ARCHITECTURE.md §3). `Patient` is deliberately excluded here even
    though the agent reads it implicitly to resolve which chart is open —
    ARCHITECTURE.md §3 calls that "not a fourth tool-scope decision," since
    it is a precondition of the workflow, not a resource a claim is ever
    attributed to.
    """

    CONDITION = "Condition"
    MEDICATION_REQUEST = "MedicationRequest"
    ALLERGY_INTOLERANCE = "AllergyIntolerance"
    ENCOUNTER = "Encounter"
    OBSERVATION = "Observation"


class Condition(BaseModel):
    """
    Active problem list (Use Case 1) and problem-level diffing (Use Case 2),
    per ARCHITECTURE.md §3's tool-scope table.
    """

    source_id: str = Field(..., description="Stable id a Claim can cite.")

    display: str | None = Field(
        ...,
        description=(
            "Human-readable problem name/description. Use Case 1 needs to "
            "state what the active problem *is* (ARCHITECTURE.md §3)."
        )
        + _NO_NAME,
    )

    clinical_status: str = Field(
        ...,
        description=(
            "e.g. 'active' / 'resolved'. Use Case 2 diffs 'problems added "
            "or resolved' between two encounters (ARCHITECTURE.md §3, "
            "citing USERS.md §3 Use Case 2) — not computable without a "
            "status field."
        ),
    )

    onset_date: datetime | None = Field(
        default=None,
        description=(
            "When the problem was noted. Use Case 2's diff is anchored to "
            "an interval between two specific encounters (ARCHITECTURE.md "
            "§3) — a problem needs a date to place it inside or outside "
            "that interval."
        ),
    )


class MedicationRequest(BaseModel):
    """
    Current medications (Use Case 1) and medication-level diffing (Use
    Case 2), per ARCHITECTURE.md §3's tool-scope table.
    """

    source_id: str = Field(..., description="Stable id a Claim can cite.")

    display: str | None = Field(
        ...,
        description=(
            "Medication name/text. Use Case 1 needs to state what the "
            "patient is currently taking (ARCHITECTURE.md §3)."
        )
        + _NO_NAME,
    )

    status: str = Field(
        ...,
        description=(
            "e.g. 'active' / 'stopped'. Use Case 2 diffs medications "
            "'started, stopped, or changed' between two encounters "
            "(ARCHITECTURE.md §3, citing USERS.md §3 Use Case 2)."
        ),
    )

    rxnorm_code: str | None = Field(
        default=None,
        description=(
            "ARCHITECTURE.md §5: medication claims bind to source_id *and* "
            "a standard RxNorm code — 99.7% coverage, 311 of 312 "
            "prescriptions (findings.md F-55). Optional, not required: "
            "coverage is 99.7%, not 100%, so the uncoded 0.3% must still "
            "be representable."
        ),
    )

    authored_date: datetime = Field(
        ...,
        description=(
            "When the medication was prescribed. Required for Use Case "
            "2's interval-based diff of what started/stopped/changed "
            "(ARCHITECTURE.md §3)."
        ),
    )


class AllergyIntolerance(BaseModel):
    """
    Allergy documentation — Use Case 1 only, per ARCHITECTURE.md §3's
    tool-scope table. USERS.md §3 Use Case 1 requires the recorded/
    not-recorded distinction to be explicit; that is a property of whether
    any row exists for a patient at all (a collection-level fact), not a
    field on a single resource, so it is not represented here. The
    active/resolved distinction *is* per-record, and is `clinical_status`
    below.
    """

    source_id: str = Field(..., description="Stable id a Claim can cite.")

    display: str | None = Field(
        ...,
        description=(
            "The allergen/substance name. Use Case 1 needs to state what "
            "the patient is allergic to (ARCHITECTURE.md §3)."
        )
        + _NO_NAME,
    )

    clinical_status: str = Field(
        ...,
        description=(
            "e.g. 'active' / 'resolved' / 'inactive'. OpenEMR emits "
            "'resolved' when the record has an end date and outcome 1, "
            "'inactive' for any other end date, and 'active' otherwise "
            "(FhirAllergyIntoleranceService.php:117-121). Required so a "
            "resolved allergy is not presented as a current one, and so "
            "'none active' is reachable for allergies exactly as it is "
            "for conditions and medications (findings.md F-58)."
        ),
    )

    recorded_date: datetime | None = Field(
        default=None,
        description="When the allergy was documented.",
    )


class Encounter(BaseModel):
    """
    Visit-level anchor for Use Case 2 (current visit vs. most recent
    prior, defining the diff interval) and Use Case 3 (the visit where
    something was noted or tried), per ARCHITECTURE.md §3's tool-scope
    table. Deliberately minimal: these use cases need an Encounter as a
    timepoint to anchor other resources against, not its own clinical
    content — that content is carried by the Condition / MedicationRequest
    / Observation resources tied to it, not by this model.
    """

    source_id: str = Field(..., description="Stable id a Claim can cite.")

    date: datetime = Field(
        ...,
        description=(
            "The encounter's own date. This *is* the value Use Case 2 "
            "needs to identify 'current visit' vs. 'most recent prior' "
            "(ARCHITECTURE.md §3)."
        ),
    )


class Observation(BaseModel):
    """
    Lab results only — Use Case 2's new-results-in-interval and Use Case
    3's reference to a specific past value, per ARCHITECTURE.md §3's
    tool-scope table. Vitals are excluded from tool scope entirely
    (ARCHITECTURE.md §3: recorded once per patient, never per encounter,
    so there is no second timepoint to diff — F-20).

    Deliberately does NOT include a normal/abnormal flag or a reference
    range field. ARCHITECTURE.md §5 is explicit this is a *data* gap, not
    a schema gap: zero reference ranges and zero abnormal flags exist
    anywhere in the source data (F-19), so a field here would silently
    imply a capability — "this value is characterized as high/low/normal"
    — the underlying system cannot support. The agent can verifiably cite
    a lab value, unit, and date; never a characterization of one.
    """

    source_id: str = Field(..., description="Stable id a Claim can cite.")

    value: str | float = Field(
        ...,
        description=(
            "The result value. ARCHITECTURE.md §5: the agent can "
            "'verifiably cite a lab value, unit, and date.'"
        ),
    )

    unit: str | None = Field(
        default=None,
        description=("Result units. ARCHITECTURE.md §5: 'cite a lab value, unit, and date.'"),
    )

    loinc_code: str = Field(
        ...,
        description=(
            "ARCHITECTURE.md §5: lab claims bind to source_id and a LOINC "
            "code ('the result_code column exists and is populated') — "
            "required, not optional, matching the source data's full "
            "coverage of this field."
        ),
    )

    effective_date: datetime = Field(
        ...,
        description=(
            "When the result was recorded. ARCHITECTURE.md §5: 'cite a lab value, unit, and date.'"
        ),
    )
