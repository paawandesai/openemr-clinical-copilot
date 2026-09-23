"""
The Week 2 ask path — one supervisor over two workers, feeding the
deterministic verifier and producing the `FinalAnswer` contract.

            ┌──────────────► intake_extractor ─┐
    START ─► supervisor ───► evidence_retriever ┼─► supervisor ─► …
            │                                  │
            ├──────────────► answer ──(failure)─┘
            │                  └────────────────► END
            └──────────────► refuse ────────────► END

  supervisor          app/supervisor/routing.py: `classify` once, `decide` on
                      every visit. Deterministic; each decision is logged as a
                      RoutingDecision (route, reason_code, correlation_id) and
                      nothing else. Bounded visits; fails closed (D4).
  intake_extractor    The extractor worker ran at upload (D7). Here it is
                      handed an IntakeExtractorInput per pending document and
                      answers with its IntakeExtractorOutput *now* — never a
                      wait, never a synchronous extraction. A document still
                      running comes back pending and the turn is refused as
                      `document_still_processing`.
  evidence_retriever  EvidenceRetrieverInput (concepts only, D5) ->
                      EvidenceRetrieverOutput, over app/retrieval/worker.py.
  answer              Week 1's chart read (the same readers, the same bounded
                      lab set), the located facts of the documents, the
                      retrieved chunks -> one drafting call ->
                      app/verification/w2_checker.py -> FinalAnswer, rendered
                      in code. A chart or model failure goes back to the
                      supervisor, which refuses with a typed reason.
  refuse              Fixed wording per reason code. States nothing about
                      the patient.

**Extends Week 1, does not replace it.** `POST /pre-visit-brief/conversation`
(app/graph/conversation.py) is untouched and eval I1 runs against it. This
path uses Week 1's pieces rather than copies of them: the Patient read as
the authorization step, the same `ConversationStore` — so a session is bound
to one patient across both paths and a patient switch on either discards the
other's context (I1's invariant) — the same chart readers and lab narrowing,
the same verifier for chart claims, the same claim lines, and the same
tracing guard.

**Tracing and PHI (invariant 1, F-56, docs/w2/root-run-trace-gap.md).** Every
node is Week 1's `_untraced(_timed(...))`; the compiled graph is reachable
only through `UntracedGraph`; and the graph *input* is `{"node_seconds": {}}`.
The question, the token, the patient and session, the document ids — all of
it is runtime context (`SupervisorContext`), which a tracer never receives.
tests/test_graph_inputs_carry_nothing.py finds this graph and checks its one
call site.

**Observability.** Every event carries the correlation id. Per decision:
`supervisor_routing_decision` (route, reason code, visit, rule ids). Per turn:
`w2_ask_turn_completed` — the node sequence, per-node latency, the routing
reason codes, tokens and cost of the drafting call, retrieval hits and
guideline ids, each document's extraction status and mean confidence, and
claim counts by source type. Ids, codes and numbers; never the question, a
value, a quote or a name.
"""

from __future__ import annotations

import asyncio
import dataclasses
import operator
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

import anthropic
import httpx
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import ValidationError

from app.conversation.lab_codes import same_test_codes
from app.conversation.routing import ENCOUNTER_COUNT, LabScope, narrow_observations
from app.conversation.state import ConversationSnapshot, ConversationStore
from app.fhir import client as fhir_client
from app.fhir.exceptions import FhirClientError, PatientNotFoundError
from app.graph import conversation as week1
from app.graph.follow_up import fetch_bounded_set
from app.graph.pre_visit_brief import UntracedGraph, _timed, _untraced
from app.middleware.correlation import correlation_scope, get_correlation_id
from app.middleware.logging import log_event
from app.models.claims import Claim, VerificationResult
from app.models.conversation import ConversationGap
from app.models.fhir_resources import FhirResourceType, Observation
from app.models.metrics_events import UseCase
from app.models.telemetry_events import Pipeline
from app.models.w2.answer import FinalAnswer, GuidelineClaim, PatientFactClaim
from app.models.w2.answer_view import AnswerView, ReportedFlagView, ViewerDocument
from app.models.w2.ask import (
    AnswerLimitation,
    AskConversation,
    AskResponse,
    AskStatus,
    CriticalValue,
    HandoffRecord,
    UnverifiedClaim,
)
from app.models.w2.citations import citation_from_claim
from app.models.w2.documents import ExtractionStatus
from app.models.w2.extraction import UnsupportedField
from app.models.w2.intake_form import Discrepancy, IntakeFormExtraction
from app.models.w2.lab_pdf import LabPdfExtraction
from app.models.w2.source_types import SourceType
from app.models.w2.workers import (
    ClinicalConcept,
    EvidenceChunk,
    EvidenceRetrieverInput,
    EvidenceRetrieverOutput,
    IntakeExtractorInput,
    IntakeExtractorOutput,
    Route,
    RoutingDecision,
    RoutingReasonCode,
)
from app.reasoning.draft_answer import AnswerDraft, AnswerTruncatedError, draft_answer
from app.reasoning.draft_claims import ClaimDraftingError
from app.reasoning.draft_follow_up import Coverage
from app.retrieval.retriever import GuidelineRetriever, parse_source_version
from app.retrieval.worker import run_evidence_retriever
from app.supervisor.critical import document_criticals, keep_critical, limit, observation_direction
from app.supervisor.facts import DocumentFact, document_facts, flagged_test_names, group_statement
from app.supervisor.routing import (
    MAX_CONCEPTS,
    DocumentState,
    Node,
    QuestionIntent,
    SupervisorView,
    classify,
    decide,
)
from app.supervisor.scope import (
    OUTPUT_LIMIT_WORDING,
    SCOPE_RULE,
    AnswerScope,
    ScopedRow,
    capped_statement,
    scope_answer,
    summary_statement,
)
from app.verification.checker import verify_claims
from app.verification.w2_checker import (
    DocumentInTurn,
    DraftedDocumentFact,
    W2Verification,
    verify_answer,
    verify_document_fact,
)
from app.workers import document_store
from app.workers.document_store import DocumentStore
from app.workers.intake_extractor.report import ExtractionReport

_USE_CASE = UseCase.FOLLOW_UP_DRILLING

#: D25: four chunks are passed to the answer step.
ANSWER_CHUNKS = 4

ROUTING_EVENT = "supervisor_routing_decision"
TURN_EVENT = "w2_ask_turn_completed"

Drafter = Callable[..., Awaitable[AnswerDraft]]
RetrieverProvider = Callable[[], GuidelineRetriever | None]


