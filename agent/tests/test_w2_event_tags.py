"""
Every `agent_event` a conversation turn emits carries the turn's
`use_case` and `patient_session_key` — the operational events included.

The live eval that found this (evals/test_i_conversation.py, I2 and I11,
`_check_events`) holds every event of a turn to both tags. After the
observability work (D38) two event types reached a turn untagged:

  - `tool_call_event` for each FHIR read: `_get_bundle` and
    `ensure_patient_exists` open `tool_call()` with no tags, and cannot be
    given any without widening a signature the Week 1 evals pin with a
    three-argument fake (docs/w2/evidence/i2-i11-event-tagging.md).
  - `http_request_event`: emitted by CorrelationIdMiddleware after the
    endpoint returns, with no patient context at all.

`test_conversation.py` never saw it: its tag assertions filter the
operational event types out, and its FakeWorld replaces the FHIR readers
whole, so no FHIR tool call is ever made. Here nothing above the socket is
faked on the FHIR side — the real readers, the real `_get_bundle`, the real
`ensure_patient_exists` against an httpx MockTransport — and the model is
the fake Anthropic stream, so the real LLM tool call runs too. The request
goes through the whole app, middleware included, as the eval harness's
does.

When the first test fails, its message lists every event that lacks a tag
and says which it lacks: that is the reproduction.

    cd agent && python -m pytest tests/test_w2_event_tags.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from collections.abc import Iterator
from typing import Any
from unittest import mock

import httpx
import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from app.conversation.state import ConversationStore
from app.fhir import client as fhir_client
from app.graph import conversation as graph
from app.main import app
from app.middleware.correlation import event_tags_scope
from app.middleware.telemetry import tool_call
from app.models.metrics_events import UseCase
from app.models.telemetry_events import (
    TELEMETRY_EVENT_TYPES,
    HttpRequestEvent,
    StepEvent,
    Tool,
    ToolCallEvent,
)
from app.reasoning import draft_conversation_turn as drafting
from app.reasoning import draft_follow_up
from tests.test_follow_up import AUTH, USER, _fake_client

PATIENT_A = "a2c0e096-23d5-4cf9-ad5b-4895597363a7"
PATIENT_B = "b7d1f207-34e6-4d0a-be6c-5906608474b8"
SESSION_ID = "5e1b0c7a-9d3f-4a2e-8b61-0c4d2f7a9e13"
OTHER_SESSION = "8f2d6a41-7c0e-4b95-a3d8-1e6f0b9c2a57"
URL = "/pre-visit-brief/conversation"
USE_CASE = "follow_up_drilling"
# A lab question, so the turn reads Observation too: five FHIR reads.
QUESTION = "SENTINEL-EVENT-TAGS what was her last creatinine?"
DRAFT = json.dumps({"claims": [], "coverage": "none", "gaps": ["lab_results"]})


def _key(patient_id: str) -> str:
    return f"{USER}:{patient_id}:{SESSION_ID}"


def _fhir(request: httpx.Request) -> httpx.Response:
    """OpenEMR, as far as the readers can tell: the patient exists, the chart is empty."""
    if request.url.path.split("/")[-2] == "Patient":
        return httpx.Response(200, json={"resourceType": "Patient"})
    return httpx.Response(200, json={"resourceType": "Bundle", "type": "searchset", "entry": []})


# What the FHIR client's transport calls; a test swaps it to change OpenEMR.
_fhir_handler = _fhir


@pytest.fixture
def client() -> Iterator[TestClient]:
    real_async_client = httpx.AsyncClient

    def fhir_http(**kwargs: Any) -> httpx.AsyncClient:
        return real_async_client(transport=httpx.MockTransport(_fhir_handler))

    # The FHIR client's own `httpx`, with AsyncClient pointed at the mock
    # transport. The module-global httpx is left alone.
    fhir_httpx = types.SimpleNamespace(**{**vars(httpx), "AsyncClient": fhir_http})
    with (
        mock.patch.object(fhir_client, "httpx", fhir_httpx),
        mock.patch.object(graph, "STORE", ConversationStore()),
        mock.patch.object(graph, "draft_conversation_turn", drafting.draft_conversation_turn),
        mock.patch.object(draft_follow_up, "_client", lambda: _fake_client(DRAFT, [])),
        TestClient(app) as test_client,
    ):
        yield test_client


def _turn(client: TestClient, patient_id: str = PATIENT_A) -> list[dict[str, Any]]:
    with capture_logs() as logs:
        response = client.post(
            URL,
            json={"patient_id": patient_id, "session_id": SESSION_ID, "question": QUESTION},
            headers=AUTH,
        )
    assert response.status_code == 200, response.text
    return [log for log in logs if log.get("event") == "agent_event"]


def _describe(event: dict[str, Any]) -> str:
    data = event["event_data"]
    detail = data.get("tool") or data.get("route") or data.get("step") or ""
    target = data.get("target")
    return f"{event['event_type']}({detail}{':' + target if target else ''})"


def untagged(events: list[dict[str, Any]], key: str) -> list[str]:
    """Every event lacking this turn's use_case or key, and which it lacks."""
    missing = []
    for event in events:
        lacks = [
            name
            for name, expected in (("use_case", USE_CASE), ("patient_session_key", key))
            if event.get(name) != expected
        ]
        if lacks:
            missing.append(f"{_describe(event)} lacks {' and '.join(lacks)}")
    return missing


