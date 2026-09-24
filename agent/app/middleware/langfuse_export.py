"""
Langfuse export for `log_event` — makes docs/KEY_METRICS.md's six metrics
queryable in a self-hosted Langfuse, in addition to stdout.

Decision and deployment status: agent/docs/observability.md. In short:
self-hosted Langfuse (never Langfuse Cloud — PHI-adjacent identifiers
leave this process), verified locally via
agent/observability/docker-compose.langfuse.yml; production placement is a
second, not-yet-provisioned droplet.

How one `log_event(...)` call maps onto Langfuse
------------------------------------------------
  trace       One per correlation_id (one HTTP request / turn). The trace id
              is derived deterministically from correlation_id, so every
              event in a request lands in the same trace without any call
              site threading a trace handle through. Each event is a true
              root observation (no parent span): Langfuse v4 only resolves
              trace name and tags for scores when the trace has a real root
              — events hung off the SDK's synthetic `trace_context` parent
              were verified to drop out of every per-use-case score query
              (server 4.37.0). `_PinnedTraceIdGenerator` below is how a
              parentless span still gets the correlation-derived trace id.
  session     patient_session_key — Langfuse sessions group traces, which is
              exactly the (user, patient, session) rollup KEY_METRICS.md
              specifies for Metrics 1, 2, 4, 5, 6.
  trace name  The use_case value (pre_visit_brief / change_detection /
              follow_up_drilling), when the event carries one.
  tags        use_case:<value>, and retrieved:<ResourceType> per
              retrieval_event (Metric 5).
  observation An EVENT observation named event_type, metadata = event_data
              plus the envelope fields. Never input/output (see below).

Metric-specific derivations, each parsed through the existing pydantic
model in app/models/metrics_events.py (parse, don't trust the dict):

  claim_verified_event       score verification_passed (BOOLEAN)       M1
                             score unverified_claim_disclosed (BOOLEAN,
                             failed claims only)                        M2
  authorization_check_event  score out_of_scope_request_rejected
                             (BOOLEAN, out-of-scope requests only);
                             observation level ERROR if accepted         M3
  latency_event              score response_latency_seconds (NUMERIC)   M4
  retrieval_event            trace tag retrieved:<ResourceType>         M5
  llm_usage_event            GENERATION observation with model +
                             token usage; Langfuse prices it            M6

Event type names are the snake_case of the model class name
(RetrievalEvent -> retrieval_event), matching the one name already emitted
in app/fhir/client.py. llm_usage_event has no model in metrics_events.py
yet; its expected shape is `_LlmUsage` below (see observability.md).

A payload that does not parse is still exported, at level WARNING with a
status message saying no score was derived — a metric gap shows up in the
Langfuse UI instead of silently not existing.

PHI boundary — enforced structurally, not by convention alone
-------------------------------------------------------------
- This module never sets observation input/output. Only event_data (which
  the metrics_events.py models constrain to identifiers, enums, booleans,
  timestamps, and scope strings) is sent, as metadata.
- The Langfuse client gets its own private OpenTelemetry TracerProvider
  (never registered as the process-global one) and
  `should_export_span=is_langfuse_span`: only spans this module creates are
  exported. The SDK's defaults would register itself globally and also
  export any OpenTelemetry GenAI / LLM-instrumentor spans in the process —
  which capture full prompts and completions, i.e. chart content — so adding
  auto-instrumentation elsewhere cannot silently start shipping PHI here.

Failure isolation
-----------------
Export must never break the clinical request path. Every export is wrapped:
a failure is written to stdout as `observability_export_failed` and the
request continues. Delivery is asynchronous (the SDK batches in a background
thread), so an unreachable Langfuse costs a request nothing; reachability is
reported by `check_export_health()` instead, for /ready.
"""

from __future__ import annotations

import os
import re
import threading
import time
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Literal

import structlog
from pydantic import BaseModel, NonNegativeInt, ValidationError

