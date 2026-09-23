"""
POST /w2/ask over HTTP (app/api/ask.py): Week 1's authorization, the request
contract, and a full turn serialized through FastAPI's response model — the
strict W2 contracts, AnswerView included, as the chart embed would read them.
Offline: FHIR, retrieval and drafting are the fakes in tests/w2_graph_support.py.
"""

from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

from app.graph import conversation, supervisor
from app.main import app
from app.workers import document_store
from app.workers.document_store import DocumentStore
from tests.w2_graph_support import PATIENT, FakeChart, ScriptedDrafter, offline_retriever


def _jwt(sub: str) -> str:
    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'none'})}.{part({'sub': sub})}.sig"


HEADERS = {"Authorization": f"Bearer {_jwt('api-test-user')}"}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    FakeChart().install(monkeypatch)
    monkeypatch.setattr(supervisor, "draft_answer", ScriptedDrafter())
    monkeypatch.setattr(supervisor, "default_retriever", offline_retriever)
    monkeypatch.setattr(document_store, "DOCUMENTS", DocumentStore())
    monkeypatch.setattr(conversation, "STORE", type(conversation.STORE)())
    with TestClient(app) as test_client:
        yield test_client


def ask(client: TestClient, question: str, **body: object):
    payload = {"patient_id": PATIENT, "session_id": "api-session-0001", "question": question}
    return client.post("/w2/ask", json={**payload, **body}, headers=body.pop("headers", HEADERS))


def test_no_token_is_401(client: TestClient) -> None:
    response = client.post(
        "/w2/ask",
        json={"patient_id": PATIENT, "session_id": "api-session-0001", "question": "x"},
    )
    assert response.status_code == 401


def test_an_unknown_patient_is_404(client: TestClient) -> None:
    response = ask(client, "What problems?", patient_id="11111111-1111-4111-8111-111111111111")
    assert response.status_code == 404
    assert response.json()["error"] == "patient_not_found"


def test_extra_fields_are_refused(client: TestClient) -> None:
    assert ask(client, "What problems?", history=["earlier"]).status_code == 422


def test_a_chart_question_answers_through_http(client: TestClient) -> None:
    response = ask(client, "What problems are on her list?")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "answered"
    assert body["routing"] == [
        {
            "decision": "answer",
            "reason_code": "chart_only_question",
            "correlation_id": body["correlation_id"],
        }
    ]
    assert body["answer_view"]["schema_version"] == "w2-ui/answer-view/1.0.0"
    assert body["answer_view"]["answer"]["patient_facts"][0]["citations"][0]["source_type"] == (
        "chart"
    )


def test_a_refusal_is_a_200_with_a_reason_code(client: TestClient) -> None:
    body = ask(client, "What is her blood pressure?").json()
    assert body["status"] == "refused" and body["reason_code"] == "out_of_scope_resource"
    assert body["answer_view"] is None


def test_a_missing_document_is_a_200_refusal_not_an_error(client: TestClient) -> None:
    body = ask(client, "What does the report say?", document_ids=["f" * 128]).json()
    assert body["reason_code"] == "document_not_found"
