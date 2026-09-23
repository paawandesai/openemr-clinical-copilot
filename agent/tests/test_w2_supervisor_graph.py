"""
The Week 2 supervisor graph end to end, offline (app/graph/supervisor.py).

Routing trajectories (EVAL_DESIGN §4: G1, G2, G5, G6), every failure mode the
work order names producing a typed response rather than an exception, the
verifier's placement between drafting and rendering, invariant 3's separate
lists, invariant 4's unsupported values, cross-patient invalidation shared
with Week 1's conversation store, and the observability events.

What is real: the graph, the routing, the extractor (text layer, locate,
checks) on a doc-factory PDF, the retriever (BM25 over the committed corpus,
fake embedder), the verifier, the rendering, the conversation store. What is
not: FHIR (patched readers), the vision call (ground-truth transcriber) and
the drafting call (scripted). See tests/w2_graph_support.py.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from structlog.testing import capture_logs

from app.conversation.state import ConversationStore
from app.fhir.exceptions import FhirServerError, PatientNotFoundError
from app.graph import supervisor
from app.models.claims import Claim
from app.models.conversation import ConversationTransition
from app.models.fhir_resources import FhirResourceType
from app.models.w2.ask import AnswerLimitation, AskResponse, AskStatus
from app.models.w2.documents import DocumentKind
from app.models.w2.source_types import SourceType
from app.models.w2.workers import ExtractionFailureReason, Route, RoutingReasonCode
from app.reasoning.draft_claims import ClaimDraftingError
from app.supervisor.routing import MAX_VISITS, Node
from app.verification.w2_checker import DraftedDocumentFact, DraftedGuideline
from app.workers.document_store import DocumentStore
from app.workers.intake_extractor.vision import VisionCallError, VisionResult
from tests.intake_extractor_support import (
    chart_for,
    ground_truth_vision,
    pdf_bytes,
)
from tests.w2_graph_support import (
    CHART_MARKER,
    INTAKE_SAMPLE,
    LAB_SAMPLE,
    MEDICATION_MARKER,
    OTHER_PATIENT,
    PATIENT,
    TOKEN,
    USER,
    FakeChart,
    ScriptedDrafter,
    draft_of,
    lab_documents,
    offline_retriever,
    stored_document,
)

SESSION = "session-w2-graph-0001"
GUIDELINE_QUESTION = (
    "What was her glucose on the uploaded lab report, and what does the USPSTF recommend "
    "for diabetes screening?"
)


@pytest.fixture
def chart(monkeypatch: pytest.MonkeyPatch) -> FakeChart:
    fake = FakeChart()
    fake.install(monkeypatch)
    return fake


def ask(
    question: str,
    document_ids: list[str] | None = None,
    *,
    documents: DocumentStore | None = None,
    drafter: ScriptedDrafter | None = None,
    store: ConversationStore | None = None,
    retriever: Any = None,
    patient: str = PATIENT,
    session: str = SESSION,
) -> AskResponse:
    return asyncio.run(
        supervisor.run_ask_turn(
            TOKEN,
            USER,
            patient,
            session,
            question,
            document_ids or [],
            store=store if store is not None else ConversationStore(),
            documents=documents or DocumentStore(),
            retriever=retriever or offline_retriever,
            drafter=drafter or ScriptedDrafter(),
        )
    )


def ask_with_document(
    question: str,
    *,
    sample: str = LAB_SAMPLE,
    drafter: ScriptedDrafter | None = None,
    chart_sample: str | None = None,
    retriever: Any = None,
) -> AskResponse:
    async def run() -> AskResponse:
        documents = DocumentStore(vision=ground_truth_vision(sample))
        document_id = await stored_document(documents, sample, chart_sample=chart_sample)
        return await supervisor.run_ask_turn(
            TOKEN,
            USER,
            PATIENT,
            SESSION,
            question,
            [document_id],
            store=ConversationStore(),
            documents=documents,
            retriever=retriever or offline_retriever,
            drafter=drafter or ScriptedDrafter(),
        )

    return asyncio.run(run())


def trajectory(response: AskResponse) -> list[tuple[str, str]]:
    return [(d.decision.value, d.reason_code.value) for d in response.routing]


# --------------------------------------------------------------------------
# routing trajectories
# --------------------------------------------------------------------------


def test_a_chart_lookup_never_calls_the_retriever(chart: FakeChart) -> None:
    """G2: a pure chart lookup answers without retrieval."""
    drafter = ScriptedDrafter()
    response = ask("What problems are on her list?", drafter=drafter)
    assert trajectory(response) == [("answer", "chart_only_question")]
    assert [h.node for h in response.handoffs] == []
    assert drafter.calls[0]["chunks"] == []
    assert response.status is AskStatus.ANSWERED
    assert CHART_MARKER in response.message


def test_a_document_and_guideline_question_cites_both_and_skips_extraction(
    chart: FakeChart,
) -> None:
    """G1 (extraction already complete: no extract node) and the PRD's core path."""
    response = ask_with_document(GUIDELINE_QUESTION)
    assert trajectory(response) == [
        ("retrieve", "evidence_needed_for_recommendation"),
        ("answer", "evidence_in_hand"),
    ]
    assert [h.node for h in response.handoffs] == ["evidence_retriever"]
    view = response.answer_view
    assert view is not None
    answer = view.answer
    assert answer.document_facts and answer.guideline_evidence and answer.chart_facts
    for claim in answer.document_facts:
        for citation in claim.citations:
            assert citation.source_type is SourceType.DOCUMENT
            assert citation.bbox is not None and citation.extraction_id is not None
    for claim in answer.guideline_evidence:
        citation = claim.citations[0]
        chunk = view.chunk(citation.field_or_chunk_id)
        assert chunk is not None and str(citation.quote_or_value) in chunk.text
    assert AnswerLimitation.GUIDELINE_APPLICABILITY_NOT_VERIFIED in response.limitations
    assert "cannot, check that the guidance applies to this patient" in response.message
    assert "not about this patient" in response.message


