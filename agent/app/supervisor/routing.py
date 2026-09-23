"""
Supervisor routing — D4: deterministic, explicit rules over request shape,
available artifacts and prior state, every decision logged with its reason.

Two pure functions, and no model anywhere in this module:

`classify(question)` reads the question's *wording* once, at the start of the
turn, and returns a `QuestionIntent`: which of five intents it is, the rule
ids that fired, the clinical concepts a guideline search would use, and which
lab/visit reads the chart step should add. This is the step D4 allows one
small LLM call for; it does not use one. Wording rules err toward refusing,
as Week 1's do (app/conversation/routing.py), and a question they route to
`answer` still meets a drafting model that sees only this turn's sources and
a verifier that passes nothing else.

`decide(view)` is the supervisor proper. It is called on every supervisor
visit with a `SupervisorView` — the intent, what the documents' extractions
look like right now, whether retrieval has run and how many chunks it found,
any worker failure, and how many times each node has run — and returns one
`RoutingDecision`: a `Route` and a `RoutingReasonCode`, never free text.
The view carries shape only (statuses, counts, codes), never chart content,
which is what keeps routing out of the PHI surface (D4's own rationale).

**No loops.** `MAX_VISITS` bounds every node. A decision that would send the
turn into a node at its bound is replaced by `refuse` /
`visit_limit_exceeded`: the turn fails closed rather than running on. Under
the rules below no node is ever entered twice, so the bound is a backstop
against a future rule that would, not a path the current rules take.

**Retrieval queries are concepts, never the question (D5).** The concepts a
search runs on come from `CONCEPT_LEXICON` — fixed text this module owns,
selected by wording — and from lab test names printed on an uploaded report
(`app/graph/supervisor.py`). No substring of the question is ever sent, so a
patient name typed into a question has no path into a retrieval query.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from app.conversation.lab_codes import analyte_matches

# Week 1's wording tables, reused rather than copied so the two paths cannot
# disagree about what "vitals" or "another patient" means. Private names,
# imported deliberately: they are the rule tables, not implementation detail.
from app.conversation.routing import (
    _LAB_WORDING,
    _REFUSAL_RULES,
    _VISIT_WORDING,
    OUT_OF_SCOPE,
)
from app.models.conversation import RefusalCategory
from app.models.w2.workers import ClinicalConcept, Route, RoutingDecision, RoutingReasonCode

# --------------------------------------------------------------------------
# intent
# --------------------------------------------------------------------------


class Intent(StrEnum):
    """What the question asks for, decided by wording alone."""

    #: A fact the chart or an uploaded document states. No retrieval (G2).
    RECORD_LOOKUP = "record_lookup"
    #: What guidance says, in general.
    GUIDELINE = "guideline"
    #: Whether guidance bears on this patient: facts and evidence side by
    #: side, never a judgment the verifier cannot check.
    RECOMMENDATION = "recommendation"
    #: A part of the chart the tools cannot read, or another patient.
    REFUSE_OUT_OF_SCOPE = "refuse_out_of_scope"
    #: A judgment, trend, dose or diagnosis nothing can verify.
    REFUSE_UNVERIFIABLE = "refuse_unverifiable"


def _rule(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


_GUIDELINE_WORDING = _rule(
    r"\b(guidelines?|guidance|uspstf|task force|recommendations?|recommended|evidence"
    r"|according to|standard of care|first[- ]line|screen(ing|ed)?|criteria|threshold"
    r"|cut-?off|targets?|goals?|how often|intervals?|what (do|does) (the )?(guidelines?"
    r"|uspstf|cdc|nih|niddk|nhlbi)\b)\b"
)
_RECOMMENDATION_WORDING = _rule(
    r"\b(should (she|he|they|we|i|the patient)|does (she|he|they|the patient) (need|qualify)"
    r"|do (they|we) need|is (she|he|they|the patient) (a )?(candidate|eligible|due)"
    r"|candidate for|eligible for|indicated|recommend\w*|advis\w*|suggest\w*)\b"
)

#: Dose selection for this patient: H1. Refused even when a guideline could
#: be quoted, because a quoted range is not a dose for this person and
#: nothing here can verify one.
_DOSING = _rule(
    r"\b(titrat\w*|uptitrat\w*)\b"
    r"|\b(dose|doses|dosage|dosing)\b[^?.!]{0,60}\b(should|start|give|prescribe|increase|decrease"
    r"|adjust|change|recommend\w*)\b"
    r"|\b(should|start|give|prescribe|increase|decrease|adjust|change|recommend\w*)\b"
    r"[^?.!]{0,60}\b(dose|doses|dosage|dosing)\b"
    r"|\bhow much\b[^?.!]{0,40}\b(give|start|prescribe)\b"
)
_DIAGNOSIS = _rule(r"\b(diagnos(e|ing)|prognosis|differential|what('s| is) wrong with)\b")
_TREND = _rule(
    r"\b(worsen\w*|worse|getting better|improv\w*|deteriorat\w*|declin\w*|trend\w*|trajectory|over time"
    r"|going (up|down)|(gone|went|come|came) (up|down)|rising|falling|fluctuat\w*)\b"
)
_CHANGE = next(p for c, p in _REFUSAL_RULES if c is RefusalCategory.CHANGE_DETECTION)
_JUDGMENT = [p for c, p in _REFUSAL_RULES if c is RefusalCategory.CLINICAL_JUDGMENT]
_OTHER_PATIENTS = next(p for c, p in _REFUSAL_RULES if c is RefusalCategory.OTHER_PATIENTS)
_CHART_SCOPE = [
    (c, p)
    for c, p in _REFUSAL_RULES
    if c in OUT_OF_SCOPE and c is not RefusalCategory.OTHER_PATIENTS
]

#: The words guideline retrieval searches on, and the only text a query is
#: ever built from. (pattern, concept text): the concept text is ours, so a
#: query cannot carry anything the question said that is not in this table.
#: Aligned with the committed corpus (app/retrieval/corpus/sources.yaml).
CONCEPT_LEXICON: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (_rule(pattern), text)
    for pattern, text in (
        (r"\bprediabetes\b|\bpre-diabetes\b", "prediabetes"),
        (r"\bdiabet\w*\b|\bt2dm?\b", "type 2 diabetes"),
        (r"\ba1c\b|\bhba1c\b|\bhemoglobin a1c\b|\bglycated\b", "hemoglobin A1C"),
        (r"\bglucose\b|\bblood sugar\b", "blood glucose"),
        (r"\bmetformin\b", "metformin"),
        (r"\bstatins?\b", "statin therapy"),
        (r"\bcholesterol\b|\blipids?\b|\blipid panel\b", "blood cholesterol"),
        (r"\bldl\b", "LDL cholesterol"),
        (r"\bhdl\b", "HDL cholesterol"),
        (r"\btriglycerides?\b", "triglycerides"),
        (
            r"\bcardiovascular\b|\bcvd\b|\bheart disease\b|\bheart attack\b",
            "cardiovascular disease",
        ),
        (r"\bstroke\b", "stroke"),
        (r"\baspirin\b", "aspirin"),
        (r"\bhypertension\b|\bblood pressure\b|\bhigh bp\b", "high blood pressure"),
        (r"\bkidney\b|\brenal\b|\bckd\b|\bnephropathy\b", "chronic kidney disease"),
        (r"\begfr\b|\bgfr\b|\bfiltration rate\b", "estimated glomerular filtration rate"),
        (r"\bcreatinine\b", "creatinine"),
        (r"\balbumin\w*\b|\bacr\b|\buacr\b", "urine albumin"),
        (r"\bthyroid\b", "thyroid dysfunction"),
        (r"\btsh\b", "thyroid-stimulating hormone test"),
        (r"\bhypothyroid\w*\b|\bunderactive thyroid\b", "hypothyroidism"),
        (r"\bhyperthyroid\w*\b|\boveractive thyroid\b", "hyperthyroidism"),
        (r"\blevothyroxine\b|\bsynthroid\b", "levothyroxine"),
        (r"\bvitamin d\b|\b25-?oh\b", "vitamin D"),
        (r"\bcalcium\b", "calcium supplementation"),
        (r"\bfractures?\b|\bosteoporo\w*\b", "fracture prevention"),
        (r"\biron\b|\bferritin\b", "iron deficiency"),
        (r"\banemi\w*\b|\banaemi\w*\b|\bhemoglobin\b(?! a1c)", "anemia"),
        (r"\bpregnan\w*\b", "pregnancy"),
        (r"\bpotassium\b", "potassium"),
        (r"\bmedication safety\b|\bpolypharmacy\b|\bside effects?\b", "medication safety"),
        (r"\bscreen(ing|ed)?\b", "screening"),
        (r"\btreat(ment|ing|ed)?\b|\bmanag(e|ement|ing)\b|\btherapy\b", "treatment"),
        (r"\bmonitor(ing)?\b|\bfollow[- ]?up\b|\bretest\w*\b|\brecheck\w*\b", "monitoring"),
    )
)

#: At most this many concepts go into one query. More dilutes BM25 and says
#: nothing a reranker needs.
MAX_CONCEPTS = 6


@dataclass(frozen=True)
class QuestionIntent:
    """
    What `classify` decided, in the form the graph keeps. `rule_ids` are
    logged; the concepts are not (they are ours, but they are chosen by the
    question, and a log line of them is a paraphrase of it).
    """

    intent: Intent
    rule_ids: tuple[str, ...]
    concepts: tuple[ClinicalConcept, ...] = ()
    wants_labs: bool = False
    wants_visits: bool = False
    lab_codes: frozenset[str] = field(default_factory=frozenset)

    @property
    def needs_evidence(self) -> bool:
        return self.intent in (Intent.GUIDELINE, Intent.RECOMMENDATION)


def concepts_for(question: str) -> tuple[ClinicalConcept, ...]:
    """Lexicon concepts the question's wording selects, in table order."""
    found = [text for pattern, text in CONCEPT_LEXICON if pattern.search(question)]
    # A topic word alone ("screening", "treatment") searches for nothing in
    # particular; it is kept only beside a clinical concept.
    generic = {"screening", "treatment", "monitoring"}
    if not set(found) - generic:
        return ()
    return tuple(ClinicalConcept(text=text) for text in dict.fromkeys(found))[:MAX_CONCEPTS]


