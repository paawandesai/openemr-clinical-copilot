"""
Supervisor routing as pure functions (app/supervisor/routing.py): wording to
intent, and the decision table over document states, retrieval, failures and
visit counts. D4: deterministic, reason-coded, bounded; D5: concepts only.
"""

from __future__ import annotations

import pytest

from app.models.w2.workers import (
    ALLOWED_REASONS,
    FORBIDDEN_RETRIEVAL_FIELD_NAMES,
    Route,
    RoutingReasonCode,
)
from app.supervisor.routing import (
    CONCEPT_LEXICON,
    MAX_VISITS,
    DocumentState,
    Intent,
    Node,
    QuestionIntent,
    SupervisorView,
    classify,
    concepts_for,
    decide,
)

CORRELATION = "3b241101-e2bb-4255-8caf-4136c566a962"


def route_of(view: SupervisorView) -> tuple[Route, RoutingReasonCode]:
    decision = decide(view, CORRELATION)
    return decision.decision, decision.reason_code


# --------------------------------------------------------------------------
# classify
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "intent"),
    [
        ("What medications is she on?", Intent.RECORD_LOOKUP),
        ("When was her last A1c?", Intent.RECORD_LOOKUP),
        ("What was the LDL on the uploaded report?", Intent.RECORD_LOOKUP),
        ("What does USPSTF say about diabetes screening?", Intent.GUIDELINE),
        ("At what age should screening for diabetes start?", Intent.GUIDELINE),
        ("What is the guideline A1c target?", Intent.GUIDELINE),
        ("Should she be on a statin given this LDL?", Intent.RECOMMENDATION),
        ("Is she eligible for aspirin for CVD prevention?", Intent.RECOMMENDATION),
        ("What is her blood pressure?", Intent.REFUSE_OUT_OF_SCOPE),
        ("Show me Jane Doe's labs from another patient", Intent.REFUSE_OUT_OF_SCOPE),
        ("What about the other patients on my panel?", Intent.REFUSE_OUT_OF_SCOPE),
        ("What dose of lisinopril should I start?", Intent.REFUSE_UNVERIFIABLE),
        ("Should we titrate her metformin?", Intent.REFUSE_UNVERIFIABLE),
        ("Is her kidney function getting worse?", Intent.REFUSE_UNVERIFIABLE),
        ("What changed since her last visit?", Intent.REFUSE_UNVERIFIABLE),
        ("Can you diagnose what is going on?", Intent.REFUSE_UNVERIFIABLE),
        ("Is her potassium high?", Intent.REFUSE_UNVERIFIABLE),
    ],
)
def test_classify(question: str, intent: Intent) -> None:
    assert classify(question).intent is intent


def test_another_patient_is_refused_even_when_the_question_asks_for_guidance() -> None:
    assert classify("What do guidelines recommend for another patient?").intent is (
        Intent.REFUSE_OUT_OF_SCOPE
    )


def test_a_dose_is_refused_even_when_the_question_asks_for_guidance() -> None:
    """H1: a guideline range is not a dose for this patient."""
    assert classify("What dose does the guideline recommend I start her on?").intent is (
        Intent.REFUSE_UNVERIFIABLE
    )


def test_rule_ids_are_codes_not_text() -> None:
    for question in ("Should she be on a statin?", "What is her blood pressure?"):
        for rule_id in classify(question).rule_ids:
            assert ":" in rule_id and " " not in rule_id


def test_concepts_are_lexicon_text_never_question_text() -> None:
    """D5: a name typed into the question has no path into the query."""
    question = "Does Margaret Hollowell (MRN 55512345, born 1961-04-02) need a statin for her LDL?"
    concepts = concepts_for(question)
    assert concepts
    lexicon = {text for _, text in CONCEPT_LEXICON}
    for concept in concepts:
        assert concept.text in lexicon
        assert "Hollowell" not in concept.text and "555" not in concept.text


def test_a_topic_word_alone_is_not_something_to_search_for() -> None:
    assert concepts_for("What do the guidelines say about screening?") == ()


def test_the_retrieval_input_still_has_no_patient_field() -> None:
    from app.models.w2.workers import EvidenceRetrieverInput

    assert not set(EvidenceRetrieverInput.model_fields) & FORBIDDEN_RETRIEVAL_FIELD_NAMES


# --------------------------------------------------------------------------
# decide
# --------------------------------------------------------------------------

LOOKUP = QuestionIntent(Intent.RECORD_LOOKUP, ("answer:record_lookup",))
GUIDELINE = QuestionIntent(Intent.GUIDELINE, ("retrieve:guideline_wording",))
RECOMMEND = QuestionIntent(Intent.RECOMMENDATION, ("retrieve:recommendation_wording",))


def view(intent: QuestionIntent = LOOKUP, **kwargs) -> SupervisorView:
    kwargs.setdefault("visits", {Node.SUPERVISOR: 1})
    return SupervisorView(intent=intent, **kwargs)