def test_a_pending_document_is_handed_to_the_extractor_once_then_refused(
    chart: FakeChart,
) -> None:
    """D7: never a wait, never a partial extraction; the handoff is inspectable."""
    release = asyncio.Event()

    async def slow_vision(kind: DocumentKind, pdf: bytes, pages: int) -> VisionResult:
        await release.wait()
        raise AssertionError("never released")

    drafter = ScriptedDrafter()

    async def run() -> AskResponse:
        documents = DocumentStore(vision=slow_vision)
        stored, _, _ = documents.accept(PATIENT, pdf_bytes(LAB_SAMPLE), DocumentKind.LAB_PDF)
        await asyncio.sleep(0)
        return await supervisor.run_ask_turn(
            TOKEN,
            USER,
            PATIENT,
            SESSION,
            "What was her glucose on this report?",
            [stored.document.document_id],
            store=ConversationStore(),
            documents=documents,
            retriever=offline_retriever,
            drafter=drafter,
        )

    response = asyncio.run(run())
    assert trajectory(response) == [
        ("extract", "document_not_yet_extracted"),
        ("refuse", "document_still_processing"),
    ]
    assert response.status is AskStatus.REFUSED
    assert response.handoffs[0].node == "intake_extractor"
    assert response.handoffs[0].extraction_statuses[0] in ("pending", "in_progress")
    assert response.answer_view is None
    assert drafter.calls == []
    assert chart.chart_reads == []


def test_an_unknown_document_is_refused_as_not_found(chart: FakeChart) -> None:
    response = ask("What does this report say?", ["f" * 128])
    assert trajectory(response) == [("refuse", "document_not_found")]


def test_another_patients_document_is_indistinguishable_from_an_unknown_one(
    chart: FakeChart,
) -> None:
    async def run() -> AskResponse:
        documents = lab_documents()
        document_id = await stored_document(documents, patient=OTHER_PATIENT)
        return await supervisor.run_ask_turn(
            TOKEN,
            USER,
            PATIENT,
            SESSION,
            "What does this report say?",
            [document_id],
            store=ConversationStore(),
            documents=documents,
            retriever=offline_retriever,
            drafter=ScriptedDrafter(),
        )

    response = asyncio.run(run())
    assert trajectory(response) == [("refuse", "document_not_found")]
    assert response.message == ask("What does this report say?", ["f" * 128]).message


