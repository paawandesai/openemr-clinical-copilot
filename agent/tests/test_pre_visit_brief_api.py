"""
POST /pre-visit-brief (app/api/pre_visit_brief.py) through the real app in
app/main.py: the contracts, the patient_session_key, the correlation id, and
every failure path. No network and no credentials. The success path runs the
real graph with FHIR retrieval and claim drafting faked; failure paths fake
run_pre_visit_brief itself to raise what the pipeline raises.

    cd agent && python -m unittest discover -s tests -v
"""

from __future__ import annotations

import base64
import json
import unittest
import uuid
from datetime import datetime
from typing import Any
from unittest import mock

import anthropic
import httpx
import httpx2
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from app.api import pre_visit_brief as api
from app.fhir.exceptions import FhirAuthError, FhirServerError, PatientNotFoundError
from app.graph import pre_visit_brief as pvb
from app.main import app
from app.models.api import ErrorResponse, PreVisitBriefResponse
from app.models.claims import Claim
from app.models.fhir_resources import Condition, FhirResourceType, MedicationRequest
from app.models.telemetry_events import TELEMETRY_EVENT_TYPES
from app.reasoning.draft_claims import ClaimDraftingError, PreVisitBriefDraft, SectionCount

PATIENT_ID = "a2c0e096-23d5-4cf9-ad5b-4895597363a7"
USER = "9d1c7a4e-3b2f-4e61-8a0d-5f6b7c8d9e01"


