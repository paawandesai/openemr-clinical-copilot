"""
Multi-turn Follow-Up Drilling — USERS.md §3 Use Case 3, ARCHITECTURE.md
§4–§5. The constrained single-turn follow-up (app/graph/follow_up.py)
extended into a conversation, with the same guarantee per turn: every
statement about the patient is a claim that passed verify_claims against a
bounded set of records retrieved this turn, or it is shown as UNVERIFIED,
or it is not said.

    authorize → open state → route → retrieve → draft → verify → render → commit

  authorize   The Patient read, under the caller's own token (§2). It is
              first on purpose: the conversation is keyed by the token's
              `sub`, which nothing has proven until OpenEMR accepts the
              token. No state is read, created or discarded before it.
  open state  app/conversation/state.py. The conversation for
              (user, session) if it is bound to this patient; otherwise
              whatever was there is deleted and an empty one starts. A
              patient switch therefore reaches the rest of the turn as an
              empty context — there is no later step that could still hold
              the previous patient's facts.
  route       app/conversation/routing.py, by rule: refuse (outside tool
              scope, or a judgment/comparison/trend the verifier cannot
              check), and otherwise which of Observation and Encounter
              join the three types read on every turn. The model is not
              consulted about what to fetch.
  retrieve    The existing readers in app/fhir/client.py, unchanged, every
              one patient-scoped under the caller's token, every turn —
              nothing retrieved is kept between turns. Lab results are
              then narrowed by rule to the turn's bounded set.
  draft       app/reasoning/draft_conversation_turn.py: the only LLM call.
              It sees this turn's bounded set, the question, and the
              verified claims of earlier turns. Never earlier questions or
              answer text.
  verify      app/verification/checker.py:verify_claims, unchanged, against
              the same mapping the draft step was given. Earlier turns'
              claims are not in that mapping: nothing verifies against
              context, only against records read this turn.
  render      Plain code. Verified claims plainly, failed ones UNVERIFIED
              and never dropped, gaps and refusals as fixed sentences, and
              a closing line stating exactly what the turn drew on.
  commit      The claims that passed are stored as the next turn's
              context. Failed claims, the question and the answer are not.

A refused turn runs the same sequence with nothing retrieved and nothing
drafted: verify_claims is still called, over zero claims, so there is one
code path and no way to render a statement that skipped it.

What is code-computed rather than claimed: the "most recent of N" tag on a
lab result or visit. "Latest" is a relation across records, which the
verifier cannot check (§5), so the model's pick is not trusted for it: the
tag is computed here from everything FHIR returned this turn — across the
LOINC codes that are one test (lab_codes.SAME_TEST_CODES) — and says so
when a later result exists.

Every event carries use_case="follow_up_drilling", the patient_session_key
and the correlation id. No event carries the question, the answer, a claim
value or record contents; routing logs rule ids only.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.conversation.lab_codes import loinc_name, same_test_codes
from app.conversation.routing import (
    ENCOUNTER_COUNT,
    LAB_RESULTS_PER_CODE,
    LabScope,
    RoutingDecision,
    narrow_observations,
    route,
)
from app.conversation.state import (
    ConversationSnapshot,
    ConversationStore,
    Invalidation,
    store_from_env,
)
from app.fhir import client as fhir_client
from app.fhir.exceptions import PatientNotFoundError
from app.graph.follow_up import (
    GAP_WORDING,
    NOT_IN_RECORD,
    _claim_line,
    _display_value,
    _FIELD_LABEL,
    _section_counts,
    answer_status,
    fetch_bounded_set,
    section_parts,
)
from app.middleware.correlation import correlation_scope, get_correlation_id
from app.middleware.langfuse_export import LATENCY_EVENT
from app.middleware.logging import log_event
from app.models.claims import VerificationResult
from app.models.conversation import (
    ConversationGap,
    RefusalCategory,
    RetrievalRule,
    TurnStatus,
)
from app.models.fhir_resources import Encounter, FhirResourceType, Observation
from app.models.follow_up import RecordGap
from app.models.metrics_events import LatencyEvent, UseCase
from app.reasoning.draft_claims import SectionCount
from app.reasoning.draft_conversation_turn import TurnDraft, draft_conversation_turn
from app.reasoning.draft_follow_up import BOUNDED_RESOURCE_MODELS, Coverage
from app.verification.checker import verify_claims

_USE_CASE = UseCase.FOLLOW_UP_DRILLING

STEPS = ("retrieve", "draft", "verify", "render")

# One store per process; see app/conversation/state.py for why that is
# enough here and what replaces it in production.
STORE = store_from_env()

OUT_OF_SCOPE_HEADLINE = "That's not in the record I can retrieve for this visit."
UNVERIFIABLE_HEADLINE = (
    "I can't answer that: it asks for a judgment, comparison or trend, and the "
    "co-pilot only states what a single chart record says, checked against that record."
)
REFUSAL_FOOTER = (
    "Nothing was retrieved for this question and nothing about the patient is "
    "stated in this answer. The co-pilot can report problems, medications, "
    "allergies, lab results and visit dates as the chart records them."
)
UNVERIFIABLE_HINT = (
    "You can ask for the recorded values themselves, for example "
    '"What were her most recent creatinine results?"'
)

REFUSAL_WORDING: dict[RefusalCategory, str] = {
    RefusalCategory.VITALS: (
        "Vitals (blood pressure, pulse, weight, temperature) are outside what the co-pilot retrieves."
    ),
    RefusalCategory.CLINICAL_NOTES: "Clinical notes and documentation are outside what the co-pilot retrieves.",
    RefusalCategory.PROCEDURES: "Procedures and surgeries are outside what the co-pilot retrieves.",
    RefusalCategory.IMMUNIZATIONS: "Immunizations are outside what the co-pilot retrieves.",
    RefusalCategory.IMAGING: "Imaging and other diagnostic studies are outside what the co-pilot retrieves.",
    RefusalCategory.FAMILY_SOCIAL_HISTORY: (
        "Family and social history are outside what the co-pilot retrieves."
    ),
    RefusalCategory.SCHEDULING: "Scheduled or upcoming appointments are outside what the co-pilot retrieves.",
    RefusalCategory.DEMOGRAPHICS_BILLING: (
        "Demographics, contact details and billing are outside what the co-pilot retrieves."
    ),
    RefusalCategory.OTHER_PATIENTS: (
        "This conversation is about the one patient whose chart is open; the co-pilot "
        "does not answer about any other patient."
    ),
    RefusalCategory.TREND_OR_COMPARISON: (
        "Whether something is improving, worsening or different from before compares "
        "several records. No chart record states it, so it cannot be verified."
    ),
    RefusalCategory.CLINICAL_JUDGMENT: (
        "Whether a value is normal, a concern or controlled, and what to do about it, "
        "is a clinical judgment. The chart holds no reference ranges or flags to check "
        "one against."
    ),
    RefusalCategory.CHANGE_DETECTION: (
        "What changed between visits is a comparison of two points in time across "
        "several records, which the co-pilot cannot verify."
    ),
}

# Gap sentences that depend on whether the type was retrieved this turn.
_GAP_WHEN_RETRIEVED: dict[ConversationGap, tuple[FhirResourceType, str]] = {
    ConversationGap.LAB_RESULTS: (
        FhirResourceType.OBSERVATION,
        "The lab results considered for this turn do not include it.",
    ),
    ConversationGap.VISIT_HISTORY: (
        FhirResourceType.ENCOUNTER,
        "The visit records retrieved for this turn do not hold it; a visit record holds its date only.",
    ),
}
_GAP_WHEN_NOT_RETRIEVED: dict[ConversationGap, str] = {
    ConversationGap.LAB_RESULTS: (
        "Lab results were not retrieved for this turn. Name the test, or ask about "
        "lab results, to have them retrieved."
    ),
    ConversationGap.VISIT_HISTORY: (
        "Visit dates were not retrieved for this turn. Ask about visits to have them retrieved."
    ),
    ConversationGap.UNRESOLVED_REFERENCE: (
        "The question refers to something from earlier in this conversation, and nothing "
        "verified in an earlier turn identifies it. Name the medication, problem, "
        "allergy or test."
    ),
}


@dataclass(frozen=True)
class RetrievalSummary:
    """`retrieved`: records FHIR returned. `considered`: records in the turn's bounded set."""

    resource_type: FhirResourceType
    rule: RetrievalRule
    retrieved: int
    considered: int