def test_a_failed_extraction_is_refused_with_the_extractors_reason(chart: FakeChart) -> None:
    async def failing(kind: DocumentKind, pdf: bytes, pages: int) -> VisionResult:
        raise VisionCallError(ExtractionFailureReason.VISION_CALL_FAILED, "test")

    async def run() -> AskResponse:
        documents = DocumentStore(vision=failing)
        document_id = await stored_document(documents)
        return await supervisor.run_ask_turn(
            TOKEN,
            USER,
            PATIENT,
            SESSION,
            "What was her glucose?",
            [document_id],
            store=ConversationStore(),
            documents=documents,
            retriever=offline_retriever,
            drafter=ScriptedDrafter(),
        )

    response = asyncio.run(run())
    assert trajectory(response) == [("refuse", "extraction_failed")]
    assert "vision_call_failed" in response.message


def test_the_wrong_patient_flag_refuses_everything_from_the_document(chart: FakeChart) -> None:
    """H5: the extractor's identity check flags it; nothing from it is stated."""
    drafter = ScriptedDrafter()
    # The lab report checked against another sample's chart: a different person.
    response = ask_with_document(
        "What was her glucose on this report?", drafter=drafter, chart_sample=INTAKE_SAMPLE
    )
    assert trajectory(response) == [("refuse", "document_identity_mismatch")]
    assert drafter.calls == []


def test_a_guideline_question_with_nothing_to_search_on_is_refused(chart: FakeChart) -> None:
    response = ask("What do the guidelines say?")
    assert trajectory(response) == [
        ("retrieve", "guideline_question"),
        ("refuse", "no_guideline_evidence"),
    ]
    assert response.handoffs[0].concept_count == 0


def test_an_unavailable_retriever_is_a_typed_refusal(chart: FakeChart) -> None:
    response = ask("What does USPSTF say about statins?", retriever=lambda: None)
    assert trajectory(response) == [
        ("retrieve", "guideline_question"),
        ("refuse", "retrieval_unavailable"),
    ]


