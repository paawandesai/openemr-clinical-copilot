"""
The operational events (app/models/telemetry_events.py, emitted by
app/middleware/telemetry.py and CorrelationIdMiddleware) and their Langfuse
scores (app/middleware/langfuse_export.py).

What is pinned here is what the dashboard and the alerts depend on: every
event carries the request's correlation id; a caller that reuses one id
across an encounter's requests gets one id on all of them; the retry count
is the SDK's real one; a 4xx is not a dependency failure; and nothing in any
of these events can carry text.

    cd agent && python -m pytest tests/test_w2_telemetry.py
"""

from __future__ import annotations

import asyncio
import time
import uuid

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from structlog.testing import capture_logs

from app.api import documents
from app.fhir.exceptions import PatientNotFoundError
from app.main import app
from app.middleware import langfuse_export
from app.middleware.correlation import accepted_correlation_id, correlation_scope
from app.middleware.telemetry import anthropic_http_client, record_step, step, tool_call
from app.models.telemetry_events import (
    TELEMETRY_EVENT_TYPES,
    Pipeline,
    StepEvent,
    StepOutcome,
    Tool,
    ToolCallEvent,
)
from app.workers import document_store
from app.workers.document_store import DocumentStore
from tests.intake_extractor_support import (
    chart_for,
    ground_truth_vision,
    patient_uuid,
    pdf_bytes,
)
from tests.test_w2_writeback_client import _post as post_writeback

TOKEN = {"Authorization": "Bearer test-token"}
ENCOUNTER = "7f3a9c2e-5b1d-4e8a-9c2f-000000000001"  # a UUID4, as D36 requires


def _events(logs: list[dict], event_type: str) -> list[dict]:
    return [
        log
        for log in logs
        if log.get("event") == "agent_event" and log.get("event_type") == event_type
    ]


# The real upload route (D7, work order 13) with main's own test doubles:
# the Patient read patched, the vision call replayed from ground truth, the
# chart the factory generated against. Text layer, locate and checks are real.

SAMPLE = "lab-clean-001"
PATIENT = patient_uuid(SAMPLE)
PDF = pdf_bytes(SAMPLE)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The same doubles as tests/test_w2_documents_api.py's fixture."""

    async def patient_exists(token: str, patient_id: str) -> None:
        if patient_id != PATIENT:
            raise PatientNotFoundError(patient_id)

    def chart_reader(token: str, **_: object):  # noqa: ANN202
        async def read(uuid: str):  # noqa: ANN202
            return chart_for(SAMPLE)

        return read

    monkeypatch.setattr(documents.fhir_client, "ensure_patient_exists", patient_exists)
    monkeypatch.setattr(documents, "fhir_chart_reader", chart_reader)
    monkeypatch.setattr(
        document_store, "DOCUMENTS", DocumentStore(vision=ground_truth_vision(SAMPLE))
    )
    with TestClient(app) as test_client:
        yield test_client


def upload(client: TestClient, headers: dict | None = None):  # noqa: ANN201
    return client.post(
        "/documents",
        params={"kind": "lab_pdf", "patient_id": PATIENT},
        content=PDF,
        headers={"Content-Type": "application/pdf", **(headers or TOKEN)},
    )


def poll(client: TestClient, document_id: str, headers: dict, seconds: float = 60.0) -> dict:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        body = client.get(
            f"/documents/{document_id}/extraction",
            params={"patient_id": PATIENT},
            headers=headers,
        ).json()
        if body["status"] in ("complete", "failed"):
            return body
        time.sleep(0.1)
    raise AssertionError("extraction did not finish")


# --- correlation id: one encounter, one id -------------------------------


