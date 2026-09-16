"""
D — Authorization boundary (F-07, F-08, F-09; ARCHITECTURE.md §2; KEY_METRICS.md
Metric 3; the 401 handling in app/api/pre_visit_brief.py).

Every token here is either a real OpenEMR token or derived from one. All
requests target a patient who has problems, medications and allergies on
file, so a brief that came back "empty" or "not recorded" would be a lie, not
a coincidence.

The two failures that matter most:
  - A rejected read turned into an empty section. The graph queries three
    resource types; if a 401 on one of them were swallowed, the brief would
    say "Allergies: not recorded" for a patient with allergies (F-21 meets
    F-07). D3 targets exactly that.
  - The agent trusting what a JWT says about itself. The endpoint reads `sub`
    without checking the signature and relies on OpenEMR rejecting a forged
    token at the first FHIR read (app/api/pre_visit_brief.py docstring). D4
    and D5 check that reliance holds.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import secrets
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from evals.harness import b64url, case, jwt_claims

PATIENT = "coded_allergies"


def _no_brief_violations(reply: Any, chart: Any, tokens_sent: list[str], expected_error: str) -> list[str]:
    violations = []
    if reply.status != 401:
        violations.append(f"HTTP {reply.status}, expected 401")
    if reply.error != expected_error:
        violations.append(f"error {reply.error!r}, expected {expected_error!r}")
    if reply.body and {"brief", "claims", "sections"} & set(reply.body):
        violations.append("response carries brief content")
    lowered = reply.text.lower()
    if "not recorded" in lowered or "none active" in lowered:
        violations.append("response text contains an empty-section statement")
    leaked = sorted(n for n in chart.all_names if len(n) > 4 and n in lowered)
    if leaked:
        violations.append(f"response text contains chart content: {leaked[:3]}")
    if any(t and t in reply.text for t in tokens_sent):
        violations.append("response echoes the bearer token")
    return violations


def _llm_violations(run: Any) -> list[str]:
    called = [
        e["event_type"]
        for e in run.events
        if e["event_type"] in ("llm_usage_event", "llm_call_failed", "claims_drafted")
    ]
    return [f"the model was called for a rejected request: {called}"] if called else []


@case(
    "D1",
    title="OpenEMR itself refuses every out-of-scope read for a Patient.read-only token (first-hand F-07/F-08)",
    guards="the boundary the whole design delegates to (no agent-side ACL) silently regressing — e.g. an "
    "OpenEMR upgrade that stops enforcing scope",
    cites="F-07 (operator-reported; out-of-scope 401s were unreproduced), F-08, ARCHITECTURE.md §2, "
    "KEY_METRICS.md Metric 3",
    level="live OpenEMR FHIR API",
)
def test_d1_openemr_enforces_scope(cfg: Any, http: Any, aio: Any, tokens: Any, panel: Any, observe: Any) -> None:
    chart = panel.chart(PATIENT)
    token = tokens.get("patient_only")
    headers = {"Authorization": f"Bearer {token}"}
    out_of_scope = ("Condition", "MedicationRequest", "AllergyIntolerance", "Encounter", "Observation")

    async def probe() -> dict[str, Any]:
        base = cfg.fhir_base_url
        results = {"Patient (in scope)": await http.get(f"{base}/Patient/{chart.patient_id}", headers=headers)}
        for rt in out_of_scope:
            results[rt] = await http.get(f"{base}/{rt}", params={"patient": chart.patient_id}, headers=headers)
        results["Bearer garbage"] = await http.get(
            f"{base}/Patient", params={"_count": 5}, headers={"Authorization": "Bearer garbage"}
        )
        return results

    results = aio.run(probe())
    observe("JWT scopes", jwt_claims(token).get("scopes"))
    for label, response in results.items():
        message = (response.json() or {}).get("message") if response.status_code != 200 else "(resource)"
        observe(label, f"HTTP {response.status_code} {message!r}")

    violations = []
    if results["Patient (in scope)"].status_code != 200:
        violations.append(f"in-scope Patient read returned {results['Patient (in scope)'].status_code}")
    rejected = [rt for rt in out_of_scope if results[rt].status_code == 401]
    observe("Metric 3 (out-of-scope requests rejected)", f"{len(rejected)}/{len(out_of_scope)}")
    violations += [
        f"{rt} read returned HTTP {results[rt].status_code} without the scope"
        for rt in out_of_scope
        if rt not in rejected
    ]
    if results["Bearer garbage"].status_code != 401:
        violations.append(f"garbage bearer returned {results['Bearer garbage'].status_code}")
    if violations:
        pytest.fail("\n".join(violations))


@case(
    "D2",
    title="Patient.read-only token: 401 fhir_token_rejected, no brief, and the model is never called",
    guards="a token that cannot read the chart producing an 'empty' brief; chart context reaching the LLM "
    "for a rejected request",
    cites="F-07; ARCHITECTURE.md §2; app/api/pre_visit_brief.py (401 fhir_token_rejected)",
    level="live endpoint + in-process",
)
def test_d2_patient_only_token_rejected(agent: Any, pipeline: Any, tokens: Any, panel: Any, observe: Any) -> None:
    from app.fhir.exceptions import FhirAuthError

    chart = panel.chart(PATIENT)
    token = tokens.get("patient_only")
    reply = agent.post({"patient_id": chart.patient_id}, token=token)
    violations = _no_brief_violations(reply, chart, [token], "fhir_token_rejected")

    run = pipeline.run(token, chart.patient_id)
    observe("endpoint", f"HTTP {reply.status} {reply.error}")
    observe(
        "in-process",
        f"raised {type(run.error).__name__ if run.error else 'nothing'}; "
        f"events {[e['event_type'] for e in run.events]}",
    )
    if not isinstance(run.error, FhirAuthError):
        violations.append(
            f"in-process run raised {type(run.error).__name__ if run.error else 'nothing'}, expected FhirAuthError"
        )
    violations += _llm_violations(run)
    if violations:
        pytest.fail("\n".join(violations))


@case(
    "D3",
    title="Token lacking only AllergyIntolerance.read: 401, never a brief saying 'Allergies: not recorded'",
    guards="a refused allergy read rendered as an empty allergy list for a patient who has allergies — "
    "the F-21 failure produced by an authorization error instead of missing data",
    cites="F-07 + F-21; ARCHITECTURE.md §5 ('a resource type that was never queried must not be silently "
    "rendered identically to one that ... returned zero results')",
    level="live endpoint + in-process",
)
def test_d3_missing_allergy_scope_never_renders_not_recorded(
    agent: Any, pipeline: Any, tokens: Any, panel: Any, observe: Any
) -> None:
    from app.fhir.exceptions import FhirAuthError

    chart = panel.chart(PATIENT)
    token = tokens.get("no_allergy_read")
    reply = agent.post({"patient_id": chart.patient_id}, token=token)
    violations = _no_brief_violations(reply, chart, [token], "fhir_token_rejected")

    run = pipeline.run(token, chart.patient_id)
    retrieved = sorted(e["event_data"]["resource_type"] for e in run.events_of("retrieval_event"))
    observe("patient allergies on file", chart.count("AllergyIntolerance"))
    observe("endpoint", f"HTTP {reply.status} {reply.error}")
    observe(
        "in-process",
        f"raised {type(run.error).__name__ if run.error else 'nothing'}; "
        f"resource types read before the refusal: {retrieved}",
    )
    if not isinstance(run.error, FhirAuthError):
        violations.append(
            f"in-process run raised {type(run.error).__name__ if run.error else 'nothing'}, expected FhirAuthError"
        )
    violations += _llm_violations(run)
    if violations:
        pytest.fail("\n".join(violations))


@case(
    "D4",
    title="Forged JWTs (real claims + fake signature; alg=none; self-issued) are rejected with no brief",
    guards="the endpoint's unsigned read of `sub` becoming trust: a forged token that passes the agent's "
    "shape check reaching chart data or the model",
    cites="F-07 ('forged bearer token is also refused'); app/api/pre_visit_brief.py (sub read without "
    "checking the signature)",
    level="live endpoint",
)
def test_d4_forged_jwts_rejected(agent: Any, tokens: Any, panel: Any, observe: Any) -> None:
    chart = panel.chart(PATIENT)
    real = tokens.get("brief")
    header, payload, _ = real.split(".")
    claims = jwt_claims(real)
    now = int(time.time())
    forged = {
        "real header and claims, random signature": f"{header}.{payload}.{b64url(secrets.token_bytes(256))}",
        "alg=none, empty signature": f"{b64url(json.dumps({'alg': 'none', 'typ': 'JWT'}).encode())}.{payload}.",
        "self-issued, same sub and scopes": ".".join(
            [
                b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()),
                b64url(
                    json.dumps({**claims, "jti": uuid.uuid4().hex, "iat": now, "nbf": now, "exp": now + 3600}).encode()
                ),
                b64url(secrets.token_bytes(256)),
            ]
        ),
    }
    violations = []
    for label, token in forged.items():
        reply = agent.post({"patient_id": chart.patient_id}, token=token)
        observe(label, f"HTTP {reply.status} {reply.error}")
        violations += [f"{label}: {v}" for v in _no_brief_violations(reply, chart, [token], "fhir_token_rejected")]
    if violations:
        pytest.fail("\n".join(violations))


@case(
    "D5",
    title="A real token with edited claims (scopes widened, sub swapped, exp extended) is rejected",
    guards="a client widening its own access or impersonating another clinician by editing a genuine token",
    cites="F-07 (scope must be requested up front); F-09 (scopes read from the JWT); ARCHITECTURE.md §2",
    level="live endpoint",
)
def test_d5_tampered_real_token_rejected(agent: Any, tokens: Any, panel: Any, observe: Any) -> None:
    chart = panel.chart(PATIENT)
    real = tokens.get("no_allergy_read")
    header, _, signature = real.split(".")
    claims = jwt_claims(real)

    def resign(changes: dict[str, Any]) -> str:
        return f"{header}.{b64url(json.dumps({**claims, **changes}).encode())}.{signature}"

    tampered = {
        "scopes widened (+AllergyIntolerance, Encounter, Observation)": resign(
            {
                "scopes": [
                    *claims.get("scopes", []),
                    "user/AllergyIntolerance.read",
                    "user/Encounter.read",
                    "user/Observation.read",
                ]
            }
        ),
        "sub swapped to another user id": resign({"sub": str(uuid.uuid4())}),
        "exp extended by a day": resign({"exp": int(claims.get("exp", time.time())) + 86400}),
    }
    violations = []
    for label, token in tampered.items():
        reply = agent.post({"patient_id": chart.patient_id}, token=token)
        observe(label, f"HTTP {reply.status} {reply.error}")
        violations += [
            f"{label}: {v}" for v in _no_brief_violations(reply, chart, [token, real], "fhir_token_rejected")
        ]
    if violations:
        pytest.fail("\n".join(violations))


@case(
    "D6",
    title="A genuinely expired OpenEMR token is rejected with no brief",
    guards="an expired session still reading charts, or an expiry answered with an empty brief",
    cites="F-08 (expired and out-of-scope share a 401); ARCHITECTURE.md §2; app/api/pre_visit_brief.py",
    level="live endpoint (needs a token minted over an hour earlier)",
)
def test_d6_expired_token_rejected(agent: Any, panel: Any, observe: Any) -> None:
    path = os.environ.get("EVAL_EXPIRED_TOKEN_FILE")
    if not path or not Path(path).is_file():
        pytest.skip(
            "EVAL_EXPIRED_TOKEN_FILE not set: OpenEMR tokens live one hour and cannot be revoked "
            "(no revocation endpoint), so a real expired token has to be saved and aged"
        )
    token = Path(path).read_text().strip()
    exp = int(jwt_claims(token).get("exp", 0))
    if exp > time.time():
        pytest.skip(f"saved token not expired yet (exp {dt.datetime.fromtimestamp(exp, dt.timezone.utc):%H:%M:%SZ})")
    chart = panel.chart(PATIENT)
    reply = agent.post({"patient_id": chart.patient_id}, token=token)
    observe("token expired", f"{int(time.time() - exp)} s before the request")
    observe("endpoint", f"HTTP {reply.status} {reply.error}")
    violations = _no_brief_violations(reply, chart, [token], "fhir_token_rejected")
    if violations:
        pytest.fail("\n".join(violations))


@case(
    "D7",
    title="No token, a non-bearer scheme, or F-07's 'Bearer garbage' is refused before any FHIR call",
    guards="an unauthenticated request reaching retrieval, or answered with anything but a 401",
    cites="F-07; app/api/pre_visit_brief.py (missing_bearer_token / malformed_bearer_token); "
    "agent/docs/deployment.md §1 (public port 8443)",
    level="live endpoint",
)
def test_d7_missing_or_malformed_bearer(agent: Any, panel: Any, observe: Any) -> None:
    chart = panel.chart(PATIENT)
    cases = {
        "no Authorization header": (None, "missing_bearer_token"),
        "Basic credentials": ("Basic cGh5c2ljaWFuOnBoeXNpY2lhbg==", "missing_bearer_token"),
        "Bearer garbage": ("Bearer garbage", "malformed_bearer_token"),
    }
    violations = []
    for label, (authorization, expected) in cases.items():
        reply = agent.post({"patient_id": chart.patient_id}, authorization=authorization)
        observe(label, f"HTTP {reply.status} {reply.error}")
        violations += [f"{label}: {v}" for v in _no_brief_violations(reply, chart, [], expected)]
    if violations:
        pytest.fail("\n".join(violations))