def test_a_retriever_that_raises_is_a_typed_refusal(chart: FakeChart) -> None:
    class Broken:
        def retrieve_with_outcome(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("index corrupted")

    response = ask("What does USPSTF say about statins?", retriever=lambda: Broken())
    assert trajectory(response)[-1] == ("refuse", "retrieval_unavailable")
    assert "index corrupted" not in response.message


def test_a_chart_failure_goes_back_to_the_supervisor_and_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeChart(fail_with=FhirServerError("boom")).install(monkeypatch)
    drafter = ScriptedDrafter()
    response = ask("What problems are on her list?", drafter=drafter)
    assert trajectory(response) == [
        ("answer", "chart_only_question"),
        ("refuse", "chart_unavailable"),
    ]
    assert drafter.calls == []
    assert "boom" not in response.message


def test_a_model_failure_is_a_typed_refusal_and_states_nothing(chart: FakeChart) -> None:
    drafter = ScriptedDrafter(fail_with=ClaimDraftingError("truncated"))
    response = ask("What problems are on her list?", drafter=drafter)
    assert trajectory(response)[-1] == ("refuse", "model_unavailable")
    assert CHART_MARKER not in response.message
    assert response.answer_view is None


@pytest.mark.parametrize(
    ("question", "reason"),
    [
        ("What is her blood pressure today?", "out_of_scope_resource"),
        ("Show me another patient's labs", "out_of_scope_resource"),
        ("What dose of lisinopril should I start her on?", "unverifiable_judgment"),
        ("Is her kidney function getting worse over time?", "unverifiable_judgment"),
        ("Is her potassium high?", "unverifiable_judgment"),
    ],
)
def test_refusals_read_nothing_and_draft_nothing(
    chart: FakeChart, question: str, reason: str
) -> None:
    """G6/H1/H2: refused before any chart read or model call."""
    drafter = ScriptedDrafter()
    response = ask(question, drafter=drafter)
    assert trajectory(response) == [("refuse", reason)]
    assert chart.chart_reads == [] and drafter.calls == []
    assert "Nothing about the patient is stated" in response.message


def test_no_node_runs_more_than_once_and_the_supervisor_is_bounded(chart: FakeChart) -> None:
    """G5: bounded visits on the longest path the rules take."""
    response = ask_with_document(GUIDELINE_QUESTION)
    assert len(response.routing) <= MAX_VISITS[Node.SUPERVISOR]
    nodes = [h.node for h in response.handoffs]
    assert len(nodes) == len(set(nodes))


# --------------------------------------------------------------------------
# verification and rendering
# --------------------------------------------------------------------------


def test_a_claim_that_fails_verification_is_disclosed_not_stated(chart: FakeChart) -> None:
    def script(chart_records: Any, documents: Any, chunks: Any):
        document_id = next(iter(documents))
        return draft_of(
            chart_claims=[
                Claim(
                    source_id="cond-1",
                    field="display",
                    value="Something the chart does not say",
                    resource_type=FhirResourceType.CONDITION,
                )
            ],
            document_claims=[
                # The page says 89. "8" is a substring of it and is not 89 (D24).
                DraftedDocumentFact(document_id, "results.cmp.glucose.value", "8"),
            ],
            guideline_claims=[
                DraftedGuideline(
                    chunks[0].chunk_id, "screen every adult annually without exception"
                )
            ],
        )

    response = ask_with_document(GUIDELINE_QUESTION, drafter=ScriptedDrafter(script=script))
    answer = response.answer_view.answer  # type: ignore[union-attr]
    # The only patient fact is code's count of the rows the scope did not
    # itemise (D50); nothing the model drafted is stated.
    assert [
        f.statement
        for f in answer.patient_facts
        if not f.statement.startswith("Not listed individually")
    ] == []
    assert answer.guideline_evidence == []
    assert response.status is AskStatus.NOT_IN_RECORD
    reasons = {u.source_type: u.reason for u in response.unverified}
    assert reasons[SourceType.DOCUMENT] == "value_mismatch"
    assert reasons[SourceType.GUIDELINE] == "excerpt_not_in_chunk"
    for unverified in response.unverified:
        assert unverified.line in response.message
    # A non-verbatim "quotation" is never shown, even as unverified (D26).
    assert "screen every adult annually" not in response.message
    assert AnswerLimitation.UNVERIFIED_CLAIMS in response.limitations


def test_an_unlocated_document_value_is_never_presented_as_fact(chart: FakeChart) -> None:
    """Invariant 4: unsupported fields ride in their own list, not as claims."""
    response = ask_with_document(
        "What medications does the intake form list?", sample=INTAKE_SAMPLE
    )
    answer = response.answer_view.answer  # type: ignore[union-attr]
    assert answer.unsupported
    located_paths = {c.field_or_chunk_id for claim in answer.patient_facts for c in claim.citations}
    for field in answer.unsupported:
        if field.citation is not None:
            assert field.citation.field_or_chunk_id not in located_paths
    assert AnswerLimitation.UNSUPPORTED_DOCUMENT_VALUES in response.limitations


def test_intake_discrepancies_are_surfaced_and_never_merged(chart: FakeChart) -> None:
    """Invariant 5: intake-missing-allergy-006 omits the chart's fish allergy."""
    response = ask_with_document("What allergies does the intake form list?", sample=INTAKE_SAMPLE)
    assert [d.kind.value for d in response.discrepancies] == ["omitted"]
    assert "nothing was merged" in response.message


def test_each_claim_draws_on_one_source_type(chart: FakeChart) -> None:
    """Invariant 3 / D17, on real output."""
    answer = ask_with_document(GUIDELINE_QUESTION).answer_view.answer  # type: ignore[union-attr]
    for claim in answer.patient_facts:
        assert len({c.source_type for c in claim.citations}) == 1
        assert claim.source_type is not SourceType.GUIDELINE
    for claim in answer.guideline_evidence:
        assert {c.source_type for c in claim.citations} == {SourceType.GUIDELINE}


def test_the_answer_view_validates_and_carries_the_documents_pages(chart: FakeChart) -> None:
    view = ask_with_document(GUIDELINE_QUESTION).answer_view
    assert view is not None
    assert len(view.documents) == 1 and view.documents[0].pages[0].width_pt > 0
    # Round-trips through JSON the way the chart embed will read it (D22).
    type(view).model_validate_json(view.model_dump_json())


# --------------------------------------------------------------------------
# conversation state — Week 1's store, Week 1's invalidation
# --------------------------------------------------------------------------


def test_a_patient_switch_discards_the_previous_patients_context(chart: FakeChart) -> None:
    store = ConversationStore()
    first_drafter, second_drafter = ScriptedDrafter(), ScriptedDrafter()
    first = ask("What problems are on her list?", store=store, drafter=first_drafter)
    assert first.conversation.state_saved and first.conversation.transition is (
        ConversationTransition.STARTED
    )
    again = ask("And her medications?", store=store, drafter=first_drafter)
    assert again.conversation.context_turns == 1

    switched = ask(
        "What problems are on the list?",
        store=store,
        drafter=second_drafter,
        patient=OTHER_PATIENT,
    )
    assert switched.conversation.transition is ConversationTransition.RESET_PATIENT_CHANGED
    assert switched.conversation.context_turns == 0
    assert second_drafter.calls[0]["context"] == ()


def test_an_unknown_patient_raises_before_any_state_and_discards_the_old_one(
    chart: FakeChart,
) -> None:
    store = ConversationStore()
    ask("What problems are on her list?", store=store)
    with pytest.raises(PatientNotFoundError):
        ask("What problems?", store=store, patient="11111111-1111-4111-8111-111111111111")
    after = ask("What problems are on her list?", store=store)
    assert after.conversation.transition is not ConversationTransition.CONTINUED


# --------------------------------------------------------------------------
# observability — events, and what they must not carry
# --------------------------------------------------------------------------


def test_every_routing_decision_is_logged_without_free_text(chart: FakeChart) -> None:
    with capture_logs() as logs:
        response = ask_with_document(GUIDELINE_QUESTION)
    decisions = [e for e in logs if e.get("event_type") == supervisor.ROUTING_EVENT]
    assert len(decisions) == len(response.routing)
    for event in decisions:
        data = event["event_data"]
        assert set(data) == {
            "decision",
            "reason_code",
            "correlation_id",
            "visit",
            "rules",
            "document_states",
        }
        assert data["correlation_id"] == response.correlation_id
        assert Route(data["decision"]) and RoutingReasonCode(data["reason_code"])


def test_the_turn_event_carries_the_sequence_latency_and_counts(chart: FakeChart) -> None:
    with capture_logs() as logs:
        response = ask_with_document(GUIDELINE_QUESTION)
    (turn,) = [e for e in logs if e.get("event_type") == supervisor.TURN_EVENT]
    data = turn["event_data"]
    assert data["tool_sequence"] == ["supervisor", "evidence_retriever", "supervisor", "answer"]
    assert set(data["node_ms"]) == {"supervisor", "evidence_retriever", "answer"}
    assert data["retrieval_hits"] > 0 and data["guideline_ids"]
    assert data["documents"][0]["status"] == "complete"
    assert data["documents"][0]["mean_confidence"] is not None
    assert data["claims"]["document"]["verified"] > 0
    assert data["correlation_id"] == response.correlation_id


def test_no_log_line_carries_the_question_a_chart_value_or_a_document_value(
    chart: FakeChart,
) -> None:
    """Invariant 1, over every event the turn emits."""
    with capture_logs() as logs:
        response = ask_with_document(GUIDELINE_QUESTION)
    blob = json.dumps(logs, default=str)
    assert GUIDELINE_QUESTION not in blob
    assert "USPSTF recommend" not in blob
    assert CHART_MARKER not in blob and MEDICATION_MARKER not in blob
    assert TOKEN not in blob
    # A located document value: the glucose result the answer states.
    assert "results.cmp.glucose.value" not in blob
    for claim in response.answer_view.answer.guideline_evidence:  # type: ignore[union-attr]
        assert str(claim.citations[0].quote_or_value) not in blob
    chart_name = chart_for(LAB_SAMPLE).identity.family
    assert chart_name is None or chart_name not in blob