class TestCorrelationId:
    @pytest.mark.parametrize(
        "inbound",
        [str(uuid.uuid4()), "corr-turn", "k6-3-14-1790000000000", ENCOUNTER, "a" * 128],
    )
    def test_an_identifier_shaped_id_is_kept(self, inbound: str) -> None:
        assert accepted_correlation_id(inbound) == inbound

    @pytest.mark.parametrize(
        "inbound",
        ["Jane Doe 1961-04-02", "", "a" * 129, "-leading-dash", "id\nwith-newline", "émile"],
    )
    def test_anything_else_is_replaced_not_logged(self, inbound: str) -> None:
        replaced = accepted_correlation_id(inbound)
        assert replaced != inbound
        assert uuid.UUID(replaced).version == 4

    def test_a_name_in_the_header_never_reaches_a_log_line(self, client: TestClient) -> None:
        with capture_logs() as logs:
            response = client.get("/health", headers={"X-Correlation-ID": "Jane Doe"})
        assert "Jane Doe" not in str(logs)
        assert "Jane Doe" not in response.headers["X-Correlation-ID"]

    def test_every_request_of_an_encounter_carries_the_callers_id(self, client: TestClient) -> None:
        """
        Upload, the background extraction it starts, and every status poll
        are one correlation id: the caller's. The extraction runs on its own
        task after the upload returns, so this is the chain break the
        extractor used to have (it minted its own id).
        """
        headers = {**TOKEN, "X-Correlation-ID": ENCOUNTER}
        with capture_logs() as logs:
            uploaded = upload(client, headers=headers)
            assert uploaded.status_code == 200, uploaded.text
            final = poll(client, uploaded.json()["document_id"], headers)
        assert final["status"] == "complete"

        agent_events = [log for log in logs if log.get("event") == "agent_event"]
        types = {e["event_type"] for e in agent_events}
        assert {
            "w2_document_upload_accepted",
            "w2_extraction_state_read",
            "intake_extraction_completed",
            "step_event",
            "http_request_event",
        } <= types
        extractor_steps = [
            e["event_data"]["step"]
            for e in agent_events
            if e["event_type"] == "step_event" and e["event_data"]["pipeline"] == "intake_extractor"
        ]
        assert extractor_steps == ["read_document", "vision", "locate", "verify"]
        # capture_logs drops the contextvars processor, so read the id each
        # event carries in its own payload.
        assert {e["event_data"]["correlation_id"] for e in agent_events} == {ENCOUNTER}

    def test_a_non_uuid_encounter_id_still_uploads_and_links_the_two_traces(
        self, client: TestClient
    ) -> None:
        """
        The extraction contract requires a UUID. An identifier-shaped
        non-UUID the middleware accepted must not turn an upload into a 500:
        the extraction gets its own id, and the link is logged under the
        request's.
        """
        headers = {**TOKEN, "X-Correlation-ID": "enc-7f3a9c2e-0001"}
        with capture_logs() as logs:
            uploaded = upload(client, headers=headers)
            assert uploaded.status_code == 200, uploaded.text
            final = poll(client, uploaded.json()["document_id"], headers)
        assert final["status"] == "complete"
        (link,) = _events(logs, "w2_extraction_correlation_replaced")
        extraction_id = link["event_data"]["extraction_correlation_id"]
        assert uuid.UUID(extraction_id).version == 4
        (completed,) = _events(logs, "intake_extraction_completed")
        assert completed["event_data"]["correlation_id"] == extraction_id


# --- http_request_event -----------------------------------------------------


class TestRequestEvents:
    def test_the_route_is_the_template_not_the_path(self, client: TestClient) -> None:
        with capture_logs() as logs:
            client.get(
                "/documents/some-unknown-document/extraction",
                params={"patient_id": PATIENT},
                headers=TOKEN,
            )
        (request,) = _events(logs, "http_request_event")
        data = request["event_data"]
        assert data["route"] == "/documents/{document_id}/extraction"
        assert data["method"] == "GET" and data["status_code"] == 404
        assert "some-unknown-document" not in str(data)
        assert data["latency_ms"] >= 0

    def test_an_unmatched_path_is_one_row_not_many(self, client: TestClient) -> None:
        with capture_logs() as logs:
            client.get("/no/such/route/12345")
        (request,) = _events(logs, "http_request_event")
        assert request["event_data"]["route"] == "unmatched"


# --- step_event -------------------------------------------------------------


