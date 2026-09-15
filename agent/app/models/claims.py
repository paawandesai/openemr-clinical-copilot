"""
Structured claim + verification result schema — ARCHITECTURE.md §5.

ARCHITECTURE.md §5: "The reasoning model never renders prose claims
directly. It drafts structured claims, each bound to source_id + field +
value. A deterministic, code-based verification step ... then confirms two
things for every claim before it renders: that the source_id exists among
what was actually retrieved this turn, and that the claimed value matches
what that source actually holds."

These two models are the data contract for that: Claim is what the model
drafts; VerificationResult is what the (not-yet-implemented) verification
step decides about it. No verification logic lives here — see
app/models/metrics_events.py for the log event shape that logic will
eventually emit, and KEY_METRICS.md Metrics 1 and 2, which are defined
directly in terms of VerificationResult's `passed` and
`disclosed_as_unverified` fields.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.fhir_resources import FhirResourceType


class Claim(BaseModel):
    """
    A single structured claim drafted by the reasoning model
    (ARCHITECTURE.md §5). Never rendered directly — always passed through
    verification first.
    """

    source_id: str = Field(
        ...,
        description=(
            "The FHIR resource id this claim is attributed to. "
            "Verification (§5) confirms this id exists among what was "
            "actually retrieved this turn before the claim may render."
        ),
    )

    field: str = Field(
        ...,
        description=(
            "Which field on the source resource this claim states a "
            "value for (e.g. 'display', 'authored_date' — see "
            "app/models/fhir_resources.py for the fields each resource "
            "type actually exposes)."
        ),
    )

    value: str | float | int | None = Field(
        ...,
        description=(
            "The value the model claims the source holds for `field`. "
            "Verification confirms this matches what the source actually "
            "contains (§5)."
        ),
    )

    resource_type: FhirResourceType = Field(
        ...,
        description=(
            "Which of the five FHIR resource types (ARCHITECTURE.md §3) "
            "source_id belongs to — Condition, MedicationRequest, "
            "AllergyIntolerance, Encounter, or Observation (see "
            "app/models/fhir_resources.py for the enum, shared with the "
            "metrics_events.py event models so this value is never a "
            "free-text string in two different shapes). Required so "
            "verification strength can vary by field/resource type per "
            "§5: medications bind to an RxNorm code, labs bind to a LOINC "
            "code only and can never carry a normal/abnormal "
            "characterization, because that data does not exist in the "
            "source (F-19)."
        ),
    )


class VerificationResult(BaseModel):
    """
    The outcome of running a Claim through the (not-yet-implemented)
    deterministic verification step (ARCHITECTURE.md §5). This shape is
    what KEY_METRICS.md Metrics 1 and 2 are computed directly from:

      Metric 1 (Verification Pass Rate)            = count(passed) / count(all)
      Metric 2 (Unverified-Claim Disclosure Rate)   = count(disclosed_as_unverified)
                                                       / count(not passed)
    """

    claim: Claim

    passed: bool = Field(
        ...,
        description=(
            "Whether source_id was found among this turn's retrieved data "
            "and the claimed value matched (§5). Feeds Metric 1 directly."
        ),
    )

    reason: str | None = Field(
        default=None,
        description=(
            "Populated on failure only: why verification failed — e.g. "
            "'source_id not retrieved this turn' or 'value mismatch'. "
            "§5 requires verification's failure mode to be legible, "
            "naming exactly which claim failed and why."
        ),
    )

    disclosed_as_unverified: bool = Field(
        ...,
        description=(
            "Whether a failed claim was surfaced to the physician as "
            "explicitly unverified. §5: a failed claim must be 'stated as "
            "explicitly unverified — never silently dropped, never "
            "softened into vaguer language that hides the failure.' "
            "Meaningful only when passed=False; feeds Metric 2 directly, "
            "which KEY_METRICS.md defines only over that failed subset."
        ),
    )
