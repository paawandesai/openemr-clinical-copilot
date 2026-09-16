"""
Structured JSON logging — the instrumentation docs/KEY_METRICS.md depends on.

KEY_METRICS.md states plainly that none of its six metrics can be computed
without this shape existing first. Every log line emitted through
`log_event` below can carry, when relevant:

  - correlation_id       Bound automatically for this request's lifetime
                          by CorrelationIdMiddleware (see
                          app/middleware/correlation.py) — never passed
                          explicitly at the call site.
  - use_case              One of the three USERS.md §3 use cases, no
                          others (pre_visit_brief / change_detection /
                          follow_up_drilling).
  - patient_session_key   The (user, patient, session) triple
                          ARCHITECTURE.md §4 defines for conversation
                          state.
  - event_type/event_data Free-form payload — see
                          app/models/metrics_events.py for the concrete
                          shapes KEY_METRICS.md's metrics are computed
                          from.

This module configures *how* logs are shaped and rendered. It does not
decide *when* to log anything, and it implements no metric aggregation —
KEY_METRICS.md's six metrics are computed from this data by something
else, later; this task is instrumentation only.

Export: every `log_event` line goes to stdout AND, when LANGFUSE_BASE_URL /
LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are set, to self-hosted Langfuse
(app/middleware/langfuse_export.py; decision and metric mapping in
agent/docs/observability.md). Unset, behaviour is stdout-only, as before.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.middleware import langfuse_export
from app.models.metrics_events import UseCase


def configure_logging(level: int = logging.INFO) -> None:
    """
    Call once at application startup (see app/main.py). Configures
    structlog to render one JSON object per log line, merging in whatever
    correlation_id the correlation middleware has bound for the current
    request (app/middleware/correlation.py).
    """
    structlog.configure(
        processors=[
            # Pulls correlation_id (and anything else bound via
            # structlog.contextvars) into every log line for the current
            # request — see CorrelationIdMiddleware.
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    # After structlog is configured, so the enabled/disabled line it writes
    # is rendered as JSON like every other line.
    langfuse_export.init_exporter()


def log_event(
    event_type: str,
    event_data: dict[str, Any] | None = None,
    *,
    # use_case: one of pre_visit_brief / change_detection /
    # follow_up_drilling (USERS.md §3) — no others. Required for Metrics
    # 4, 5, 6 (each explicitly reported or defined per use case in
    # KEY_METRICS.md); optionally present on other event types for future
    # segmentation, though KEY_METRICS.md doesn't require it there.
    use_case: UseCase | None = None,
    # patient_session_key: required for Metrics 1, 2, 4, 5, 6 (all roll up
    # per (user, patient, session) per KEY_METRICS.md). Metric 3
    # (Authorization Boundary Integrity) is the one exception — it's
    # computed per-request against the agent's own scope record, not
    # rolled up by patient/session, so this may legitimately be None there.
    patient_session_key: str | None = None,
    logger: structlog.typing.FilteringBoundLogger | None = None,
) -> None:
    """
    Emit one structured log line carrying every field KEY_METRICS.md's
    metrics need to filter/aggregate on. This function only shapes and
    emits the line — it does not decide what should be logged (that's each
    call site's job, in later tasks) and it does not aggregate anything.

    correlation_id is deliberately NOT a parameter here: it is merged in
    automatically from structlog's contextvars, which
    CorrelationIdMiddleware binds for the lifetime of the current request
    (app/middleware/correlation.py). Passing it explicitly would risk a
    call site supplying a stale or wrong value.
    """
    log = logger or structlog.get_logger()
    log.info(
        "agent_event",
        event_type=event_type,
        event_data=event_data or {},
        use_case=use_case.value if use_case else None,
        patient_session_key=patient_session_key,
    )
    # In addition to stdout, not instead of it. No-op when export is not
    # configured; never raises (app/middleware/langfuse_export.py).
    langfuse_export.export_event(
        event_type=event_type,
        event_data=event_data or {},
        use_case=use_case,
        patient_session_key=patient_session_key,
    )
