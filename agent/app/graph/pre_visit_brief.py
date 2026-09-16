"""
Pre-Visit Brief pipeline — USERS.md §3 Use Case 1, end to end.

    retrieve → draft → verify → render

  retrieve  Condition, MedicationRequest, AllergyIntolerance — exactly the
            "Data required" for Use Case 1 (USERS.md §3, ARCHITECTURE.md
            §3), all three on every run, under the caller's own clinician
            token (§2). Encounter and Observation belong to Change
            Detection and are never queried here. First reads the Patient
            itself: FHIR search answers an unknown patient id with empty
            results, which would otherwise render as "not recorded".
  draft     app/reasoning/draft_claims.py: structured claims plus
            section_counts. The only LLM call in the graph.
  verify    app/verification/checker.py: one VerificationResult per claim
            (§5). Plain code.
  render    Physician-facing text, in plain code (render_brief below).
            Verified claims are stated plainly; failed claims are stated as
            UNVERIFIED, never dropped (§5); empty and none-active sections
            come from section_counts, with no LLM involvement.

Entry point: run_pre_visit_brief().

Every event any node logs — retrieval_event, llm_usage_event,
claims_drafted, claim_verified_event, latency_event — carries the caller's
patient_session_key, the (user, patient, session) key of ARCHITECTURE.md
§4. This entry point is the first place in the codebase that has that key,
so it is passed explicitly into every call that logs, never looked up.

Three things kept out of anything that could leave the process:
- The bearer token is LangGraph runtime *context*, not graph state, so no
  checkpointer or state dump can ever hold it. (No checkpointer is used.)
  A fully traced run's payload was checked and does not contain it
  (docs/audit/findings.md F-56; tests/test_langsmith_tracing_guard.py).
- LangSmith tracing can never run over this graph. langsmith is installed
  with LangGraph, and with LANGSMITH_TRACING=true set it posts full graph
  state — here, retrieved chart records — to an external service (F-56).
  Two layers, so no single mistake reopens it:
    1. PRE_VISIT_BRIEF_GRAPH, the only handle to the compiled graph this
       module exports, runs it with tracing forced off, and takes no
       RunnableConfig — tracing_context(enabled=False) does not stop a
       tracer passed in explicitly as a callback.
    2. Every node refuses to run while a LangSmith tracer is attached to
       its run (TracingEnabledError), however it got there: a graph
       reached some other way fails before any chart record is retrieved.
  Same reasoning as the Langfuse exporter's refusal to export
  auto-instrumented spans (app/middleware/langfuse_export.py).
- No node logs rendered text, claim values, or record contents.

Not built here: the brief's one-line "changed since last visit" pointer
(USERS.md §3 Use Case 1) is consumed from Change Detection, which does not
exist yet.
"""

from __future__ import annotations

import asyncio
import operator
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from langchain_core.callbacks import BaseCallbackManager
from langchain_core.runnables import RunnableConfig
from langchain_core.tracers.langchain import LangChainTracer
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langsmith import tracing_context

from app.fhir import client as fhir_client
from app.middleware.correlation import correlation_scope, get_correlation_id
from app.middleware.langfuse_export import LATENCY_EVENT
from app.middleware.logging import log_event
from app.models.claims import VerificationResult
from app.models.fhir_resources import FhirResourceType
from app.models.metrics_events import LatencyEvent, UseCase
from app.reasoning.draft_claims import (
    PreVisitBriefDraft,
    SectionCount,
    brief_records,
    draft_pre_visit_brief,
)
from app.verification.checker import FailureReason, verify_claims

_USE_CASE = UseCase.PRE_VISIT_BRIEF

# Render order and wording, one entry per Use Case 1 section.
_SECTIONS: tuple[tuple[FhirResourceType, str, str], ...] = (
    (FhirResourceType.CONDITION, "Active problems", "active problem"),
    (FhirResourceType.MEDICATION_REQUEST, "Current medications", "active medication"),
    (FhirResourceType.ALLERGY_INTOLERANCE, "Allergies", "allergy record"),
)