class TestSteps:
    def test_upload_and_status_are_steps_with_outcomes(self, client: TestClient) -> None:
        with capture_logs() as logs:
            uploaded = upload(client)
            poll(client, uploaded.json()["document_id"], TOKEN)
        steps = [
            (e["event_data"]["pipeline"], e["event_data"]["step"], e["event_data"]["outcome"])
            for e in _events(logs, "step_event")
            if e["event_data"]["pipeline"] == "documents"
        ]
        assert steps[0] == ("documents", "upload", "ok")
        assert steps[-1] == ("documents", "extraction_status", "ok")
        assert all(step == "extraction_status" for _, step, _ in steps[1:])
        (upload_counts,) = [
            e["event_data"]["counts"]
            for e in _events(logs, "step_event")
            if e["event_data"]["step"] == "upload"
        ]
        assert upload_counts == {"byte_length": len(PDF), "duplicate": False}

    def test_a_failing_step_records_the_class_name_and_reraises(self) -> None:
        with capture_logs() as logs, pytest.raises(KeyError):
            with correlation_scope("corr-step"), step(Pipeline.WRITEBACK, "writeback"):
                raise KeyError("Jane Doe, glucose 89 mg/dL")
        (event,) = _events(logs, "step_event")
        data = event["event_data"]
        assert (data["outcome"], data["error_type"]) == ("error", "KeyError")
        assert data["correlation_id"] == "corr-step"
        assert "Jane" not in str(logs) and "89" not in str(data)

    def test_counts_cannot_carry_text(self) -> None:
        with pytest.raises(ValidationError):
            StepEvent(
                pipeline=Pipeline.DOCUMENTS,
                step="upload",
                outcome=StepOutcome.OK,
                latency_ms=1.0,
                counts={"patient": "Jane Doe"},  # type: ignore[dict-item]
                correlation_id="c",
            )

    def test_an_invalid_step_is_dropped_not_raised(self) -> None:
        with capture_logs() as logs:
            record_step(Pipeline.DOCUMENTS, "Not A Step Name", 0.1)
        assert _events(logs, "step_event") == []
        (invalid,) = [log for log in logs if log.get("event") == "telemetry_event_invalid"]
        assert invalid["fields"] == ["step"]


# --- tool_call_event and retries ---------------------------------------------


def _anthropic_with(responses: list[httpx2.Response]) -> tuple[anthropic.AsyncAnthropic, list]:
    # The Anthropic SDK is built on httpx2, not httpx (anthropic._base_client).
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return responses[len(seen) - 1]

    client = anthropic.AsyncAnthropic(
        api_key="test-key",
        http_client=anthropic_http_client(httpx2.MockTransport(handler)),
        max_retries=2,
    )
    return client, seen


_MESSAGE = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-opus-5",
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 1, "output_tokens": 1},
}