@dataclass(frozen=True)
class TurnResult:
    """
    `claim_lines` has one entry per verification result, in order: the
    physician-facing line `text` renders for that claim.
    """

    text: str
    status: TurnStatus
    gaps: list[ConversationGap]
    refusals: list[RefusalCategory]
    correlation_id: str
    patient_session_key: str
    trigger_ts: float
    rendered_ts: float
    step_seconds: dict[str, float]
    verification_results: list[VerificationResult]
    claim_lines: list[str]
    retrieval: list[RetrievalSummary]
    section_counts: dict[FhirResourceType, SectionCount]
    snapshot: ConversationSnapshot
    state_saved: bool

    @property
    def total_seconds(self) -> float:
        return self.rendered_ts - self.trigger_ts


def patient_session_key(user: str, patient_id: str, session_id: str) -> str:
    """(user, patient, session) — ARCHITECTURE.md §4."""
    return f"{user}:{patient_id}:{session_id}"


def turn_status(
    decision: RoutingDecision,
    coverage: Coverage,
    gaps: list[ConversationGap],
    results: list[VerificationResult],
) -> TurnStatus:
    """
    A refusal is decided by routing alone. Otherwise exactly
    app/graph/follow_up.py:answer_status: what the model drafted can lower
    the status, never raise it past what verification supports.
    """
    if decision.refused:
        return (
            TurnStatus.REFUSED_OUT_OF_SCOPE
            if decision.refused_out_of_scope
            else TurnStatus.REFUSED_UNVERIFIABLE
        )
    # answer_status only asks whether any gap was named, not which.
    named = [RecordGap.NOT_STATED_IN_RECORD] * len(gaps)
    return TurnStatus(answer_status(coverage, named, results).value)


