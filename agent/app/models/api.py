"""
HTTP contracts for the agent service — the source of truth for what
POST /pre-visit-brief and GET /ready accept and return.

Every model forbids unknown fields and coerces nothing (strict), so a
request that does not match the contract is rejected with 422 rather than
reinterpreted. The clinician's bearer token is deliberately not a field
anywhere here: it travels only in the Authorization header
(ARCHITECTURE.md §2), so it cannot end up in a logged or echoed body.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.fhir_resources import FhirResourceType

# OpenEMR's FHIR ids are lowercase UUIDs (confirmed live). A canonical form
# only: anything else is rejected at the boundary instead of being sent to
# FHIR, which answers a malformed id in a search with empty results.
PATIENT_ID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"

# Letters, digits and hyphens only: the id becomes the third part of the
# colon-separated patient_session_key, so it can never contain a colon.
SESSION_ID_PATTERN = r"^[A-Za-z0-9-]{8,64}$"


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class PreVisitBriefRequest(_Contract):
    patient_id: str = Field(
        pattern=PATIENT_ID_PATTERN,
        description="FHIR Patient id (lowercase UUID) of the chart being opened.",
        examples=["a2c0e096-23d5-4cf9-ad5b-4895597363a7"],
    )
    session_id: str | None = Field(
        default=None,
        pattern=SESSION_ID_PATTERN,
        description=(
            "The caller's session for this chart. Omit to have one generated; "
            "reuse the returned value to keep later calls in the same session."
        ),
    )


class VerificationSummary(_Contract):
    claims: int
    verified: int
    unverified: int


class ClaimOutcome(_Contract):
    resource_type: FhirResourceType
    source_id: str
    field: str
    value: str | float | int | None
    verified: bool
    unverified_reason: str | None = Field(
        description="Why verification failed; null when verified."
    )


class SectionSummary(_Contract):
    resource_type: FhirResourceType
    retrieved: int = Field(description="Records FHIR returned for this section.")
    in_brief: int = Field(description="Of those, the ones the brief reports on (active).")


class Timing(_Contract):
    retrieve_seconds: float
    draft_seconds: float
    verify_seconds: float
    render_seconds: float
    total_seconds: float = Field(
        description="Pipeline start to brief rendered; the logged latency_event interval."
    )


class PreVisitBriefResponse(_Contract):
    brief: str = Field(description="The physician-facing Pre-Visit Brief text.")
    patient_id: str
    session_id: str
    patient_session_key: str = Field(
        description="(user, patient, session) as user:patient:session — ARCHITECTURE.md §4."
    )
    correlation_id: str
    verification: VerificationSummary
    claims: list[ClaimOutcome]
    sections: list[SectionSummary]
    timing: Timing


class ErrorCode(str, Enum):
    MISSING_BEARER_TOKEN = "missing_bearer_token"
    MALFORMED_BEARER_TOKEN = "malformed_bearer_token"
    FHIR_TOKEN_REJECTED = "fhir_token_rejected"
    INVALID_REQUEST = "invalid_request"
    PATIENT_NOT_FOUND = "patient_not_found"
    FHIR_UNAVAILABLE = "fhir_unavailable"
    BRIEF_GENERATION_FAILED = "brief_generation_failed"
    INTERNAL_ERROR = "internal_error"


class ErrorResponse(_Contract):
    error: ErrorCode
    message: str = Field(description="Safe to show; never contains exception text.")
    correlation_id: str


class DependencyCheck(_Contract):
    status: Literal["healthy", "unhealthy", "disabled"]
    required: bool = Field(description="Whether this check decides readiness.")
    detail: str | None = None


class ReadyResponse(_Contract):
    status: Literal["ready", "not_ready"]
    checks: dict[str, DependencyCheck]
