"""
Operational event schemas — the shapes the Week 2 dashboard and alerts are
computed from (docs/w2/OBSERVABILITY.md).

`metrics_events.py` holds the shapes docs/KEY_METRICS.md's six product
metrics are computed from. These are the operational ones those six never
needed: how many requests, how many failed, how long each step took, how
many tool calls and retries. Kept in their own module rather than appended
to Week 1's, so the two sets can be read, and changed, apart.

Every model here is emitted through `app.middleware.logging.log_event`, so
every line carries the request's correlation_id from structlog's
contextvars, is written to stdout, and is exported to Langfuse when export
is configured. Emission helpers live in `app/middleware/telemetry.py`.

**No PHI, by construction rather than by care.** Every field is an enum, a
number, a boolean, a route template, an exception class name or an id.
There is no free-text field anywhere in this module. `counts` is typed
`int | float | bool`, so a string — a value, a name, a question — cannot
be put in it without failing validation (CLAUDE.md invariant 1).

Plain `BaseModel`, not `W2Contract`: these are read back from log dicts by
the exporter and the dashboard, and under `strict=True` an enum refuses its
own string value when validated from a dict (D22).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class StepOutcome(StrEnum):
    """How one step of a pipeline ended."""

    OK = "ok"
    #: Raised, or returned a failure the caller will surface as one.
    ERROR = "error"
    #: Declined by rule before doing the work (a refused question, a
    #: refused write). Not an error: the step did what it is for.
    REFUSED = "refused"
    #: Accepted but not finished — an upload whose extraction is still
    #: running (D7).
    PENDING = "pending"


class Pipeline(StrEnum):
    """
    Which pipeline a step belongs to. Step names are only unique within a
    pipeline — Week 1's `retrieve` is a FHIR read, the evidence retriever's
    is a guideline search — so a step is always read as `pipeline.step`.
    """

    PRE_VISIT_BRIEF = "pre_visit_brief"
    FOLLOW_UP = "follow_up"
    CONVERSATION = "conversation"
    DOCUMENTS = "documents"
    INTAKE_EXTRACTOR = "intake_extractor"
    EVIDENCE_RETRIEVER = "evidence_retriever"
    WRITEBACK = "writeback"


class Tool(StrEnum):
    """What a tool call called. One member per external dependency."""

    FHIR_READ = "fhir_read"
    LLM = "llm"
    VISION = "vision"
    GUIDELINE_RETRIEVER = "guideline_retriever"
    LAB_WRITEBACK = "lab_writeback"


_Count = int | float | bool


class StepEvent(BaseModel):
    """
    Emitted once per pipeline step, on success or failure.

    Feeds: p50/p95 latency by step, error count by step, and — through
    `counts` — the per-step numbers the dashboard reads (hits, located
    fields, claims). `latency_ms` is wall-clock for the step alone.
    """

    model_config = ConfigDict(extra="forbid")

    pipeline: Pipeline
    step: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    outcome: StepOutcome
    latency_ms: float = Field(ge=0)
    #: The exception's class name only. Never its message, which is where
    #: a value, a URL with a patient id in it, or a response body ends up.
    error_type: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")
    counts: dict[str, _Count] = Field(default_factory=dict)
    correlation_id: str


class ToolCallEvent(BaseModel):
    """
    Emitted once per logical call to an external dependency.

    `attempts` counts HTTP attempts inside that one call, so `retries` is
    what the SDK or client retried silently. For the Anthropic SDK that is
    read from the `x-stainless-retry-count` header it stamps on each
    attempt (app/middleware/telemetry.py); for a client that never retries
    it is always 1.
    """

    model_config = ConfigDict(extra="forbid")

    tool: Tool
    #: A closed-vocabulary sub-target — a FHIR resource type, a model id.
    target: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{1,64}$")
    outcome: StepOutcome
    latency_ms: float = Field(ge=0)
    attempts: int = Field(ge=1)
    retries: int = Field(ge=0)
    error_type: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")
    status_code: int | None = None
    correlation_id: str


class HttpRequestEvent(BaseModel):
    """
    Emitted once per HTTP request the agent serves, by
    CorrelationIdMiddleware, after the response status is known.

    `route` is the matched route *template* (`/documents/{document_id}/
    extraction`), never the raw path: a raw path carries a document id and
    would give every request its own row. Unmatched paths are `unmatched`.
    """

    model_config = ConfigDict(extra="forbid")

    method: str = Field(pattern=r"^[A-Z]{3,7}$")
    route: str = Field(max_length=128)
    status_code: int = Field(ge=100, le=599)
    latency_ms: float = Field(ge=0)
    correlation_id: str

    @property
    def is_server_error(self) -> bool:
        return self.status_code >= 500

    @property
    def is_client_error(self) -> bool:
        return 400 <= self.status_code < 500


STEP_EVENT = "step_event"
TOOL_CALL_EVENT = "tool_call_event"
HTTP_REQUEST_EVENT = "http_request_event"

#: The operational event types. Product-metric consumers (KEY_METRICS.md's
#: six, and tests asserting a pipeline's product events) filter these out.
TELEMETRY_EVENT_TYPES = frozenset({STEP_EVENT, TOOL_CALL_EVENT, HTTP_REQUEST_EVENT})
