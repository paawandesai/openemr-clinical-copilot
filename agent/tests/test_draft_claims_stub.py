"""
Tests for AGENT_STUB_LLM_DRAFTING (app/reasoning/draft_claims.py) — the
load-test-only short circuit around this module's one Anthropic call
(agent/load-test/README.md).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest import mock

import pytest
from structlog.testing import capture_logs

from app.models.fhir_resources import (
    AllergyIntolerance,
    Condition,
    FhirResourceType,
    MedicationRequest,
)
from app.reasoning import draft_claims

RETRIEVED = {
    FhirResourceType.CONDITION: [
        Condition(source_id="cond-1", display="Type 2 diabetes mellitus", clinical_status="active"),
        Condition(source_id="cond-2", display="Resolved sprain", clinical_status="resolved"),
    ],
    FhirResourceType.MEDICATION_REQUEST: [
        MedicationRequest(
            source_id="med-1",
            display="Metformin 500 MG Oral Tablet",
            status="active",
            authored_date=datetime(2020, 1, 1),
        ),
    ],
    FhirResourceType.ALLERGY_INTOLERANCE: [
        AllergyIntolerance(source_id="allergy-1", display="Penicillin", clinical_status="active"),
        # No name in the chart (F-57): withheld from drafting, stub included.
        AllergyIntolerance(source_id="allergy-2", display=None, clinical_status="active"),
    ],
}


def test_stub_drafts_claims_without_calling_anthropic() -> None:
    with (
        mock.patch.object(draft_claims, "_STUB_LLM_DRAFTING", True),
        mock.patch.object(
            draft_claims,
            "_client",
            side_effect=AssertionError("Anthropic client must not be built"),
        ),
    ):
        with capture_logs() as logs:
            draft = asyncio.run(
                draft_claims.draft_pre_visit_brief(RETRIEVED, patient_session_key="user:pt:sess")
            )

    # One claim per brief record the chart names: the resolved condition
    # is excluded (brief_records) and so is the unnamed allergy
    # (drafted_records), exactly as the real path would exclude them.
    assert [(c.resource_type, c.source_id, c.field, c.value) for c in draft.claims] == [
        (FhirResourceType.CONDITION, "cond-1", "display", "Type 2 diabetes mellitus"),
        (FhirResourceType.MEDICATION_REQUEST, "med-1", "display", "Metformin 500 MG Oral Tablet"),
        (FhirResourceType.ALLERGY_INTOLERANCE, "allergy-1", "display", "Penicillin"),
    ]
    assert draft.section_counts[FhirResourceType.CONDITION].retrieved == 2
    assert draft.section_counts[FhirResourceType.CONDITION].in_brief == 1

    event_types = [log["event_type"] for log in logs if log.get("event") == "agent_event"]
    assert event_types == ["llm_call_stubbed", "claims_drafted"]
    stub_event = next(log for log in logs if log.get("event_type") == "llm_call_stubbed")
    assert stub_event["event_data"]["claim_count"] == 3
    assert stub_event["patient_session_key"] == "user:pt:sess"


def test_stub_still_validates_missing_required_resource_type() -> None:
    incomplete = {FhirResourceType.CONDITION: RETRIEVED[FhirResourceType.CONDITION]}
    with mock.patch.object(draft_claims, "_STUB_LLM_DRAFTING", True):
        with pytest.raises(ValueError, match="MedicationRequest retrieval"):
            asyncio.run(draft_claims.draft_pre_visit_brief(incomplete))


def test_stub_flag_defaults_off() -> None:
    assert draft_claims._STUB_LLM_DRAFTING is False