def _jwt(claims: dict[str, Any]) -> str:
    def segment(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return f"{segment({'alg': 'RS256', 'typ': 'JWT'})}.{segment(claims)}.c2lnbmF0dXJl"


TOKEN = _jwt({"sub": USER, "scopes": ["openid", "user/Patient.read"]})


def _post(body: dict[str, Any], headers: dict[str, str] | None = None) -> httpx.Response:
    with capture_logs():
        return TestClient(app).post("/pre-visit-brief", json=body, headers=headers or {})


def _auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class SuccessTest(unittest.TestCase):
    """The real graph behind the endpoint; only FHIR and drafting are faked."""

    def setUp(self) -> None:
        self.tokens_seen: list[str] = []
        records: dict[FhirResourceType, list[Any]] = {
            FhirResourceType.CONDITION: [
                Condition(source_id="cond-1", display="HTN", clinical_status="active")
            ],
            FhirResourceType.MEDICATION_REQUEST: [
                MedicationRequest(
                    source_id="med-1",
                    display="Norvasc",
                    status="active",
                    authored_date=datetime(2014, 2, 1),
                )
            ],
            FhirResourceType.ALLERGY_INTOLERANCE: [],
        }

        async def patient_read(token: str, patient_id: str) -> None:
            self.tokens_seen.append(token)

        def fetch(resource_type: FhirResourceType) -> Any:
            async def fetch_records(token: str, patient_id: str, **_: Any) -> list[Any]:
                self.tokens_seen.append(token)
                return records[resource_type]

            return fetch_records

        async def draft(
            retrieved: dict[FhirResourceType, list[Any]], **_: Any
        ) -> PreVisitBriefDraft:
            return PreVisitBriefDraft(
                claims=[
                    Claim(
                        source_id="cond-1",
                        field="display",
                        value="HTN",
                        resource_type=FhirResourceType.CONDITION,
                    ),
                    Claim(
                        source_id="med-1",
                        field="display",
                        value="Norvasc 10 MG",
                        resource_type=FhirResourceType.MEDICATION_REQUEST,
                    ),
                ],
                section_counts={
                    FhirResourceType.CONDITION: SectionCount(retrieved=1, in_brief=1),
                    FhirResourceType.MEDICATION_REQUEST: SectionCount(retrieved=1, in_brief=1),
                    FhirResourceType.ALLERGY_INTOLERANCE: SectionCount(retrieved=0, in_brief=0),
                },
            )

        patches = [
            mock.patch.object(pvb.fhir_client, "ensure_patient_exists", patient_read),
            mock.patch.object(pvb.fhir_client, "get_conditions", fetch(FhirResourceType.CONDITION)),
            mock.patch.object(
                pvb.fhir_client,
                "get_medication_requests",
                fetch(FhirResourceType.MEDICATION_REQUEST),
            ),
            mock.patch.object(
                pvb.fhir_client,
                "get_allergy_intolerances",
                fetch(FhirResourceType.ALLERGY_INTOLERANCE),
            ),
            mock.patch.object(pvb, "draft_pre_visit_brief", draft),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_brief_contract_session_key_and_correlation_id(self) -> None:
        with capture_logs() as logs:
            response = TestClient(app).post(
                "/pre-visit-brief",
                json={"patient_id": PATIENT_ID},
                headers={**_auth(), "X-Correlation-ID": "corr-test-4711"},
            )

        self.assertEqual(response.status_code, 200)
        body = PreVisitBriefResponse.model_validate_json(response.text)
        self.assertEqual(uuid.UUID(body.session_id).version, 4)
        self.assertEqual(body.patient_session_key, f"{USER}:{PATIENT_ID}:{body.session_id}")
        self.assertEqual(body.correlation_id, "corr-test-4711")
        self.assertEqual(response.headers["X-Correlation-ID"], "corr-test-4711")
        self.assertIn("Allergies: not recorded", body.brief)
        self.assertIn('UNVERIFIED: "Norvasc 10 MG"', body.brief)
        self.assertEqual(
            (body.verification.claims, body.verification.verified, body.verification.unverified),
            (2, 1, 1),
        )
        self.assertEqual(
            [(c.source_id, c.verified, c.unverified_reason) for c in body.claims],
            [("cond-1", True, None), ("med-1", False, "value does not match source record")],
        )
        self.assertEqual([s.retrieved for s in body.sections], [1, 1, 0])
        self.assertGreaterEqual(body.timing.total_seconds, body.timing.retrieve_seconds)
        # The clinician's token went to FHIR on every call and nowhere into the body.
        self.assertEqual(self.tokens_seen, [TOKEN] * 4)
        self.assertNotIn(TOKEN, response.text)
        # Every pipeline event is attributed to this request's key and correlation id.
        events = [
            log
            for log in logs
            if log.get("event") == "agent_event"
            and log.get("event_type") not in TELEMETRY_EVENT_TYPES
        ]
        self.assertTrue(events)
        self.assertEqual({e["patient_session_key"] for e in events}, {body.patient_session_key})
        self.assertEqual(
            {
                e["event_data"]["correlation_id"]
                for e in events
                if "correlation_id" in e["event_data"]
            },
            {"corr-test-4711"},
        )

    def test_caller_session_id_is_kept(self) -> None:
        response = _post({"patient_id": PATIENT_ID, "session_id": "exam-room-3-morning"}, _auth())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["session_id"], "exam-room-3-morning")
        self.assertEqual(body["patient_session_key"], f"{USER}:{PATIENT_ID}:exam-room-3-morning")


class FailureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.run = mock.AsyncMock()
        patcher = mock.patch.object(api, "run_pre_visit_brief", self.run)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_error(self, response: httpx.Response, status: int, error: str) -> ErrorResponse:
        self.assertEqual(response.status_code, status, response.text)
        body = ErrorResponse.model_validate_json(response.text)
        self.assertEqual(body.error.value, error)
        self.assertEqual(body.correlation_id, response.headers["X-Correlation-ID"])
        return body

    def test_missing_or_non_bearer_authorization_is_401_before_any_fhir_call(self) -> None:
        for label, headers in {
            "none": {},
            "basic": {"Authorization": "Basic dXNlcjpwYXNz"},
        }.items():
            with self.subTest(label):
                response = _post({"patient_id": PATIENT_ID}, headers)
                self.assert_error(response, 401, "missing_bearer_token")
                self.assertEqual(response.headers["WWW-Authenticate"], "Bearer")
        self.run.assert_not_awaited()

    def test_token_that_is_not_a_jwt_with_a_usable_subject_is_401(self) -> None:
        for label, token in {
            "not a jwt": "opaque-token-value",
            "no sub": _jwt({"scopes": []}),
            "sub with a colon": _jwt({"sub": "user:admin"}),
        }.items():
            with self.subTest(label):
                self.assert_error(
                    _post({"patient_id": PATIENT_ID}, _auth(token)), 401, "malformed_bearer_token"
                )
        self.run.assert_not_awaited()

    def test_body_outside_the_contract_is_422_without_echoing_input(self) -> None:
        for label, body in {
            "not a uuid": {"patient_id": "patient-DROP-TABLE"},
            "uppercase uuid": {"patient_id": PATIENT_ID.upper()},
            "token in body": {"patient_id": PATIENT_ID, "token": "secret-in-body"},
            "bad session id": {"patient_id": PATIENT_ID, "session_id": "a:b"},
            "missing patient": {},
        }.items():
            with self.subTest(label):
                response = _post(body, _auth())
                self.assert_error(response, 422, "invalid_request")
                self.assertNotIn("DROP-TABLE", response.text)
                self.assertNotIn("secret-in-body", response.text)
        self.run.assert_not_awaited()

    def test_fhir_401_names_every_cause_and_claims_none(self) -> None:
        self.run.side_effect = FhirAuthError(
            'FHIR request rejected with 401: {"error":"invalid_token"}'
        )

        response = _post({"patient_id": PATIENT_ID}, _auth())

        body = self.assert_error(response, 401, "fhir_token_rejected")
        for cause in ("expired", "revoked", "scope", "cannot be determined"):
            self.assertIn(cause, body.message)
        self.assertNotIn("invalid_token", body.message)

    def test_pipeline_failures_map_to_distinct_statuses_without_internals(self) -> None:
        request = httpx2.Request("POST", "https://api.anthropic.test/v1/messages")
        cases = {
            "patient not found": (
                PatientNotFoundError("FHIR Patient read returned 404"),
                404,
                "patient_not_found",
            ),
            "fhir error": (
                FhirServerError("FHIR server returned 500: SQLSTATE[42S02]"),
                502,
                "fhir_unavailable",
            ),
            "fhir unreachable": (
                httpx.ConnectError("connect to 172.17.0.1:9300 failed"),
                502,
                "fhir_unavailable",
            ),
            "drafting output": (
                ClaimDraftingError("Model output did not match the claim schema"),
                502,
                "brief_generation_failed",
            ),
            "llm unreachable": (
                anthropic.APIConnectionError(request=request),
                502,
                "brief_generation_failed",
            ),
            "unexpected": (
                RuntimeError("KeyError: 'retrieved' at node render"),
                500,
                "internal_error",
            ),
        }
        for label, (error, status, code) in cases.items():
            with self.subTest(label):
                self.run.side_effect = error
                response = _post({"patient_id": PATIENT_ID}, _auth())
                self.assert_error(response, status, code)
                for internal in ("SQLSTATE", "172.17.0.1", "claim schema", "KeyError"):
                    self.assertNotIn(internal, response.text)


class OpenApiTest(unittest.TestCase):
    def test_contract_is_published_with_bearer_auth(self) -> None:
        schema = TestClient(app).get("/openapi.json").json()

        operation = schema["paths"]["/pre-visit-brief"]["post"]
        self.assertEqual(set(operation["responses"]), {"200", "401", "404", "422", "500", "502"})
        self.assertIn("HTTPBearer", schema["components"]["securitySchemes"])
        self.assertIn("PreVisitBriefResponse", schema["components"]["schemas"])


if __name__ == "__main__":
    unittest.main()
