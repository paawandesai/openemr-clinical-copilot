"""
The document upload and extraction-status routes (app/api/documents.py).

Work order 11 tested these as a stub; work order 13 made them run the real
intake-extractor at upload (D7). What the browser depends on is unchanged —
the response fields, the digest-derived document id, the status it polls —
and this file pins that, plus what is new: the patient binding, the Patient
read that proves the token, the 404 for another patient's document, and an
extraction id that appears only once a run has really completed.

No network: the Patient read is patched, the vision call is the ground-truth
transcriber (tests/intake_extractor_support.py) and the chart is the
factory's own chart cache. Text layer, locate and the checks are real.

    cd agent && python -m pytest tests/test_w2_documents_api.py
"""

from __future__ import annotations

import hashlib
import time

import pytest
from fastapi.testclient import TestClient

from app.api import documents
from app.fhir.exceptions import PatientNotFoundError
from app.main import app
from app.models.w2.documents import ExtractionStatus
from app.models.w2.writeback import document_idempotency_key
from app.workers import document_store
from app.workers.document_store import DocumentStore
from tests.intake_extractor_support import (
    chart_for,
    ground_truth_vision,
    patient_uuid,
    pdf_bytes,
)

SAMPLE = "lab-clean-001"
PATIENT = patient_uuid(SAMPLE)
OTHER_PATIENT = "00000000-0000-4000-8000-000000000000"
TOKEN = {"Authorization": "Bearer test-token"}
PDF = pdf_bytes(SAMPLE)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def patient_exists(token: str, patient_id: str) -> None:
        if patient_id not in (PATIENT, OTHER_PATIENT):
            raise PatientNotFoundError(patient_id)

    def chart_reader(token: str, **_: object):
        async def read(uuid: str):
            return chart_for(SAMPLE)

        return read

    monkeypatch.setattr(documents.fhir_client, "ensure_patient_exists", patient_exists)
    monkeypatch.setattr(documents, "fhir_chart_reader", chart_reader)
    monkeypatch.setattr(
        document_store, "DOCUMENTS", DocumentStore(vision=ground_truth_vision(SAMPLE))
    )
    with TestClient(app) as test_client:
        yield test_client


def upload(
    client: TestClient,
    body: bytes = PDF,
    kind: str = "lab_pdf",
    patient: str = PATIENT,
    **kwargs,
):
    return client.post(
        "/documents",
        params={"kind": kind, "patient_id": patient},
        content=body,
        headers={"Content-Type": "application/pdf", **kwargs.pop("headers", TOKEN)},
        **kwargs,
    )


def status(client: TestClient, document_id: str, patient: str = PATIENT):
    return client.get(
        f"/documents/{document_id}/extraction", params={"patient_id": patient}, headers=TOKEN
    )


def wait_for_terminal(client: TestClient, document_id: str, seconds: float = 60.0) -> dict:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        body = status(client, document_id).json()
        if body["status"] in (ExtractionStatus.COMPLETE.value, ExtractionStatus.FAILED.value):
            return body
        time.sleep(0.1)
    raise AssertionError("extraction did not finish")


class TestAuthorization:
    def test_upload_without_a_token_is_401(self, client: TestClient) -> None:
        response = upload(client, headers={})
        assert response.status_code == 401
        assert response.json()["error"] == "missing_bearer_token"

    def test_status_without_a_token_is_401(self, client: TestClient) -> None:
        response = client.get(f"/documents/{'a' * 128}/extraction", params={"patient_id": PATIENT})
        assert response.status_code == 401

    def test_an_unknown_patient_is_404_before_anything_is_stored(self, client: TestClient) -> None:
        response = upload(client, patient="11111111-1111-4111-8111-111111111111")
        assert response.status_code == 404
        assert response.json()["error"] == "patient_not_found"
        assert (
            document_store.DOCUMENTS.lookup(
                document_idempotency_key(hashlib.sha3_512(PDF).hexdigest()), PATIENT
            )
            is None
        )

    def test_patient_id_is_required(self, client: TestClient) -> None:
        response = client.post(
            "/documents",
            params={"kind": "lab_pdf"},
            content=PDF,
            headers={"Content-Type": "application/pdf", **TOKEN},
        )
        assert response.status_code == 422


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
        response = client.post(
            "/documents",
            params={"kind": "lab_pdf", "patient_id": PATIENT},
            content=PDF,
            headers={"Content-Type": "text/plain", **TOKEN},
        )
        assert response.status_code == 415

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


class TestNoPhiInResponses:
    def test_the_upload_response_carries_no_file_name(self, client: TestClient) -> None:
        body = upload(client).json()
        assert set(body) == {"document_id", "kind", "status", "byte_length", "correlation_id"}

    def test_the_status_response_carries_no_payload(self, client: TestClient) -> None:
        document_id = upload(client).json()["document_id"]
        body = wait_for_terminal(client, document_id)
        assert "payload" not in body