from app.models.metrics_events import (
    AuthorizationCheckEvent,
    ClaimVerifiedEvent,
    LatencyEvent,
    RetrievalEvent,
    UseCase,
)
from app.models.telemetry_events import (
    HTTP_REQUEST_EVENT,
    STEP_EVENT,
    TOOL_CALL_EVENT,
    HttpRequestEvent,
    StepEvent,
    StepOutcome,
    ToolCallEvent,
)

if TYPE_CHECKING:
    from langfuse import Langfuse

_REQUIRED_ENV = ("LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")

# How long a reachability check result is trusted before
# check_export_health() schedules another one in the background, and the
# age beyond which a result is not reported at all (see check_health).
_AUTH_CHECK_TTL_SECONDS = 15.0
_AUTH_CHECK_MAX_STALENESS_SECONDS = 60.0

_AuthResult = Literal["ok", "unreachable", "rejected", "failed"]

# Returned through /ready, so: no URLs, paths or exception text (F-37).
_AUTH_FAILURE_MESSAGES: dict[str, str] = {
    "unreachable": "Langfuse unreachable",
    "rejected": "Langfuse rejected the configured API keys",
    "failed": "Langfuse reachability check failed",
}

SpanLevel = Literal["DEBUG", "DEFAULT", "WARNING", "ERROR"]

# Set by LangfuseExporter.export() only for the duration of creating one
# observation; read by the private TracerProvider's id generator.
_pinned_trace_id: ContextVar[int | None] = ContextVar("langfuse_pinned_trace_id", default=None)


def _event_type_for(model: type[BaseModel]) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", model.__name__).lower()


CLAIM_VERIFIED_EVENT = _event_type_for(ClaimVerifiedEvent)
AUTHORIZATION_CHECK_EVENT = _event_type_for(AuthorizationCheckEvent)
LATENCY_EVENT = _event_type_for(LatencyEvent)
RETRIEVAL_EVENT = _event_type_for(RetrievalEvent)
RETRIEVAL_EVENT_SKIPPED = "retrieval_event_skipped"
LLM_USAGE_EVENT = "llm_usage_event"


class _LlmUsage(BaseModel):
    """
    Expected event_data for llm_usage_event (KEY_METRICS.md Metric 6).
    Token counts only — never the prompt or completion. Field names mirror
    the Anthropic SDK's `response.usage` so a call site can pass
    `response.usage.model_dump()` plus `model` directly. Belongs in
    app/models/metrics_events.py as `LlmUsageEvent` once the parallel
    tracks have merged; kept private here to avoid editing shared models.
    """

    model: str
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    cache_creation_input_tokens: NonNegativeInt | None = None
    cache_read_input_tokens: NonNegativeInt | None = None


_Score = tuple[str, float, Literal["BOOLEAN", "NUMERIC"]]


class _Derived(BaseModel):
    """What an operational event contributes: scores, tags, and a level."""

    scores: list[_Score] = []
    tags: list[str] = []
    level: SpanLevel | None = None
    status_message: str | None = None


def _step_scores(data: dict[str, Any]) -> _Derived:
    """
    step_event -> NUMERIC `step_latency_ms:<pipeline>.<step>`. One score
    name per step, so a Scores Numeric widget broken down by Name is the
    by-step p50/p95. A failed step is exported at level ERROR with the
    exception's class name — never its message.
    """
    event = StepEvent.model_validate(data)
    derived = _Derived(
        scores=[
            (f"step_latency_ms:{event.pipeline.value}.{event.step}", event.latency_ms, "NUMERIC")
        ],
        tags=[f"pipeline:{event.pipeline.value}"],
    )
    if event.outcome is StepOutcome.ERROR:
        derived.level = "ERROR"
        derived.status_message = f"step failed: {event.error_type or 'unknown'}"
    return derived