# -- recency, computed in code -------------------------------------------------


def _sortable(moment: datetime) -> datetime | None:
    # datetime.min is the FHIR client's "absent" placeholder, never a date.
    return None if moment == datetime.min else moment.replace(tzinfo=None)


def _later_count(moment: datetime, others: list[datetime]) -> int | None:
    mine = _sortable(moment)
    if mine is None:
        return None
    return sum(1 for other in others if (theirs := _sortable(other)) is not None and theirs > mine)


def _recency_tag(later: int | None, total: int, noun: str) -> str:
    if later is None:
        return ""
    if later == 0:
        return f" [most recent of {total} {noun}]"
    return f" [not the most recent: {later} later of {total} {noun}]"


# -- rendering -----------------------------------------------------------------


def _day(moment: datetime) -> str:
    return "date not recorded" if moment == datetime.min else moment.date().isoformat()


def _lab_name(record: Observation) -> str:
    name = loinc_name(record.loinc_code)
    label = name if name is not None else "name not in the co-pilot's lab table"
    return f"LOINC {record.loinc_code} ({label}) of {_day(record.effective_date)}"


def _turn_claim_line(
    result: VerificationResult,
    bounded: dict[FhirResourceType, list[Any]],
    all_observations: list[Observation],
) -> str:
    """
    follow_up._claim_line for everything it already renders. A verified
    lab or visit claim is rendered here: those records have no `display`,
    so the line names the record from its own LOINC code and date — read
    from the chart record, never from the claim — and adds the recency tag.
    """
    claim = result.claim
    if not result.passed or claim.resource_type not in (
        FhirResourceType.OBSERVATION,
        FhirResourceType.ENCOUNTER,
    ):
        return _claim_line(result, bounded)

    record = next(r for r in bounded[claim.resource_type] if r.source_id == claim.source_id)
    if isinstance(record, Encounter):
        visits = [e.date for e in bounded[FhirResourceType.ENCOUNTER]]
        tag = _recency_tag(_later_count(record.date, visits), len(visits), "visits retrieved")
        return f"  - Visit: {_day(record.date)}{tag}"

    codes = same_test_codes(record.loinc_code)
    same_test = [o.effective_date for o in all_observations if o.loinc_code in codes]
    noun = (
        "results with this code"
        if len(codes) == 1
        else f"results for this test (LOINC {', '.join(sorted(codes))})"
    )
    tag = _recency_tag(_later_count(record.effective_date, same_test), len(same_test), noun)
    if claim.field == "loinc_code":
        return f"  - Lab result: {_lab_name(record)}{tag}"
    field_label = _FIELD_LABEL.get(claim.field, claim.field)
    return f"  - Lab result {_lab_name(record)}, {field_label}: {_display_value(claim.value)}{tag}"


