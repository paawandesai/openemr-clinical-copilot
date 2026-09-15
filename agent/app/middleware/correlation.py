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

import uuid
from contextvars import ContextVar

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

CORRELATION_ID_HEADER = "X-Correlation-ID"

# Module-level contextvar so any code running within this request's async
# task can read the current correlation_id without it being passed as a
# function argument. Correctly isolated per request: each ASGI request
# runs in its own asyncio Task, and contextvars are task-local.
_correlation_id_var: ContextVar[str | None] = ContextVar(
    "correlation_id", default=None
)


def get_correlation_id() -> str | None:
    """Read the correlation_id bound for the currently-executing request."""
    return _correlation_id_var.get()


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """
    Generates a UUID per request if the caller didn't supply one via the
    X-Correlation-ID header, binds it to a contextvar and to structlog's
    contextvars (so every structlog call anywhere in this request's
    lifetime carries it automatically — see app/middleware/logging.py),
    and echoes it back on the response.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        correlation_id = request.headers.get(CORRELATION_ID_HEADER) or str(
            uuid.uuid4()
        )

        token = _correlation_id_var.set(correlation_id)
        # Bind into structlog's context so every log line emitted anywhere
        # during this request — without passing correlation_id explicitly
        # at each call site — carries it (app/middleware/logging.py).
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
            _correlation_id_var.reset(token)

        response.headers[CORRELATION_ID_HEADER] = correlation_id
        return response
