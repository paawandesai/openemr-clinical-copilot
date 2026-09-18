"""
Tests for render_brief in app/graph/pre_visit_brief.py — the render step's
guarantees, which a live smoke run cannot be relied on to exercise (the
model rarely drafts a failing claim, and the droplet's data decides which
sections are empty). Claims go through the real verifier; nothing is mocked.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from structlog.testing import capture_logs

from app.graph.pre_visit_brief import render_brief
from app.models.claims import Claim, VerificationResult
from app.models.fhir_resources import (
    AllergyIntolerance,
    Condition,
    FhirResourceType,
    MedicationRequest,
)
from app.models.metrics_events import UseCase
from app.reasoning.draft_claims import SectionCount, brief_records, unnamed_record_claims
from app.verification.checker import verify_claims

R = FhirResourceType

HTN = Condition(source_id="cond-1", display="HTN", clinical_status="active")
OLD_PROBLEM = Condition(source_id="cond-2", display="Resolved thing", clinical_status="resolved")
NORVASC = MedicationRequest(
    source_id="med-1", display="Norvasc", status="active", authored_date=datetime(2014, 2, 1)
)
LISINOPRIL = MedicationRequest(
    source_id="med-2", display="Lisinopril", status="active", authored_date=datetime(2014, 2, 1)
)
PENICILLIN = AllergyIntolerance(source_id="allergy-1", display="Penicillin")


def display_claim(resource_type: FhirResourceType, source_id: str, value: str) -> Claim:
    return Claim(resource_type=resource_type, source_id=source_id, field="display", value=value)


def render(retrieved: dict[FhirResourceType, list[Any]], claims: list[Claim]) -> str:
    """Counts exactly as draft_claims.py computes them, then verify, then render."""
    section_counts = {
        rt: SectionCount(retrieved=len(records), in_brief=len(brief_records(rt, records)))
        for rt, records in retrieved.items()
    }
    with capture_logs():
        results: list[VerificationResult] = verify_claims(
            claims, retrieved, use_case=UseCase.PRE_VISIT_BRIEF, patient_session_key="u:p:s"
        )
    return render_brief(results, section_counts, retrieved)


def test_verified_claims_render_plainly_in_drafted_order() -> None:
    text = render(
        {R.CONDITION: [HTN], R.MEDICATION_REQUEST: [NORVASC, LISINOPRIL], R.ALLERGY_INTOLERANCE: [PENICILLIN]},
        [
            display_claim(R.CONDITION, "cond-1", "HTN"),
            display_claim(R.MEDICATION_REQUEST, "med-1", "Norvasc"),
            display_claim(R.MEDICATION_REQUEST, "med-2", "Lisinopril"),
            display_claim(R.ALLERGY_INTOLERANCE, "allergy-1", "Penicillin"),
        ],
    )

    assert text == (
        "Pre-Visit Brief\n"
        "\n"
        "Active problems:\n"
        "  - HTN\n"
        "\n"
        "Current medications:\n"
        "  - Norvasc\n"
        "  - Lisinopril\n"
        "\n"
        "Allergies:\n"
        "  - Penicillin"
    )


def test_zero_retrieved_is_not_recorded_and_all_inactive_is_none_active() -> None:
    text = render(
        {R.CONDITION: [OLD_PROBLEM], R.MEDICATION_REQUEST: [NORVASC], R.ALLERGY_INTOLERANCE: []},
        [display_claim(R.MEDICATION_REQUEST, "med-1", "Norvasc")],
    )

    assert "Active problems: none active (1 on file, none with active status)" in text
    assert "Allergies: not recorded" in text
    assert "Resolved thing" not in text
    assert "no known allergies" not in text.lower()


def test_failed_claims_are_marked_unverified_never_dropped() -> None:
    text = render(
        {R.CONDITION: [HTN], R.MEDICATION_REQUEST: [NORVASC], R.ALLERGY_INTOLERANCE: []},
        [
            display_claim(R.CONDITION, "cond-1", "HTN"),
            display_claim(R.MEDICATION_REQUEST, "med-1", "Norvasc 10 MG"),
            # Cites a record in a section that came back empty.
            display_claim(R.ALLERGY_INTOLERANCE, "allergy-404", "Sulfa"),
        ],
    )

    assert '  - UNVERIFIED: "Norvasc 10 MG" (does not match the chart record)' in text
    assert "Allergies: not recorded\n" in text
    assert '  - UNVERIFIED: "Sulfa" (no matching record in the chart)' in text
    assert text.endswith("2 item(s) above could not be verified against the chart and are marked UNVERIFIED.")
    assert "  - HTN" in text


def test_record_with_no_claim_is_counted_not_silently_missing() -> None:
    text = render(
        {R.CONDITION: [HTN], R.MEDICATION_REQUEST: [NORVASC, LISINOPRIL], R.ALLERGY_INTOLERANCE: [PENICILLIN]},
        [
            display_claim(R.CONDITION, "cond-1", "HTN"),
            display_claim(R.MEDICATION_REQUEST, "med-1", "Norvasc"),
        ],
    )

    assert "  - 1 more active medication(s) on file, not included in this brief" in text
    assert "  - 1 more allergy record(s) on file, not included in this brief" in text
    assert "UNVERIFIED" not in text


def test_record_with_no_name_is_unverified_not_a_placeholder() -> None:
    """F-57: the pipeline states it (unnamed_record_claims) and the verifier fails it."""
    unnamed_allergy = AllergyIntolerance(source_id="allergy-2", display=None)
    unnamed_resolved = Condition(source_id="cond-3", display=None, clinical_status="resolved")
    retrieved: dict[FhirResourceType, list[Any]] = {
        R.CONDITION: [HTN, unnamed_resolved],
        R.MEDICATION_REQUEST: [],
        R.ALLERGY_INTOLERANCE: [PENICILLIN, unnamed_allergy],
    }
    text = render(
        retrieved,
        [
            display_claim(R.CONDITION, "cond-1", "HTN"),
            display_claim(R.ALLERGY_INTOLERANCE, "allergy-1", "Penicillin"),
            *unnamed_record_claims(retrieved),
        ],
    )

    assert text == (
        "Pre-Visit Brief\n"
        "\n"
        "Active problems:\n"
        "  - HTN\n"
        "\n"
        "Current medications: not recorded\n"
        "\n"
        "Allergies:\n"
        "  - Penicillin\n"
        '  - UNVERIFIED: "(name not available)" (the chart record has no usable name; review it in the chart)\n'
        "\n"
        "1 item(s) above could not be verified against the chart and are marked UNVERIFIED."
    )