def _gap_sentence(gap: ConversationGap, retrieved_types: set[FhirResourceType]) -> str:
    if gap in _GAP_WHEN_RETRIEVED:
        resource_type, sentence = _GAP_WHEN_RETRIEVED[gap]
        if resource_type in retrieved_types:
            return sentence
    if gap in _GAP_WHEN_NOT_RETRIEVED:
        return _GAP_WHEN_NOT_RETRIEVED[gap]
    return GAP_WORDING[RecordGap(gap.value)]


def _lab_part(scope: LabScope | None) -> str:
    if scope is None:
        return "lab results not retrieved"
    if scope.retrieved == 0:
        return "no lab results with a value and a LOINC code on file"
    if not scope.codes:
        return f"the {scope.considered} most recent of {scope.retrieved} lab results"
    which = (
        f"LOINC {', '.join(scope.codes)}"
        if len(scope.codes) <= 4
        else f"{len(scope.codes)} LOINC codes"
    )
    if scope.considered == 0:
        return f"none of {scope.retrieved} lab results match the tests asked about ({which})"
    return (
        f"{scope.considered} of {scope.retrieved} lab results (up to the "
        f"{LAB_RESULTS_PER_CODE} most recent per test, {which})"
    )


def _visit_part(encounters: list[Encounter] | None) -> str:
    if encounters is None:
        return "visit dates not retrieved"
    if not encounters:
        return "no dated visits on file"
    return f"the {len(encounters)} most recent visit dates"


def _closing_line(
    section_counts: dict[FhirResourceType, SectionCount],
    lab_scope: LabScope | None,
    encounters: list[Encounter] | None,
) -> str:
    parts = [*section_parts(section_counts), _lab_part(lab_scope), _visit_part(encounters)]
    return (
        "Answered only from what was retrieved for this turn: "
        + ", ".join(parts)
        + ". Nothing else from the chart was retrieved."
    )


def render_refusal(turn: int, status: TurnStatus, refusals: list[RefusalCategory]) -> str:
    lines = [f"Follow-up answer (turn {turn})", ""]
    lines.append(
        OUT_OF_SCOPE_HEADLINE if status is TurnStatus.REFUSED_OUT_OF_SCOPE else UNVERIFIABLE_HEADLINE
    )
    lines.extend(f"  - {REFUSAL_WORDING[category]}" for category in refusals)
    lines.append("")
    if status is TurnStatus.REFUSED_UNVERIFIABLE:
        lines.extend([UNVERIFIABLE_HINT, ""])
    lines.append(REFUSAL_FOOTER)
    return "\n".join(lines)


def render_turn(
    turn: int,
    status: TurnStatus,
    gaps: list[ConversationGap],
    claim_lines: list[str],
    results: list[VerificationResult],
    retrieved_types: set[FhirResourceType],
    closing_line: str,
) -> str:
    """
    The same trust model as follow_up.render_follow_up: every claim
    renders, in drafted order, verified ones plainly and failed ones as
    UNVERIFIED with the reason; when nothing verified the answer opens
    with NOT_IN_RECORD; gaps are fixed sentences; the closing line states
    the bounded set from retrieval counts.
    """
    lines = [f"Follow-up answer (turn {turn})", ""]
    if status is TurnStatus.ANSWERED:
        lines.append("From the records retrieved for this turn:")
    elif status is TurnStatus.PARTIALLY_ANSWERED:
        lines.append("Partly answered from the records retrieved for this turn:")
    else:
        lines.append(NOT_IN_RECORD)

    lines.extend(claim_lines)

    if status is not TurnStatus.ANSWERED and gaps:
        if status is TurnStatus.PARTIALLY_ANSWERED:
            lines.extend(["", "The rest of that isn't something the retrieved record can answer:"])
        elif results:
            lines.extend(["", "Not in the retrieved record:"])
        lines.extend(f"  - {_gap_sentence(gap, retrieved_types)}" for gap in gaps)

    unverified = sum(1 for result in results if not result.passed)
    if unverified:
        lines.extend([
            "",
            f"{unverified} item(s) above could not be verified against the "
            "chart and are marked UNVERIFIED.",
        ])

    lines.extend(["", closing_line])
    return "\n".join(lines)