def _tool_scores(data: dict[str, Any]) -> _Derived:
    event = ToolCallEvent.model_validate(data)
    failed = is_dependency_failure(event)
    derived = _Derived(
        scores=[
            ("tool_call_failed", float(failed), "BOOLEAN"),
            ("tool_call_retries", float(event.retries), "NUMERIC"),
        ],
        tags=[f"tool:{event.tool.value}"],
    )
    if failed:
        derived.level = "ERROR"
        derived.status_message = f"tool call failed: {event.error_type or event.status_code}"
    return derived


def _request_scores(data: dict[str, Any]) -> _Derived:
    event = HttpRequestEvent.model_validate(data)
    derived = _Derived(
        scores=[
            ("request_latency_ms", event.latency_ms, "NUMERIC"),
            ("request_server_error", float(event.is_server_error), "BOOLEAN"),
        ],
        tags=[f"route:{event.route}"],
    )
    if event.is_server_error:
        derived.level = "ERROR"
        derived.status_message = f"HTTP {event.status_code}"
    return derived


class _EvidenceRetrieval(BaseModel):
    """The fields of app/retrieval/worker.py's evidence_retrieval_event read here."""

    hit_count: NonNegativeInt
    reranked: bool


def _retrieval_scores(data: dict[str, Any]) -> _Derived:
    event = _EvidenceRetrieval.model_validate(data)
    return _Derived(
        scores=[
            ("retrieval_hit_count", float(event.hit_count), "NUMERIC"),
            ("retrieval_reranked", float(event.reranked), "BOOLEAN"),
        ]
    )


class _ExtractionSummary(BaseModel):
    """
    The fields of the intake-extractor's `intake_extraction_completed`
    summary read here (`ExtractionReport.log_summary()`, feat/w2-extractor).
    """

    field_outcomes: dict[str, NonNegativeInt]
    mean_confidence: float | None = None


def _extraction_scores(data: dict[str, Any]) -> _Derived:
    """
    NUMERIC `extraction_located_rate` = located / (located + unlocated):
    of the values the model read, the share the deterministic locate step
    found on the page (D3). `missing` and `illegible` describe the
    document, not the extractor, and are left out of both sides.
    """
    event = _ExtractionSummary.model_validate(data)
    derived = _Derived()
    rate = located_rate(event.field_outcomes)
    if rate is not None:
        derived.scores.append(("extraction_located_rate", rate, "NUMERIC"))
    if event.mean_confidence is not None:
        derived.scores.append(("extraction_mean_confidence", event.mean_confidence, "NUMERIC"))
    return derived


def located_rate(field_outcomes: dict[str, int]) -> float | None:
    """located / (located + unlocated), or None when neither occurred."""
    located = field_outcomes.get("located", 0)
    read = located + field_outcomes.get("unlocated", 0)
    return located / read if read else None


def is_dependency_failure(event: ToolCallEvent) -> bool:
    """
    A tool call that failed because the dependency did: no response at all,
    or a 5xx / 429. A 4xx that is the caller's own business — an unknown
    patient, a token OpenEMR rejects, a write the ledger refused — is not a
    dependency failure and would make a tool-failure alert page on-call for
    a typo.
    """
    if event.outcome is not StepOutcome.ERROR:
        return False
    if event.status_code is None:
        return True
    return event.status_code >= 500 or event.status_code == 429


_OPERATIONAL_SCORERS = {
    STEP_EVENT: _step_scores,
    TOOL_CALL_EVENT: _tool_scores,
    HTTP_REQUEST_EVENT: _request_scores,
    "evidence_retrieval_event": _retrieval_scores,
    "intake_extraction_completed": _extraction_scores,
}


class ObservabilityExportUnavailable(RuntimeError):
    """Raised by check_export_health(). Messages are safe to return from /ready."""