# --------------------------------------------------------------------------
# context and state
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SupervisorContext:
    """Per-turn inputs. Runtime context — never graph state, never graph input."""

    token: str
    patient_id: str
    patient_session_key: str
    question: str
    document_ids: tuple[str, ...]
    snapshot: ConversationSnapshot
    correlation_id: str
    documents: DocumentStore
    retriever: RetrieverProvider
    drafter: Drafter


@dataclass
class TurnOutcome:
    """What the answer or refuse node produced. Internal; becomes AskResponse."""

    status: AskStatus
    message: str
    answer_view: AnswerView | None = None
    unverified: list[UnverifiedClaim] = field(default_factory=list)
    discrepancies: list[Discrepancy] = field(default_factory=list)
    limitations: list[AnswerLimitation] = field(default_factory=list)
    gaps: list[ConversationGap] = field(default_factory=list)
    chart_results: list[VerificationResult] = field(default_factory=list)
    verification: list[W2Verification] = field(default_factory=list)
    draft: AnswerDraft | None = None
    critical_values: list[CriticalValue] = field(default_factory=list)
    #: Critical lab results the cap had dropped and keep_critical put back.
    critical_restored: int = 0
    #: AnswerScope.counts() — how many lab rows were itemised, counted, capped.
    scope: dict[str, int] = field(default_factory=dict)
    output_limited: bool = False


def _add_counts(left: dict[str, float], right: dict[str, float]) -> dict[str, float]:
    merged = dict(left)
    for key, value in right.items():
        merged[key] = merged.get(key, 0) + value
    return merged


class SupervisorState(TypedDict, total=False):
    intent: QuestionIntent
    route: Route
    decisions: Annotated[list[RoutingDecision], operator.add]
    handoffs: Annotated[list[HandoffRecord], operator.add]
    sequence: Annotated[list[str], operator.add]
    visits: Annotated[dict[str, float], _add_counts]
    extractor_outputs: dict[str, IntakeExtractorOutput]
    retrieval_ran: bool
    retrieval: EvidenceRetrieverOutput | None
    failure: RoutingReasonCode | None
    turn: TurnOutcome
    # Summed, not overwritten: the supervisor runs more than once a turn.
    node_seconds: Annotated[dict[str, float], _add_counts]


def _visited(node: Node) -> dict[str, Any]:
    return {"visits": {node.value: 1}, "sequence": [node.value]}


# --------------------------------------------------------------------------
# documents, as the supervisor and the answer step see them
# --------------------------------------------------------------------------


def _extraction(
    ctx: SupervisorContext, document_id: str, state: SupervisorState
) -> tuple[DocumentState, ExtractionReport | None]:
    """One named document's state for routing, and its report when ready."""
    if ctx.documents.lookup(document_id, ctx.patient_id) is None:
        return DocumentState.NOT_FOUND, None
    # After the extractor has run, its handoff is what routing acts on.
    output = state.get("extractor_outputs", {}).get(document_id) or ctx.documents.status(
        document_id
    )
    if output is None:
        return DocumentState.NOT_FOUND, None
    if output.status is ExtractionStatus.FAILED:
        return DocumentState.FAILED, None
    if output.status is not ExtractionStatus.COMPLETE:
        return DocumentState.PENDING, None
    report = ctx.documents.report(document_id)
    if report is None or report.output.payload is None:
        return DocumentState.PENDING, None
    if report.identity is not None and report.identity.hard_flag:
        return DocumentState.IDENTITY_MISMATCH, report
    return DocumentState.READY, report


def _ready_reports(ctx: SupervisorContext, state: SupervisorState) -> dict[str, ExtractionReport]:
    ready: dict[str, ExtractionReport] = {}
    for document_id in ctx.document_ids:
        document_state, report = _extraction(ctx, document_id, state)
        if document_state is DocumentState.READY and report is not None:
            ready[document_id] = report
    return ready


# --------------------------------------------------------------------------
# nodes
# --------------------------------------------------------------------------


async def _supervisor(
    state: SupervisorState, runtime: Runtime[SupervisorContext]
) -> dict[str, Any]:
    ctx = runtime.context
    first = "intent" not in state
    intent = state["intent"] if not first else classify(ctx.question)
    visits = _add_counts(state.get("visits", {}), {Node.SUPERVISOR.value: 1})
    retrieval = state.get("retrieval")
    view = SupervisorView(
        intent=intent,
        documents=tuple(_extraction(ctx, d, state)[0] for d in ctx.document_ids),
        retrieval_ran=state.get("retrieval_ran", False),
        retrieval_hits=len(retrieval.chunks) if retrieval is not None else 0,
        failure=state.get("failure"),
        visits={Node(name): int(count) for name, count in visits.items()},
    )
    decision = decide(view, ctx.correlation_id)
    log_event(
        ROUTING_EVENT,
        event_data={
            **decision.model_dump(mode="json"),
            "visit": int(visits[Node.SUPERVISOR.value]),
            # Rule ids are codes; logged once, on the visit that classified.
            "rules": list(intent.rule_ids) if first else [],
            "document_states": [s.value for s in view.documents],
        },
        use_case=_USE_CASE,
        patient_session_key=ctx.patient_session_key,
    )
    return {
        "intent": intent,
        "route": decision.decision,
        "decisions": [decision],
        **_visited(Node.SUPERVISOR),
    }


async def _intake_extractor(
    state: SupervisorState, runtime: Runtime[SupervisorContext]
) -> dict[str, Any]:
    ctx = runtime.context
    started = time.perf_counter()
    outputs = dict(state.get("extractor_outputs", {}))
    for document_id in ctx.document_ids:
        stored = ctx.documents.lookup(document_id, ctx.patient_id)
        if stored is None:
            continue
        handed = IntakeExtractorInput(document=stored.document, correlation_id=ctx.correlation_id)
        # The worker's answer as of now. Never waits and never starts work (D7).
        output = ctx.documents.status(handed.document.document_id)
        if output is not None:
            outputs[document_id] = output
    record = HandoffRecord(
        sequence=len(state.get("handoffs", [])) + 1,
        node=Node.INTAKE_EXTRACTOR.value,
        handed=IntakeExtractorInput.__name__,
        returned=IntakeExtractorOutput.__name__,
        document_ids=list(outputs),
        extraction_statuses=[output.status.value for output in outputs.values()],
        latency_ms=round((time.perf_counter() - started) * 1000),
    )
    return {"extractor_outputs": outputs, "handoffs": [record], **_visited(Node.INTAKE_EXTRACTOR)}


def _retrieval_concepts(
    intent: QuestionIntent, reports: dict[str, ExtractionReport]
) -> list[ClinicalConcept]:
    """
    The lexicon's concepts; failing those, the printed names of the rows a
    laboratory flagged on a report this turn holds. Never question text (D5).
    """
    concepts = list(intent.concepts)
    if not concepts:
        for report in reports.values():
            payload = report.output.payload
            if payload is None:
                continue
            for name in flagged_test_names(payload):
                try:
                    concepts.append(ClinicalConcept(text=name))
                except ValidationError:
                    continue  # identifier-shaped or overlong: not a concept
    unique = {concept.text.casefold(): concept for concept in concepts}
    return list(unique.values())[:MAX_CONCEPTS]