# -- the turn ------------------------------------------------------------------


async def _retrieve(
    token: str,
    patient_id: str,
    key: str,
    decision: RoutingDecision,
) -> tuple[dict[FhirResourceType, list[Any]], list[Observation] | None, list[Encounter] | None]:
    """Base set always; Observation and Encounter only when routing says so."""

    async def none() -> None:
        return None

    wants_labs = FhirResourceType.OBSERVATION in decision.expansions
    wants_visits = FhirResourceType.ENCOUNTER in decision.expansions
    base, observations, encounters = await asyncio.gather(
        fetch_bounded_set(token, patient_id, key),
        fhir_client.get_observations(
            token, patient_id, use_case=_USE_CASE, patient_session_key=key
        )
        if wants_labs
        else none(),
        fhir_client.get_encounters(
            token, patient_id, ENCOUNTER_COUNT, use_case=_USE_CASE, patient_session_key=key
        )
        if wants_visits
        else none(),
    )
    return base, observations, encounters


async def run_conversation_turn(
    token: str,
    user: str,
    patient_id: str,
    session_id: str,
    question: str,
    *,
    store: ConversationStore | None = None,
) -> TurnResult:
    """
    One turn of the conversation for (user, patient_id, session_id).

    `token` is the requesting clinician's own bearer token (§2) and `user`
    its `sub`. Exceptions from the Patient read, retrieval or drafting
    propagate: nothing renders, nothing is stored, and no latency_event is
    logged for a turn that did not render.
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

    store = STORE if store is None else store
    key = patient_session_key(user, patient_id, session_id)
    trigger_ts = time.time()
    correlation_id = get_correlation_id() or str(uuid.uuid4())
    step_seconds: dict[str, float] = {}

    with correlation_scope(correlation_id):
        started = time.perf_counter()

        # authorize: OpenEMR accepts the token (proving `user`) and knows
        # the patient, before any conversation state is touched.
        try:
            await fhir_client.ensure_patient_exists(token, patient_id)
        except PatientNotFoundError:
            dropped = store.discard_if_bound_elsewhere(user, session_id, patient_id)
            if dropped is not None:
                _log_invalidation(dropped, user, session_id, key)
            raise

        snapshot = store.open(user, session_id, patient_id)
        if snapshot.invalidation is not None:
            _log_invalidation(snapshot.invalidation, user, session_id, key)

        decision = route(question, snapshot.context_resource_types)
        log_event(
            "conversation_turn_routed",
            event_data={
                "correlation_id": correlation_id,
                "turn": snapshot.turn,
                "transition": snapshot.transition.value,
                "context_turns": len(snapshot.context),
                "context_claims": snapshot.context_claim_count,
                "rules": list(decision.rule_ids),
                "refusals": [category.value for category in decision.refusals],
                "expansions": {
                    resource_type.value: rule.value
                    for resource_type, rule in decision.expansions.items()
                },
            },
            use_case=_USE_CASE,
            patient_session_key=key,
        )

        bounded: dict[FhirResourceType, list[Any]] = {}
        all_observations: list[Observation] = []
        encounters: list[Encounter] | None = None
        lab_scope: LabScope | None = None
        retrieval: list[RetrievalSummary] = []
        if not decision.refused:
            base, observations, encounters = await _retrieve(token, patient_id, key, decision)
            bounded = dict(base)
            retrieval = [
                RetrievalSummary(resource_type, RetrievalRule.BASE_SET, len(records), len(records))
                for resource_type, records in base.items()
            ]
            if observations is not None:
                all_observations = observations
                context_ids = frozenset(
                    claim.source_id
                    for turn in snapshot.context
                    for claim in turn.claims
                    if claim.resource_type is FhirResourceType.OBSERVATION
                )
                narrowed, lab_scope = narrow_observations(
                    observations, decision.lab_codes, context_ids
                )
                bounded[FhirResourceType.OBSERVATION] = narrowed
                retrieval.append(
                    RetrievalSummary(
                        FhirResourceType.OBSERVATION,
                        decision.expansions[FhirResourceType.OBSERVATION],
                        len(observations),
                        len(narrowed),
                    )
                )
            if encounters is not None:
                bounded[FhirResourceType.ENCOUNTER] = encounters
                retrieval.append(
                    RetrievalSummary(
                        FhirResourceType.ENCOUNTER,
                        decision.expansions[FhirResourceType.ENCOUNTER],
                        len(encounters),
                        len(encounters),
                    )
                )
        step_seconds["retrieve"] = time.perf_counter() - started

        started = time.perf_counter()
        if decision.refused:
            draft = TurnDraft(claims=[], coverage=Coverage.NONE, gaps=[])
        else:
            draft = await draft_conversation_turn(
                bounded, question, snapshot.context, patient_session_key=key
            )
        step_seconds["draft"] = time.perf_counter() - started

        # The same verifier as the brief, over the same mapping the draft
        # step saw. Context from earlier turns is not part of it.
        started = time.perf_counter()
        results = verify_claims(
            draft.claims, bounded, use_case=_USE_CASE, patient_session_key=key
        )
        step_seconds["verify"] = time.perf_counter() - started

        started = time.perf_counter()
        status = turn_status(decision, draft.coverage, draft.gaps, results)
        section_counts = _section_counts(
            {rt: records for rt, records in bounded.items() if rt in BOUNDED_RESOURCE_MODELS}
        )
        claim_lines = [_turn_claim_line(result, bounded, all_observations) for result in results]
        if decision.refused:
            text = render_refusal(snapshot.turn, status, list(decision.refusals))
        else:
            text = render_turn(
                snapshot.turn,
                status,
                draft.gaps,
                claim_lines,
                results,
                set(bounded),
                _closing_line(section_counts, lab_scope, encounters),
            )
        state_saved = store.commit(snapshot, results)
        rendered_ts = time.time()
        log_event(
            "conversation_turn_completed",
            event_data={
                "correlation_id": correlation_id,
                "turn": snapshot.turn,
                "status": status.value,
                "claims": len(results),
                "verified": sum(1 for result in results if result.passed),
                "state_saved": state_saved,
            },
            use_case=_USE_CASE,
            patient_session_key=key,
        )
        log_event(
            LATENCY_EVENT,
            event_data=LatencyEvent(
                use_case=_USE_CASE,
                trigger_ts=trigger_ts,
                rendered_ts=rendered_ts,
                correlation_id=correlation_id,
            ).model_dump(mode="json"),
            use_case=_USE_CASE,
            patient_session_key=key,
        )
        step_seconds["render"] = time.perf_counter() - started

    return TurnResult(
        text=text,
        status=status,
        gaps=draft.gaps,
        refusals=list(decision.refusals),
        correlation_id=correlation_id,
        patient_session_key=key,
        trigger_ts=trigger_ts,
        rendered_ts=rendered_ts,
        step_seconds=step_seconds,
        verification_results=results,
        claim_lines=claim_lines,
        retrieval=retrieval,
        section_counts=section_counts,
        snapshot=snapshot,
        state_saved=state_saved,
    )


def _log_invalidation(dropped: Invalidation, user: str, session_id: str, key: str) -> None:
    log_event(
        "conversation_invalidated",
        event_data={
            "correlation_id": get_correlation_id() or "no-request-context",
            "reason": dropped.reason.value,
            "previous_patient_session_key": patient_session_key(
                user, dropped.previous_patient_id, session_id
            ),
            "turns_discarded": dropped.turns_discarded,
            "claims_discarded": dropped.claims_discarded,
        },
        use_case=_USE_CASE,
        patient_session_key=key,
    )