class LangfuseExporter:
    def __init__(self, client: Langfuse) -> None:
        self._client = client
        self._auth_lock = threading.Lock()
        self._auth_result: _AuthResult | None = None
        self._auth_checked_at = float("-inf")
        self._auth_check_running = False

    # -- export -----------------------------------------------------------

    def export(
        self,
        *,
        event_type: str,
        event_data: dict[str, Any],
        use_case: UseCase | None,
        patient_session_key: str | None,
        correlation_id: str | None,
    ) -> None:
        from langfuse import propagate_attributes
        from opentelemetry import context as otel_context

        metadata: dict[str, Any] = dict(event_data)
        metadata["event_type"] = event_type
        for key, value in (
            ("use_case", use_case.value if use_case else None),
            ("patient_session_key", patient_session_key),
            ("correlation_id", correlation_id),
        ):
            if value is not None:
                metadata.setdefault(key, value)

        tags: list[str] = []
        if use_case is not None:
            tags.append(f"use_case:{use_case.value}")

        level: SpanLevel = "DEFAULT"
        status_message: str | None = None
        scores: list[_Score] = []
        llm_usage: _LlmUsage | None = None

        try:
            if event_type == CLAIM_VERIFIED_EVENT:
                claim = ClaimVerifiedEvent.model_validate(event_data)
                scores.append(("verification_passed", float(claim.passed), "BOOLEAN"))
                if not claim.passed:
                    scores.append(
                        (
                            "unverified_claim_disclosed",
                            float(claim.disclosed_as_unverified),
                            "BOOLEAN",
                        )
                    )
            elif event_type == AUTHORIZATION_CHECK_EVENT:
                auth = AuthorizationCheckEvent.model_validate(event_data)
                if auth.requested_scope not in auth.held_scopes:
                    if auth.outcome == "rejected":
                        scores.append(("out_of_scope_request_rejected", 1.0, "BOOLEAN"))
                    elif auth.outcome == "accepted":
                        scores.append(("out_of_scope_request_rejected", 0.0, "BOOLEAN"))
                        level = "ERROR"
                        status_message = (
                            "Out-of-scope FHIR request was ACCEPTED — "
                            "authorization regression (KEY_METRICS.md Metric 3)"
                        )
                    else:
                        level = "WARNING"
                        status_message = (
                            f"Unrecognized outcome {auth.outcome!r}; Metric 3 score not derived"
                        )
            elif event_type == LATENCY_EVENT:
                latency = LatencyEvent.model_validate(event_data)
                seconds = latency.rendered_ts - latency.trigger_ts
                if seconds >= 0:
                    scores.append(("response_latency_seconds", seconds, "NUMERIC"))
                else:
                    level = "WARNING"
                    status_message = "rendered_ts precedes trigger_ts; Metric 4 score not derived"
            elif event_type == RETRIEVAL_EVENT:
                retrieval = RetrievalEvent.model_validate(event_data)
                tags.append(f"retrieved:{retrieval.resource_type.value}")
            elif event_type == RETRIEVAL_EVENT_SKIPPED:
                level = "WARNING"
                status_message = "Retrieval not attributable to a use case; excluded from Metric 5"
            elif event_type == LLM_USAGE_EVENT:
                llm_usage = _LlmUsage.model_validate(event_data)
            elif event_type in _OPERATIONAL_SCORERS:
                derived = _OPERATIONAL_SCORERS[event_type](event_data)
                scores.extend(derived.scores)
                tags.extend(derived.tags)
                if derived.level is not None:
                    level, status_message = derived.level, derived.status_message
        except ValidationError as exc:
            level = "WARNING"
            status_message = (
                f"event_data does not match the {event_type} schema "
                f"({exc.error_count()} validation errors); no metric derived"
            )

        pinned = (
            int(self._client.create_trace_id(seed=correlation_id), 16) if correlation_id else None
        )

        # Start from an EMPTY OpenTelemetry context so the observation is a
        # true root even if some other instrumentation has a span active in
        # this request — then pin its trace id to the correlation_id.
        empty_context_token = otel_context.attach(otel_context.Context())
        try:
            with propagate_attributes(
                session_id=patient_session_key,
                tags=tags or None,
                trace_name=use_case.value if use_case else None,
            ):
                pin_token = _pinned_trace_id.set(pinned)
                try:
                    if llm_usage is not None:
                        usage_details = {
                            "input": llm_usage.input_tokens,
                            "output": llm_usage.output_tokens,
                        }
                        if llm_usage.cache_creation_input_tokens is not None:
                            usage_details["input_cache_creation"] = (
                                llm_usage.cache_creation_input_tokens
                            )
                        if llm_usage.cache_read_input_tokens is not None:
                            usage_details["input_cache_read"] = llm_usage.cache_read_input_tokens
                        observation = self._client.start_observation(
                            name=event_type,
                            as_type="generation",
                            model=llm_usage.model,
                            usage_details=usage_details,
                            metadata=metadata,
                        )
                        observation.end()
                    else:
                        observation = self._client.create_event(
                            name=event_type,
                            metadata=metadata,
                            level=level,
                            status_message=status_message,
                        )
                finally:
                    _pinned_trace_id.reset(pin_token)
        finally:
            otel_context.detach(empty_context_token)

        for name, value, data_type in scores:
            self._client.create_score(
                name=name,
                value=value,
                data_type=data_type,
                trace_id=observation.trace_id,
                observation_id=observation.id,
            )

    def flush(self) -> None:
        self._client.flush()

    # -- health -----------------------------------------------------------

    def check_health(self) -> None:
        """
        Non-blocking: reports the most recent background reachability check
        and schedules a new one when the last is older than the TTL. Never
        makes a network call on the caller's thread, so /ready cannot stall
        the event loop while Langfuse is down.

        Staleness is bounded: a result older than
        _AUTH_CHECK_MAX_STALENESS_SECONDS is never reported as healthy, so
        after Langfuse goes down /ready turns unhealthy within at most that
        long even if nothing probed it in between.
        """
        with self._auth_lock:
            age = time.monotonic() - self._auth_checked_at
            if age > _AUTH_CHECK_TTL_SECONDS and not self._auth_check_running:
                self._auth_check_running = True
                threading.Thread(
                    target=self._run_auth_check, name="langfuse-auth-check", daemon=True
                ).start()
            result = self._auth_result

        if result is None or age > _AUTH_CHECK_MAX_STALENESS_SECONDS:
            raise ObservabilityExportUnavailable("Langfuse reachability not yet confirmed")
        if result != "ok":
            raise ObservabilityExportUnavailable(_AUTH_FAILURE_MESSAGES[result])

    def _run_auth_check(self) -> None:
        from httpx import TransportError

        result: _AuthResult
        try:
            result = "ok" if self._client.auth_check() else "failed"
        except TransportError:
            result = "unreachable"
        except Exception as exc:
            result = "rejected" if getattr(exc, "status_code", None) in (401, 403) else "failed"
        with self._auth_lock:
            self._auth_result = result
            self._auth_checked_at = time.monotonic()
            self._auth_check_running = False
        structlog.get_logger().info(
            "observability_export_auth_check", ok=result == "ok", result=result
        )