async def _evidence_retriever(
    state: SupervisorState, runtime: Runtime[SupervisorContext]
) -> dict[str, Any]:
    ctx = runtime.context
    started = time.perf_counter()
    concepts = _retrieval_concepts(state["intent"], _ready_reports(ctx, state))

    def record(
        output: EvidenceRetrieverOutput | None, failure: RoutingReasonCode | None
    ) -> HandoffRecord:
        return HandoffRecord(
            sequence=len(state.get("handoffs", [])) + 1,
            node=Node.EVIDENCE_RETRIEVER.value,
            handed=EvidenceRetrieverInput.__name__,
            returned=EvidenceRetrieverOutput.__name__,
            concept_count=len(concepts),
            hit_count=len(output.chunks) if output is not None else 0,
            reranked=output.reranked if output is not None else None,
            guideline_ids=sorted({c.source.guideline_id for c in output.chunks}) if output else [],
            failure=failure,
            latency_ms=round((time.perf_counter() - started) * 1000),
        )

    update: dict[str, Any] = {"retrieval_ran": True, **_visited(Node.EVIDENCE_RETRIEVER)}
    if not concepts:
        # Nothing to search on; the supervisor refuses as no_guideline_evidence.
        return {**update, "retrieval": None, "handoffs": [record(None, None)]}

    retriever = ctx.retriever()
    if retriever is None:
        failure = RoutingReasonCode.RETRIEVAL_UNAVAILABLE
        return {**update, "failure": failure, "handoffs": [record(None, failure)]}

    request = EvidenceRetrieverInput(
        concepts=concepts, top_k=ANSWER_CHUNKS, correlation_id=ctx.correlation_id
    )
    try:
        output = await asyncio.to_thread(run_evidence_retriever, request, retriever)
    except Exception as error:  # noqa: BLE001 - a tool failure is a typed refusal
        log_event(
            "w2_retrieval_failed",
            event_data={"correlation_id": ctx.correlation_id, "error_type": type(error).__name__},
            use_case=_USE_CASE,
            patient_session_key=ctx.patient_session_key,
        )
        failure = RoutingReasonCode.RETRIEVAL_UNAVAILABLE
        return {**update, "failure": failure, "handoffs": [record(None, failure)]}
    return {**update, "retrieval": output, "handoffs": [record(output, None)]}


async def _read_chart(
    ctx: SupervisorContext, intent: QuestionIntent
) -> tuple[dict[FhirResourceType, list[Any]], list[Observation], LabScope | None, int]:
    """
    Week 1's readers and lab narrowing, under the clinician's own token —
    and then every critical result the cap dropped put back (H6). Returns
    the chart, every Observation retrieved, the lab scope, and how many
    critical results were restored.
    """

    async def none() -> None:
        return None

    key = ctx.patient_session_key
    base, observations, encounters = await asyncio.gather(
        fetch_bounded_set(ctx.token, ctx.patient_id, key),
        fhir_client.get_observations(
            ctx.token, ctx.patient_id, use_case=_USE_CASE, patient_session_key=key
        )
        if intent.wants_labs
        else none(),
        fhir_client.get_encounters(
            ctx.token, ctx.patient_id, ENCOUNTER_COUNT, use_case=_USE_CASE, patient_session_key=key
        )
        if intent.wants_visits
        else none(),
    )
    chart: dict[FhirResourceType, list[Any]] = dict(base)
    scope: LabScope | None = None
    restored = 0
    if observations is not None:
        context_ids = frozenset(
            claim.source_id
            for turn in ctx.snapshot.context
            for claim in turn.claims
            if claim.resource_type is FhirResourceType.OBSERVATION
        )
        narrowed, scope = narrow_observations(observations, intent.lab_codes, context_ids)
        narrowed, restored = keep_critical(narrowed, observations)
        if restored:
            scope = dataclasses.replace(
                scope, considered=len(narrowed), omitted=max(scope.omitted - restored, 0)
            )
        chart[FhirResourceType.OBSERVATION] = narrowed
    if encounters is not None:
        chart[FhirResourceType.ENCOUNTER] = encounters
    return chart, list(observations or []), scope, restored


async def _answer(state: SupervisorState, runtime: Runtime[SupervisorContext]) -> dict[str, Any]:
    ctx = runtime.context
    intent = state["intent"]
    visited = _visited(Node.ANSWER)
    try:
        chart, all_observations, _, restored = await _read_chart(ctx, intent)
    except (FhirClientError, httpx.TransportError) as error:
        log_event(
            "w2_chart_read_failed",
            event_data={"correlation_id": ctx.correlation_id, "error_type": type(error).__name__},
            use_case=_USE_CASE,
            patient_session_key=ctx.patient_session_key,
        )
        return {"failure": RoutingReasonCode.CHART_UNAVAILABLE, **visited}

    reports = _ready_reports(ctx, state)
    facts: dict[str, list[DocumentFact]] = {}
    for document_id, report in reports.items():
        assert report.output.payload is not None  # READY means complete
        facts[document_id] = document_facts(report.output.payload)
    retrieval = state.get("retrieval")
    chunks = list(retrieval.chunks) if retrieval is not None else []
    # D50: which lab rows the drafter is offered, by rule. The verifier and
    # the critical scan below still see every located fact.
    scope = scope_answer(ctx.question, reports, facts)

    output_limited = False
    try:
        draft = await ctx.drafter(
            chart,
            scope.offered,
            chunks,
            ctx.question,
            ctx.snapshot.context,
            patient_session_key=ctx.patient_session_key,
            withheld_rows=len(scope.summarised) + len(scope.capped),
        )
    except AnswerTruncatedError as error:
        # The output bound bound. Nothing drafted is used; what code states
        # on its own — critical values, the scope's statements, unsupported
        # fields — still renders, and the answer says why it is short.
        log_event(
            "w2_draft_output_limit",
            event_data={
                "correlation_id": ctx.correlation_id,
                "output_tokens": error.usage.output_tokens,
            },
            use_case=_USE_CASE,
            patient_session_key=ctx.patient_session_key,
        )
        draft = AnswerDraft([], [], [], Coverage.NONE, [], usage=error.usage)
        output_limited = True
    except (ClaimDraftingError, anthropic.APIError) as error:
        log_event(
            "w2_drafting_failed",
            event_data={"correlation_id": ctx.correlation_id, "error_type": type(error).__name__},
            use_case=_USE_CASE,
            patient_session_key=ctx.patient_session_key,
        )
        return {"failure": RoutingReasonCode.MODEL_UNAVAILABLE, **visited}

    in_turn = {
        document_id: DocumentInTurn(
            document_id=document_id,
            facts={fact.field_path: fact for fact in facts[document_id]},
            all_field_paths=frozenset(field.field_path for field in report.fields),
        )
        for document_id, report in reports.items()
    }
    results = verify_answer(
        draft.chart_claims,
        draft.document_claims,
        draft.guideline_claims,
        chart=chart,
        documents=in_turn,
        chunks={chunk.chunk_id: chunk for chunk in chunks},
        use_case=_USE_CASE,
        patient_session_key=ctx.patient_session_key,
    )
    criticals = critical_findings(ctx, chart, all_observations, reports, in_turn)
    outcome = render_answer(
        ctx=ctx,
        draft=draft,
        results=results,
        chart=chart,
        all_observations=all_observations,
        reports=reports,
        retrieval=retrieval,
        criticals=criticals,
        scope=scope_findings(scope, in_turn, whole_report=intent.wants_labs),
        output_limited=output_limited,
    )
    outcome.critical_restored = restored
    outcome.scope = scope.counts()
    outcome.output_limited = output_limited
    return {"turn": outcome, **visited}


