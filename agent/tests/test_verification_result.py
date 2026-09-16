"""
Tests for VerificationResult's structural guard (app/models/claims.py):
an accidental silent failure — passed=False, disclosed_as_unverified=False,
no suppression_reason — is a construction error anywhere in the codebase,
not only inside app/verification/checker.py.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.claims import Claim, VerificationResult
from app.models.fhir_resources import FhirResourceType
from app.models.metrics_events import UseCase
from app.verification.checker import verify_claims
from tests.test_verifier import RETRIEVED, SESSION_KEY

CLAIM = Claim(
    resource_type=FhirResourceType.CONDITION,
    source_id="cond-1",
    field="display",
    value="Type 2 diabetes mellitus",
)


def test_checker_output_still_validates() -> None:
    claims = [
        CLAIM,
        CLAIM.model_copy(update={"value": "Hypertension"}),
        CLAIM.model_copy(update={"source_id": "cond-404"}),
        CLAIM.model_copy(update={"field": "no_such_field"}),
    ]

    results = verify_claims(
        claims,
        RETRIEVED,
        use_case=UseCase.PRE_VISIT_BRIEF,
        patient_session_key=SESSION_KEY,
    )

    assert [r.passed for r in results] == [True, False, False, False]
    for result in results:
        assert result.suppression_reason is None
        assert VerificationResult.model_validate(result.model_dump()) == result


def test_failed_and_hidden_without_suppression_reason_raises() -> None:
    with pytest.raises(ValidationError, match="suppression_reason declares why"):
        VerificationResult(
            claim=CLAIM,
            passed=False,
            reason="value does not match source record",
            disclosed_as_unverified=False,
        )


def test_failed_and_hidden_with_suppression_reason_succeeds() -> None:
    result = VerificationResult(
        claim=CLAIM,
        passed=False,
        reason="value does not match source record",
        disclosed_as_unverified=False,
        suppression_reason="superseded by a verified claim on the same source",
    )

    assert result.disclosed_as_unverified is False
    assert result.suppression_reason == "superseded by a verified claim on the same source"


def test_failed_and_disclosed_needs_no_suppression_reason() -> None:
    result = VerificationResult(
        claim=CLAIM,
        passed=False,
        reason="value does not match source record",
        disclosed_as_unverified=True,
    )

    assert result.suppression_reason is None


@pytest.mark.parametrize("passed", [True, False])
@pytest.mark.parametrize("disclosed_as_unverified", [True, False])
@pytest.mark.parametrize("suppression_reason", [None, "deliberately suppressed"])
def test_only_undeclared_silent_failure_is_rejected(
    passed: bool, disclosed_as_unverified: bool, suppression_reason: str | None
) -> None:
    def build() -> VerificationResult:
        return VerificationResult(
            claim=CLAIM,
            passed=passed,
            disclosed_as_unverified=disclosed_as_unverified,
            suppression_reason=suppression_reason,
        )

    if not passed and not disclosed_as_unverified and suppression_reason is None:
        with pytest.raises(ValidationError):
            build()
    else:
        build()


def test_validating_serialized_silent_failure_raises() -> None:
    payload = {
        "claim": CLAIM.model_dump(),
        "passed": False,
        "reason": "source_id not found in retrieved resources",
        "disclosed_as_unverified": False,
    }

    with pytest.raises(ValidationError):
        VerificationResult.model_validate(payload)
