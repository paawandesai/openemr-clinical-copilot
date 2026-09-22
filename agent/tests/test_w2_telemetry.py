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
import uuid

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from structlog.testing import capture_logs

from app.api import documents
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
from tests.test_w2_writeback_client import _post as post_writeback

TOKEN = {"Authorization": "Bearer test-token"}
PDF = b"%PDF-1.7\n% a tiny but plausible file\n%%EOF\n"
ENCOUNTER = "enc-7f3a9c2e-0001"


def _events(logs: list[dict], event_type: str) -> list[dict]:
    return [
        log
        for log in logs
        if log.get("event") == "agent_event" and log.get("event_type") == event_type
    ]


@pytest.fixture
def client() -> TestClient:
    documents._reset_for_tests()
    with TestClient(app) as test_client:
        yield test_client
    documents._reset_for_tests()


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
        headers = {**TOKEN, "X-Correlation-ID": ENCOUNTER}
        with capture_logs() as logs:
            uploaded = client.post(
                "/documents",
                params={"kind": "lab_pdf"},
                content=PDF,
                headers={**headers, "Content-Type": "application/pdf"},
            )
            document_id = uploaded.json()["document_id"]
            client.get(f"/documents/{document_id}/extraction", headers=headers)

        agent_events = [log for log in logs if log.get("event") == "agent_event"]
        assert {e["event_type"] for e in agent_events} >= {
            "w2_document_upload_accepted",
            "w2_extraction_state_read",
            "step_event",
            "http_request_event",
        }
        # capture_logs drops the contextvars processor, so read the id each
        # event carries in its own payload.
        assert {e["event_data"]["correlation_id"] for e in agent_events} == {ENCOUNTER}


# --- http_request_event -----------------------------------------------------


class TestRequestEvents:
    def test_the_route_is_the_template_not_the_path(self, client: TestClient) -> None:
        with capture_logs() as logs:
            client.get("/documents/some-unknown-document/extraction", headers=TOKEN)
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
            uploaded = client.post(
                "/documents",
                params={"kind": "lab_pdf"},
                content=PDF,
                headers={**TOKEN, "Content-Type": "application/pdf"},
            )
            client.get(f"/documents/{uploaded.json()['document_id']}/extraction", headers=TOKEN)
        steps = [e["event_data"] for e in _events(logs, "step_event")]
        assert [(s["pipeline"], s["step"], s["outcome"]) for s in steps] == [
            ("documents", "upload", "ok"),
            ("documents", "extraction_status", "pending"),
        ]
        assert steps[0]["counts"] == {"byte_length": len(PDF), "duplicate": False, "stub": True}

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
