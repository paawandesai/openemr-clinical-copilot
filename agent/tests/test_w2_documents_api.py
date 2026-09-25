"""
The document upload and extraction-status routes (app/api/documents.py), and
the guard in front of them (app/api/upload_guard.py).

Work order 11 tested these as a stub; work order 13 made them run the real
intake-extractor at upload (D7); `chore/w2-deploy-rehearsal` put a guard in
front of the stub; work order 17 carried that guard's check order onto the
real route. What the browser depends on is unchanged — the response fields,
the digest-derived document id, the status it polls — and this file pins
that, plus:

- the patient binding: the 404 for another patient's document, and the 409
  for the same bytes uploaded to a different chart (D40 item 8);
- everything the guard refuses before the body is read: a token that is not
  an OpenEMR token, one without the scopes extraction needs, one OpenEMR
  rejects, the rate limits, and a full house of extraction slots;
- that an extraction slot is held for the whole background job and handed
  back exactly once on every way out, because a leaked slot is a permanent
  503 after `AGENT_EXTRACTION_MAX_CONCURRENT` leaks.

No network: OpenEMR is faked at `fhir_client.ensure_patient_exists`, the one
call the guard makes; the vision call is the ground-truth transcriber
(tests/intake_extractor_support.py) and the chart is the factory's own chart
cache. Text layer, locate and the checks are real.

    cd agent && python -m pytest tests/test_w2_documents_api.py
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import threading
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from app.api import documents, upload_guard
from app.fhir import client as fhir_client
from app.fhir.exceptions import FhirAuthError, PatientNotFoundError
from app.main import app
from app.models.w2.documents import DocumentKind, ExtractionStatus
from app.models.w2.writeback import document_idempotency_key
from app.workers import document_store
from app.workers.document_store import DocumentBoundElsewhere, DocumentStore, UnreadablePdf
from tests.intake_extractor_support import (
    chart_for,
    ground_truth_vision,
    patient_uuid,
    pdf_bytes,
)

SAMPLE = "lab-clean-001"
PATIENT = patient_uuid(SAMPLE)
OTHER_PATIENT = "00000000-0000-4000-8000-000000000000"
PDF = pdf_bytes(SAMPLE)

#: What the chart embed's token carries (ClinicianTokenBroker::CONVERSATION_SCOPES).
ALL_SCOPES = [
    "openid",
    "user/Patient.read",
    "user/Condition.read",
    "user/MedicationRequest.read",
    "user/AllergyIntolerance.read",
    "user/Observation.read",
    "user/Encounter.read",
]


def jwt(sub: str = "user-1", aud: str = "client-1", scopes: list[str] | None = None) -> str:
    """An unsigned token shaped like OpenEMR's (AccessTokenEntity::convertToJWT)."""

    def part(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    claims = {"aud": aud, "sub": sub, "scopes": ALL_SCOPES if scopes is None else scopes}
    return f"{part({'alg': 'RS256'})}.{part(claims)}.c2ln"


def bearer(token: str | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token or jwt()}"}


def variant(n: int) -> bytes:
    """The sample with bytes after its end: still a readable PDF, a different digest."""
    return PDF + f"\n% variant {n}\n".encode()


class FakeOpenEMR:
    """`ensure_patient_exists`, deciding by token and patient."""

    def __init__(self) -> None:
        self.rejected_tokens: set[str] = set()
        self.known_patients = {PATIENT, OTHER_PATIENT}
        self.calls = 0

    async def ensure_patient_exists(self, token: str, patient_id: str) -> None:
        self.calls += 1
        if token in self.rejected_tokens:
            raise FhirAuthError("rejected")
        if patient_id not in self.known_patients:
            raise PatientNotFoundError(patient_id)


class GatedVision:
    """The ground-truth transcriber, held until `open()`: a job that is visibly still running."""

    def __init__(self, sample: str = SAMPLE) -> None:
        self._inner = ground_truth_vision(sample)
        self._gate = threading.Event()

    def open(self) -> None:
        self._gate.set()

    async def __call__(self, kind: DocumentKind, pdf: bytes, page_count: int):  # noqa: ANN204
        await asyncio.to_thread(self._gate.wait, 30)
        return await self._inner(kind, pdf, page_count)


@pytest.fixture
def openemr(monkeypatch: pytest.MonkeyPatch) -> FakeOpenEMR:
    fake = FakeOpenEMR()
    monkeypatch.setattr(fhir_client, "ensure_patient_exists", fake.ensure_patient_exists)
    return fake


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> DocumentStore:
    fresh = DocumentStore(vision=ground_truth_vision(SAMPLE))
    monkeypatch.setattr(document_store, "DOCUMENTS", fresh)
    return fresh


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch, openemr: FakeOpenEMR, store: DocumentStore
) -> Iterator[TestClient]:
    def chart_reader(token: str, **_: object):
        async def read(uuid: str):
            return chart_for(SAMPLE)

        return read

    monkeypatch.setattr(documents, "fhir_chart_reader", chart_reader)
    upload_guard.reset_for_tests()
    with TestClient(app) as test_client:
        yield test_client
    upload_guard.reset_for_tests()


