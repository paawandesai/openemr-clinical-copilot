"""
Correlation ID middleware — load-bearing for docs/KEY_METRICS.md, not
optional scaffolding.

Every one of KEY_METRICS.md's six metrics is computed by joining log lines
that were emitted during the same request/turn — a claim's verification
result, the resource types retrieved for it, the latency from trigger to
render. This middleware is what makes that join possible: it assigns one
correlation_id per request and makes it available to every log line
emitted for that request's lifetime, without any call site needing to
thread it through by hand.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

CORRELATION_ID_HEADER = "X-Correlation-ID"

# What an inbound X-Correlation-ID may look like. A caller that sends the
# same id on every request of one encounter — upload, extraction status,
# question, write-back — gets one trace for the whole encounter
# (docs/w2/OBSERVABILITY.md §2). That makes the header a trace key written
# into every log line and every exported event, so it is held to an
# identifier's alphabet: a value that could carry a name, a date of birth or
# a sentence is replaced with a fresh UUID rather than logged (CLAUDE.md
# invariant 1). UUIDs, "corr-..." test ids and k6's "k6-1-2-..." all pass.
_ACCEPTED_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def accepted_correlation_id(inbound: str | None) -> str:
    """The inbound id when it is identifier-shaped, else a new UUID4."""
    if inbound and _ACCEPTED_CORRELATION_ID.fullmatch(inbound):
        return inbound
    return str(uuid.uuid4())


# Module-level contextvar so any code running within this request's async
# task can read the current correlation_id without it being passed as a
# function argument. Correctly isolated per request: each ASGI request
# runs in its own asyncio Task, and contextvars are task-local.
_correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def get_correlation_id() -> str | None:
    """Read the correlation_id bound for the currently-executing request."""
    return _correlation_id_var.get()


def bind_correlation_id(correlation_id: str) -> None:
    """
    Bind a correlation_id outside of an HTTP request — for background jobs
    or standalone scripts (e.g. scripts/smoke_test_fhir.py) that want the
    same correlation_id propagation CorrelationIdMiddleware provides for a
    real request, without going through the ASGI app at all. Same two
    operations the middleware itself performs. Does not auto-clear: a
    short-lived script exiting is sufficient cleanup, unlike a
    long-running server handling many requests in the same process.
    """
    _correlation_id_var.set(correlation_id)
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)


@contextmanager
def correlation_scope(correlation_id: str) -> Iterator[None]:
    """
    Bind a correlation_id for the duration of a `with` block, then restore
    whatever was bound before. For code that runs several interactions in
    one process outside an HTTP request (e.g. app/graph/pre_visit_brief.py
    invoked from a script): unlike bind_correlation_id, nothing leaks into
    the next interaction, so their log lines never share an id.
    """
    token = _correlation_id_var.set(correlation_id)
    try:
        with structlog.contextvars.bound_contextvars(correlation_id=correlation_id):
            yield
    finally:
        _correlation_id_var.reset(token)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """
    Generates a UUID per request if the caller didn't supply one via the
    X-Correlation-ID header, binds it to a contextvar and to structlog's
    contextvars (so every structlog call anywhere in this request's
    lifetime carries it automatically — see app/middleware/logging.py),
    and echoes it back on the response.

    It also emits one `http_request_event` per request (route template,
    method, status, latency) — the request and error counts and the
    overall latency percentiles on the Week 2 dashboard. Emitted before
    the contextvars are cleared, so it carries the same correlation_id as
    every other line the request produced.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        correlation_id = accepted_correlation_id(request.headers.get(CORRELATION_ID_HEADER))
        started = time.perf_counter()
        status_code = 500

        token = _correlation_id_var.set(correlation_id)
        # Bind into structlog's context so every log line emitted anywhere
        # during this request — without passing correlation_id explicitly
        # at each call site — carries it (app/middleware/logging.py).
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
        try:
            response = await call_next(request)
            status_code = response.status_code
        finally:
            _emit_request_event(request, status_code, time.perf_counter() - started)
            structlog.contextvars.clear_contextvars()
            _correlation_id_var.reset(token)

        response.headers[CORRELATION_ID_HEADER] = correlation_id
        return response


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


def _emit_request_event(request: Request, status_code: int, seconds: float) -> None:
    # Local imports: app.middleware.logging must stay importable without
    # this module and the reverse, and telemetry must never fail a request.
    from pydantic import ValidationError

    from app.middleware.logging import log_event
    from app.models.telemetry_events import HTTP_REQUEST_EVENT, HttpRequestEvent

    try:
        event = HttpRequestEvent(
            method=request.method.upper(),
            route=_route_template(request)[:128],
            status_code=status_code,
            latency_ms=round(seconds * 1000, 3),
            correlation_id=get_correlation_id() or "no-request-context",
        )
    except ValidationError:
        structlog.get_logger().warning("telemetry_event_invalid", event_type=HTTP_REQUEST_EVENT)
        return
    log_event(HTTP_REQUEST_EVENT, event.model_dump(mode="json"))