_exporter: LangfuseExporter | None = None
_disabled_reason: str | None = "configure_logging() has not run"


def _build_client() -> Langfuse:
    """
    Imports are local so that this module (and therefore logging.py) stays
    importable in an environment where the langfuse package — and the
    OpenTelemetry SDK it brings — is not yet installed.
    """
    from langfuse import Langfuse, is_langfuse_span
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.id_generator import RandomIdGenerator

    class _PinnedTraceIdGenerator(RandomIdGenerator):
        def generate_trace_id(self) -> int:
            pinned = _pinned_trace_id.get()
            return pinned if pinned is not None else super().generate_trace_id()

    return Langfuse(
        base_url=os.environ["LANGFUSE_BASE_URL"],
        public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
        secret_key=os.environ["LANGFUSE_SECRET_KEY"],
        # Private provider: never registered globally (see module docstring).
        tracer_provider=TracerProvider(
            id_generator=_PinnedTraceIdGenerator(), resource=service_resource()
        ),
        should_export_span=is_langfuse_span,
    )


#: The OpenTelemetry `service.name` on every exported span. Without a
#: resource the SDK sends `unknown_service`, which is what a trace from this
#: agent was labelled before. OTEL_SERVICE_NAME overrides it, as the
#: OpenTelemetry spec says it should.
SERVICE_NAME = "clinical-copilot-agent"


