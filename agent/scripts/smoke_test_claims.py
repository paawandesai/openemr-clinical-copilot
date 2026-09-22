#!/usr/bin/env python3
"""
Smoke test for app/reasoning/draft_claims.py — real FHIR retrieval from
the deployed OpenEMR droplet fed into a real Anthropic API call, no mocks.
Not a test suite: a one-shot script that prints the claims the model
actually drafts, and the section counts returned alongside them.

Credentials come from the environment exactly as in
scripts/smoke_test_fhir.py (see its module docstring for the one-time
OAuth client provisioning), plus Anthropic credentials for the model call:

    export FHIR_CLIENT_ID=...
    export FHIR_CLIENT_SECRET=...
    export ANTHROPIC_API_KEY=...
    python3 scripts/smoke_test_claims.py

Two patients:

1. The same patient smoke_test_fhir.py tests against (FHIR_PATIENT_ID, or
   the first patient the server returns).
2. A patient whose AllergyIntolerance query returns zero records — the
   USERS.md §3 Use Case 1 case. The model drafts no allergy claim for it;
   the empty section must instead show up in `section_counts` as zero
   retrieved, for the rendering step to state as "not recorded".
   FHIR_EMPTY_ALLERGY_PATIENT_ID overrides discovery; otherwise the script
   picks the first patient with zero allergy records and at least one
   condition or medication record. No hardcoded UUID, for the same reason
   smoke_test_fhir.py gives (F-43: seeded data can change on reseed).

Exits nonzero if any claim cites a "query:" source_id, or if the
empty-allergy patient's section counts don't record the empty section.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from dataclasses import asdict

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.fhir import client as fhir_client  # noqa: E402
from app.middleware.correlation import bind_correlation_id  # noqa: E402
from app.middleware.logging import configure_logging  # noqa: E402
from app.models.fhir_resources import FhirResourceType  # noqa: E402
from app.models.metrics_events import UseCase  # noqa: E402
from app.reasoning.draft_claims import (  # noqa: E402
    PreVisitBriefDraft,
    SectionCount,
    draft_pre_visit_brief,
)
from scripts.smoke_test_fhir import (  # noqa: E402
    _discover_a_patient_id,
    _get_password_grant_token,
    _require_env,
)

_ALLERGY = FhirResourceType.ALLERGY_INTOLERANCE


async def _retrieve(token: str, patient_id: str) -> dict[FhirResourceType, list]:
    conditions, medications, allergies = await asyncio.gather(
        fhir_client.get_conditions(token, patient_id, use_case=UseCase.PRE_VISIT_BRIEF),
        fhir_client.get_medication_requests(token, patient_id, use_case=UseCase.PRE_VISIT_BRIEF),
        fhir_client.get_allergy_intolerances(token, patient_id, use_case=UseCase.PRE_VISIT_BRIEF),
    )
    return {
        FhirResourceType.CONDITION: conditions,
        FhirResourceType.MEDICATION_REQUEST: medications,
        _ALLERGY: allergies,
    }


async def _find_empty_allergy_patient(token: str, exclude: str) -> str:
    override = os.environ.get("FHIR_EMPTY_ALLERGY_PATIENT_ID")
    if override:
        return override
    async with httpx.AsyncClient(verify=fhir_client._VERIFY_TLS, timeout=30.0) as http:
        response = await http.get(
            f"{fhir_client.FHIR_BASE_URL}/Patient",
            headers={"Authorization": f"Bearer {token}"},
            params={"_count": 100},
        )
    response.raise_for_status()
    for entry in response.json().get("entry", []):
        patient_id = entry["resource"]["id"]
        if patient_id == exclude:
            continue
        allergies = await fhir_client.get_allergy_intolerances(token, patient_id)
        if allergies:
            continue
        conditions = await fhir_client.get_conditions(token, patient_id)
        medications = await fhir_client.get_medication_requests(token, patient_id)
        if conditions or medications:
            return patient_id
    print(
        "ERROR: no patient with zero AllergyIntolerance records and some "
        "other data. Set FHIR_EMPTY_ALLERGY_PATIENT_ID.",
        file=sys.stderr,
    )
    sys.exit(1)


async def _run_brief(label: str, token: str, patient_id: str) -> PreVisitBriefDraft:
    print(f"\n{'=' * 72}\n{label}\npatient: {patient_id}\n{'=' * 72}")
    retrieved = await _retrieve(token, patient_id)
    for resource_type, records in retrieved.items():
        print(f"  retrieved {resource_type.value}: {len(records)} record(s)")
    print()

    draft = await draft_pre_visit_brief(retrieved)

    print(f"\n{len(draft.claims)} claim(s) drafted:")
    for claim in draft.claims:
        print(f"  {claim.model_dump_json()}")

    print("\nsection_counts:")
    for resource_type, count in draft.section_counts.items():
        print(f"  {resource_type.value}: {asdict(count)}")

    # A quick look at whether each claim points at something that was
    # actually provided. Not the §5 verifier — just enough to tell a
    # fabricated source_id apart from a real one in this output.
    retrieved_ids = {record.source_id for records in retrieved.values() for record in records}
    unknown = [c for c in draft.claims if c.source_id not in retrieved_ids]
    print(f"\n  claims citing a source_id that was never retrieved: {len(unknown)}")
    for claim in unknown:
        print(f"    {claim.model_dump_json()}")
    return draft


def _report(passed: bool, message: str) -> bool:
    print(f"  {'PASS' if passed else 'FAIL'}: {message}")
    return passed


async def main() -> None:
    configure_logging()
    bind_correlation_id(str(uuid.uuid4()))

    client_id = _require_env("FHIR_CLIENT_ID")
    client_secret = _require_env("FHIR_CLIENT_SECRET")
    _require_env("ANTHROPIC_API_KEY")
    username = os.environ.get("FHIR_TEST_USERNAME", "physician")
    password = os.environ.get("FHIR_TEST_PASSWORD", "physician")

    print(f"Requesting a password-grant token for user '{username}'...")
    token = await _get_password_grant_token(client_id, client_secret, username, password)
    print("Token acquired.")

    default_patient = await _discover_a_patient_id(token)
    empty_allergy_patient = await _find_empty_allergy_patient(token, exclude=default_patient)

    default_draft = await _run_brief("Case 1: smoke_test_fhir.py's patient", token, default_patient)
    empty_draft = await _run_brief(
        "Case 2: zero AllergyIntolerance records", token, empty_allergy_patient
    )

    print(f"\n{'=' * 72}\nChecks\n{'=' * 72}")
    query_claims = [
        claim
        for claim in default_draft.claims + empty_draft.claims
        if claim.source_id.startswith("query:")
    ]
    no_query_claims = _report(
        not query_claims, f'no "query:" claims in either case ({len(query_claims)} found)'
    )

    empty_count = empty_draft.section_counts[_ALLERGY]
    empty_allergy_claims = [c for c in empty_draft.claims if c.resource_type == _ALLERGY]
    empty_captured = _report(
        empty_count == SectionCount(retrieved=0, in_brief=0) and not empty_allergy_claims,
        f"case 2 section_counts[AllergyIntolerance] = {asdict(empty_count)}, "
        f"with {len(empty_allergy_claims)} allergy claim(s) drafted",
    )

    # Control: allergy records present, counted, and drafted as claims.
    default_count = default_draft.section_counts[_ALLERGY]
    default_allergy_claims = [c for c in default_draft.claims if c.resource_type == _ALLERGY]
    if default_count.retrieved:
        _report(
            len(default_allergy_claims) == default_count.in_brief,
            f"case 1 section_counts[AllergyIntolerance] = {asdict(default_count)}, "
            f"with {len(default_allergy_claims)} allergy claim(s) drafted",
        )

    if not (no_query_claims and empty_captured):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
