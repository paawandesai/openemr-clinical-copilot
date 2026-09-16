#!/usr/bin/env python3
"""
Smoke test for app/reasoning/draft_claims.py — real FHIR retrieval from
the deployed OpenEMR droplet fed into a real Anthropic API call, no mocks.
Not a test suite: a one-shot script that prints the claims the model
actually drafts.

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
   USERS.md §3 Use Case 1 / KEY_METRICS.md Metric 2 case. The brief must
   carry an explicit "not recorded" allergy claim, not omit allergies.
   FHIR_EMPTY_ALLERGY_PATIENT_ID overrides discovery; otherwise the script
   picks the first patient with zero allergy records and at least one
   condition or medication record, so the allergy claim has to hold its
   place in a brief that has other content. No hardcoded UUID, for the
   same reason smoke_test_fhir.py gives (F-43: seeded data can change on
   reseed).

Exits nonzero if the empty-allergy patient's brief lacks the explicit
claim.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.fhir import client as fhir_client  # noqa: E402
from app.middleware.correlation import bind_correlation_id  # noqa: E402
from app.middleware.logging import configure_logging  # noqa: E402
from app.models.claims import Claim  # noqa: E402
from app.models.fhir_resources import FhirResourceType  # noqa: E402
from app.models.metrics_events import UseCase  # noqa: E402
from app.reasoning.draft_claims import (  # noqa: E402
    DOCUMENTATION_STATUS_FIELD,
    NOT_RECORDED,
    draft_pre_visit_brief,
    query_source_id,
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
        fhir_client.get_medication_requests(
            token, patient_id, use_case=UseCase.PRE_VISIT_BRIEF
        ),
        fhir_client.get_allergy_intolerances(
            token, patient_id, use_case=UseCase.PRE_VISIT_BRIEF
        ),
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


async def _run_brief(
    label: str, token: str, patient_id: str
) -> tuple[dict[FhirResourceType, list], list[Claim]]:
    print(f"\n{'=' * 72}\n{label}\npatient: {patient_id}\n{'=' * 72}")
    retrieved = await _retrieve(token, patient_id)
    for resource_type, records in retrieved.items():
        print(f"  retrieved {resource_type.value}: {len(records)} record(s)")
    print()

    claims = await draft_pre_visit_brief(retrieved)

    print(f"\n{len(claims)} claim(s) drafted:")
    for claim in claims:
        print(f"  {claim.model_dump_json()}")

    # A quick look at whether each claim points at something that was
    # actually provided. Not the §5 verifier — just enough to tell a
    # fabricated source_id apart from a real one in this output.
    retrieved_ids = {
        record.source_id for records in retrieved.values() for record in records
    }
    query_ids = {query_source_id(resource_type) for resource_type in retrieved}
    unknown = [c for c in claims if c.source_id not in retrieved_ids | query_ids]
    print(f"\n  claims citing a source_id that was never retrieved: {len(unknown)}")
    for claim in unknown:
        print(f"    {claim.model_dump_json()}")
    return retrieved, claims


def _allergy_claims(claims: list[Claim]) -> list[Claim]:
    return [claim for claim in claims if claim.resource_type == _ALLERGY]


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

    default_retrieved, default_claims = await _run_brief(
        "Case 1: smoke_test_fhir.py's patient", token, default_patient
    )
    _, empty_claims = await _run_brief(
        "Case 2: zero AllergyIntolerance records", token, empty_allergy_patient
    )

    expected = Claim(
        source_id=query_source_id(_ALLERGY),
        field=DOCUMENTATION_STATUS_FIELD,
        value=NOT_RECORDED,
        resource_type=_ALLERGY,
    )
    print(f"\n{'=' * 72}\nEmpty-allergy check\n{'=' * 72}")
    print(f"  expected: {expected.model_dump_json()}")
    allergy_claims = _allergy_claims(empty_claims)
    print(f"  allergy claims drafted: {len(allergy_claims)}")
    for claim in allergy_claims:
        print(f"    {claim.model_dump_json()}")
    passed = allergy_claims == [expected]
    print(f"  {'PASS' if passed else 'FAIL'}: explicit not-recorded allergy claim")

    if default_retrieved[_ALLERGY]:
        # Control: with allergy records present, the query-level source
        # isn't offered, so a "not recorded" claim would be fabricated.
        control = expected not in default_claims and bool(_allergy_claims(default_claims))
        print(
            f"  {'PASS' if control else 'FAIL'}: case 1 has allergy records, "
            "states them, and does not claim 'not recorded'"
        )

    if not passed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