async def _refuse(state: SupervisorState, runtime: Runtime[SupervisorContext]) -> dict[str, Any]:
    ctx = runtime.context
    reason = state["decisions"][-1].reason_code
    failure_reasons = [
        output.failure_reason.value
        for output in state.get("extractor_outputs", {}).values()
        if output.failure_reason is not None
    ]
    if reason is RoutingReasonCode.EXTRACTION_FAILED and not failure_reasons:
        failure_reasons = [
            output.failure_reason.value
            for document_id in ctx.document_ids
            if (output := ctx.documents.status(document_id)) is not None
            and output.failure_reason is not None
            and ctx.documents.lookup(document_id, ctx.patient_id) is not None
        ]
    return {
        "turn": TurnOutcome(
            status=AskStatus.REFUSED,
            message=render_refusal(ctx.snapshot.turn, reason, failure_reasons),
        ),
        **_visited(Node.REFUSE),
    }


# --------------------------------------------------------------------------
# rendering — plain code
# --------------------------------------------------------------------------

REFUSAL_WORDING: dict[RoutingReasonCode, str] = {
    RoutingReasonCode.OUT_OF_SCOPE_RESOURCE: week1.OUT_OF_SCOPE_HEADLINE
    + " The co-pilot reads problems, medications, allergies, lab results and visit dates "
    "for the one patient whose chart is open, uploaded documents, and published guidance.",
    RoutingReasonCode.UNVERIFIABLE_JUDGMENT: (
        "I can't answer that: it asks for a dose, a diagnosis, a judgment or a trend, and the "
        "co-pilot only states what a single source says, checked against that source."
    ),
    RoutingReasonCode.DOCUMENT_STILL_PROCESSING: (
        "That document is still being read — extraction starts when a document is uploaded "
        "and has not finished. Nothing from it is stated until it has; ask again shortly."
    ),
    RoutingReasonCode.DOCUMENT_NOT_FOUND: (
        "The co-pilot holds no uploaded document with that id for this patient's chart, so "
        "nothing was answered from it."
    ),
    RoutingReasonCode.EXTRACTION_FAILED: (
        "That document could not be read, so nothing from it is stated."
    ),
    RoutingReasonCode.DOCUMENT_IDENTITY_MISMATCH: (
        "The name or date of birth printed on that document does not match this chart, so "
        "nothing from it is stated. Check that it was uploaded to the right patient."
    ),
    RoutingReasonCode.NO_GUIDELINE_EVIDENCE: (
        "No passage in the co-pilot's guideline corpus was found for that question, so no "
        "guidance is quoted."
    ),
    RoutingReasonCode.RETRIEVAL_UNAVAILABLE: (
        "Guideline search is unavailable right now, so no guidance is quoted."
    ),
    RoutingReasonCode.CHART_UNAVAILABLE: (
        "OpenEMR's chart could not be read for this question, so nothing is stated."
    ),
    RoutingReasonCode.MODEL_UNAVAILABLE: (
        "The answer could not be drafted: the language model call failed. Nothing is stated."
    ),
    RoutingReasonCode.VISIT_LIMIT_EXCEEDED: (
        "The question could not be completed within the co-pilot's step limit, so nothing "
        "is stated."
    ),
}

REFUSAL_FOOTER = "Nothing about the patient is stated in this answer."

GUIDELINE_HEADING = (
    "From published guidance — general, not about this patient; quoted verbatim, and the "
    "publisher does not endorse this answer:"
)

LIMITATION_WORDING: dict[AnswerLimitation, str] = {
    AnswerLimitation.GUIDELINE_APPLICABILITY_NOT_VERIFIED: (
        "The co-pilot checked that each quoted passage says what is quoted. It did not, and "
        "cannot, check that the guidance applies to this patient."
    ),
    AnswerLimitation.UNSUPPORTED_DOCUMENT_VALUES: (
        "Some values on the uploaded document could not be located on the page; they are "
        "listed as unsupported and are not stated as fact."
    ),
    AnswerLimitation.UNVERIFIED_CLAIMS: (
        "Items marked UNVERIFIED did not match their source and are not stated as fact."
    ),
    AnswerLimitation.RERANK_SKIPPED: (
        "Guideline passages are in search order; the reranker did not run for this question."
    ),
    AnswerLimitation.DATED_GUIDELINE_SOURCE: (
        "At least one quoted guideline source is flagged as dated; newer guidance may differ."
    ),
    AnswerLimitation.ANSWER_SCOPED: SCOPE_RULE,
    AnswerLimitation.ANSWER_CAPPED: SCOPE_RULE,
    AnswerLimitation.OUTPUT_LIMIT_REACHED: OUTPUT_LIMIT_WORDING,
}

_DOCUMENT_WHY = {
    "document_not_in_turn": "not a document resolved for this question",
    "field_not_in_document": "no such field on the document",
    "field_not_located": "the value could not be located on the page",
    "value_mismatch": "does not match what the page says",
}
_GUIDELINE_WHY = {
    "chunk_not_retrieved": "not a passage retrieved for this question",
    "excerpt_too_short": "too short to be a quotation",
    "excerpt_not_in_chunk": "not a verbatim quotation of the passage",
}


CRITICAL_HEADING = (
    "CRITICAL VALUES — beyond the co-pilot's critical-value table; review before anything else:"
)
CRITICAL_FOOTNOTE = (
    "(Flagged by rule against the co-pilot's configured critical-value table, not this "
    "laboratory's reference ranges and not a clinical judgment.)"
)