# §5 requires a failure to be legible; these say why in the physician's
# terms rather than the verifier's.
_UNVERIFIED_WHY: dict[FailureReason, str] = {
    FailureReason.SOURCE_NOT_FOUND: "no matching record in the chart",
    FailureReason.FIELD_NOT_ON_SOURCE: "not a field the chart record has",
    FailureReason.VALUE_MISMATCH: "does not match the chart record",
}


@dataclass(frozen=True)
class PreVisitBriefContext:
    """Per-run inputs every node reads. Immutable, and never graph state."""

    token: str
    patient_id: str
    patient_session_key: str
    trigger_ts: float


class PreVisitBriefState(TypedDict, total=False):
    retrieved: dict[FhirResourceType, list[Any]]
    draft: PreVisitBriefDraft
    results: list[VerificationResult]
    text: str
    rendered_ts: float
    # Node name -> wall-clock seconds; each node adds its own entry.
    node_seconds: Annotated[dict[str, float], operator.or_]


@dataclass(frozen=True)
class PreVisitBriefResult:
    """
    The rendered brief plus what LatencyEvent and the timing report need.
    `node_seconds` has one entry each for retrieve, draft, verify, render;
    `total_seconds` is the same trigger-to-render interval the logged
    latency_event carries (KEY_METRICS.md Metric 4).
    """

    text: str
    correlation_id: str
    patient_session_key: str
    trigger_ts: float
    rendered_ts: float
    node_seconds: dict[str, float]
    verification_results: list[VerificationResult]
    section_counts: dict[FhirResourceType, SectionCount]

    @property
    def total_seconds(self) -> float:
        return self.rendered_ts - self.trigger_ts


_Node = Callable[
    [PreVisitBriefState, Runtime[PreVisitBriefContext]], Awaitable[dict[str, Any]]
]


def _timed(name: str, node: _Node) -> _Node:
    async def timed_node(
        state: PreVisitBriefState, runtime: Runtime[PreVisitBriefContext]
    ) -> dict[str, Any]:
        started = time.perf_counter()
        update = await node(state, runtime)
        return {**update, "node_seconds": {name: time.perf_counter() - started}}

    return timed_node


class TracingEnabledError(RuntimeError):
    """A node was asked to run with a LangSmith tracer attached (F-56)."""


def _refuse_if_traced(config: RunnableConfig) -> None:
    """
    Raises if a LangSmith tracer is attached to this run. That is the
    ground truth for whether this node's inputs and outputs are about to be
    posted, whatever put the tracer there: LANGSMITH_TRACING, an explicit
    callback, or a parent traced run.
    """
    callbacks = config.get("callbacks")
    if isinstance(callbacks, BaseCallbackManager):
        handlers = [*callbacks.handlers, *callbacks.inheritable_handlers]
    else:
        handlers = list(callbacks or [])
    if any(isinstance(handler, LangChainTracer) for handler in handlers):
        raise TracingEnabledError(
            "Refusing to run a Pre-Visit Brief node with a LangSmith tracer "
            "attached: graph state holds chart records (docs/audit/findings.md "
            "F-56). Run the graph through PRE_VISIT_BRIEF_GRAPH or "
            "run_pre_visit_brief()."
        )


def _untraced(node: _Node) -> Callable[..., Awaitable[dict[str, Any]]]:
    async def untraced_node(
        state: PreVisitBriefState,
        config: RunnableConfig,
        runtime: Runtime[PreVisitBriefContext],
    ) -> dict[str, Any]:
        _refuse_if_traced(config)
        return await node(state, runtime)

    return untraced_node