def test_every_event_of_a_conversation_turn_carries_its_use_case_and_key(
    client: TestClient,
) -> None:
    events = _turn(client)

    # Not vacuous: the turn really produced the two kinds that were untagged,
    # plus the LLM call and the pipeline's own events.
    kinds = {_describe(e) for e in events}
    assert {
        "tool_call_event(fhir_read:Patient)",
        "tool_call_event(fhir_read:AllergyIntolerance)",
        "tool_call_event(fhir_read:Observation)",
        "tool_call_event(llm:claude-opus-5)",
        f"http_request_event({URL})",
        "conversation_turn_completed()",
    } <= kinds, sorted(kinds)

    assert untagged(events, _key(PATIENT_A)) == [], "\n".join(
        [f"{len(events)} events, untagged:", *untagged(events, _key(PATIENT_A))]
    )


# --- No stale tags ------------------------------------------------------------
#
# The failure mode worse than an untagged event: one tagged with a previous
# turn's key. A request that runs no turn must carry none.


def _request_event(logs: list[dict[str, Any]]) -> dict[str, Any]:
    (event,) = [log for log in logs if log.get("event_type") == "http_request_event"]
    return event


@pytest.mark.parametrize("path", ["/health", "/no/such/route"])
def test_a_request_after_a_turn_carries_no_tags(client: TestClient, path: str) -> None:
    _turn(client)
    with capture_logs() as logs:
        client.get(path)
    event = _request_event(logs)
    assert (event["use_case"], event["patient_session_key"]) == (None, None)


def test_a_request_refused_before_the_turn_starts_carries_no_tags(client: TestClient) -> None:
    _turn(client)
    with capture_logs() as logs:
        response = client.post(
            URL, json={"patient_id": PATIENT_A, "session_id": SESSION_ID, "question": QUESTION}
        )
    assert response.status_code == 401
    event = _request_event(logs)
    assert (event["use_case"], event["patient_session_key"]) == (None, None)


def test_a_turn_openemr_rejects_is_attributed_to_the_key_it_was_asked_for(
    client: TestClient,
) -> None:
    """
    The key is computed before the Patient read, from the token's unchecked
    `sub`, so a rejected turn's events carry it. That is the claimed user,
    held to `_SUBJECT`'s alphabet, and the same key Week 1's brief has
    always logged on a rejected token. Pinned so a change to it is a choice.
    """
    rejected = lambda request: httpx.Response(401, json={"error": "invalid_token"})  # noqa: E731
    with mock.patch.object(sys.modules[__name__], "_fhir_handler", rejected):
        with capture_logs() as logs:
            response = client.post(
                URL,
                json={"patient_id": PATIENT_A, "session_id": SESSION_ID, "question": QUESTION},
                headers=AUTH,
            )
    assert response.status_code == 401
    events = [log for log in logs if log.get("event") == "agent_event"]
    assert [_describe(e) for e in events] == [
        "tool_call_event(fhir_read:Patient)",
        f"http_request_event({URL})",
    ]
    assert untagged(events, _key(PATIENT_A)) == []