@dataclass
class CriticalBlock:
    values: list[CriticalValue] = field(default_factory=list)
    claims: list[PatientFactClaim] = field(default_factory=list)


def _number_text(value: object) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


def critical_findings(
    ctx: SupervisorContext,
    chart: dict[FhirResourceType, list[Any]],
    all_observations: list[Observation],
    reports: dict[str, ExtractionReport],
    in_turn: dict[str, DocumentInTurn],
) -> CriticalBlock:
    """
    Every critical value in this turn's sources (H6), whatever was asked.
    Each stated one passes the same verifier as any claim; its sentence is
    built from the source, never from the model.
    """
    block = CriticalBlock()
    observations = chart.get(FhirResourceType.OBSERVATION, [])
    for observation in observations:
        hit = observation_direction(observation)
        if hit is None:
            continue
        row, direction = hit
        claims = [
            Claim(
                source_id=observation.source_id,
                field=name,
                value=getattr(observation, name),
                resource_type=FhirResourceType.OBSERVATION,
            )
            for name in ("value", "unit")
        ]
        results = verify_claims(
            claims, chart, use_case=_USE_CASE, patient_session_key=ctx.patient_session_key
        )
        if not all(result.passed for result in results):
            continue  # unreachable for a record read this turn; never stated if it happens
        codes = same_test_codes(observation.loinc_code)
        same_test = [o.effective_date for o in all_observations if o.loinc_code in codes]
        tag = week1._recency_tag(
            week1._later_count(observation.effective_date, same_test),
            len(same_test),
            "results for this test",
        )
        unit = observation.unit or ""
        statement = (
            f"Chart lab result {week1._lab_name(observation)}: "
            f"{_number_text(observation.value)} {unit} — {direction.value}, beyond the critical "
            f"limit of {limit(row, direction)} {unit}{tag}"
        )
        citations = [citation_from_claim(claim) for claim in claims]
        block.claims.append(PatientFactClaim(statement=statement, citations=citations))
        block.values.append(
            CriticalValue(
                source_type=SourceType.CHART,
                source_id=observation.source_id,
                analyte=row.analyte,
                direction=direction.value,
                critical_limit=limit(row, direction),
                unit=unit,
                stated=True,
                citations=citations,
                line=statement,
            )
        )

    for document_id, report in reports.items():
        payload = report.output.payload
        if not isinstance(payload, LabPdfExtraction):
            continue
        for found in document_criticals(payload):
            result, row, direction = found.result, found.row, found.direction
            name = result.test_name.as_presented_fact()
            test = name.value if name is not None else result.test_key
            unit = found.unit
            if found.located:
                verified = [
                    verify_document_fact(
                        DraftedDocumentFact(
                            document_id, field.citation.field_or_chunk_id, fact.quote
                        ),
                        in_turn,
                    )
                    for field in (result.value, result.unit)
                    if field.citation is not None
                    and (fact := in_turn[document_id].facts.get(field.citation.field_or_chunk_id))
                    is not None
                ]
                if len(verified) != 2 or not all(v.passed and v.citation for v in verified):
                    continue
                value_quote, unit = verified[0].fact.quote, verified[1].fact.quote  # type: ignore[union-attr]
                statement = (
                    f"Lab report, page {found.page}: {test}: {value_quote} {unit} — "
                    f"{direction.value}, beyond the critical limit of "
                    f"{limit(row, direction)} {unit}"
                )
                citations = [v.citation for v in verified if v.citation is not None]
                block.claims.append(PatientFactClaim(statement=statement, citations=citations))
            else:
                where = f"page {found.page}" if found.page else "the uploaded lab report"
                statement = (
                    f"POSSIBLE CRITICAL VALUE — {test} on {where} was read as {direction.value} "
                    "beyond the critical limit, but could not be located on the page, so the "
                    "value is not stated. Open the document and check it."
                )
                citations = []
            block.values.append(
                CriticalValue(
                    source_type=SourceType.DOCUMENT,
                    source_id=document_id,
                    analyte=row.analyte,
                    direction=direction.value,
                    critical_limit=limit(row, direction),
                    unit=unit,
                    stated=found.located,
                    page=found.page,
                    citations=citations,
                    line=statement,
                )
            )
    return block


@dataclass
class ScopeBlock:
    """What the scope rule left out, as cited statements (D50)."""

    lines: list[str] = field(default_factory=list)
    claims: list[PatientFactClaim] = field(default_factory=list)
    summarised: bool = False
    capped: bool = False
    #: A lab question that names no test or panel ("what were the results"):
    #: counting rows instead of listing them answers it only in part.
    whole_report: bool = False


def scope_findings(
    scope: AnswerScope, in_turn: dict[str, DocumentInTurn], *, whole_report: bool
) -> ScopeBlock:
    """
    One statement per document for the rows counted, one for the rows the
    cap left out. Each cites located facts, passed through the verifier like
    any other claim; a statement whose citations do not all pass is still
    shown — it discloses an omission — but is not listed as a verified fact.
    """
    block = ScopeBlock(
        summarised=bool(scope.summarised),
        capped=bool(scope.capped),
        whole_report=whole_report and scope.binds and not scope.question_names_rows,
    )

    def per_document(rows: list[ScopedRow]) -> dict[str, list[ScopedRow]]:
        grouped: dict[str, list[ScopedRow]] = {}
        for row in rows:
            grouped.setdefault(row.document_id, []).append(row)
        return grouped

    for build, rows in ((summary_statement, scope.summarised), (capped_statement, scope.capped)):
        for document_id, document_rows in per_document(rows).items():
            built = build(document_rows)
            if built is None:
                continue
            statement, citations = built
            block.lines.append(statement)
            facts = in_turn[document_id].facts
            verified = [
                verify_document_fact(
                    DraftedDocumentFact(document_id, c.field_or_chunk_id, facts[path].quote),
                    in_turn,
                )
                for c in citations
                if (path := c.field_or_chunk_id) in facts
            ]
            if len(verified) == len(citations) and all(v.passed and v.citation for v in verified):
                block.claims.append(
                    PatientFactClaim(
                        statement=statement,
                        citations=[v.citation for v in verified if v.citation is not None],
                    )
                )
    return block


def render_refusal(turn: int, reason: RoutingReasonCode, failure_reasons: list[str]) -> str:
    lines = [f"Answer (turn {turn})", "", REFUSAL_WORDING[reason]]
    if failure_reasons:
        # Closed-set codes (ExtractionFailureReason), never exception text.
        lines.append(
            f"Reason recorded by the extractor: {', '.join(sorted(set(failure_reasons)))}."
        )
    lines.extend(["", REFUSAL_FOOTER])
    return "\n".join(lines)


