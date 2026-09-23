"""
The /documents guard on its own (app/api/upload_guard.py): who may upload,
how often, and how many extractions run at once.

The route tests exercise the guard through `/documents`; these pin the
guard's own contract so a route change cannot quietly weaken it. OpenEMR
is faked at `fhir_client.ensure_patient_exists`, the one call the guard
makes. No network.

    cd agent && python -m pytest tests/test_upload_guard.py
"""

from __future__ import annotations

import asyncio
import base64
import json

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from structlog.testing import capture_logs

from app.api import upload_guard
from app.api.pre_visit_brief import ApiError
from app.fhir import client as fhir_client
from app.fhir.exceptions import FhirAuthError, PatientNotFoundError

PATIENT = "a2c373ec-57bf-41cb-8767-94410dcf663d"
EMBED_SCOPES = [
    "openid",
    "user/Patient.read",
    "user/Condition.read",
    "user/MedicationRequest.read",
    "user/AllergyIntolerance.read",
    "user/Observation.read",
    "user/Encounter.read",
]


def jwt(sub: str = "user-1", aud: object = "client-1", scopes: object = None) -> str:
    """An unsigned token shaped like OpenEMR's (AccessTokenEntity::convertToJWT)."""

    def part(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    claims = {"aud": aud, "sub": sub, "scopes": EMBED_SCOPES if scopes is None else scopes}
    return f"{part({'alg': 'RS256'})}.{part(claims)}.c2ln"


def creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


class FakeOpenEMR:
    def __init__(self) -> None:
        self.rejected: set[str] = set()
        self.calls = 0

    async def ensure_patient_exists(self, token: str, patient_id: str) -> None:
        self.calls += 1
        if token in self.rejected:
            raise FhirAuthError("rejected")
        if patient_id != PATIENT:
            raise PatientNotFoundError("unknown")


@pytest.fixture
def openemr(monkeypatch: pytest.MonkeyPatch) -> FakeOpenEMR:
    fake = FakeOpenEMR()
    monkeypatch.setattr(fhir_client, "ensure_patient_exists", fake.ensure_patient_exists)
    return fake


def authorize(credentials, patient: str = PATIENT) -> upload_guard.DocumentCaller:
    return asyncio.run(upload_guard.authorize_document_request(credentials, patient))


def refusal(credentials, patient: str = PATIENT) -> ApiError:
    with pytest.raises(ApiError) as raised:
        authorize(credentials, patient)
    return raised.value


class TestAuthorize:
    def test_no_bearer_is_401(self, openemr: FakeOpenEMR) -> None:
        error = refusal(None)
        assert (error.status_code, error.error.value) == (401, "missing_bearer_token")
        assert openemr.calls == 0

    @pytest.mark.parametrize(
        "token",
        [
            "rehearsal-not-a-token",
            "a.b.c",
            jwt(sub=""),
            jwt(aud=["one", "two"]),
            jwt(aud=None),
            jwt(scopes="user/Patient.read"),
            jwt(scopes=[1, 2]),
        ],
    )
    def test_anything_that_is_not_an_openemr_token_is_401_without_asking_openemr(
        self, openemr: FakeOpenEMR, token: str
    ) -> None:
        error = refusal(creds(token))
        assert (error.status_code, error.error.value) == (401, "malformed_bearer_token")
        assert openemr.calls == 0

    def test_a_missing_extraction_scope_is_403_naming_it(self, openemr: FakeOpenEMR) -> None:
        error = refusal(creds(jwt(scopes=["openid", "user/Patient.read", "user/Condition.read"])))
        assert (error.status_code, error.error.value) == (403, "insufficient_scope")
        assert "user/MedicationRequest.read" in error.message
        assert "user/AllergyIntolerance.read" in error.message
        assert 'error="insufficient_scope"' in error.headers["WWW-Authenticate"]
        assert openemr.calls == 0

    def test_the_required_scopes_are_what_extraction_reads(self) -> None:
        # app/workers/intake_extractor/chart.py reads Patient, medications
        # and allergies for every document kind.
        assert upload_guard.REQUIRED_SCOPES == (
            "user/Patient.read",
            "user/MedicationRequest.read",
            "user/AllergyIntolerance.read",
        )

    def test_a_token_openemr_rejects_is_401(self, openemr: FakeOpenEMR) -> None:
        token = jwt(sub="revoked")
        openemr.rejected.add(token)
        error = refusal(creds(token))
        assert (error.status_code, error.error.value) == (401, "fhir_token_rejected")

    def test_an_unknown_patient_is_404(self, openemr: FakeOpenEMR) -> None:
        error = refusal(creds(jwt()), patient="no-such-patient")
        assert (error.status_code, error.error.value) == (404, "patient_not_found")

    def test_an_accepted_token_names_its_caller(self, openemr: FakeOpenEMR) -> None:
        caller = authorize(creds(jwt(sub="u-7", aud=["embed-client"])))
        assert (caller.user, caller.client_id, caller.patient_id) == (
            "u-7",
            "embed-client",
            PATIENT,
        )
        assert openemr.calls == 1

    def test_refusals_log_no_patient_user_or_token(self, openemr: FakeOpenEMR) -> None:
        narrow = jwt(sub="user-secret-sub", scopes=["openid"])
        rejected = jwt(sub="user-secret-sub-2")
        openemr.rejected.add(rejected)
        with capture_logs() as logs:
            for credentials in (creds(narrow), creds("nope"), creds(rejected)):
                with pytest.raises(ApiError):
                    authorize(credentials)
        assert logs, "refusals are logged"
        text = json.dumps(logs, default=str)
        for secret in (PATIENT, "user-secret-sub", narrow, rejected, "nope"):
            assert secret not in text


def caller(user: str = "u", client: str = "c", patient: str = "p") -> upload_guard.DocumentCaller:
    return upload_guard.DocumentCaller(token="t", user=user, client_id=client, patient_id=patient)


class TestUploadLimits:
    def limits(self, **overrides: int) -> upload_guard.UploadLimits:
        values = {"per_client": 100, "per_user": 100, "per_patient": 100, "window_seconds": 600}
        return upload_guard.UploadLimits(**{**values, **overrides})

    def assert_refused(self, limits, who, now: float = 1.0) -> None:
        with pytest.raises(ApiError) as refused:
            limits.check_and_record(who, now=now)
        assert (refused.value.status_code, refused.value.error.value) == (429, "rate_limited")
        assert int(refused.value.headers["Retry-After"]) >= 1

    def test_per_patient(self) -> None:
        limits = self.limits(per_patient=2)
        limits.check_and_record(caller(user="u1", patient="p"), now=0.0)
        limits.check_and_record(caller(user="u2", patient="p"), now=0.0)
        self.assert_refused(limits, caller(user="u3", patient="p"))
        limits.check_and_record(caller(user="u3", patient="q"), now=1.0)

    def test_per_user(self) -> None:
        limits = self.limits(per_user=2)
        limits.check_and_record(caller(user="u", patient="p1"), now=0.0)
        limits.check_and_record(caller(user="u", patient="p2"), now=0.0)
        self.assert_refused(limits, caller(user="u", patient="p3"))
        limits.check_and_record(caller(user="v", patient="p3"), now=1.0)

    def test_per_oauth_client(self) -> None:
        limits = self.limits(per_client=2)
        limits.check_and_record(caller(client="embed", user="u1", patient="p1"), now=0.0)
        limits.check_and_record(caller(client="embed", user="u2", patient="p2"), now=0.0)
        self.assert_refused(limits, caller(client="embed", user="u3", patient="p3"))
        limits.check_and_record(caller(client="other", user="u3", patient="p3"), now=1.0)

    def test_the_window_slides(self) -> None:
        limits = self.limits(per_patient=1, window_seconds=60)
        limits.check_and_record(caller(), now=0.0)
        with pytest.raises(ApiError):
            limits.check_and_record(caller(), now=59.0)
        limits.check_and_record(caller(), now=60.0)

    def test_a_refused_request_is_not_counted(self) -> None:
        limits = self.limits(per_patient=1, window_seconds=60)
        limits.check_and_record(caller(), now=0.0)
        for moment in (10.0, 20.0, 30.0):
            with pytest.raises(ApiError):
                limits.check_and_record(caller(), now=moment)
        limits.check_and_record(caller(), now=60.0)

    def test_limits_read_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENT_UPLOAD_LIMIT_PER_PATIENT", "3")
        monkeypatch.setenv("AGENT_UPLOAD_LIMIT_PER_USER", "not-a-number")
        limits = upload_guard.UploadLimits.from_env()
        assert limits.windows["patient"].limit == 3
        assert limits.windows["user"].limit == 20


class TestExtractionSlots:
    def test_a_full_house_is_503_and_does_not_wait(self) -> None:
        slots = upload_guard.ExtractionSlots(limit=2)
        slots.try_acquire()
        slots.try_acquire()
        with pytest.raises(ApiError) as refused:
            slots.try_acquire()
        assert (refused.value.status_code, refused.value.error.value) == (
            503,
            "extraction_capacity",
        )
        assert refused.value.headers["Retry-After"] == "10"
        slots.release()
        slots.try_acquire()
        assert slots.in_use == 2

    def test_the_context_manager_releases_on_failure(self) -> None:
        slots = upload_guard.ExtractionSlots(limit=1)
        with pytest.raises(RuntimeError), slots.slot():
            raise RuntimeError("extraction failed")
        assert slots.in_use == 0

    def test_release_never_goes_negative(self) -> None:
        slots = upload_guard.ExtractionSlots(limit=1)
        slots.release()
        assert slots.in_use == 0
