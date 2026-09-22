"""
The vision call, with the Anthropic client replaced by a fake — no network.

Checks the request the worker builds (model, schema, one image per page,
the Week 1 fallback beta), what it does with each kind of response, the
page-window pagination and merge, and that the hand-written JSON schema and
the strict raw models agree.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest
from structlog.testing import capture_logs

from app.models.w2.documents import DocumentKind
from app.models.w2.workers import ExtractionFailureReason
from app.reasoning import provider_health
from app.reasoning.draft_claims import _FALLBACK_BETA, MODEL
from app.workers.intake_extractor import vision
from app.workers.intake_extractor.vision_schema import (
    INTAKE_OUTPUT_SCHEMA,
    LAB_OUTPUT_SCHEMA,
    RawField,
    RawIntakeDocument,
    RawLabDocument,
)
from tests.intake_extractor_support import pdf_bytes, raw_from_ground_truth


class _FakeStream:
    def __init__(self, message: Any, error: Exception | None) -> None:
        self._message, self._error = message, error
        self.request_id = "req_test"

    async def __aenter__(self) -> _FakeStream:
        if self._error is not None:
            raise self._error
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get_final_message(self) -> Any:
        return self._message


class FakeClient:
    def __init__(self, messages: list[Any] | None = None, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._messages = list(messages or [])
        self._error = error
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs: Any) -> _FakeStream:
        self.calls.append(kwargs)
        message = self._messages.pop(0) if self._messages else None
        return _FakeStream(message, self._error)


def message(text: str, *, stop_reason: str = "end_turn", model: str = MODEL) -> Any:
    return SimpleNamespace(
        model=model,
        stop_reason=stop_reason,
        stop_details=None,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(
            input_tokens=4000,
            output_tokens=1000,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            iterations=None,
        ),
    )


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    def install(client: FakeClient) -> FakeClient:
        monkeypatch.setattr(vision, "_client", lambda: client)
        return client

    # The failure tests record provider failures; /ready reads that counter,
    # so it must not leak into another test module.
    provider_health.reset()
    yield install
    provider_health.reset()


def test_one_call_carries_every_page_and_the_strict_schema(fake) -> None:  # noqa: ANN001
    raw = raw_from_ground_truth("intake-clean-004")
    client = fake(FakeClient([message(raw.model_dump_json())]))
    with capture_logs() as logs:
        result = asyncio.run(
            vision.extract_with_claude(
                DocumentKind.INTAKE_FORM, pdf_bytes("intake-clean-004"), page_count=2
            )
        )
    assert result.raw == raw
    (call,) = client.calls
    assert call["model"] == MODEL
    assert call["betas"] == [_FALLBACK_BETA] and call["fallbacks"] == "default"
    assert call["output_config"]["format"] == {
        "type": "json_schema",
        "schema": INTAKE_OUTPUT_SCHEMA,
    }
    content = call["messages"][0]["content"]
    assert [block["type"] for block in content].count("image") == 2
    assert [block["text"] for block in content if block["type"] == "text"][:2] == [
        "Page 1",
        "Page 2",
    ]
    assert result.usage.calls == 1
    assert result.usage.cost_usd == pytest.approx((4000 * 5 + 1000 * 25) / 1_000_000)
    usage_events = [log for log in logs if log.get("event_type") == "llm_usage_event"]
    assert usage_events and usage_events[0]["event_data"]["cost_usd"] == result.usage.cost_usd
    # Nothing from the page or the transcription reaches the log line.
    assert "Jae222" not in json.dumps(logs, default=str)


def test_unparseable_output_is_model_output_invalid(fake) -> None:  # noqa: ANN001
    fake(FakeClient([message('{"patient": "not the schema"}')]))
    with pytest.raises(vision.VisionCallError) as raised:
        asyncio.run(vision.extract_with_claude(DocumentKind.LAB_PDF, pdf_bytes("lab-clean-001"), 1))
    assert raised.value.reason is ExtractionFailureReason.MODEL_OUTPUT_INVALID
    assert "not the schema" not in str(raised.value)  # the error message holds no output


def test_intake_output_is_not_accepted_for_a_lab_report(fake) -> None:  # noqa: ANN001
    fake(FakeClient([message(raw_from_ground_truth("intake-clean-004").model_dump_json())]))
    with pytest.raises(vision.VisionCallError) as raised:
        asyncio.run(vision.extract_with_claude(DocumentKind.LAB_PDF, pdf_bytes("lab-clean-001"), 1))
    assert raised.value.reason is ExtractionFailureReason.MODEL_OUTPUT_INVALID


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_refusal_or_truncation_is_vision_call_failed(fake, stop_reason: str) -> None:  # noqa: ANN001
    fake(FakeClient([message("{}", stop_reason=stop_reason)]))
    with pytest.raises(vision.VisionCallError) as raised:
        asyncio.run(vision.extract_with_claude(DocumentKind.LAB_PDF, pdf_bytes("lab-clean-001"), 1))
    assert raised.value.reason is ExtractionFailureReason.VISION_CALL_FAILED


def test_api_error_is_vision_call_failed_and_logged_by_type(fake) -> None:  # noqa: ANN001
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.APIConnectionError(request=request)
    fake(FakeClient(error=error))
    with capture_logs() as logs, pytest.raises(vision.VisionCallError) as raised:
        asyncio.run(vision.extract_with_claude(DocumentKind.LAB_PDF, pdf_bytes("lab-clean-001"), 1))
    assert raised.value.reason is ExtractionFailureReason.VISION_CALL_FAILED
    failed = [log for log in logs if log.get("event_type") == "llm_call_failed"]
    assert failed[0]["event_data"]["error_type"] == "APIConnectionError"


def test_timeout_is_timed_out(fake) -> None:  # noqa: ANN001
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    fake(FakeClient(error=anthropic.APITimeoutError(request=request)))
    with pytest.raises(vision.VisionCallError) as raised:
        asyncio.run(vision.extract_with_claude(DocumentKind.LAB_PDF, pdf_bytes("lab-clean-001"), 1))
    assert raised.value.reason is ExtractionFailureReason.TIMED_OUT


def test_page_windows() -> None:
    assert vision.page_windows(1) == [(1, 1)]
    assert vision.page_windows(10) == [(1, 10)]
    assert vision.page_windows(23) == [(1, 10), (11, 20), (21, 23)]
    with pytest.raises(vision.VisionCallError) as raised:
        vision.page_windows(vision.MAX_PAGES + 1)
    assert raised.value.reason is ExtractionFailureReason.PAGE_LIMIT_EXCEEDED


def test_merge_concatenates_rows_and_keeps_first_present_header() -> None:
    first = raw_from_ground_truth("lab-multipage-003")
    assert isinstance(first, RawLabDocument)
    absent = RawField(state="absent", text="", page=None)
    second = first.model_copy(
        update={
            "patient": first.patient.model_copy(update={"name": absent}),
            "results": first.results[:2],
        }
    )
    merged = vision.merge_raw_documents([first, second])
    assert merged.patient.name == first.patient.name
    assert len(merged.results) == len(first.results) + 2
    keys = [row.test_key for row in merged.results]
    assert len(set(keys)) == len(keys)
    assert keys[-2:] == [f"{first.results[0].test_key}_2", f"{first.results[1].test_key}_2"]


def test_merge_of_intake_windows() -> None:
    raw = raw_from_ground_truth("intake-clean-004")
    assert isinstance(raw, RawIntakeDocument)
    merged = vision.merge_raw_documents([raw, raw])
    assert len(merged.medications) == 2 * len(raw.medications)


def _schema_keys(schema: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in schema:
        return _schema_keys(defs[schema["$ref"].split("/")[-1]], defs)
    if schema.get("type") == "object":
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        return {key: _schema_keys(value, defs) for key, value in schema["properties"].items()}
    if schema.get("type") == "array":
        return {"[]": _schema_keys(schema["items"], defs)}
    return {}


def _model_keys(model: Any) -> dict[str, Any]:
    import typing

    out: dict[str, Any] = {}
    hints = typing.get_type_hints(model)
    for name in model.model_fields:
        annotation = hints[name]
        if typing.get_origin(annotation) is list:
            (item,) = typing.get_args(annotation)
            out[name] = {"[]": _model_keys(item)}
        elif hasattr(annotation, "model_fields"):
            out[name] = _model_keys(annotation)
        else:
            out[name] = {}
    return out


@pytest.mark.parametrize(
    ("schema", "model"),
    [(LAB_OUTPUT_SCHEMA, RawLabDocument), (INTAKE_OUTPUT_SCHEMA, RawIntakeDocument)],
)
def test_the_hand_written_schema_matches_the_strict_model(schema, model) -> None:  # noqa: ANN001
    assert _schema_keys(schema, schema["$defs"]) == _model_keys(model)