def _guideline_statement(chunk: EvidenceChunk, quote: str) -> str:
    source = chunk.source
    who = source.publisher or source.title
    return f'{who}, "{source.title}" — {source.section}: "{quote}"'


def _unverified_line(result: W2Verification) -> str:
    if result.source_type is SourceType.DOCUMENT:
        why = _DOCUMENT_WHY.get(result.reason or "", "did not verify")
        return f'  - UNVERIFIED: document value "{result.drafted_value}" ({why})'
    why = _GUIDELINE_WHY.get(result.reason or "", "did not verify")
    title = result.chunk.source.title if result.chunk is not None else "an unretrieved source"
    # The drafted text is not shown: a non-verbatim "quotation" attributed
    # to a publisher is exactly what D26 forbids presenting.
    return f'  - UNVERIFIED: a quotation attributed to "{title}" ({why}); not shown'


def _lab_flag_views(
    reports: dict[str, ExtractionReport], cited_paths: set[tuple[str, str]]
) -> list[ReportedFlagView]:
    """D20's flag view for every lab row the answer cites."""
    views: list[ReportedFlagView] = []
    for document_id, report in reports.items():
        payload = report.output.payload
        if not isinstance(payload, LabPdfExtraction):
            continue
        for result in payload.results:
            paths = {
                field.citation.field_or_chunk_id
                for field in (result.test_name, result.value, result.reported_flag)
                if field.citation is not None
            }
            if not any((document_id, path) in cited_paths for path in paths):
                continue
            cross_check = result.cross_check_flag()
            name = result.test_name.as_presented_fact()
            if cross_check is None or name is None:
                continue
            views.append(
                ReportedFlagView(
                    test_name=name.value,
                    panel=result.panel,
                    cross_check=cross_check,
                    citation=result.reported_flag.citation,
                )
            )
    return views


def _answer_status(draft: AnswerDraft, verified: int) -> AskStatus:
    if verified == 0:
        return AskStatus.NOT_IN_RECORD
    if draft.coverage is Coverage.FULL and not draft.gaps:
        return AskStatus.ANSWERED
    return AskStatus.PARTIALLY_ANSWERED


def render_answer(
    *,
    ctx: SupervisorContext,
    draft: AnswerDraft,
    results: list[W2Verification],
    chart: dict[FhirResourceType, list[Any]],
    all_observations: list[Observation],
    reports: dict[str, ExtractionReport],
    retrieval: EvidenceRetrieverOutput | None,
    criticals: CriticalBlock | None = None,
    scope: ScopeBlock | None = None,
    output_limited: bool = False,
) -> TurnOutcome:
    """
    Every drafted claim renders: passed ones as facts or evidence, failed
    ones as UNVERIFIED lines, never dropped. Sentences come from the
    sources (facts.py, Week 1's claim lines), never from the model.
    """
    patient_facts: list[PatientFactClaim] = []
    fact_lines: list[str] = []
    for result in results:
        if result.source_type is SourceType.CHART and result.passed:
            assert result.chart is not None and result.citation is not None
            line = week1._turn_claim_line(result.chart, chart, all_observations)
            statement = line.removeprefix("  - ")
            patient_facts.append(PatientFactClaim(statement=statement, citations=[result.citation]))
            fact_lines.append(line)

    groups: dict[tuple[str, str], list[DocumentFact]] = {}
    for result in results:
        if result.source_type is SourceType.DOCUMENT and result.passed:
            assert result.fact is not None
            groups.setdefault(result.fact.group_key, [])
            if result.fact not in groups[result.fact.group_key]:
                groups[result.fact.group_key].append(result.fact)
    for group in groups.values():
        statement = group_statement(group)
        patient_facts.append(
            PatientFactClaim(statement=statement, citations=[fact.citation for fact in group])
        )
        fact_lines.append(f"  - {statement}")

    guideline_claims: list[GuidelineClaim] = []
    guideline_lines: list[str] = []
    for result in results:
        if result.source_type is SourceType.GUIDELINE and result.passed:
            assert result.chunk is not None and result.citation is not None
            statement = _guideline_statement(result.chunk, str(result.citation.quote_or_value))
            guideline_claims.append(
                GuidelineClaim(statement=statement, citations=[result.citation])
            )
            guideline_lines.append(f"  - {statement}")

    failed = [result for result in results if not result.passed]
    unverified: list[UnverifiedClaim] = []
    unverified_lines: list[str] = []
    for result in failed:
        if result.source_type is SourceType.CHART:
            assert result.chart is not None
            line = week1._turn_claim_line(result.chart, chart, all_observations)
        else:
            line = _unverified_line(result)
        unverified_lines.append(line)
        unverified.append(
            UnverifiedClaim(
                source_type=result.source_type,
                source_id=result.source_id,
                field_or_chunk_id=result.field_or_chunk_id,
                reason=result.reason or "unverified",
                line=line.removeprefix("  - "),
            )
        )

    unsupported: list[UnsupportedField] = [
        field for report in reports.values() for field in report.unsupported
    ]
    discrepancies: list[Discrepancy] = [
        discrepancy
        for report in reports.values()
        if isinstance(report.output.payload, IntakeFormExtraction) and report.discrepancies
        for discrepancy in report.discrepancies.discrepancies
    ]

    chunks = list(retrieval.chunks) if retrieval is not None else []
    limitations: list[AnswerLimitation] = []
    if guideline_claims:
        limitations.append(AnswerLimitation.GUIDELINE_APPLICABILITY_NOT_VERIFIED)
    if unsupported:
        limitations.append(AnswerLimitation.UNSUPPORTED_DOCUMENT_VALUES)
    if failed:
        limitations.append(AnswerLimitation.UNVERIFIED_CLAIMS)
    if retrieval is not None and chunks and not retrieval.reranked:
        limitations.append(AnswerLimitation.RERANK_SKIPPED)
    if any(
        parse_source_version(result.chunk.source.version)[1]
        for result in results
        if result.passed and result.chunk is not None
    ):
        limitations.append(AnswerLimitation.DATED_GUIDELINE_SOURCE)
    scope = scope or ScopeBlock()
    if scope.summarised:
        limitations.append(AnswerLimitation.ANSWER_SCOPED)
    if scope.capped:
        limitations.append(AnswerLimitation.ANSWER_CAPPED)
    if output_limited:
        limitations.append(AnswerLimitation.OUTPUT_LIMIT_REACHED)

    verified = sum(1 for result in results if result.passed)
    status = _answer_status(draft, verified)
    criticals = criticals or CriticalBlock()
    # D50: an answer the scope or the output bound cut short says so in its
    # status. The scope's count answers a whole-report question in part; for
    # a question about something else it is a disclosure, not an answer.
    stated = verified + len(scope.claims) + sum(1 for v in criticals.values if v.stated)
    if output_limited or ((scope.capped or scope.whole_report) and stated):
        status = AskStatus.PARTIALLY_ANSWERED

    lines = [f"Answer (turn {ctx.snapshot.turn})", ""]
    if criticals.values:
        lines.extend([CRITICAL_HEADING, *(f"  - {v.line}" for v in criticals.values)])
        lines.extend([CRITICAL_FOOTNOTE, ""])
    if status is AskStatus.NOT_IN_RECORD:
        lines.append("Nothing in the sources read for this question answers it.")
    if fact_lines:
        lines.extend(["From this patient's record and uploaded documents:", *fact_lines])
    if scope.lines:
        lines.extend(["", SCOPE_RULE, *(f"  - {line}" for line in scope.lines)])
    if discrepancies:
        lines.extend(
            [
                "",
                "Intake form and chart differ — for a clinician to resolve; nothing was merged:",
                *(
                    f"  - {d.category.value} {d.kind.value}: {d.subject}"
                    + (f" (form: {d.document_value})" if d.document_value else "")
                    + (f" (chart: {d.chart_value})" if d.chart_value else "")
                    for d in discrepancies
                ),
            ]
        )
    if guideline_lines:
        lines.extend(["", GUIDELINE_HEADING, *guideline_lines])
    if unverified_lines:
        lines.extend(["", "Not verified against their source:", *unverified_lines])
    if unsupported:
        lines.extend(
            [
                "",
                f"{len(unsupported)} value(s) on the uploaded document(s) are unsupported: "
                "not located on the page, missing, or illegible. They are listed, not stated.",
            ]
        )
    if draft.gaps and status is not AskStatus.ANSWERED:
        lines.extend(["", "Not in the sources read for this question:"])
        lines.extend(f"  - {week1._gap_sentence(gap, set(chart))}" for gap in draft.gaps)
    # The unsupported count and the scope block already say what these say.
    said = {
        AnswerLimitation.UNSUPPORTED_DOCUMENT_VALUES,
        AnswerLimitation.ANSWER_SCOPED,
        AnswerLimitation.ANSWER_CAPPED,
    }
    worded = [x for x in limitations if x not in said]
    if worded:
        lines.append("")
        lines.extend(LIMITATION_WORDING[limitation] for limitation in worded)
    lines.extend(
        [
            "",
            f"Sources read for this question: the chart ({', '.join(t.value for t in chart)}), "
            f"{len(reports)} uploaded document(s), {len(chunks)} guideline passage(s).",
        ]
    )
    message = "\n".join(lines)

    cited = {
        (result.source_id, result.field_or_chunk_id)
        for result in results
        if result.passed and result.source_type is SourceType.DOCUMENT
    }
    # Stated critical values are verified patient facts in their own right;
    # one the draft already cites in full is not listed twice.
    cited_already = {
        (c.source_id, c.field_or_chunk_id) for claim in patient_facts for c in claim.citations
    }
    critical_claims = [
        claim
        for claim in criticals.claims
        if not all((c.source_id, c.field_or_chunk_id) in cited_already for c in claim.citations)
    ]
    answer = FinalAnswer(
        answer=message,
        patient_facts=[*critical_claims, *patient_facts, *scope.claims],
        guideline_evidence=guideline_claims,
        unsupported=unsupported,
        correlation_id=ctx.correlation_id,
    )
    view = AnswerView(
        answer=answer,
        documents=[
            ViewerDocument(document=report.output.payload.document, pages=list(stored.pages))
            for document_id, report in reports.items()
            if report.output.payload is not None
            and (stored := ctx.documents.lookup(document_id, ctx.patient_id)) is not None
        ],
        evidence_chunks=chunks,
        lab_flags=_lab_flag_views(reports, cited),
    )
    return TurnOutcome(
        status=status,
        message=message,
        answer_view=view,
        unverified=unverified,
        discrepancies=discrepancies,
        limitations=limitations,
        gaps=list(draft.gaps),
        chart_results=[result.chart for result in results if result.chart is not None],
        verification=results,
        draft=draft,
        critical_values=criticals.values,
    )


