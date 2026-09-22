#!/usr/bin/env python3
"""
Smoke test for app/fhir/client.py — real calls against the deployed
OpenEMR droplet, no mocks. Not a test suite: a small, one-shot script that
proves the retrieval path end to end.

--------------------------------------------------------------------------
ONE-TIME PROVISIONING PREREQUISITE (this script does NOT do this for you)
--------------------------------------------------------------------------
findings.md F-02: a client registered via OpenEMR's own
`/oauth2/default/registration` endpoint is always created with
`is_enabled=0` and only the `authorization_code` grant — it cannot mint a
token as shipped. Enabling it is a direct-database administrative action,
not something a FHIR client (or this script) should do over the API. This
was done once, by hand, exactly like this, to produce the credentials this
script expects in the environment:

    docker exec <openemr-container> /root/devtools register-oauth2-client
    # prints a client_id and client_secret — save them, they are not
    # retrievable again (findings.md F-05: stored encrypted, not
    # reversible)

    # then, directly against the OpenEMR database (admin/operator action):
    UPDATE oauth_clients
       SET is_enabled = 1,
           grant_types = 'authorization_code,password,refresh_token'
     WHERE client_id = '<the id printed above>';

Then export the credentials before running this script:

    export FHIR_CLIENT_ID=<the id printed above>
    export FHIR_CLIENT_SECRET=<the secret printed above>
    python3 scripts/smoke_test_fhir.py

--------------------------------------------------------------------------
Grant type: password, not client_credentials
--------------------------------------------------------------------------
ARCHITECTURE.md §2: "Every FHIR read [the agent] performs runs under the
OAuth2 token of the clinician who is using it" — a client_credentials
grant has no specific clinician identity bound to it, so it cannot satisfy
that constraint even in principle. Checked directly against this
deployment before writing this script: its own OIDC discovery document
advertises `grant_types_supported: ["authorization_code", "password",
"refresh_token"]` — client_credentials isn't offered at all. The password
grant is what's actually configured and is what produces a token bound to
a real OpenEMR user, matching §2.

OpenEMR's password grant additionally requires a `user_role` form
parameter beyond the OAuth2 standard fields (confirmed by reading
src/Common/Auth/OpenIDConnect/Grant/CustomPasswordGrant.php,
src/Common/Auth/UuidUserAccount.php) — `users` for a clinician/staff login,
`patient` for the portal (which also requires `email`). This script always
requests `users`.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.fhir import client as fhir_client  # noqa: E402
from app.fhir.exceptions import FhirClientError  # noqa: E402
from app.middleware.correlation import bind_correlation_id  # noqa: E402
from app.middleware.logging import configure_logging  # noqa: E402
from app.models.metrics_events import UseCase  # noqa: E402

TOKEN_URL = f"{fhir_client.FHIR_BASE_URL.rsplit('/apis/', 1)[0]}/oauth2/default/token"

# The six scopes this script requests map exactly to the five in-scope
# resource types (ARCHITECTURE.md §3) plus `openid` (required for any
# OIDC-flavored grant on this server) and `user/Patient.read` (the
# implicit precondition read, §3 — "not a fourth tool-scope decision").
REQUIRED_SCOPES = (
    "openid "
    "user/Patient.read "
    "user/Condition.read "
    "user/MedicationRequest.read "
    "user/AllergyIntolerance.read "
    "user/Encounter.read "
    "user/Observation.read"
)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(
            f"ERROR: {name} is not set.\n\n"
            "This script never hardcodes a client_secret. See the "
            "provisioning prerequisite in this file's module docstring, "
            "then:\n\n"
            f"    export {name}=<value>\n",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


async def _get_password_grant_token(
    client_id: str, client_secret: str, username: str, password: str
) -> str:
    async with httpx.AsyncClient(verify=fhir_client._VERIFY_TLS, timeout=30.0) as http:
        response = await http.post(
            TOKEN_URL,
            data={
                "grant_type": "password",
                "client_id": client_id,
                "client_secret": client_secret,
                "username": username,
                "password": password,
                "user_role": "users",
                "scope": REQUIRED_SCOPES,
            },
        )
    if response.status_code != 200:
        print(f"Token request failed: HTTP {response.status_code}", file=sys.stderr)
        print(response.text, file=sys.stderr)
        sys.exit(1)
    body = response.json()
    granted = body.get("scope", "")
    requested = set(REQUIRED_SCOPES.split())
    missing = requested - set(granted.split())
    if missing:
        # findings.md F-09: the token response's own `scope` field has
        # been observed to disagree with what the JWT actually contains —
        # printed as a warning, not treated as fatal, for exactly that
        # documented reason.
        print(
            f"WARNING: token response 'scope' omits {missing} — per F-09 "
            "this field can understate what the JWT actually holds; "
            "proceeding.",
            file=sys.stderr,
        )
    return body["access_token"]


async def _discover_a_patient_id(token: str) -> str:
    """
    No hardcoded patient UUID: seeded data is population-specific and can
    change on reseed (findings.md F-43). FHIR_PATIENT_ID overrides this;
    otherwise, ask the server for one real patient. (`Patient` itself is
    not part of app/fhir/client.py's five in-scope functions — §3 treats
    it as an implicit precondition, not a tool-scope resource — so this
    lookup is inlined here rather than added to the committed client.)
    """
    override = os.environ.get("FHIR_PATIENT_ID")
    if override:
        return override
    async with httpx.AsyncClient(verify=fhir_client._VERIFY_TLS, timeout=30.0) as http:
        response = await http.get(
            f"{fhir_client.FHIR_BASE_URL}/Patient",
            headers={"Authorization": f"Bearer {token}"},
            params={"_count": 1},
        )
    response.raise_for_status()
    entries = response.json().get("entry", [])
    if not entries:
        print("ERROR: no patients found on this server at all.", file=sys.stderr)
        sys.exit(1)
    return entries[0]["resource"]["id"]


async def main() -> None:
    configure_logging()
    bind_correlation_id(str(uuid.uuid4()))

    client_id = _require_env("FHIR_CLIENT_ID")
    client_secret = _require_env("FHIR_CLIENT_SECRET")
    # Not secrets in the same sense as the client credentials above: these
    # are the documented, already-public demo values from findings.md
    # F-32 (username-as-password demo accounts), not something newly
    # introduced here. Still overridable, never required to be this.
    username = os.environ.get("FHIR_TEST_USERNAME", "physician")
    password = os.environ.get("FHIR_TEST_PASSWORD", "physician")

    print(f"Requesting a password-grant token for user '{username}'...")
    token = await _get_password_grant_token(client_id, client_secret, username, password)
    print("Token acquired.\n")

    patient_id = await _discover_a_patient_id(token)
    print(f"Testing against patient: {patient_id}\n")

    # use_case values below reflect each resource type's PRIMARY use case
    # per ARCHITECTURE.md §3's tool-scope table — this script doesn't run
    # as any one product use case, but tagging retrieval events this way
    # (rather than leaving use_case unset) proves the RetrievalEvent path
    # KEY_METRICS.md Metric 5 depends on actually emits, with a real,
    # source-grounded label rather than a placeholder.
    calls = [
        (
            "Condition",
            fhir_client.get_conditions(token, patient_id, use_case=UseCase.PRE_VISIT_BRIEF),
        ),
        (
            "MedicationRequest",
            fhir_client.get_medication_requests(
                token, patient_id, use_case=UseCase.PRE_VISIT_BRIEF
            ),
        ),
        (
            "AllergyIntolerance",
            fhir_client.get_allergy_intolerances(
                token, patient_id, use_case=UseCase.PRE_VISIT_BRIEF
            ),
        ),
        (
            "Encounter",
            fhir_client.get_encounters(
                token, patient_id, count=2, use_case=UseCase.CHANGE_DETECTION
            ),
        ),
        (
            "Observation",
            fhir_client.get_observations(token, patient_id, use_case=UseCase.CHANGE_DETECTION),
        ),
    ]

    for label, coro in calls:
        print(f"=== {label} ===")
        try:
            results = await coro
        except FhirClientError as exc:
            print(f"  FAILED: {type(exc).__name__}: {exc}")
            continue
        print(f"  {len(results)} result(s)")
        for item in results[:5]:
            print(f"    {item.model_dump()}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