class UntracedGraph:
    """
    The only handle to the compiled graph this module exports. Its one way
    to run the graph forces LangSmith tracing off first. Deliberately not a
    LangGraph Runnable: stream, astream_events, batch, with_config and the
    rest each start a run of their own, and LangGraph adds such entry
    points over time, so anything not listed here is simply unavailable
    rather than unguarded. No RunnableConfig is accepted, because a tracer
    passed as a callback is attached even inside tracing_context(enabled=False).
    """

    def __init__(self, compiled: Any) -> None:
        self._compiled = compiled

    async def ainvoke(
        self, input: PreVisitBriefState, *, context: PreVisitBriefContext
    ) -> dict[str, Any]:
        with tracing_context(enabled=False):
            return await self._compiled.ainvoke(input, context=context)


async def _retrieve(
    state: PreVisitBriefState, runtime: Runtime[PreVisitBriefContext]
) -> dict[str, Any]:
    ctx = runtime.context
    # Raises PatientNotFoundError before any search runs, so an unknown id
    # never reaches rendering and logs no retrieval events.
    await fhir_client.ensure_patient_exists(ctx.token, ctx.patient_id)
    conditions, medications, allergies = await asyncio.gather(
        fhir_client.get_conditions(
            ctx.token,
            ctx.patient_id,
            use_case=_USE_CASE,
            patient_session_key=ctx.patient_session_key,
        ),
        fhir_client.get_medication_requests(
            ctx.token,
            ctx.patient_id,
            use_case=_USE_CASE,
            patient_session_key=ctx.patient_session_key,
        ),
        fhir_client.get_allergy_intolerances(
            ctx.token,
            ctx.patient_id,
            use_case=_USE_CASE,
            patient_session_key=ctx.patient_session_key,
        ),
    )
    return {
        "retrieved": {
            FhirResourceType.CONDITION: conditions,
            FhirResourceType.MEDICATION_REQUEST: medications,
            FhirResourceType.ALLERGY_INTOLERANCE: allergies,
        }
    }


async def _draft(
    state: PreVisitBriefState, runtime: Runtime[PreVisitBriefContext]
) -> dict[str, Any]:
    draft = await draft_pre_visit_brief(
        state["retrieved"],
        patient_session_key=runtime.context.patient_session_key,
    )
    return {"draft": draft}


async def _verify(
    state: PreVisitBriefState, runtime: Runtime[PreVisitBriefContext]
) -> dict[str, Any]:
    results = verify_claims(
        state["draft"].claims,
        state["retrieved"],
        use_case=_USE_CASE,
        patient_session_key=runtime.context.patient_session_key,
    )
    return {"results": results}


async def _render(
    state: PreVisitBriefState, runtime: Runtime[PreVisitBriefContext]
) -> dict[str, Any]:
    ctx = runtime.context
    text = render_brief(
        state["results"], state["draft"].section_counts, state["retrieved"]
    )
    rendered_ts = time.time()
    event = LatencyEvent(
        use_case=_USE_CASE,
        trigger_ts=ctx.trigger_ts,
        rendered_ts=rendered_ts,
        correlation_id=get_correlation_id() or "no-request-context",
    )
    log_event(
        LATENCY_EVENT,
        event_data=event.model_dump(mode="json"),
        use_case=_USE_CASE,
        patient_session_key=ctx.patient_session_key,
    )
    return {"text": text, "rendered_ts": rendered_ts}