def test_each_turn_carries_its_own_key_when_the_patient_changes(client: TestClient) -> None:
    first = _turn(client, PATIENT_A)
    second = _turn(client, PATIENT_B)
    assert untagged(first, _key(PATIENT_A)) == []
    assert untagged(second, _key(PATIENT_B)) == []


def test_concurrent_turns_do_not_cross_tags(client: TestClient) -> None:
    """
    One holder per request: two turns in flight at once keep their own.
    Each turn's Patient read waits until the other's has started, so both
    are inside their turn, with their tags set, at the same time.
    """
    started: list[str] = []

    async def overlapping(request: httpx.Request) -> httpx.Response:
        if request.url.path.split("/")[-2] == "Patient":
            started.append(request.url.path)
            for _ in range(200):
                if len(started) == 2:
                    break
                await asyncio.sleep(0.01)
            assert len(started) == 2, "the two turns never overlapped"
        return _fhir(request)

    async def both() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://agent.local") as http:
            return await asyncio.gather(
                *(
                    http.post(
                        URL,
                        json={"patient_id": pid, "session_id": sid, "question": QUESTION},
                        headers={**AUTH, "X-Correlation-ID": f"corr-{pid}"},
                    )
                    for pid, sid in ((PATIENT_A, SESSION_ID), (PATIENT_B, OTHER_SESSION))
                )
            )

    with mock.patch.object(sys.modules[__name__], "_fhir_handler", overlapping):
        with capture_logs() as logs:
            responses = asyncio.run(both())
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    assert len(started) == 2

    events = [log for log in logs if log.get("event") == "agent_event"]
    for pid, key in (
        (PATIENT_A, _key(PATIENT_A)),
        (PATIENT_B, f"{USER}:{PATIENT_B}:{OTHER_SESSION}"),
    ):
        mine = [e for e in events if e["event_data"].get("correlation_id") == f"corr-{pid}"]
        assert len(mine) > 10
        assert untagged(mine, key) == []
    assert all(e["event_data"].get("correlation_id") for e in events)


# --- The scope itself ---------------------------------------------------------


def _tool_call_tags(**explicit: Any) -> tuple[Any, Any]:
    with capture_logs() as logs:
        with tool_call(Tool.FHIR_READ, "Condition", **explicit):
            pass
    (event,) = logs
    return event["use_case"], event["patient_session_key"]


def test_outside_a_request_the_scope_tags_only_its_own_block() -> None:
    key = _key(PATIENT_A)
    assert _tool_call_tags() == (None, None)
    with event_tags_scope(UseCase.FOLLOW_UP_DRILLING, key):
        assert _tool_call_tags() == (USE_CASE, key)
    assert _tool_call_tags() == (None, None)


def test_a_callers_own_tags_win_over_the_turns() -> None:
    with event_tags_scope(UseCase.FOLLOW_UP_DRILLING, _key(PATIENT_A)):
        assert _tool_call_tags(
            use_case=UseCase.PRE_VISIT_BRIEF, patient_session_key=_key(PATIENT_B)
        ) == ("pre_visit_brief", _key(PATIENT_B))


# --- No PHI rides in with the tags --------------------------------------------


def test_the_tags_add_the_key_and_nothing_else(client: TestClient) -> None:
    events = _turn(client)
    operational = [e for e in events if e["event_type"] in TELEMETRY_EVENT_TYPES]
    # The log line's shape is unchanged: the same five fields as every
    # Week 1 event, and the payloads are still the closed D38 schemas.
    assert {frozenset(e) for e in operational} == {
        frozenset(
            {"event", "log_level", "event_type", "event_data", "use_case", "patient_session_key"}
        )
    }
    for event in operational:
        schema = {
            "step_event": StepEvent,
            "tool_call_event": ToolCallEvent,
            "http_request_event": HttpRequestEvent,
        }[event["event_type"]]
        schema.model_validate(event["event_data"])
    assert "SENTINEL-EVENT-TAGS" not in json.dumps(events)