# --------------------------------------------------------------------------
# the graph
# --------------------------------------------------------------------------


def _after_supervisor(state: SupervisorState) -> str:
    return {
        Route.EXTRACT: Node.INTAKE_EXTRACTOR.value,
        Route.RETRIEVE: Node.EVIDENCE_RETRIEVER.value,
        Route.ANSWER: Node.ANSWER.value,
        Route.REFUSE: Node.REFUSE.value,
    }[state["route"]]


def _after_answer(state: SupervisorState) -> str:
    return Node.SUPERVISOR.value if state.get("failure") is not None else END


def _build_graph() -> Any:
    graph = StateGraph(SupervisorState, context_schema=SupervisorContext)
    # Week 1's wrappers are annotated with the brief's state type; LangGraph
    # infers a node's input schema from that, so it is given explicitly
    # (the same fix app/graph/intake_extractor.py needed).
    for node, run in (
        (Node.SUPERVISOR, _supervisor),
        (Node.INTAKE_EXTRACTOR, _intake_extractor),
        (Node.EVIDENCE_RETRIEVER, _evidence_retriever),
        (Node.ANSWER, _answer),
        (Node.REFUSE, _refuse),
    ):
        # Named pipeline: every node run is also a step_event, so the ask
        # path is on the same by-step view and trace as upload and brief.
        graph.add_node(
            node.value,
            _untraced(_timed(node.value, run, Pipeline.SUPERVISOR)),
            input_schema=SupervisorState,
        )
    graph.add_edge(START, Node.SUPERVISOR.value)
    graph.add_conditional_edges(
        Node.SUPERVISOR.value,
        _after_supervisor,
        [node.value for node in Node if node is not Node.SUPERVISOR],
    )
    graph.add_edge(Node.INTAKE_EXTRACTOR.value, Node.SUPERVISOR.value)
    graph.add_edge(Node.EVIDENCE_RETRIEVER.value, Node.SUPERVISOR.value)
    graph.add_conditional_edges(Node.ANSWER.value, _after_answer, [Node.SUPERVISOR.value, END])
    graph.add_edge(Node.REFUSE.value, END)
    return graph.compile()


SUPERVISOR_GRAPH = UntracedGraph(_build_graph())


# --------------------------------------------------------------------------
# the retriever the ask path uses
# --------------------------------------------------------------------------

_RETRIEVER: dict[str, GuidelineRetriever | None] = {}


def set_retriever(retriever: GuidelineRetriever | None) -> None:
    """Installed by app/main.py's lifespan when retrieval is preloaded."""
    _RETRIEVER["default"] = retriever


