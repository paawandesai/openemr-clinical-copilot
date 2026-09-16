"""
Tests for app/verification/checker.py — the ARCHITECTURE.md §5 trust
boundary. No mocks of the checker's own logic: every test builds real
Claim and FHIR resource models and runs them through verify_claims.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from structlog.testing import capture_logs

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
from app.verification import checker
from app.verification.checker import FailureReason, verify_claims

SESSION_KEY = "user-1:patient-42:session-abc"

CONDITION = Condition(
    source_id="cond-1",
    display="Type 2 diabetes mellitus",
    clinical_status="active",
    onset_date=datetime(2021, 6, 1, 9, 30),
)
MEDICATION = MedicationRequest(
    source_id="med-1",
    display="Metformin 500 MG Oral Tablet",
    status="active",
    rxnorm_code="860975",
    authored_date=datetime(2024, 3, 15, 23, 30, tzinfo=timezone(timedelta(hours=-5))),
)
UNCODED_MEDICATION = MedicationRequest(
    source_id="med-2",
    display="Herbal supplement",
    status="active",
    rxnorm_code=None,
    # What app/fhir/client.py substitutes when authoredOn is absent.
    authored_date=datetime.min,
)
ALLERGY = AllergyIntolerance(source_id="allergy-1", display="Penicillin")
ENCOUNTER = Encounter(source_id="enc-1", date=datetime(2024, 3, 15, 10, 0))
OBSERVATION = Observation(
    source_id="obs-1",
    value=7.2,
    unit="%",
    loinc_code="4548-4",
    effective_date=datetime(2024, 3, 15, 10, 0),
)

RETRIEVED: dict[FhirResourceType, list] = {
    FhirResourceType.CONDITION: [CONDITION],
    FhirResourceType.MEDICATION_REQUEST: [MEDICATION, UNCODED_MEDICATION],
    FhirResourceType.ALLERGY_INTOLERANCE: [ALLERGY],
    FhirResourceType.ENCOUNTER: [ENCOUNTER],
    FhirResourceType.OBSERVATION: [OBSERVATION],
}


def claim(
    resource_type: FhirResourceType,
    source_id: str,
    field: str,
    value: str | float | int | None,
) -> Claim:
    return Claim(
        resource_type=resource_type,
        source_id=source_id,
        field=field,
        value=value,
    )


def verify(claims: list[Claim], retrieved: dict | None = None) -> list[VerificationResult]:
    return verify_claims(
        claims,
        RETRIEVED if retrieved is None else retrieved,
        use_case=UseCase.PRE_VISIT_BRIEF,
        patient_session_key=SESSION_KEY,
    )


def assert_passed(result: VerificationResult) -> None:
    assert result.passed is True
    assert result.reason is None
    assert result.disclosed_as_unverified is False


def assert_failed(result: VerificationResult, reason: FailureReason) -> None:
    assert result.passed is False
    assert result.reason == reason.value
    assert result.disclosed_as_unverified is True


# --- Required cases -------------------------------------------------------


def test_claim_with_existing_source_and_matching_value_passes() -> None:
    c = claim(FhirResourceType.CONDITION, "cond-1", "display", "Type 2 diabetes mellitus")

    [result] = verify([c])

    assert_passed(result)
    assert result.claim == c


def test_claim_with_source_id_not_in_retrieved_set_fails_and_is_disclosed() -> None:
    c = claim(FhirResourceType.CONDITION, "cond-999", "display", "Type 2 diabetes mellitus")

    [result] = verify([c])

    assert_failed(result, FailureReason.SOURCE_NOT_FOUND)
    assert result.reason == "source_id not found in retrieved resources"


def test_claim_with_existing_source_but_wrong_value_fails_and_is_disclosed() -> None:
    c = claim(FhirResourceType.CONDITION, "cond-1", "display", "Type 1 diabetes mellitus")

    [result] = verify([c])

    assert_failed(result, FailureReason.VALUE_MISMATCH)
    assert result.reason == "value does not match source record"


def test_claim_citing_resource_type_not_retrieved_this_turn_fails_and_is_disclosed() -> None:
    retrieved = {FhirResourceType.CONDITION: [CONDITION]}
    c = claim(FhirResourceType.ALLERGY_INTOLERANCE, "allergy-1", "display", "Penicillin")

    [result] = verify([c], retrieved)

    assert_failed(result, FailureReason.SOURCE_NOT_FOUND)


def test_claim_citing_resource_type_retrieved_but_empty_fails_and_is_disclosed() -> None:
    retrieved = {FhirResourceType.ALLERGY_INTOLERANCE: []}
    c = claim(FhirResourceType.ALLERGY_INTOLERANCE, "allergy-1", "display", "Penicillin")

    [result] = verify([c], retrieved)

    assert_failed(result, FailureReason.SOURCE_NOT_FOUND)


def test_empty_claims_list_returns_empty_results_and_emits_nothing() -> None:
    with capture_logs() as logs:
        results = verify([])

    assert results == []
    assert logs == []


def test_mixed_claims_each_get_their_own_result_in_order() -> None:
    claims = [
        claim(FhirResourceType.MEDICATION_REQUEST, "med-1", "rxnorm_code", "860975"),
        claim(FhirResourceType.CONDITION, "cond-404", "display", "Hypertension"),
        claim(FhirResourceType.OBSERVATION, "obs-1", "value", 7.2),
        claim(FhirResourceType.OBSERVATION, "obs-1", "value", 9.1),
        claim(FhirResourceType.ALLERGY_INTOLERANCE, "allergy-1", "display", "Penicillin"),
        claim(FhirResourceType.ENCOUNTER, "enc-1", "no_such_field", "x"),
    ]

    results = verify(claims)

    assert [r.claim for r in results] == claims
    assert_passed(results[0])
    assert_failed(results[1], FailureReason.SOURCE_NOT_FOUND)
    assert_passed(results[2])
    assert_failed(results[3], FailureReason.VALUE_MISMATCH)
    assert_passed(results[4])
    assert_failed(results[5], FailureReason.FIELD_NOT_ON_SOURCE)


# --- Event emission (KEY_METRICS.md Metrics 1 and 2) ----------------------


def test_claim_verified_event_fires_for_every_claim_pass_or_fail() -> None:
    claims = [
        claim(FhirResourceType.CONDITION, "cond-1", "clinical_status", "active"),
        claim(FhirResourceType.CONDITION, "cond-404", "clinical_status", "active"),
        claim(FhirResourceType.CONDITION, "cond-1", "clinical_status", "resolved"),
    ]

    with capture_logs() as logs:
        results = verify(claims)

    events = [log for log in logs if log.get("event_type") == "claim_verified"]
    assert len(events) == len(claims) == len(logs)
    for log, result in zip(events, results):
        event = ClaimVerifiedEvent.model_validate(log["event_data"])
        assert event.source_id == result.claim.source_id
        assert event.resource_type == result.claim.resource_type
        assert event.passed == result.passed
        assert event.disclosed_as_unverified == result.disclosed_as_unverified
        assert event.use_case == UseCase.PRE_VISIT_BRIEF
        assert event.patient_session_key == SESSION_KEY
        assert event.correlation_id == "no-request-context"
        assert log["use_case"] == UseCase.PRE_VISIT_BRIEF.value
        assert log["patient_session_key"] == SESSION_KEY
    assert [e["event_data"]["passed"] for e in events] == [True, False, False]


# --- Structural invariants -------------------------------------------------


def test_every_failed_result_is_disclosed_and_every_passed_result_is_not() -> None:
    claims = [
        claim(resource_type, source_id, field, value)
        for resource_type in FhirResourceType
        for source_id in ("cond-1", "med-1", "med-2", "allergy-1", "enc-1", "obs-1", "missing")
        for field, value in (
            ("display", "Penicillin"),
            ("value", 7.2),
            ("rxnorm_code", None),
            ("date", "2024-03-15"),
            ("source_id", source_id),
        )
    ]

    results = verify(claims)

    assert len(results) == len(claims)
    assert any(r.passed for r in results) and any(not r.passed for r in results)
    for result in results:
        assert result.disclosed_as_unverified is (not result.passed)
        assert (result.reason is None) is result.passed


def test_checker_module_imports_no_model_or_network_client() -> None:
    """ARCHITECTURE.md §5: verification is plain code, never a second model call."""
    source = Path(checker.__file__).read_text()
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden = ("anthropic", "langgraph", "langchain", "openai", "httpx", "requests", "app.fhir")
    violations = {
        module
        for module in imported
        for prefix in forbidden
        if module == prefix or module.startswith(prefix + ".")
    }
    assert violations == set()


# --- Lookup scoping ---------------------------------------------------------


def test_source_id_under_a_different_resource_type_does_not_verify() -> None:
    # "med-1" was retrieved, but as a MedicationRequest, not a Condition.
    c = claim(FhirResourceType.CONDITION, "med-1", "display", "Metformin 500 MG Oral Tablet")

    [result] = verify([c])

    assert_failed(result, FailureReason.SOURCE_NOT_FOUND)


@pytest.mark.parametrize(
    "field",
    ["loinc_code", "not_a_field", "model_dump", "__class__", "source_id", ""],
)
def test_field_that_is_not_a_claimable_model_field_fails(field: str) -> None:
    c = claim(FhirResourceType.CONDITION, "cond-1", field, "cond-1")

    [result] = verify([c])

    assert_failed(result, FailureReason.FIELD_NOT_ON_SOURCE)


def test_duplicate_retrieved_copies_that_agree_verify() -> None:
    retrieved = {FhirResourceType.CONDITION: [CONDITION, CONDITION.model_copy()]}
    c = claim(FhirResourceType.CONDITION, "cond-1", "clinical_status", "active")

    [result] = verify([c], retrieved)

    assert_passed(result)


def test_duplicate_retrieved_copies_that_disagree_do_not_verify() -> None:
    resolved = CONDITION.model_copy(update={"clinical_status": "resolved"})
    retrieved = {FhirResourceType.CONDITION: [CONDITION, resolved]}
    c = claim(FhirResourceType.CONDITION, "cond-1", "clinical_status", "active")

    [result] = verify([c], retrieved)

    assert_failed(result, FailureReason.VALUE_MISMATCH)


# --- Value comparison rules --------------------------------------------------


@pytest.mark.parametrize(
    ("claimed", "expected"),
    [
        ("Metformin 500 MG Oral Tablet", True),
        ("metformin 500 mg oral tablet", True),
        ("  Metformin   500 MG\tOral Tablet ", True),
        ("Metformin", False),
        ("Metformin 500 MG Oral Tablet, daily", False),
        ("Metformin 850 MG Oral Tablet", False),
        ("", False),
    ],
)
def test_text_fields_match_ignoring_case_and_whitespace_only(claimed: str, expected: bool) -> None:
    c = claim(FhirResourceType.MEDICATION_REQUEST, "med-1", "display", claimed)

    [result] = verify([c])

    assert result.passed is expected


@pytest.mark.parametrize(
    ("claimed", "expected"),
    [
        ("860975", True),
        (" 860975 ", True),
        (860975, False),
        (860975.0, False),
        ("860976", False),
    ],
)
def test_code_fields_require_a_string_claim(claimed: str | int | float, expected: bool) -> None:
    c = claim(FhirResourceType.MEDICATION_REQUEST, "med-1", "rxnorm_code", claimed)

    [result] = verify([c])

    assert result.passed is expected


@pytest.mark.parametrize(
    ("claimed", "expected"),
    [
        (7.2, True),
        ("7.2", True),
        ("7.20", True),
        (" 7.2 ", True),
        ("72e-1", True),
        (7.25, False),
        (7, False),
        ("7.2 %", False),
        ("7.2 mg/dL", False),
        ("7,2", False),
        ("nan", False),
        ("inf", False),
        ("", False),
        (None, False),
    ],
)
def test_numeric_lab_values_match_exactly_with_no_tolerance(
    claimed: str | float | int | None, expected: bool
) -> None:
    c = claim(FhirResourceType.OBSERVATION, "obs-1", "value", claimed)

    [result] = verify([c])

    assert result.passed is expected


def test_integer_claim_matches_whole_number_float_source() -> None:
    whole = OBSERVATION.model_copy(update={"source_id": "obs-2", "value": 140.0})
    retrieved = {FhirResourceType.OBSERVATION: [whole]}

    results = verify(
        [
            claim(FhirResourceType.OBSERVATION, "obs-2", "value", 140),
            claim(FhirResourceType.OBSERVATION, "obs-2", "value", "140"),
        ],
        retrieved,
    )

    assert all(r.passed for r in results)


@pytest.mark.parametrize(
    ("claimed", "expected"),
    [
        # Source is 2024-03-15T23:30-05:00; its own calendar date is the 15th
        # even though the same instant is the 16th in UTC.
        ("2024-03-15", True),
        ("2024-03-16", False),
        ("2024-03-15T23:30:00-05:00", True),
        ("2024-03-16T04:30:00+00:00", True),
        ("2024-03-15T23:30:00", False),
        ("2024-03-15T23:31:00-05:00", False),
        ("March 15, 2024", False),
        ("03/15/2024", False),
        (20240315, False),
        (None, False),
    ],
)
def test_datetime_fields(claimed: str | int | None, expected: bool) -> None:
    c = claim(FhirResourceType.MEDICATION_REQUEST, "med-1", "authored_date", claimed)

    [result] = verify([c])

    assert result.passed is expected


@pytest.mark.parametrize(
    ("field", "claimed", "expected"),
    [
        ("rxnorm_code", None, True),
        ("rxnorm_code", "860975", False),
        ("rxnorm_code", "", False),
        # authored_date is datetime.min here: a client placeholder, not data.
        ("authored_date", "0001-01-01", False),
        ("authored_date", "0001-01-01T00:00:00", False),
        ("authored_date", None, True),
    ],
)
def test_absent_source_values_only_match_a_null_claim(
    field: str, claimed: str | None, expected: bool
) -> None:
    c = claim(FhirResourceType.MEDICATION_REQUEST, "med-2", field, claimed)

    [result] = verify([c])

    assert result.passed is expected


def test_null_claim_against_present_source_value_fails() -> None:
    c = claim(FhirResourceType.CONDITION, "cond-1", "onset_date", None)

    [result] = verify([c])

    assert_failed(result, FailureReason.VALUE_MISMATCH)