def classify(question: str) -> QuestionIntent:
    """
    Order matters, and is the policy:

    1. Another patient — always refused, whatever else the question says.
    2. A dose for this patient (H1), a diagnosis, a trend or a change over
       time — refused: nothing a single source states can verify them.
    3. Guideline or recommendation wording — retrieve. Week 1's chart-scope
       refusals (vitals, demographics, ...) do not apply here: "at what age
       should screening start" is a question about guidance, not a read of
       this patient's age, and the tools still read only what they read.
    4. Week 1's chart-scope refusals and its judgment refusals.
    5. Otherwise, a record lookup.
    """
    if _OTHER_PATIENTS.search(question):
        return QuestionIntent(Intent.REFUSE_OUT_OF_SCOPE, ("refuse:other_patients",))

    unverifiable = [
        rule_id
        for rule_id, pattern in (
            ("refuse:dosing", _DOSING),
            ("refuse:diagnosis", _DIAGNOSIS),
            ("refuse:trend", _TREND),
            ("refuse:change_detection", _CHANGE),
        )
        if pattern.search(question)
    ]
    if unverifiable:
        return QuestionIntent(Intent.REFUSE_UNVERIFIABLE, tuple(unverifiable))

    analytes = analyte_matches(question)
    wants_labs = bool(analytes) or bool(_LAB_WORDING.search(question))
    wants_visits = bool(_VISIT_WORDING.search(question))
    lab_codes = frozenset(code for _, codes in analytes for code in codes)
    reads = (("read:labs",) if wants_labs else ()) + (("read:visits",) if wants_visits else ())

    intent: Intent | None = None
    rule_ids: list[str] = []
    if _RECOMMENDATION_WORDING.search(question):
        intent = Intent.RECOMMENDATION
        rule_ids.append("retrieve:recommendation_wording")
    elif _GUIDELINE_WORDING.search(question):
        intent = Intent.GUIDELINE
        rule_ids.append("retrieve:guideline_wording")
    if intent is not None:
        return QuestionIntent(
            intent,
            (*rule_ids, *reads),
            concepts=concepts_for(question),
            wants_labs=wants_labs,
            wants_visits=wants_visits,
            lab_codes=lab_codes,
        )

    scope = [f"refuse:{c.value}" for c, pattern in _CHART_SCOPE if pattern.search(question)]
    if scope:
        return QuestionIntent(Intent.REFUSE_OUT_OF_SCOPE, tuple(scope))
    if any(pattern.search(question) for pattern in _JUDGMENT):
        return QuestionIntent(Intent.REFUSE_UNVERIFIABLE, ("refuse:clinical_judgment",))

    return QuestionIntent(
        Intent.RECORD_LOOKUP,
        ("answer:record_lookup", *reads),
        wants_labs=wants_labs,
        wants_visits=wants_visits,
        lab_codes=lab_codes,
    )


