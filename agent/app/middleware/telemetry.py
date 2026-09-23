"""
Emitting the operational events — steps, tool calls, retries.

The schemas are in `app/models/telemetry_events.py`; the dashboard and the
alerts that read them are `agent/scripts/w2_dashboard.py` and
docs/w2/OBSERVABILITY.md. Everything here goes through `log_event`, so it
lands on stdout with the request's correlation_id and is exported to
Langfuse on the same path as every Week 1 event.

**Telemetry never breaks a request.** An event that fails its own schema is
reported as `telemetry_event_invalid` (event type and field names only) and
dropped; nothing in this module raises into the caller except the caller's
own exception, which the context managers re-raise unchanged after
recording it.

**Retries.** The Anthropic SDK retries a failed request itself (twice by
default) and says nothing about it: a call that took three attempts looks,
from the call site, like one slow call. It does stamp every attempt with
an `x-stainless-retry-count` header, so `anthropic_http_client()` returns
an httpx client whose request hook reads that header into the tool call
that is currently open. That is the only way the SDK exposes the count on
a streamed call.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import structlog
from pydantic import ValidationError

from app.middleware.correlation import get_correlation_id
from app.middleware.logging import log_event
from app.models.metrics_events import UseCase
from app.models.telemetry_events import (
    STEP_EVENT,
    TOOL_CALL_EVENT,
    Pipeline,
    StepEvent,
    StepOutcome,
    Tool,
    ToolCallEvent,
)

_NO_CONTEXT = "no-request-context"


def _correlation_id() -> str:
    return get_correlation_id() or _NO_CONTEXT


def _invalid(event_type: str, error: ValidationError) -> None:
    # Field locations only: a validation message quotes the rejected input.
    structlog.get_logger().warning(
        "telemetry_event_invalid",
        event_type=event_type,
        fields=sorted({".".join(str(p) for p in e["loc"]) for e in error.errors()}),
    )


def record_step(
    pipeline: Pipeline,
    step: str,
    latency_seconds: float,
    outcome: StepOutcome = StepOutcome.OK,
    *,
    error_type: str | None = None,
    counts: dict[str, int | float | bool] | None = None,
    use_case: UseCase | None = None,
    patient_session_key: str | None = None,
) -> None:
    """One `step_event`. Never raises."""
    try:
        event = StepEvent(
            pipeline=pipeline,
            step=step,
            outcome=outcome,
            latency_ms=round(max(latency_seconds, 0.0) * 1000, 3),
            error_type=error_type,
            counts=counts or {},
            correlation_id=_correlation_id(),
        )
    except ValidationError as error:
        _invalid(STEP_EVENT, error)
        return
    log_event(
        STEP_EVENT,
        event.model_dump(mode="json"),
        use_case=use_case,
        patient_session_key=patient_session_key,
    )


@dataclass
class StepRecorder:
    """What a `step()` block may set before it exits."""

    outcome: StepOutcome = StepOutcome.OK
    counts: dict[str, int | float | bool] = field(default_factory=dict)


@contextmanager
def step(
    pipeline: Pipeline,
    name: str,
    *,
    use_case: UseCase | None = None,
    patient_session_key: str | None = None,
) -> Iterator[StepRecorder]:
    """
    Time a block as one step. An exception is recorded as `error` with its
    class name, then re-raised unchanged.
    """
    recorder = StepRecorder()
    started = time.perf_counter()
    try:
        yield recorder
    except BaseException as error:
        record_step(
            pipeline,
            name,
            time.perf_counter() - started,
            StepOutcome.ERROR,
            error_type=type(error).__name__,
            counts=recorder.counts,
            use_case=use_case,
            patient_session_key=patient_session_key,
        )
        raise
    record_step(
        pipeline,
        name,
        time.perf_counter() - started,
        recorder.outcome,
        counts=recorder.counts,
        use_case=use_case,
        patient_session_key=patient_session_key,
    )


def record_steps(
    pipeline: Pipeline,
    seconds_by_step: dict[str, float],
    *,
    use_case: UseCase | None = None,
    patient_session_key: str | None = None,
) -> None:
    """
    One `step_event` per entry of a pipeline's own timing dict — the
    `node_seconds` / `step_seconds` the Week 1 graphs already compute and
    return, so their per-step latency reaches the dashboard without
    touching the graphs.
    """
    for name, seconds in seconds_by_step.items():
        record_step(
            pipeline,
            name,
            seconds,
            use_case=use_case,
            patient_session_key=patient_session_key,
        )


@dataclass
class ToolCallRecorder:
    """The open tool call. `attempts` is raised by the httpx hook below."""

    attempts: int = 1
    status_code: int | None = None
    outcome: StepOutcome = StepOutcome.OK


_open_call: ContextVar[ToolCallRecorder | None] = ContextVar("open_tool_call", default=None)


def _record_tool_call(
    tool: Tool,
    target: str | None,
    recorder: ToolCallRecorder,
    latency_seconds: float,
    outcome: StepOutcome,
    error_type: str | None,
    use_case: UseCase | None,
    patient_session_key: str | None,
) -> None:
    try:
        event = ToolCallEvent(
            tool=tool,
            target=target,
            outcome=outcome,
            latency_ms=round(max(latency_seconds, 0.0) * 1000, 3),
            attempts=recorder.attempts,
            retries=recorder.attempts - 1,
            error_type=error_type,
            status_code=recorder.status_code,
            correlation_id=_correlation_id(),
        )
    except ValidationError as error:
        _invalid(TOOL_CALL_EVENT, error)
        return
    log_event(
        TOOL_CALL_EVENT,
        event.model_dump(mode="json"),
        use_case=use_case,
        patient_session_key=patient_session_key,
    )


@contextmanager
def tool_call(
    tool: Tool,
    target: str | None = None,
    *,
    use_case: UseCase | None = None,
    patient_session_key: str | None = None,
) -> Iterator[ToolCallRecorder]:
    """
    Time one call to an external dependency and emit a `tool_call_event`.

    An exception is recorded as `error` with its class name and re-raised.
    A call that returned but failed (a 5xx the client maps to a value
    rather than raising) sets `recorder.outcome` itself.
    """
    recorder = ToolCallRecorder()
    token = _open_call.set(recorder)
    started = time.perf_counter()
    try:
        yield recorder
    except BaseException as error:
        _record_tool_call(
            tool,
            target,
            recorder,
            time.perf_counter() - started,
            StepOutcome.ERROR,
            type(error).__name__,
            use_case,
            patient_session_key,
        )
        raise
    finally:
        _open_call.reset(token)
    _record_tool_call(
        tool,
        target,
        recorder,
        time.perf_counter() - started,
        recorder.outcome,
        None,
        use_case,
        patient_session_key,
    )


_RETRY_HEADER = "x-stainless-retry-count"


async def _count_attempt(request: Any) -> None:
    # `request` is an httpx2.Request: the Anthropic SDK is built on httpx2,
    # not httpx. Only `.headers.get` is used, which both provide.
    recorder = _open_call.get()
    if recorder is None:
        return
    try:
        retries_so_far = int(request.headers.get(_RETRY_HEADER, "0"))
    except ValueError:
        return
    recorder.attempts = max(recorder.attempts, retries_so_far + 1)


def anthropic_http_client(transport: Any = None) -> Any:
    """
    The httpx client to hand `anthropic.AsyncAnthropic(http_client=...)`.

    The SDK's own default client plus one request hook. The hook reads a
    header and writes an integer; it never reads the body. `transport` is
    for tests, and must be an httpx2 transport.
    """
    import anthropic

    extra = {"transport": transport} if transport is not None else {}
    return anthropic.DefaultAsyncHttpxClient(event_hooks={"request": [_count_attempt]}, **extra)