class TestToolCalls:
    def test_the_sdks_silent_retries_are_counted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # No backoff sleep in a unit test; the retry itself is what is measured.
        monkeypatch.setattr(anthropic.AsyncAnthropic, "_calculate_retry_timeout", lambda *a: 0.0)
        overloaded = httpx2.Response(529, json={"type": "error", "error": {"type": "overloaded"}})
        client, seen = _anthropic_with(
            [overloaded, overloaded, httpx2.Response(200, json=_MESSAGE)]
        )

        async def call() -> None:
            with tool_call(Tool.LLM, "claude-opus-5"):
                await client.messages.create(
                    model="claude-opus-5",
                    max_tokens=1,
                    messages=[{"role": "user", "content": "hi"}],
                )

        with capture_logs() as logs:
            asyncio.run(call())
        assert len(seen) == 3
        (event,) = _events(logs, "tool_call_event")
        assert (event["event_data"]["attempts"], event["event_data"]["retries"]) == (3, 2)
        assert event["event_data"]["outcome"] == "ok"

    def test_a_call_with_no_retry_is_one_attempt(self) -> None:
        client, _ = _anthropic_with([httpx2.Response(200, json=_MESSAGE)])

        async def call() -> None:
            with tool_call(Tool.LLM, "claude-opus-5"):
                await client.messages.create(
                    model="claude-opus-5",
                    max_tokens=1,
                    messages=[{"role": "user", "content": "hi"}],
                )

        with capture_logs() as logs:
            asyncio.run(call())
        (event,) = _events(logs, "tool_call_event")
        assert (event["event_data"]["attempts"], event["event_data"]["retries"]) == (1, 0)

    def test_a_writeback_is_a_step_and_a_tool_call(self) -> None:
        with capture_logs() as logs:
            post_writeback(200, None)
        (tool,) = _events(logs, "tool_call_event")
        assert tool["event_data"]["tool"] == "lab_writeback"
        assert (tool["event_data"]["outcome"], tool["event_data"]["status_code"]) == ("ok", 200)
        (writeback,) = _events(logs, "step_event")
        counts = writeback["event_data"]["counts"]
        assert counts["results_total"] == counts["results_created"] >= 1

    def test_a_refused_writeback_is_not_a_dependency_failure(self) -> None:
        from app.writeback.exceptions import WriteBackRefused

        with capture_logs() as logs, pytest.raises(WriteBackRefused):
            post_writeback(409, {"error": "x", "message": "duplicate", "field": None})
        (tool,) = _events(logs, "tool_call_event")
        event = ToolCallEvent.model_validate(tool["event_data"])
        assert event.outcome is StepOutcome.ERROR and event.status_code == 409
        assert not langfuse_export.is_dependency_failure(event)

    def test_an_unreachable_writeback_is_a_dependency_failure(self) -> None:
        from app.writeback.exceptions import WriteBackUnavailable

        with capture_logs() as logs, pytest.raises(WriteBackUnavailable):
            post_writeback(503, "upstream down")
        (tool,) = _events(logs, "tool_call_event")
        assert langfuse_export.is_dependency_failure(
            ToolCallEvent.model_validate(tool["event_data"])
        )


# --- Langfuse scores ----------------------------------------------------------


class TestScores:
    def test_a_step_is_scored_by_name(self) -> None:
        derived = langfuse_export._step_scores(
            {
                "pipeline": "conversation",
                "step": "draft",
                "outcome": "error",
                "latency_ms": 812.5,
                "error_type": "APIConnectionError",
                "counts": {},
                "correlation_id": "c",
            }
        )
        assert derived.scores == [("step_latency_ms:conversation.draft", 812.5, "NUMERIC")]
        assert derived.level == "ERROR" and "APIConnectionError" in derived.status_message

    def test_a_server_error_is_scored_and_raised_to_error_level(self) -> None:
        derived = langfuse_export._request_scores(
            {
                "method": "POST",
                "route": "/pre-visit-brief",
                "status_code": 502,
                "latency_ms": 30.0,
                "correlation_id": "c",
            }
        )
        assert ("request_server_error", 1.0, "BOOLEAN") in derived.scores
        assert derived.level == "ERROR"

    @pytest.mark.parametrize(
        ("outcomes", "rate"),
        [
            ({"located": 9, "unlocated": 1, "missing": 5}, 0.9),
            ({"located": 0, "unlocated": 4}, 0.0),
            ({"missing": 3, "illegible": 1}, None),
        ],
    )
    def test_located_rate_leaves_out_what_the_document_lacks(
        self, outcomes: dict[str, int], rate: float | None
    ) -> None:
        assert langfuse_export.located_rate(outcomes) == rate

    def test_the_extractors_summary_becomes_two_scores(self) -> None:
        derived = langfuse_export._extraction_scores(
            {
                "document_id": "d",
                "field_outcomes": {"located": 18, "unlocated": 2},
                "mean_confidence": 0.93,
            }
        )
        assert derived.scores == [
            ("extraction_located_rate", 0.9, "NUMERIC"),
            ("extraction_mean_confidence", 0.93, "NUMERIC"),
        ]

    def test_retrieval_hits_and_rerank_are_scored(self) -> None:
        derived = langfuse_export._retrieval_scores({"hit_count": 5, "reranked": True})
        assert derived.scores == [
            ("retrieval_hit_count", 5.0, "NUMERIC"),
            ("retrieval_reranked", 1.0, "BOOLEAN"),
        ]


def test_the_telemetry_types_are_the_three_operational_events() -> None:
    assert TELEMETRY_EVENT_TYPES == {"step_event", "tool_call_event", "http_request_event"}