# --------------------------------------------------------------------------
# the supervisor decision
# --------------------------------------------------------------------------


class DocumentState(StrEnum):
    """One named document, as routing sees it: a status, never content."""

    NOT_FOUND = "not_found"
    PENDING = "pending"
    FAILED = "failed"
    IDENTITY_MISMATCH = "identity_mismatch"
    READY = "ready"


class Node(StrEnum):
    SUPERVISOR = "supervisor"
    INTAKE_EXTRACTOR = "intake_extractor"
    EVIDENCE_RETRIEVER = "evidence_retriever"
    ANSWER = "answer"
    REFUSE = "refuse"


#: Where each route goes.
ROUTE_NODE: dict[Route, Node] = {
    Route.EXTRACT: Node.INTAKE_EXTRACTOR,
    Route.RETRIEVE: Node.EVIDENCE_RETRIEVER,
    Route.ANSWER: Node.ANSWER,
    Route.REFUSE: Node.REFUSE,
}

#: The most times a node may run in one turn. The supervisor runs once per
#: handoff back to it: at the start, after the extractor, after the
#: retriever, and after a failed answer — four at most under the rules.
MAX_VISITS: dict[Node, int] = {
    Node.SUPERVISOR: 4,
    Node.INTAKE_EXTRACTOR: 1,
    Node.EVIDENCE_RETRIEVER: 1,
    Node.ANSWER: 1,
    Node.REFUSE: 1,
}