def service_resource() -> Any:
    from opentelemetry.sdk.resources import SERVICE_NAME as SERVICE_NAME_KEY
    from opentelemetry.sdk.resources import Resource

    return Resource.create({SERVICE_NAME_KEY: os.environ.get("OTEL_SERVICE_NAME") or SERVICE_NAME})


def init_exporter() -> None:
    """
    Idempotent. Builds the Langfuse client from LANGFUSE_BASE_URL,
    LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY (LANGFUSE_TRACING_ENVIRONMENT
    is read by the SDK itself). With none of them set, export is disabled —
    stdout-only, the pre-existing behaviour — and one line says so. A
    partial configuration, or the SDK missing while configured, is logged as
    an error rather than guessed around.
    """
    global _exporter, _disabled_reason
    if _exporter is not None:
        return

    log = structlog.get_logger()
    present = [name for name in _REQUIRED_ENV if os.environ.get(name)]
    if not present:
        _disabled_reason = "Langfuse export not configured"
        log.info("observability_export_disabled", reason="LANGFUSE_* env vars not set")
        return
    if len(present) != len(_REQUIRED_ENV):
        missing = sorted(set(_REQUIRED_ENV) - set(present))
        _disabled_reason = "Langfuse export partially configured"
        log.error("observability_export_disabled", reason="partial configuration", missing=missing)
        return

    try:
        client = _build_client()
    except ImportError:
        _disabled_reason = "Langfuse SDK not installed"
        log.error("observability_export_disabled", reason="langfuse package not installed")
        return

    _exporter = LangfuseExporter(client)
    _disabled_reason = None
    log.info(
        "observability_export_enabled", backend="langfuse", base_url=os.environ["LANGFUSE_BASE_URL"]
    )
    # Kick off the first reachability check now so /ready has an answer
    # soon after startup; the result is ignored here.
    try:
        _exporter.check_health()
    except ObservabilityExportUnavailable:
        pass


def export_event(
    *,
    event_type: str,
    event_data: dict[str, Any],
    use_case: UseCase | None,
    patient_session_key: str | None,
) -> None:
    """Called by log_event after the stdout line is written. Never raises."""
    if _exporter is None:
        return
    correlation_id = structlog.contextvars.get_contextvars().get("correlation_id")
    try:
        _exporter.export(
            event_type=event_type,
            event_data=event_data,
            use_case=use_case,
            patient_session_key=patient_session_key,
            correlation_id=correlation_id if isinstance(correlation_id, str) else None,
        )
    except Exception:
        structlog.get_logger().error(
            "observability_export_failed", event_type=event_type, exc_info=True
        )


def export_configured() -> bool:
    """
    True when any LANGFUSE_* export variable is set, i.e. export was meant to
    be on. A partial configuration counts, so it is reported, not hidden.
    False is the intended state for this submission (ARCHITECTURE.md §1).
    """
    return any(os.environ.get(name) for name in _REQUIRED_ENV)


def check_export_health() -> None:
    """
    Called by /ready (app/health.py `_check_observability_backend`). Raises
    ObservabilityExportUnavailable when export is disabled, misconfigured,
    Langfuse is unreachable, or it rejects the API keys. Message strings
    contain no paths, URLs or exception text (docs/AUDIT.md F-37).
    """
    if _exporter is None:
        raise ObservabilityExportUnavailable(_disabled_reason or "Langfuse export disabled")
    _exporter.check_health()


def flush_export() -> None:
    """Block until queued events are sent. For short-lived scripts; the SDK also flushes at exit."""
    if _exporter is not None:
        _exporter.flush()