def default_retriever() -> GuidelineRetriever | None:
    """
    The preloaded retriever, or one loaded on first use. A load failure is
    remembered as None: a question then gets `retrieval_unavailable`, and
    /ready's log line from the preload says why.
    """
    if "default" not in _RETRIEVER:
        from app.retrieval.preload import preload_retrieval

        try:
            _RETRIEVER["default"] = preload_retrieval()
        except Exception as error:  # noqa: BLE001 - a missing model must not fail a turn
            log_event(
                "retrieval_preload_failed_event", event_data={"error_type": type(error).__name__}
            )
            _RETRIEVER["default"] = None
    return _RETRIEVER["default"]


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


async def run_ask_turn(
    token: str,
    user: str,
    patient_id: str,
    session_id: str,
    question: str,
    document_ids: list[str] | tuple[str, ...] = (),
    *,
    store: ConversationStore | None = None,
    documents: DocumentStore | None = None,
    retriever: RetrieverProvider | None = None,
    drafter: Drafter | None = None,
) -> AskResponse:
    """
    One Week 2 turn for (user, patient_id, session_id).

    The Patient read is the authorization step, first, as in Week 1: an
    unknown patient raises PatientNotFoundError and a rejected token raises
    FhirAuthError, both before any state is touched — those are HTTP errors,
    not clinical outcomes. Everything after it returns a typed AskResponse,
    refusals and tool or model failures included.
    """
    for name, value in (
        ("token", token),
        ("user", user),
        ("patient_id", patient_id),
        ("session_id", session_id),
        ("question", question),
    ):
        if not value.strip():
            raise ValueError(f"{name} must be a non-empty string")

    store = week1.STORE if store is None else store
    key = week1.patient_session_key(user, patient_id, session_id)
    correlation_id = get_correlation_id() or str(uuid.uuid4())
    started = time.perf_counter()

    with correlation_scope(correlation_id):
        try:
            await fhir_client.ensure_patient_exists(token, patient_id)
        except PatientNotFoundError:
            dropped = store.discard_if_bound_elsewhere(user, session_id, patient_id)
            if dropped is not None:
                week1._log_invalidation(dropped, user, session_id, key)
            raise

        snapshot = store.open(user, session_id, patient_id)
        if snapshot.invalidation is not None:
            week1._log_invalidation(snapshot.invalidation, user, session_id, key)

        context = SupervisorContext(
            token=token,
            patient_id=patient_id,
            patient_session_key=key,
            question=question,
            document_ids=tuple(dict.fromkeys(document_ids)),
            snapshot=snapshot,
            correlation_id=correlation_id,
            documents=document_store.DOCUMENTS if documents is None else documents,
            retriever=default_retriever if retriever is None else retriever,
            drafter=draft_answer if drafter is None else drafter,
        )
        final = await SUPERVISOR_GRAPH.ainvoke({"node_seconds": {}}, context=context)

        outcome: TurnOutcome = final["turn"]
        decisions: list[RoutingDecision] = final["decisions"]
        state_saved = store.commit(snapshot, outcome.chart_results)
        node_ms = {name: round(seconds * 1000) for name, seconds in final["node_seconds"].items()}
        total_ms = round((time.perf_counter() - started) * 1000)
        _log_turn(context, final, outcome, node_ms, total_ms, state_saved)

    return AskResponse(
        status=outcome.status,
        reason_code=decisions[-1].reason_code,
        message=outcome.message,
        answer_view=outcome.answer_view,
        unverified=outcome.unverified,
        critical_values=outcome.critical_values,
        discrepancies=outcome.discrepancies,
        limitations=outcome.limitations,
        gaps=outcome.gaps,
        routing=decisions,
        handoffs=final.get("handoffs", []),
        conversation=AskConversation(
            turn=snapshot.turn,
            transition=snapshot.transition,
            context_turns=len(snapshot.context),
            state_saved=state_saved,
        ),
        patient_session_key=key,
        correlation_id=correlation_id,
        node_ms=node_ms,
        total_ms=total_ms,
    )


def _log_turn(
    ctx: SupervisorContext,
    final: dict[str, Any],
    outcome: TurnOutcome,
    node_ms: dict[str, int],
    total_ms: int,
    state_saved: bool,
) -> None:
    """The per-turn observability line. Ids, codes and numbers only (invariant 1)."""
    by_type: dict[str, dict[str, int]] = {
        kind.value: {"drafted": 0, "verified": 0} for kind in SourceType
    }
    for result in outcome.verification:
        by_type[result.source_type.value]["drafted"] += 1
        by_type[result.source_type.value]["verified"] += int(result.passed)
    retrieval: EvidenceRetrieverOutput | None = final.get("retrieval")
    documents = []
    for document_id in ctx.document_ids:
        status = ctx.documents.status(document_id)
        report = ctx.documents.report(document_id)
        bound = ctx.documents.lookup(document_id, ctx.patient_id) is not None
        documents.append(
            {
                "document_id": document_id if bound else None,
                "status": status.status.value if status is not None and bound else "not_found",
                "mean_confidence": report.mean_confidence if report is not None and bound else None,
                "unsupported": len(report.unsupported) if report is not None and bound else None,
            }
        )
    usage = outcome.draft.usage if outcome.draft is not None else None
    log_event(
        TURN_EVENT,
        event_data={
            "correlation_id": ctx.correlation_id,
            "turn": ctx.snapshot.turn,
            "transition": ctx.snapshot.transition.value,
            "status": outcome.status.value,
            "tool_sequence": final.get("sequence", []),
            "routing": [
                {"route": d.decision.value, "reason_code": d.reason_code.value}
                for d in final["decisions"]
            ],
            "node_ms": node_ms,
            "total_ms": total_ms,
            "documents": documents,
            "retrieval_hits": len(retrieval.chunks) if retrieval is not None else 0,
            "reranked": retrieval.reranked if retrieval is not None else None,
            "guideline_ids": sorted({c.source.guideline_id for c in retrieval.chunks})
            if retrieval is not None
            else [],
            "claims": by_type,
            "limitations": [limitation.value for limitation in outcome.limitations],
            # Counts only: which analyte crossed which way is chart content.
            "critical_values": sum(1 for v in outcome.critical_values if v.stated),
            "possible_critical_values": sum(1 for v in outcome.critical_values if not v.stated),
            "critical_restored_past_cap": outcome.critical_restored,
            "answer_scope": outcome.scope,
            "output_limited": outcome.output_limited,
            "llm_input_tokens": usage.input_tokens if usage else 0,
            "llm_output_tokens": usage.output_tokens if usage else 0,
            "llm_cost_usd": usage.cost_usd if usage else 0.0,
            "state_saved": state_saved,
        },
        use_case=_USE_CASE,
        patient_session_key=ctx.patient_session_key,
    )