@pytest.mark.parametrize(
    ("the_view", "expected"),
    [
        (view(), (Route.ANSWER, RoutingReasonCode.CHART_ONLY_QUESTION)),
        (
            view(documents=(DocumentState.READY,)),
            (Route.ANSWER, RoutingReasonCode.EXTRACTION_AVAILABLE),
        ),
        (
            view(documents=(DocumentState.PENDING,)),
            (Route.EXTRACT, RoutingReasonCode.DOCUMENT_NOT_YET_EXTRACTED),
        ),
        (
            view(
                documents=(DocumentState.PENDING,),
                visits={Node.SUPERVISOR: 2, Node.INTAKE_EXTRACTOR: 1},
            ),
            (Route.REFUSE, RoutingReasonCode.DOCUMENT_STILL_PROCESSING),
        ),
        (
            view(documents=(DocumentState.READY, DocumentState.NOT_FOUND)),
            (Route.REFUSE, RoutingReasonCode.DOCUMENT_NOT_FOUND),
        ),
        (
            view(documents=(DocumentState.FAILED,)),
            (Route.REFUSE, RoutingReasonCode.EXTRACTION_FAILED),
        ),
        (
            view(documents=(DocumentState.IDENTITY_MISMATCH,)),
            (Route.REFUSE, RoutingReasonCode.DOCUMENT_IDENTITY_MISMATCH),
        ),
        (view(GUIDELINE), (Route.RETRIEVE, RoutingReasonCode.GUIDELINE_QUESTION)),
        (
            view(RECOMMEND),
            (Route.RETRIEVE, RoutingReasonCode.EVIDENCE_NEEDED_FOR_RECOMMENDATION),
        ),
        (
            view(GUIDELINE, retrieval_ran=True, retrieval_hits=0),
            (Route.REFUSE, RoutingReasonCode.NO_GUIDELINE_EVIDENCE),
        ),
        (
            view(GUIDELINE, retrieval_ran=True, retrieval_hits=4),
            (Route.ANSWER, RoutingReasonCode.EVIDENCE_IN_HAND),
        ),
        (
            view(failure=RoutingReasonCode.MODEL_UNAVAILABLE),
            (Route.REFUSE, RoutingReasonCode.MODEL_UNAVAILABLE),
        ),
        (
            view(QuestionIntent(Intent.REFUSE_OUT_OF_SCOPE, ("refuse:vitals",))),
            (Route.REFUSE, RoutingReasonCode.OUT_OF_SCOPE_RESOURCE),
        ),
        (
            view(QuestionIntent(Intent.REFUSE_UNVERIFIABLE, ("refuse:dosing",))),
            (Route.REFUSE, RoutingReasonCode.UNVERIFIABLE_JUDGMENT),
        ),
    ],
)
def test_decide(the_view: SupervisorView, expected: tuple[Route, RoutingReasonCode]) -> None:
    assert route_of(the_view) == expected


def test_a_refused_question_never_touches_its_documents() -> None:
    refused = QuestionIntent(Intent.REFUSE_OUT_OF_SCOPE, ("refuse:other_patients",))
    assert route_of(view(refused, documents=(DocumentState.PENDING,)))[0] is Route.REFUSE


def test_a_document_problem_wins_over_retrieval() -> None:
    """No evidence is fetched for a question whose document cannot be used."""
    assert route_of(view(GUIDELINE, documents=(DocumentState.FAILED,)))[0] is Route.REFUSE


def test_the_supervisor_fails_closed_past_its_visit_bound() -> None:
    over = {Node.SUPERVISOR: MAX_VISITS[Node.SUPERVISOR] + 1}
    assert route_of(view(visits=over)) == (Route.REFUSE, RoutingReasonCode.VISIT_LIMIT_EXCEEDED)


@pytest.mark.parametrize("node", [Node.INTAKE_EXTRACTOR, Node.EVIDENCE_RETRIEVER, Node.ANSWER])
def test_no_worker_is_entered_past_its_bound(node: Node) -> None:
    intent = {
        Node.INTAKE_EXTRACTOR: LOOKUP,
        Node.EVIDENCE_RETRIEVER: GUIDELINE,
        Node.ANSWER: LOOKUP,
    }[node]
    documents = (DocumentState.PENDING,) if node is Node.INTAKE_EXTRACTOR else ()
    visits = {Node.SUPERVISOR: 1, node: MAX_VISITS[node]}
    decision = route_of(view(intent, documents=documents, visits=visits))
    assert decision[0] is Route.REFUSE


def test_every_reason_decide_can_return_is_allowed_for_its_route() -> None:
    """RoutingDecision validates this too; this checks the table reaches all of them."""
    reachable = {
        route_of(v)
        for v in (
            view(),
            view(documents=(DocumentState.READY,)),
            view(documents=(DocumentState.PENDING,)),
            view(GUIDELINE),
            view(RECOMMEND),
            view(GUIDELINE, retrieval_ran=True, retrieval_hits=2),
        )
    }
    for route, reason in reachable:
        assert reason in ALLOWED_REASONS[route]
