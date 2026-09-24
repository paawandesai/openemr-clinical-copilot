"""
No emitted event carries a patient uuid (D56, app/middleware/telemetry_key.py).

`patient_session_key` is `<sub>:<patient uuid>:<session id>`. What leaves the
process — the stdout line `configure_logging` renders and what
`export_event` hands the Langfuse exporter — carries its HMAC instead. The
regression tests here run the real app and read both exits, not the
in-process event dicts (`capture_logs` replaces the processor chain, so it
sees the raw key by design: that is what the Week 1 I-suite reads):

  - a conversation turn on patient A, then a turn on patient B in the same
    session, so the switch emits `conversation_invalidated` with A's key in
    its `event_data`, and the drafted claim emits `claim_verified_event`
    and `claim_disclosure_event` with the key in theirs;
  - a lab-PDF upload, through extraction to its terminal state.

Known gap: a module-level `structlog.get_logger()` proxy that was already
used before `_emitted` reconfigures logging keeps writing to the stream it
was first bound to, so its lines are not in the buffer. Every `log_event`
call takes a fresh proxy and is covered.

    cd agent && python -m pytest tests/test_telemetry_key.py
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import subprocess
import sys
import types
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest import mock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import documents, upload_guard
from app.conversation.state import ConversationStore
from app.fhir import client as fhir_client
from app.graph import conversation as graph
from app.main import app
from app.middleware import langfuse_export, telemetry_key
from app.middleware import logging as agent_logging
from app.reasoning import draft_conversation_turn as drafting
from app.reasoning import draft_follow_up
from app.workers import document_store
from app.workers.document_store import DocumentStore
from tests.intake_extractor_support import chart_for, ground_truth_vision
from tests.test_follow_up import AUTH, USER, _fake_client
from tests.test_w2_documents_api import PATIENT as UPLOAD_PATIENT
from tests.test_w2_documents_api import SAMPLE, FakeOpenEMR, upload, wait_for_terminal
from tests.test_w2_event_tags import PATIENT_A, PATIENT_B, QUESTION, SESSION_ID, URL, _fhir

AGENT_DIR = Path(__file__).resolve().parents[1]
SECRET = "test-telemetry-hmac-secret-0123456789abcdef"

# A claim citing a record the (empty) chart does not hold: it fails
# verification, so the turn emits both claim events, each with the key in
# its event_data.
DRAFT = json.dumps(
    {
        "claims": [
            {
                "resource_type": "MedicationRequest",
                "source_id": "med-not-on-chart",
                "field": "display",
                "value": "Warfarin 5 MG Oral Tablet",
            }
        ],
        "coverage": "full",
        "gaps": [],
    }
)


def _key(patient_id: str) -> str:
    return f"{USER}:{patient_id}:{SESSION_ID}"


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv(telemetry_key.MODE_ENV, raising=False)
    monkeypatch.setenv(telemetry_key.SECRET_ENV, SECRET)
    telemetry_key.reset_for_tests()
    yield
    telemetry_key.reset_for_tests()


# --- the two exits ------------------------------------------------------------


@dataclass
class _RecordingExporter:
    """Stands in for LangfuseExporter: what `export_event` hands it is what Langfuse gets."""

    calls: list[dict[str, Any]] = field(default_factory=list)

    def export(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


@dataclass
class Emitted:
    stdout: io.StringIO
    langfuse: _RecordingExporter

    def lines(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.stdout.getvalue().splitlines()]

    def events(self) -> list[dict[str, Any]]:
        return [line for line in self.lines() if line.get("event") == "agent_event"]

    def text(self) -> str:
        """Everything that left the process, as one string to search."""
        return self.stdout.getvalue() + json.dumps(self.langfuse.calls, default=str)


@contextmanager
def _emitted() -> Iterator[Emitted]:
    """The real processor chain, rendering into a buffer, and a recording Langfuse exporter."""
    out = Emitted(io.StringIO(), _RecordingExporter())
    with mock.patch("sys.stdout", out.stdout):
        agent_logging.configure_logging()
    langfuse_export._exporter = out.langfuse  # type: ignore[assignment]
    try:
        yield out
    finally:
        langfuse_export._exporter = None
        agent_logging.configure_logging()


def _assert_no_patient_uuid(out: Emitted, *patients: str) -> None:
    text = out.text()
    leaked = [patient for patient in patients if patient in text]
    if leaked:
        # A count, not the list: the failure message is output too.
        pytest.fail(f"{len(leaked)} patient uuid(s) in emitted telemetry")


# --- a conversation turn, and a patient switch --------------------------------


@pytest.fixture
def conversation_client() -> Iterator[TestClient]:
    real_async_client = httpx.AsyncClient

    def fhir_http(**kwargs: Any) -> httpx.AsyncClient:
        return real_async_client(transport=httpx.MockTransport(_fhir))

    fhir_httpx = types.SimpleNamespace(**{**vars(httpx), "AsyncClient": fhir_http})
    with (
        mock.patch.object(fhir_client, "httpx", fhir_httpx),
        mock.patch.object(graph, "STORE", ConversationStore()),
        mock.patch.object(graph, "draft_conversation_turn", drafting.draft_conversation_turn),
        mock.patch.object(draft_follow_up, "_client", lambda: _fake_client(DRAFT, [])),
        TestClient(app) as test_client,
    ):
        yield test_client


def _turn(client: TestClient, patient_id: str) -> dict[str, Any]:
    response = client.post(
        URL,
        json={"patient_id": patient_id, "session_id": SESSION_ID, "question": QUESTION},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_a_conversation_turn_and_a_patient_switch_emit_no_patient_uuid(
    conversation_client: TestClient,
) -> None:
    with _emitted() as out:
        body_a = _turn(conversation_client, PATIENT_A)
        body_b = _turn(conversation_client, PATIENT_B)

    # Not vacuous: the key fields were really emitted, on the envelope and
    # inside event_data, and the switch really happened.
    kinds = {e["event_type"] for e in out.events()}
    assert {
        "tool_call_event",
        "http_request_event",
        "claim_verified_event",
        "claim_disclosure_event",
        "conversation_invalidated",
        "conversation_turn_completed",
    } <= kinds, sorted(kinds)
    assert body_b["conversation"]["transition"] == "reset_patient_changed"

    _assert_no_patient_uuid(out, PATIENT_A, PATIENT_B)

    # Internal behaviour is unchanged: the response body still names the
    # raw key (the I-suite's G1/I1 assert this).
    assert body_a["patient_session_key"] == _key(PATIENT_A)
    assert body_b["patient_session_key"] == _key(PATIENT_B)


def test_every_event_of_a_turn_carries_one_hmac_so_traces_still_group(
    conversation_client: TestClient,
) -> None:
    with _emitted() as out:
        _turn(conversation_client, PATIENT_A)
        _turn(conversation_client, PATIENT_B)

    a, b = telemetry_key.emitted_key(_key(PATIENT_A)), telemetry_key.emitted_key(_key(PATIENT_B))
    turn_events = [e for e in out.events() if e.get("patient_session_key")]
    assert {e["patient_session_key"] for e in turn_events} == {a, b}

    # event_data's copies agree with the envelope, and the invalidation
    # names A's conversation by the same value A's own events carry.
    for event in turn_events:
        inner = event["event_data"].get("patient_session_key")
        assert inner in (None, event["patient_session_key"])
    (invalidated,) = [e for e in turn_events if e["event_type"] == "conversation_invalidated"]
    assert invalidated["patient_session_key"] == b
    assert invalidated["event_data"]["previous_patient_session_key"] == a

    # Langfuse: the session id is the same value stdout carries.
    sessions = {call["patient_session_key"] for call in out.langfuse.calls}
    assert sessions == {a, b}


def test_a_cited_record_id_leaves_as_its_hmac_on_both_exits(
    conversation_client: TestClient,
) -> None:
    """
    D58: `source_id` is the FHIR id of the chart record a claim cites, and
    resolves to the patient as directly as the patient's uuid. Both claim
    events carry it; neither exit may.
    """
    with _emitted() as out:
        _turn(conversation_client, PATIENT_A)

    raw = json.loads(DRAFT)["claims"][0]["source_id"]
    expected = telemetry_key.emitted_record_id(raw)
    claim_events = [
        e
        for e in out.events()
        if e["event_type"] in ("claim_verified_event", "claim_disclosure_event")
    ]
    assert {e["event_type"] for e in claim_events} == {
        "claim_verified_event",
        "claim_disclosure_event",
    }
    assert {e["event_data"]["source_id"] for e in claim_events} == {expected}
    exported = [
        call["event_data"]["source_id"]
        for call in out.langfuse.calls
        if "source_id" in call["event_data"]
    ]
    assert exported and set(exported) == {expected}
    assert raw not in out.text()


def test_a_record_id_is_hmac_sha256_under_the_secret_with_a_domain_tag() -> None:
    record = "a2c37439-5dd0-40b8-9916-0011fdf4c121"
    digest = hmac.new(
        SECRET.encode(), b"source_id\x00" + record.encode(), hashlib.sha256
    ).hexdigest()
    assert telemetry_key.emitted_record_id(record) == "rid_" + digest
    # One record, one value; and never the value a session key of the same text gets.
    assert telemetry_key.emitted_record_id(record) == telemetry_key.emitted_record_id(record)
    assert telemetry_key.emitted_record_id(record)[4:] != telemetry_key.emitted_key(record)[4:]
    emitted = telemetry_key.emitted_record_id(record)
    assert telemetry_key.emitted_record_id(emitted) == emitted
    assert telemetry_key.emitted_record_id("") == ""


def test_the_dev_default_labels_a_record_id_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(telemetry_key.SECRET_ENV)
    telemetry_key.reset_for_tests()
    assert telemetry_key.emitted_record_id("med-1").startswith("rid_dev_")


# --- an upload ----------------------------------------------------------------


@pytest.fixture
def upload_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(fhir_client, "ensure_patient_exists", FakeOpenEMR().ensure_patient_exists)
    monkeypatch.setattr(
        document_store, "DOCUMENTS", DocumentStore(vision=ground_truth_vision(SAMPLE))
    )

    def chart_reader(token: str, **_: object):
        async def read(uuid: str):
            return chart_for(SAMPLE)

        return read

    monkeypatch.setattr(documents, "fhir_chart_reader", chart_reader)
    upload_guard.reset_for_tests()
    with TestClient(app) as test_client:
        yield test_client
    upload_guard.reset_for_tests()


def test_an_upload_through_extraction_emits_no_patient_uuid(upload_client: TestClient) -> None:
    with _emitted() as out:
        response = upload(upload_client)
        assert response.status_code == 200, response.text
        final = wait_for_terminal(upload_client, response.json()["document_id"])

    assert final["status"] == "complete"
    kinds = {e["event_type"] for e in out.events()}
    assert {"w2_document_upload_accepted", "intake_extraction_completed", "http_request_event"} <= (
        kinds
    ), sorted(kinds)
    _assert_no_patient_uuid(out, UPLOAD_PATIENT)


# --- the HMAC itself -----------------------------------------------------------


def test_the_emitted_value_is_hmac_sha256_of_the_key_under_the_secret() -> None:
    key = _key(PATIENT_A)
    expected = hmac.new(SECRET.encode(), key.encode(), hashlib.sha256).hexdigest()
    assert telemetry_key.emitted_key(key) == f"psk_{expected}"


def test_one_key_one_value_and_distinct_keys_distinct_values() -> None:
    a = telemetry_key.emitted_key(_key(PATIENT_A))
    assert telemetry_key.emitted_key(_key(PATIENT_A)) == a
    assert telemetry_key.emitted_key(_key(PATIENT_B)) != a
    assert telemetry_key.emitted_key(f"other-user:{PATIENT_A}:{SESSION_ID}") != a


def test_another_secret_gives_another_value(monkeypatch: pytest.MonkeyPatch) -> None:
    first = telemetry_key.emitted_key(_key(PATIENT_A))
    monkeypatch.setenv(telemetry_key.SECRET_ENV, SECRET + "-rotated")
    telemetry_key.reset_for_tests()
    assert telemetry_key.emitted_key(_key(PATIENT_A)) != first


def test_an_emitted_value_is_not_hashed_twice_and_empty_stays_empty() -> None:
    once = telemetry_key.emitted_key(_key(PATIENT_A))
    assert telemetry_key.emitted_key(once) == once
    assert telemetry_key.emitted_key(None) is None
    assert telemetry_key.emitted_key("") == ""


def test_event_data_is_copied_not_mutated() -> None:
    data = {"previous_patient_session_key": _key(PATIENT_A), "turns_discarded": 2}
    emitted = telemetry_key.emitted_event_data(data)
    assert data["previous_patient_session_key"] == _key(PATIENT_A)
    assert emitted["previous_patient_session_key"] == telemetry_key.emitted_key(_key(PATIENT_A))
    assert emitted["turns_discarded"] == 2


# --- the secret: production refuses, dev is labelled ---------------------------


def test_outside_production_an_unset_secret_uses_the_labelled_dev_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(telemetry_key.SECRET_ENV)
    telemetry_key.reset_for_tests()
    value = telemetry_key.emitted_key(_key(PATIENT_A))
    assert value is not None and value.startswith("psk_dev_")
    assert PATIENT_A not in value


@pytest.mark.parametrize(
    ("secret", "problem"),
    [
        (None, "is not set"),
        ("", "is not set"),
        ("too-short", "shorter than 32"),
        (telemetry_key.DEV_DEFAULT_SECRET, "published dev default"),
    ],
)
def test_production_refuses_an_unusable_secret(
    monkeypatch: pytest.MonkeyPatch, secret: str | None, problem: str
) -> None:
    monkeypatch.setenv(telemetry_key.MODE_ENV, "production")
    if secret is None:
        monkeypatch.delenv(telemetry_key.SECRET_ENV)
    else:
        monkeypatch.setenv(telemetry_key.SECRET_ENV, secret)
    telemetry_key.reset_for_tests()

    with pytest.raises(telemetry_key.TelemetrySecretError, match=problem) as refused:
        telemetry_key.require_secret()
    assert telemetry_key.SECRET_ENV in str(refused.value)
    if secret:
        assert secret not in str(refused.value)
    # Past startup (a script that never called require_secret): a marker,
    # never the raw key.
    assert telemetry_key.emitted_key(_key(PATIENT_A)) == telemetry_key.UNAVAILABLE


def test_production_accepts_a_real_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(telemetry_key.MODE_ENV, "production")
    telemetry_key.reset_for_tests()
    telemetry_key.require_secret()
    value = telemetry_key.emitted_key(_key(PATIENT_A))
    assert value is not None and value.startswith("psk_") and not value.startswith("psk_dev_")


def _import_app(**env: str) -> subprocess.CompletedProcess[str]:
    base = {
        name: value
        for name, value in os.environ.items()
        if name not in (telemetry_key.MODE_ENV, telemetry_key.SECRET_ENV)
    }
    return subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=AGENT_DIR,
        env={**base, **env},
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_the_service_does_not_start_in_production_without_the_secret() -> None:
    refused = _import_app(AGENT_ENV="production")
    assert refused.returncode != 0
    assert "TelemetrySecretError" in refused.stderr
    assert '"event": "startup_refused"' in refused.stdout

    started = _import_app(AGENT_ENV="production", AGENT_TELEMETRY_HMAC_SECRET=SECRET)
    assert started.returncode == 0, started.stderr[-2000:]


def test_the_image_is_production_unless_told_otherwise() -> None:
    dockerfile = (AGENT_DIR / "Dockerfile").read_text()
    assert "ENV AGENT_ENV=production" in dockerfile


def test_the_deploy_compose_passes_the_langfuse_config_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    D58: the staged LANGFUSE_* values reach the container only if the compose
    file names them. Each defaults to empty, and empty is "not configured",
    so a host whose env file does not set them keeps stdout as its backend.
    """
    compose = (AGENT_DIR / "deploy" / "docker-compose.agent.yml").read_text()
    for name in langfuse_export._REQUIRED_ENV:
        assert f"{name}: ${{{name}:-}}" in compose, name
    assert "LANGFUSE_TRACING_ENVIRONMENT: ${LANGFUSE_TRACING_ENVIRONMENT:-production}" in compose
    for name in langfuse_export._REQUIRED_ENV:
        monkeypatch.setenv(name, "")
    assert not langfuse_export.export_configured()
