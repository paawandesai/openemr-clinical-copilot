"""
The document upload and extraction-status stub (app/api/documents.py).

Covers what the browser depends on and what the stub promises not to do:
the D7 state machine the client polls, the digest-derived document id, and
the fact that a stub extraction never produces a `payload` or an
`extraction_id` for a run that did not happen.

    cd agent && python -m pytest tests/test_w2_documents_api.py
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from app.api import documents
from app.main import app
from app.models.w2.documents import ExtractionStatus
from app.models.w2.writeback import document_idempotency_key

TOKEN = {"Authorization": "Bearer test-token"}
PDF = b"%PDF-1.7\n% a tiny but plausible file\n%%EOF\n"


@pytest.fixture
def client() -> TestClient:
    documents._reset_for_tests()
    with TestClient(app) as test_client:
        yield test_client
    documents._reset_for_tests()


def upload(client: TestClient, body: bytes = PDF, kind: str = "lab_pdf", **kwargs):
    return client.post(
        "/documents",
        params={"kind": kind},
        content=body,
        headers={"Content-Type": "application/pdf", **kwargs.pop("headers", TOKEN)},
        **kwargs,
    )


class TestAuthorization:
    def test_upload_without_a_token_is_401(self, client: TestClient) -> None:
        response = upload(client, headers={})
        assert response.status_code == 401
        assert response.json()["error"] == "missing_bearer_token"
        assert response.headers["WWW-Authenticate"] == "Bearer"

    def test_status_without_a_token_is_401(self, client: TestClient) -> None:
        response = client.get("/documents/anything/extraction")
        assert response.status_code == 401


class TestUpload:
    def test_a_pdf_is_accepted_and_starts_extracting(self, client: TestClient) -> None:
        response = upload(client)
        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "lab_pdf"
        assert body["byte_length"] == len(PDF)
        # D7: extraction is already under way when the upload answers.
        assert body["status"] in {s.value for s in ExtractionStatus}
        assert body["status"] != ExtractionStatus.COMPLETE.value

    def test_the_document_id_is_the_d1_key_over_the_bytes(self, client: TestClient) -> None:
        body = upload(client).json()
        assert body["document_id"] == document_idempotency_key(hashlib.sha3_512(PDF).hexdigest())

    def test_the_same_bytes_are_the_same_document(self, client: TestClient) -> None:
        first = upload(client).json()
        second = upload(client).json()
        assert first["document_id"] == second["document_id"]

    def test_different_bytes_are_a_different_document(self, client: TestClient) -> None:
        first = upload(client).json()
        second = upload(client, body=PDF + b"more\n").json()
        assert first["document_id"] != second["document_id"]

    def test_an_empty_body_is_refused(self, client: TestClient) -> None:
        assert upload(client, body=b"").status_code == 400

    def test_something_that_is_not_a_pdf_is_refused(self, client: TestClient) -> None:
        response = upload(client, body=b"GIF89a not a pdf")
        assert response.status_code == 415

    def test_a_non_pdf_content_type_is_refused(self, client: TestClient) -> None:
        response = client.post(
            "/documents",
            params={"kind": "lab_pdf"},
            content=PDF,
            headers={"Content-Type": "application/octet-stream", **TOKEN},
        )
        assert response.status_code == 415

    def test_an_oversize_body_is_refused(self, client: TestClient) -> None:
        big = b"%PDF-1.7\n" + b"0" * (documents.MAX_UPLOAD_BYTES + 1)
        assert upload(client, body=big).status_code == 413

    def test_an_unknown_document_kind_is_refused(self, client: TestClient) -> None:
        assert upload(client, kind="radiology_report").status_code == 422


class TestExtractionState:
    def test_an_unknown_document_is_404(self, client: TestClient) -> None:
        response = client.get("/documents/w2x-nope/extraction", headers=TOKEN)
        assert response.status_code == 404

    def test_the_status_advances_from_pending_to_complete(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        document_id = upload(client).json()["document_id"]

        def read() -> str:
            response = client.get(f"/documents/{document_id}/extraction", headers=TOKEN)
            assert response.status_code == 200
            return response.json()["status"]

        assert read() == ExtractionStatus.PENDING.value

        accepted = documents._ACCEPTED[document_id]
        # Move the accepted-at time rather than sleeping: the schedule is
        # the thing under test, not the clock.
        accepted.accepted_at -= documents.STUB_QUEUE_SECONDS + 0.01
        assert read() == ExtractionStatus.IN_PROGRESS.value

        accepted.accepted_at -= documents.STUB_WORK_SECONDS
        assert read() == ExtractionStatus.COMPLETE.value

    def test_a_stub_extraction_never_claims_an_extraction_id(self, client: TestClient) -> None:
        # An extraction id names a run that produced values. There was no
        # run, so a citation could not be traced back to one (D1, D15).
        document_id = upload(client).json()["document_id"]
        documents._ACCEPTED[document_id].accepted_at -= 1000
        body = client.get(f"/documents/{document_id}/extraction", headers=TOKEN).json()
        assert body["status"] == ExtractionStatus.COMPLETE.value
        assert body["extraction_id"] is None
        assert body["failure_reason"] is None
        assert "payload" not in body

    def test_an_expired_stub_is_forgotten(self, client: TestClient) -> None:
        document_id = upload(client).json()["document_id"]
        documents._ACCEPTED[document_id].accepted_at -= documents.STUB_TTL_SECONDS + 1
        assert client.get(f"/documents/{document_id}/extraction", headers=TOKEN).status_code == 404


class TestNoPhiInLogs:
    def test_the_upload_response_carries_no_file_name(self, client: TestClient) -> None:
        # The endpoint never sees a file name — the body is the PDF — so
        # there is nothing for a log line to leak. Invariant 1.
        body = upload(client).json()
        assert set(body) == {"document_id", "kind", "status", "byte_length", "correlation_id"}