def render_brief(
    results: list[VerificationResult],
    section_counts: dict[FhirResourceType, SectionCount],
    retrieved: dict[FhirResourceType, list[Any]],
) -> str:
    """
    Plain code, no LLM. Per section:

    - section_counts decides whether the section is "not recorded"
      (nothing retrieved) or "none active" (records retrieved, none
      active). Those are the F-21 distinctions USERS.md §3 Use Case 1
      requires; neither is ever rendered as an empty list.
    - Every claim for the section is rendered, in drafted order: verified
      ones plainly, failed ones as UNVERIFIED with the reason. A failed
      claim is never dropped (§5), including one filed under a section
      that has no records.
    - Records the brief covers that no claim cites at all are counted, so
      a record the model skipped is not silently missing either.
    """
    lines = ["Pre-Visit Brief"]
    unverified = 0
    for resource_type, heading, noun in _SECTIONS:
        counts = section_counts[resource_type]
        brief_ids = {
            record.source_id
            for record in brief_records(resource_type, retrieved[resource_type])
        }
        section = [r for r in results if r.claim.resource_type == resource_type]

        lines.append("")
        if counts.retrieved == 0:
            lines.append(f"{heading}: not recorded")
        elif counts.in_brief == 0:
            lines.append(
                f"{heading}: none active "
                f"({counts.retrieved} on file, none with active status)"
            )
        else:
            lines.append(f"{heading}:")

        for result in section:
            value = result.claim.value
            if not result.passed:
                unverified += 1
                why = _UNVERIFIED_WHY[FailureReason(result.reason)]
                lines.append(f'  - UNVERIFIED: "{value}" ({why})')
            elif result.claim.source_id in brief_ids:
                lines.append(f"  - {value}")
            else:
                lines.append(f"  - {value} (on file, but not an {noun})")

        undrafted = brief_ids - {r.claim.source_id for r in section}
        if undrafted:
            lines.append(
                f"  - {len(undrafted)} more {noun}(s) on file, "
                "not included in this brief"
            )

    if unverified:
        lines.append("")
        lines.append(
            f"{unverified} item(s) above could not be verified against the "
            "chart and are marked UNVERIFIED."
        )
    return "\n".join(lines)


def _build_graph() -> Any:
    graph = StateGraph(PreVisitBriefState, context_schema=PreVisitBriefContext)
    graph.add_node("retrieve", _untraced(_timed("retrieve", _retrieve)))
    graph.add_node("draft", _untraced(_timed("draft", _draft)))
    graph.add_node("verify", _untraced(_timed("verify", _verify)))
    graph.add_node("render", _untraced(_timed("render", _render)))
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "draft")
    graph.add_edge("draft", "verify")
    graph.add_edge("verify", "render")
    graph.add_edge("render", END)
    return graph.compile()


PRE_VISIT_BRIEF_GRAPH = UntracedGraph(_build_graph())


async def run_pre_visit_brief(
    token: str,
    patient_id: str,
    patient_session_key: str,
    use_case: UseCase | str = UseCase.PRE_VISIT_BRIEF,
) -> PreVisitBriefResult:
    """
    Runs the Pre-Visit Brief for one patient and returns the rendered text.

    `token` is the requesting clinician's own bearer token (§2).
    `patient_session_key` is the caller's (user, patient, session) key
    (§4); it is attributed to every event this run logs. The caller owns
    its construction — nothing here can check that it names `patient_id`.

    Uses the correlation_id already bound for this request if there is one
    (CorrelationIdMiddleware); otherwise binds a fresh one for this run
    only. Exceptions from retrieval or drafting propagate; nothing is
    rendered and no latency_event is logged for a run that did not render.
    """
    if UseCase(use_case) is not UseCase.PRE_VISIT_BRIEF:
        raise ValueError(
            f"This graph serves {UseCase.PRE_VISIT_BRIEF.value} only, "
            f"not {UseCase(use_case).value}"
        )
    for name, value in (
        ("token", token),
        ("patient_id", patient_id),
        ("patient_session_key", patient_session_key),
    ):
        if not value.strip():
            raise ValueError(f"{name} must be a non-empty string")

    trigger_ts = time.time()
    context = PreVisitBriefContext(
        token=token,
        patient_id=patient_id,
        patient_session_key=patient_session_key,
        trigger_ts=trigger_ts,
    )
    correlation_id = get_correlation_id() or str(uuid.uuid4())
    with correlation_scope(correlation_id):
        final = await PRE_VISIT_BRIEF_GRAPH.ainvoke(
            {"node_seconds": {}}, context=context
        )

    return PreVisitBriefResult(
        text=final["text"],
        correlation_id=correlation_id,
        patient_session_key=patient_session_key,
        trigger_ts=trigger_ts,
        rendered_ts=final["rendered_ts"],
        node_seconds=final["node_seconds"],
        verification_results=final["results"],
        section_counts=final["draft"].section_counts,
    )