@dataclass(frozen=True)
class SupervisorView:
    """Everything `decide` reads. Shape only — see the module docstring."""

    intent: QuestionIntent
    documents: tuple[DocumentState, ...] = ()
    retrieval_ran: bool = False
    retrieval_hits: int = 0
    failure: RoutingReasonCode | None = None
    visits: Mapping[Node, int] = field(default_factory=dict)


_REFUSAL_FOR_INTENT = {
    Intent.REFUSE_OUT_OF_SCOPE: RoutingReasonCode.OUT_OF_SCOPE_RESOURCE,
    Intent.REFUSE_UNVERIFIABLE: RoutingReasonCode.UNVERIFIABLE_JUDGMENT,
}

_REFUSAL_FOR_DOCUMENT = (
    (DocumentState.NOT_FOUND, RoutingReasonCode.DOCUMENT_NOT_FOUND),
    (DocumentState.FAILED, RoutingReasonCode.EXTRACTION_FAILED),
)


def _choose(view: SupervisorView) -> tuple[Route, RoutingReasonCode]:
    if view.failure is not None:
        return Route.REFUSE, view.failure
    refusal = _REFUSAL_FOR_INTENT.get(view.intent.intent)
    if refusal is not None:
        return Route.REFUSE, refusal

    states = set(view.documents)
    for state, reason in _REFUSAL_FOR_DOCUMENT:
        if state in states:
            return Route.REFUSE, reason
    if DocumentState.PENDING in states:
        # D7: the extractor worker ran at upload. The ask path hands the
        # document to it once, to read whether it has finished; it never
        # waits and never starts a synchronous extraction.
        if view.visits.get(Node.INTAKE_EXTRACTOR, 0) == 0:
            return Route.EXTRACT, RoutingReasonCode.DOCUMENT_NOT_YET_EXTRACTED
        return Route.REFUSE, RoutingReasonCode.DOCUMENT_STILL_PROCESSING
    if DocumentState.IDENTITY_MISMATCH in states:
        return Route.REFUSE, RoutingReasonCode.DOCUMENT_IDENTITY_MISMATCH

    if view.intent.needs_evidence:
        if not view.retrieval_ran:
            return Route.RETRIEVE, (
                RoutingReasonCode.EVIDENCE_NEEDED_FOR_RECOMMENDATION
                if view.intent.intent is Intent.RECOMMENDATION
                else RoutingReasonCode.GUIDELINE_QUESTION
            )
        if view.retrieval_hits == 0:
            return Route.REFUSE, RoutingReasonCode.NO_GUIDELINE_EVIDENCE
        return Route.ANSWER, RoutingReasonCode.EVIDENCE_IN_HAND

    if view.documents:
        return Route.ANSWER, RoutingReasonCode.EXTRACTION_AVAILABLE
    return Route.ANSWER, RoutingReasonCode.CHART_ONLY_QUESTION


def decide(view: SupervisorView, correlation_id: str) -> RoutingDecision:
    """One supervisor decision. Fails closed on any visit bound."""
    over = view.visits.get(Node.SUPERVISOR, 0) > MAX_VISITS[Node.SUPERVISOR]
    route, reason = (
        (Route.REFUSE, RoutingReasonCode.VISIT_LIMIT_EXCEEDED) if over else _choose(view)
    )
    target = ROUTE_NODE[route]
    if route is not Route.REFUSE and view.visits.get(target, 0) >= MAX_VISITS[target]:
        route, reason = Route.REFUSE, RoutingReasonCode.VISIT_LIMIT_EXCEEDED
    return RoutingDecision(decision=route, reason_code=reason, correlation_id=correlation_id)