def upload(
    client: TestClient,
    body: bytes = PDF,
    kind: str = "lab_pdf",
    patient: str = PATIENT,
    headers: dict[str, str] | None = None,
    content_type: str = "application/pdf",
):
    return client.post(
        "/documents",
        params={"kind": kind, "patient_id": patient},
        content=body,
        headers={"Content-Type": content_type, **(bearer() if headers is None else headers)},
    )


def status(
    client: TestClient,
    document_id: str,
    patient: str = PATIENT,
    headers: dict[str, str] | None = None,
):
    return client.get(
        f"/documents/{document_id}/extraction",
        params={"patient_id": patient},
        headers=bearer() if headers is None else headers,
    )


def wait_for_terminal(client: TestClient, document_id: str, seconds: float = 60.0) -> dict:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        body = status(client, document_id).json()
        if body["status"] in (ExtractionStatus.COMPLETE.value, ExtractionStatus.FAILED.value):
            return body
        time.sleep(0.1)
    raise AssertionError("extraction did not finish")


def wait_for_slots(in_use: int, seconds: float = 30.0) -> None:
    """The slot is handed back from a done-callback on the app's loop, a moment after the job."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if upload_guard.SLOTS.in_use == in_use:
            return
        time.sleep(0.02)
    raise AssertionError(f"slots in use stayed {upload_guard.SLOTS.in_use}, expected {in_use}")


class TestAuthorization:
    def test_upload_without_a_token_is_401(self, client: TestClient) -> None:
        response = upload(client, headers={})
        assert response.status_code == 401
        assert response.json()["error"] == "missing_bearer_token"
        assert response.headers["WWW-Authenticate"] == "Bearer"

    def test_a_made_up_bearer_is_401_and_openemr_is_never_asked(
        self, client: TestClient, openemr: FakeOpenEMR
    ) -> None:
        response = upload(client, headers={"Authorization": "Bearer rehearsal-not-a-token"})
        assert response.status_code == 401
        assert response.json()["error"] == "malformed_bearer_token"
        assert openemr.calls == 0

    def test_a_token_without_the_extraction_scopes_is_403(self, client: TestClient) -> None:
        token = jwt(scopes=["openid", "user/Patient.read", "user/Condition.read"])
        response = upload(client, headers=bearer(token))
        assert response.status_code == 403
        body = response.json()
        assert body["error"] == "insufficient_scope"
        assert "user/MedicationRequest.read" in body["message"]
        assert "user/AllergyIntolerance.read" in body["message"]
        assert 'error="insufficient_scope"' in response.headers["WWW-Authenticate"]

    def test_a_token_openemr_rejects_is_401(self, client: TestClient, openemr: FakeOpenEMR) -> None:
        token = jwt(sub="revoked")
        openemr.rejected_tokens.add(token)
        response = upload(client, headers=bearer(token))
        assert response.status_code == 401
        assert response.json()["error"] == "fhir_token_rejected"

    def test_an_unknown_patient_is_404_before_anything_is_stored(
        self, client: TestClient, store: DocumentStore
    ) -> None:
        response = upload(client, patient="11111111-1111-4111-8111-111111111111")
        assert response.status_code == 404
        assert response.json()["error"] == "patient_not_found"
        document_id = document_idempotency_key(hashlib.sha3_512(PDF).hexdigest())
        assert store.lookup(document_id, PATIENT) is None

    def test_patient_id_is_required(self, client: TestClient) -> None:
        response = client.post(
            "/documents",
            params={"kind": "lab_pdf"},
            content=PDF,
            headers={"Content-Type": "application/pdf", **bearer()},
        )
        assert response.status_code == 422

    def test_auth_is_decided_before_the_body_is_judged(self, client: TestClient) -> None:
        # An oversize body with a bad token is a 401, not a 413: nothing
        # about the body is looked at for a caller who is not allowed in.
        big = b"%PDF-1.7\n" + b"0" * (documents.MAX_UPLOAD_BYTES + 1)
        response = upload(client, body=big, headers={"Authorization": "Bearer nope"})
        assert response.status_code == 401

    def test_status_is_guarded_the_same_way(self, client: TestClient) -> None:
        document_id = "a" * 128
        assert status(client, document_id, headers={}).status_code == 401
        malformed = status(client, document_id, headers={"Authorization": "Bearer nope"})
        assert malformed.status_code == 401
        narrow = jwt(scopes=["openid", "user/Patient.read"])
        assert status(client, document_id, headers=bearer(narrow)).status_code == 403

    def test_polling_does_not_spend_the_upload_budget(self, client: TestClient) -> None:
        upload_guard.LIMITS = upload_guard.UploadLimits(
            per_client=1, per_user=1, per_patient=1, window_seconds=600
        )
        document_id = upload(client).json()["document_id"]
        for _ in range(5):
            assert status(client, document_id).status_code == 200


class TestRateLimits:
    @pytest.fixture(autouse=True)
    def small_limits(self, client: TestClient) -> None:
        # After `client`, whose reset would otherwise replace these. Slots are
        # raised so the extractions these uploads start never hit the cap.
        upload_guard.LIMITS = upload_guard.UploadLimits(
            per_client=5, per_user=3, per_patient=2, window_seconds=600
        )
        upload_guard.SLOTS = upload_guard.ExtractionSlots(limit=50)

    def test_per_patient(self, client: TestClient) -> None:
        assert upload(client, body=variant(1)).status_code == 200
        assert upload(client, body=variant(2)).status_code == 200
        refused = upload(client, body=variant(3))
        assert refused.status_code == 429
        assert refused.json()["error"] == "rate_limited"
        assert int(refused.headers["Retry-After"]) >= 1
        # Another chart is a separate budget.
        assert upload(client, body=variant(4), patient=OTHER_PATIENT).status_code == 200

    def test_per_user(self, client: TestClient) -> None:
        for n, patient in enumerate((PATIENT, PATIENT, OTHER_PATIENT)):
            assert upload(client, body=variant(n), patient=patient).status_code == 200
        assert upload(client, body=variant(9), patient=OTHER_PATIENT).status_code == 429
        # Another clinician on the same client is counted separately.
        other_user = bearer(jwt(sub="user-2"))
        response = upload(client, body=variant(10), patient=OTHER_PATIENT, headers=other_user)
        assert response.status_code == 200

    def test_per_client(self) -> None:
        limits = upload_guard.UploadLimits(
            per_client=3, per_user=100, per_patient=100, window_seconds=600
        )
        callers = [
            upload_guard.DocumentCaller(
                token="t", user=f"u{n}", client_id="embed", patient_id=f"p{n}"
            )
            for n in range(4)
        ]
        for caller in callers[:3]:
            limits.check_and_record(caller, now=0.0)
        with pytest.raises(upload_guard.ApiError) as refused:
            limits.check_and_record(callers[3], now=1.0)
        assert refused.value.status_code == 429
        # Another OAuth client is not affected.
        other = upload_guard.DocumentCaller(
            token="t", user="u9", client_id="other", patient_id="p9"
        )
        limits.check_and_record(other, now=1.0)

    def test_the_window_slides(self) -> None:
        limits = upload_guard.UploadLimits(
            per_client=100, per_user=100, per_patient=1, window_seconds=60
        )
        caller = upload_guard.DocumentCaller(token="t", user="u", client_id="c", patient_id="p")
        limits.check_and_record(caller, now=0.0)
        with pytest.raises(upload_guard.ApiError):
            limits.check_and_record(caller, now=59.0)
        limits.check_and_record(caller, now=60.0)

    def test_a_refused_request_is_not_counted(self, client: TestClient) -> None:
        # Bad tokens cannot spend a patient's budget.
        for _ in range(5):
            assert upload(client, headers={"Authorization": "Bearer nope"}).status_code == 401
        assert upload(client).status_code == 200

    def test_a_request_refused_for_its_bytes_is_counted(self, client: TestClient) -> None:
        # It passed authorization and made this service read a body; a 409 or
        # 415 afterwards does not give that back (documents.py docstring).
        assert upload(client, body=b"GIF89a not a pdf").status_code == 415
        assert upload(client, body=variant(1)).status_code == 200
        assert upload(client, body=variant(2)).status_code == 429


class TestExtractionSlots:
    def test_a_full_house_is_503_not_a_queue(self, client: TestClient) -> None:
        upload_guard.SLOTS = upload_guard.ExtractionSlots(limit=1)
        upload_guard.SLOTS.try_acquire()  # someone else's extraction is running
        response = upload(client)
        assert response.status_code == 503
        assert response.json()["error"] == "extraction_capacity"
        assert response.headers["Retry-After"] == "10"
        upload_guard.SLOTS.release()
        document_id = upload(client).json()["document_id"]
        wait_for_terminal(client, document_id)
        wait_for_slots(0)

    def test_the_slot_is_held_until_the_background_extraction_ends(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vision = GatedVision()
        monkeypatch.setattr(document_store, "DOCUMENTS", DocumentStore(vision=vision))
        upload_guard.SLOTS = upload_guard.ExtractionSlots(limit=1)
        try:
            accepted = upload(client)
            assert accepted.status_code == 200
            assert accepted.json()["status"] == ExtractionStatus.PENDING.value
            # The upload has returned; its extraction has not.
            assert upload_guard.SLOTS.in_use == 1
            assert upload(client, body=variant(1)).status_code == 503
        finally:
            vision.open()
        body = wait_for_terminal(client, accepted.json()["document_id"])
        assert body["status"] == ExtractionStatus.COMPLETE.value
        wait_for_slots(0)

    def test_a_failed_extraction_hands_its_slot_back(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def broken(kind: DocumentKind, pdf: bytes, page_count: int):  # noqa: ANN202
            raise RuntimeError("vision is down")

        monkeypatch.setattr(document_store, "DOCUMENTS", DocumentStore(vision=broken))
        upload_guard.SLOTS = upload_guard.ExtractionSlots(limit=1)
        document_id = upload(client).json()["document_id"]
        assert wait_for_terminal(client, document_id)["status"] == ExtractionStatus.FAILED.value
        wait_for_slots(0)

    def test_a_body_refused_after_the_slot_hands_it_back(self, client: TestClient) -> None:
        upload_guard.SLOTS = upload_guard.ExtractionSlots(limit=1)
        assert upload(client, body=b"GIF89a not a pdf").status_code == 415
        assert upload(client, body=b"").status_code == 400
        assert upload_guard.SLOTS.in_use == 0

    def test_an_unreadable_pdf_hands_its_slot_back(self, client: TestClient) -> None:
        upload_guard.SLOTS = upload_guard.ExtractionSlots(limit=1)
        assert upload(client, body=b"%PDF-1.7\nnot really\n").status_code == 415
        assert upload_guard.SLOTS.in_use == 0

    def test_the_409_hands_its_slot_back(self, client: TestClient) -> None:
        document_id = upload(client).json()["document_id"]
        wait_for_terminal(client, document_id)
        wait_for_slots(0)
        upload_guard.SLOTS = upload_guard.ExtractionSlots(limit=1)
        assert upload(client, patient=OTHER_PATIENT).status_code == 409
        assert upload_guard.SLOTS.in_use == 0

    def test_a_duplicate_hands_its_slot_back_at_once(self, client: TestClient) -> None:
        document_id = upload(client).json()["document_id"]
        wait_for_terminal(client, document_id)
        wait_for_slots(0)
        upload_guard.SLOTS = upload_guard.ExtractionSlots(limit=1)
        again = upload(client)
        assert again.status_code == 200
        assert again.json()["status"] == ExtractionStatus.COMPLETE.value
        assert upload_guard.SLOTS.in_use == 0


class TestAcceptCallsBackExactlyOnce:
    """`DocumentStore.accept(on_finished=...)` directly, on its own event loop."""

    @staticmethod
    def counter() -> tuple[list[int], object]:
        calls: list[int] = []
        return calls, lambda: calls.append(1)

    def test_when_the_job_completes(self) -> None:
        calls, callback = self.counter()

        async def run() -> None:
            store = DocumentStore(vision=ground_truth_vision(SAMPLE))
            stored, _, _ = store.accept(
                PATIENT, PDF, DocumentKind.LAB_PDF, chart_reader=None, on_finished=callback
            )
            assert calls == []
            await store.wait(stored.document.document_id)
            for _ in range(10):
                await asyncio.sleep(0)

        asyncio.run(run())
        assert calls == [1]

    def test_when_the_job_is_cancelled(self) -> None:
        calls, callback = self.counter()

        async def run() -> None:
            vision = GatedVision()
            store = DocumentStore(vision=vision)
            stored, _, _ = store.accept(
                PATIENT, PDF, DocumentKind.LAB_PDF, chart_reader=None, on_finished=callback
            )
            store.jobs._tasks[stored.document.document_id].cancel()
            for _ in range(10):
                await asyncio.sleep(0)
            vision.open()

        asyncio.run(run())
        assert calls == [1]

    def test_when_nothing_starts(self) -> None:
        calls, callback = self.counter()

        async def run() -> None:
            store = DocumentStore(vision=ground_truth_vision(SAMPLE))
            with pytest.raises(UnreadablePdf):
                store.accept(PATIENT, b"%PDF-1.7\nno\n", DocumentKind.LAB_PDF, on_finished=callback)
            assert calls == [1]
            stored, _, _ = store.accept(PATIENT, PDF, DocumentKind.LAB_PDF)
            await store.wait(stored.document.document_id)
            with pytest.raises(DocumentBoundElsewhere):
                store.accept(OTHER_PATIENT, PDF, DocumentKind.LAB_PDF, on_finished=callback)
            assert calls == [1, 1]
            _, _, duplicate = store.accept(PATIENT, PDF, DocumentKind.LAB_PDF, on_finished=callback)
            assert duplicate
            assert calls == [1, 1, 1]

        asyncio.run(run())


class TestUpload:
    def test_a_pdf_is_accepted_and_starts_extracting(self, client: TestClient) -> None:
        response = upload(client)
        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "lab_pdf"
        assert body["byte_length"] == len(PDF)
        # D7: the upload answers before the extraction finishes.
        assert body["status"] == ExtractionStatus.PENDING.value

    def test_the_document_id_is_the_d1_key_over_the_bytes(self, client: TestClient) -> None:
        expected = document_idempotency_key(hashlib.sha3_512(PDF).hexdigest())
        assert upload(client).json()["document_id"] == expected

    def test_the_same_bytes_are_the_same_document(self, client: TestClient) -> None:
        first = upload(client).json()
        second = upload(client).json()
        assert first["document_id"] == second["document_id"]

    def test_the_same_bytes_for_another_chart_are_refused(self, client: TestClient) -> None:
        # D40 item 8: never rebound, and not a second upload either.
        upload(client)
        response = upload(client, patient=OTHER_PATIENT)
        assert response.status_code == 409

    def test_an_empty_body_is_refused(self, client: TestClient) -> None:
        assert upload(client, body=b"").status_code == 400

    def test_something_that_is_not_a_pdf_is_refused(self, client: TestClient) -> None:
        assert upload(client, body=b"GIF89a not a pdf").status_code == 415

    def test_a_pdf_header_on_garbage_is_refused(self, client: TestClient) -> None:
        assert upload(client, body=b"%PDF-1.7\nnot really\n").status_code == 415

    def test_a_non_pdf_content_type_is_refused(self, client: TestClient) -> None:
        assert upload(client, content_type="text/plain").status_code == 415

    def test_an_oversize_body_is_refused(self, client: TestClient) -> None:
        big = b"%PDF-" + b"0" * documents.MAX_UPLOAD_BYTES
        assert upload(client, body=big).status_code == 413

    def test_an_unknown_document_kind_is_refused(self, client: TestClient) -> None:
        assert upload(client, kind="radiology_report").status_code == 422


class TestExtractionState:
    def test_an_unknown_document_is_404(self, client: TestClient) -> None:
        assert status(client, "f" * 128).status_code == 404

    def test_another_patients_document_is_the_same_404(self, client: TestClient) -> None:
        document_id = upload(client).json()["document_id"]
        response = status(client, document_id, patient=OTHER_PATIENT)
        assert response.status_code == 404
        assert response.json()["message"] == status(client, "f" * 128).json()["message"]

    def test_the_extraction_runs_to_complete_with_a_real_extraction_id(
        self, client: TestClient
    ) -> None:
        document_id = upload(client).json()["document_id"]
        body = wait_for_terminal(client, document_id)
        assert body["status"] == ExtractionStatus.COMPLETE.value
        assert body["extraction_id"] is not None and body["extraction_id"].startswith("w2x-")
        assert body["failure_reason"] is None

    def test_no_extraction_id_before_the_run_completes(self, client: TestClient) -> None:
        document_id = upload(client).json()["document_id"]
        body = status(client, document_id).json()
        if body["status"] != ExtractionStatus.COMPLETE.value:
            assert body["extraction_id"] is None


class TestNoPhi:
    def test_the_upload_response_carries_no_file_name(self, client: TestClient) -> None:
        body = upload(client).json()
        assert set(body) == {
            "document_id",
            "kind",
            "status",
            "byte_length",
            "correlation_id",
            # D60: a closed-set outcome, never the name it compared.
            "identity_check",
        }
        assert body["identity_check"] in (None, "mismatch", "confirmed", "not_confirmed")

    def test_the_status_response_carries_no_payload(self, client: TestClient) -> None:
        document_id = upload(client).json()["document_id"]
        body = wait_for_terminal(client, document_id)
        assert "payload" not in body

    def test_refusals_log_no_patient_user_or_token(self, client: TestClient) -> None:
        token = jwt(sub="user-secret-sub", scopes=["openid"])
        with capture_logs() as logs:
            upload(client, headers=bearer(token))
            upload(client, headers={"Authorization": "Bearer nope"})
        text = json.dumps(logs, default=str)
        assert logs, "refusals are logged"
        for secret in (PATIENT, "user-secret-sub", token, "nope"):
            assert secret not in text
