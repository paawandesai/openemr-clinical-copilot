"""
Deterministic claim verification — ARCHITECTURE.md §5.

§5: "A deterministic, code-based verification step — not a second model
call — then confirms two things for every claim before it renders: that
the source_id exists among what was actually retrieved this turn, and
that the claimed value matches what that source actually holds."

This module is plain comparison code. It must never call an LLM, a FHIR
endpoint, or anything else with its own uncertainty — the whole point of
§5 is that this check's failure mode is legible and its answer is not a
second opinion. tests/test_verifier.py enforces the import side of that.

Every claim this module checks produces exactly one VerificationResult
and exactly one ClaimVerifiedEvent, pass or fail (KEY_METRICS.md Metrics
1 and 2).

Invariant: a failed claim always leaves here with
disclosed_as_unverified=True. KEY_METRICS.md Metric 2 allows "not
disclosed" only as a deliberate, later render-step decision — never as
something the verifier itself produces. VerificationResult
(app/models/claims.py) lets any caller set that field freely, so the
guarantee is enforced here instead: results are built only by _passed()
and _failed(), and _failed() has no parameter through which the field
could be set to anything but True.

What this module deliberately does NOT do (§5 known limitations):
retrieval completeness, source-data accuracy, multi-record synthesis
claims, and lab interpretation. It also has no patient field to compare
against — cross-patient contamination is caught only to the extent that
`retrieved` holds a single patient's resources.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from enum import Enum

from app.middleware.correlation import get_correlation_id
from app.middleware.logging import log_event
from app.models.claims import Claim, VerificationResult
from app.models.fhir_resources import (
    AllergyIntolerance,
    Condition,
    Encounter,
    FhirResourceType,
    MedicationRequest,
    Observation,
)
from app.models.metrics_events import ClaimVerifiedEvent, UseCase

FhirResource = (
    Condition | MedicationRequest | AllergyIntolerance | Encounter | Observation
)

# The FHIR client (app/fhir/client.py) substitutes datetime.min when a
# MedicationRequest.authoredOn or Observation.effectiveDateTime is absent,
# because those model fields are non-optional. That value is a placeholder,
# not chart data, so it is compared as if the field were None: no date
# claim can verify against it.
_ABSENT_DATETIME_SENTINEL = datetime.min

# Plain decimal / scientific notation only. Python's float() also accepts
# "nan", "inf", and "1_000", none of which is a value a chart holds.
_DECIMAL_PATTERN = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


class FailureReason(str, Enum):
    """
    The complete set of reasons a claim can fail. §5 requires the failure
    mode to be legible — "it names exactly which claim failed and why" —
    so reasons are a closed set, not free text.
    """

    SOURCE_NOT_FOUND = "source_id not found in retrieved resources"
    FIELD_NOT_ON_SOURCE = "field is not a claimable field on the source resource"
    VALUE_MISMATCH = "value does not match source record"


def verify_claims(
    claims: list[Claim],
    retrieved: Mapping[FhirResourceType, Sequence[FhirResource]],
    *,
    use_case: UseCase,
    patient_session_key: str,
) -> list[VerificationResult]:
    """
    Check every claim against what was actually retrieved this turn.
    Returns one VerificationResult per claim, in the same order as
    `claims`, and emits one ClaimVerifiedEvent per claim.

    `use_case` and `patient_session_key` are required, not optional:
    ClaimVerifiedEvent cannot be built without them, and emitting an event
    with a guessed value would corrupt Metrics 1 and 2's per-interaction
    rollup.
    """
    index = _index_by_source_id(retrieved)
    results: list[VerificationResult] = []
    for claim in claims:
        result = _verify_claim(claim, index)
        _emit_claim_verified_event(result, use_case, patient_session_key)
        results.append(result)
    return results


def _index_by_source_id(
    retrieved: Mapping[FhirResourceType, Sequence[FhirResource]],
) -> dict[FhirResourceType, dict[str, list[FhirResource]]]:
    """
    Per resource type, source_id -> every retrieved copy with that id. A
    list rather than a single resource: if the same resource was fetched
    twice in one turn, both copies are kept and a claim must agree with
    all of them (see _verify_claim).
    """
    index: dict[FhirResourceType, dict[str, list[FhirResource]]] = {}
    for resource_type, resources in retrieved.items():
        by_id = index.setdefault(resource_type, {})
        for resource in resources:
            by_id.setdefault(resource.source_id, []).append(resource)
    return index


def _verify_claim(
    claim: Claim,
    index: dict[FhirResourceType, dict[str, list[FhirResource]]],
) -> VerificationResult:
    # Scoped to the claim's own resource_type: an id that exists only
    # under a different type was not retrieved as the thing the claim
    # says it is.
    copies = index.get(claim.resource_type, {}).get(claim.source_id)
    if not copies:
        return _failed(claim, FailureReason.SOURCE_NOT_FOUND)

    if any(claim.field not in _claimable_fields(copy) for copy in copies):
        return _failed(claim, FailureReason.FIELD_NOT_ON_SOURCE)

    if not all(_values_match(claim.value, getattr(copy, claim.field)) for copy in copies):
        return _failed(claim, FailureReason.VALUE_MISMATCH)

    return _passed(claim)


def _passed(claim: Claim) -> VerificationResult:
    return VerificationResult(
        claim=claim,
        passed=True,
        reason=None,
        disclosed_as_unverified=False,
    )


def _failed(claim: Claim, reason: FailureReason) -> VerificationResult:
    # No disclosed_as_unverified parameter, by design — see module docstring.
    return VerificationResult(
        claim=claim,
        passed=False,
        reason=reason.value,
        disclosed_as_unverified=True,
    )


def _claimable_fields(resource: FhirResource) -> set[str]:
    """
    Declared model fields only — never arbitrary attributes, so a claim's
    `field` can't resolve to a method or dunder via getattr. source_id is
    excluded: a claim that a resource's id equals its own id asserts
    nothing about the chart and would inflate Metric 1.
    """
    return set(type(resource).model_fields) - {"source_id"}


def _values_match(claimed: str | float | int | None, actual: object) -> bool:
    """
    Fails closed: any source type not handled explicitly below never
    matches.
    """
    if actual is None or (
        isinstance(actual, datetime) and actual == _ABSENT_DATETIME_SENTINEL
    ):
        return claimed is None
    if claimed is None:
        return False
    if isinstance(actual, datetime):
        return _datetime_matches(claimed, actual)
    if isinstance(actual, bool):
        return False
    if isinstance(actual, (int, float)):
        return _number_matches(claimed, actual)
    if isinstance(actual, str):
        return isinstance(claimed, str) and _normalize_text(claimed) == _normalize_text(actual)
    return False


def _normalize_text(value: str) -> str:
    """
    Case- and whitespace-insensitive, nothing more. No substring, prefix,
    or fuzzy matching: "Metformin" does not match "Metformin 500 MG Oral
    Tablet".
    """
    return " ".join(value.split()).casefold()


def _number_matches(claimed: str | float | int, actual: int | float) -> bool:
    """
    Exact numeric equality, no tolerance — a rounded lab value is a
    different value. A string claim is accepted only if it is a plain
    decimal number ("7.2", "7.20"); anything with units or other text
    ("7.2 mg/dL") does not match.
    """
    if isinstance(claimed, bool):
        return False
    if isinstance(claimed, str):
        text = claimed.strip()
        if not _DECIMAL_PATTERN.fullmatch(text):
            return False
        return float(text) == actual
    return claimed == actual


def _datetime_matches(claimed: str | float | int, actual: datetime) -> bool:
    """
    A date-only ISO claim ("2024-03-15") matches if the source's calendar
    date — in the source's own recorded offset, not converted to UTC — is
    that day. A claim with a time component must equal the source
    datetime exactly (naive vs. timezone-aware never matches). Numeric
    claims (e.g. epoch seconds) never match.
    """
    if not isinstance(claimed, str):
        return False
    text = claimed.strip()
    try:
        return date.fromisoformat(text) == actual.date()
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text) == actual
    except ValueError:
        return False


def _emit_claim_verified_event(
    result: VerificationResult,
    use_case: UseCase,
    patient_session_key: str,
) -> None:
    event = ClaimVerifiedEvent(
        source_id=result.claim.source_id,
        resource_type=result.claim.resource_type,
        passed=result.passed,
        disclosed_as_unverified=result.disclosed_as_unverified,
        # Same fallback app/fhir/client.py uses outside a request context.
        correlation_id=get_correlation_id() or "no-request-context",
        use_case=use_case,
        patient_session_key=patient_session_key,
    )
    log_event(
        "claim_verified",
        event_data=event.model_dump(mode="json"),
        use_case=use_case,
        patient_session_key=patient_session_key,
    )
