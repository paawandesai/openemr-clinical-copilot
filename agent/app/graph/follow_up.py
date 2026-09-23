"""
Constrained single-turn follow-up — the smallest real form of USERS.md §3
Use Case 3 (Follow-Up Drilling), built so that it keeps the Pre-Visit
Brief's verification guarantee intact.

    retrieve → draft → verify → render

  retrieve  Exactly the brief's retrieval: the Patient read, then
            Condition, MedicationRequest and AllergyIntolerance, under the
            caller's own token (ARCHITECTURE.md §2), through the same
            app/fhir/client.py functions. All three on every run, so a
            type that was never queried can never render like one that
            returned nothing (§5). Encounter and Observation are never
            queried: a question about labs or visits is answered "not in
            the retrieved record", not by fetching more.
  draft     app/reasoning/draft_follow_up.py: claims plus coverage and
            gaps. The only LLM call.
  verify    app/verification/checker.py:verify_claims, unchanged, against
            the same `retrieved` mapping the draft step was given — one
            VerificationResult and one claim_verified_event per claim.
  render    Plain code (render_follow_up below). Verified claims plainly,
            failed ones as UNVERIFIED with the brief's wording, never
            dropped; what the record does not hold in fixed sentences.

What this deliberately is not, compared with Use Case 3 as USERS.md and
ARCHITECTURE.md §3–§4 describe it:
- No conversation state. Nothing is stored between requests; each
  question stands alone, and the caller cannot send records, claims or
  earlier answers (app/models/api.py:FollowUpRequest). Re-reading the
  records under the caller's token, rather than accepting them in the
  request, is what keeps the verified-against set out of the caller's
  hands: a caller-supplied set would let any claim "verify".
- No re-query of other resource types, and no multi-record synthesis
  claims (§5 known limitation): a claim is always one value of one record.
- The records are those current at the time of the question. If the
  chart changed since the brief rendered, the answer and its verification
  both reflect the chart now — never a mix.

Not a LangGraph graph: a fixed four-step sequence needs no graph runtime,
and without one there is no graph state for a LangSmith tracer to post
(docs/audit/findings.md F-56). No LangChain runnable is involved anywhere
below.

Every event this run logs carries use_case="follow_up_drilling" and the
caller's patient_session_key: retrieval_event, llm_usage_event,
claims_drafted, claim_verified_event, latency_event. No event carries the
question, the answer, claim values or record contents.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any

from app.fhir import client as fhir_client
from app.graph.pre_visit_brief import NAME_NOT_AVAILABLE
from app.middleware.correlation import correlation_scope, get_correlation_id
from app.middleware.langfuse_export import LATENCY_EVENT
from app.middleware.logging import log_event
from app.middleware.telemetry import record_steps
from app.models.claims import VerificationResult
from app.models.fhir_resources import FhirResourceType
from app.models.follow_up import AnswerStatus, RecordGap
from app.models.metrics_events import LatencyEvent, UseCase
from app.models.telemetry_events import Pipeline
from app.reasoning.draft_claims import SectionCount, brief_records
from app.reasoning.draft_follow_up import Coverage, draft_follow_up
from app.verification.checker import FailureReason, verify_claims
from app.verification.disclosure import audit_disclosure

_USE_CASE = UseCase.FOLLOW_UP_DRILLING

STEPS = ("retrieve", "draft", "verify", "render")

NOT_IN_RECORD = "That information isn't in the retrieved record for this visit."

# Same physician-facing reasons as the brief (app/graph/pre_visit_brief.py).
_UNVERIFIED_WHY: dict[FailureReason, str] = {
    FailureReason.SOURCE_NOT_FOUND: "no matching record in the chart",
    FailureReason.FIELD_NOT_ON_SOURCE: "not a field the chart record has",
    FailureReason.VALUE_MISMATCH: "does not match the chart record",
    FailureReason.NO_NAME_ON_SOURCE: "the chart record has no usable name; review it in the chart",
}

GAP_WORDING: dict[RecordGap, str] = {
    RecordGap.LAB_RESULTS: "Lab results were not retrieved for this visit.",
    RecordGap.VISIT_HISTORY: "Visit history was not retrieved for this visit.",
    RecordGap.OTHER_CHART_DATA: (
        "That part of the chart (vitals, notes, procedures, immunizations, "
        "history) was not retrieved for this visit."
    ),
    RecordGap.NOT_STATED_IN_RECORD: (
        "The retrieved records do not state it (for example, why a "
        "medication was started or stopped)."
    ),
    RecordGap.INTERPRETATION: (
        "The co-pilot reports what the chart states; it does not interpret, compare or trend it."
    ),
}

# Record label, and label per claimable field, per bounded record type.
_RECORD_LABEL: dict[FhirResourceType, str] = {
    FhirResourceType.CONDITION: "Problem",
    FhirResourceType.MEDICATION_REQUEST: "Medication",
    FhirResourceType.ALLERGY_INTOLERANCE: "Allergy",
    # Retrieved only by the multi-turn conversation (app/graph/conversation.py).
    FhirResourceType.OBSERVATION: "Lab result",
    FhirResourceType.ENCOUNTER: "Visit",
}
_FIELD_LABEL: dict[str, str] = {
    "clinical_status": "clinical status",
    "onset_date": "onset date",
    "status": "status",
    "rxnorm_code": "RxNorm code",
    "authored_date": "date prescribed",
    "recorded_date": "date recorded",
    "loinc_code": "LOINC code",
    "effective_date": "date",
    "date": "date",
}

# Closing-line wording per section: (name when nothing was retrieved,
# record noun, what counts as in the brief — "" when every record does).
_SECTION_NOUNS: dict[FhirResourceType, tuple[str, str, str]] = {
    FhirResourceType.CONDITION: ("problems", "problem records", "active"),
    FhirResourceType.MEDICATION_REQUEST: ("medications", "medication records", "active"),
    FhirResourceType.ALLERGY_INTOLERANCE: ("allergies", "allergy records", ""),
}


@dataclass(frozen=True)
class FollowUpResult:
    """
    The rendered answer plus what the response and latency report need.
    `step_seconds` has one entry per name in STEPS; `total_seconds` is the
    trigger-to-render interval the logged latency_event carries
    (KEY_METRICS.md Metric 4, reported separately for this use case).
    """

    text: str
    answer_status: AnswerStatus
    gaps: list[RecordGap]
    correlation_id: str
    patient_session_key: str
    trigger_ts: float
    rendered_ts: float
    step_seconds: dict[str, float]
    verification_results: list[VerificationResult]
    section_counts: dict[FhirResourceType, SectionCount]

    @property
    def total_seconds(self) -> float:
        return self.rendered_ts - self.trigger_ts


async def retrieve_bounded_set(
    token: str, patient_id: str, patient_session_key: str
) -> dict[FhirResourceType, list[Any]]:
    """The Pre-Visit Brief's retrieval, tagged follow_up_drilling."""
    # Raises PatientNotFoundError before any search runs (§5): FHIR search
    # answers an unknown patient id with empty results.
    await fhir_client.ensure_patient_exists(token, patient_id)
    return await fetch_bounded_set(token, patient_id, patient_session_key)


async def fetch_bounded_set(
    token: str, patient_id: str, patient_session_key: str
) -> dict[FhirResourceType, list[Any]]:
    """
    The three searches alone, for a caller that has already made the
    Patient read this turn (app/graph/conversation.py). Never call it
    without one: see retrieve_bounded_set.
    """
    conditions, medications, allergies = await asyncio.gather(
        fhir_client.get_conditions(
            token, patient_id, use_case=_USE_CASE, patient_session_key=patient_session_key
        ),
        fhir_client.get_medication_requests(
            token, patient_id, use_case=_USE_CASE, patient_session_key=patient_session_key
        ),
        fhir_client.get_allergy_intolerances(
            token, patient_id, use_case=_USE_CASE, patient_session_key=patient_session_key
        ),
    )
    return {
        FhirResourceType.CONDITION: conditions,
        FhirResourceType.MEDICATION_REQUEST: medications,
        FhirResourceType.ALLERGY_INTOLERANCE: allergies,
    }


def answer_status(
    coverage: Coverage, gaps: list[RecordGap], results: list[VerificationResult]
) -> AnswerStatus:
    """
    What the model drafted can only lower the status, never raise it past
    what verification supports: no verified claim means nothing verified
    backs an answer, whatever coverage says. ANSWERED needs all three of
    full coverage, no gap named, and every claim verified.
    """
    if not any(result.passed for result in results):
        return AnswerStatus.NOT_IN_RECORD
    if coverage is Coverage.FULL and not gaps and all(result.passed for result in results):
        return AnswerStatus.ANSWERED
    return AnswerStatus.PARTIALLY_ANSWERED


def _section_counts(
    retrieved: dict[FhirResourceType, list[Any]],
) -> dict[FhirResourceType, SectionCount]:
    return {
        resource_type: SectionCount(
            retrieved=len(records),
            in_brief=len(brief_records(resource_type, records)),
        )
        for resource_type, records in retrieved.items()
    }


def _display_value(value: str | float | int | None) -> str:
    return "not recorded" if value is None else str(value)


def _claim_line(result: VerificationResult, retrieved: dict[FhirResourceType, list[Any]]) -> str:
    claim = result.claim
    record_label = _RECORD_LABEL.get(claim.resource_type, claim.resource_type.value)
    if not result.passed:
        why = _UNVERIFIED_WHY[FailureReason(result.reason)]
        if claim.field == "display":
            # A null display claim is about a record with no name (F-57);
            # it always fails, and says so the way the brief does.
            name = NAME_NOT_AVAILABLE if claim.value is None else claim.value
            return f'  - UNVERIFIED: {record_label} "{name}" ({why})'
        field_label = _FIELD_LABEL.get(claim.field, claim.field)
        return (
            f'  - UNVERIFIED: {record_label} {field_label} "{_display_value(claim.value)}" ({why})'
        )
    if claim.field == "display":
        return f"  - {record_label}: {claim.value}"
    # A verified claim's source is in `retrieved` by definition; its
    # display names which record the value belongs to. Read from the
    # chart record itself, not from anything the model wrote.
    record = next(r for r in retrieved[claim.resource_type] if r.source_id == claim.source_id)
    field_label = _FIELD_LABEL.get(claim.field, claim.field)
    name = NAME_NOT_AVAILABLE if record.display is None else f'"{record.display}"'
    return f"  - {record_label} {name}, {field_label}: {_display_value(claim.value)}"


def section_parts(section_counts: dict[FhirResourceType, SectionCount]) -> list[str]:
    """One phrase per bounded section, from retrieval counts alone (F-21)."""
    parts = []
    for resource_type, (name, noun, in_brief_word) in _SECTION_NOUNS.items():
        counts = section_counts[resource_type]
        if counts.retrieved == 0:
            parts.append(f"{name} not recorded")
        elif in_brief_word and counts.in_brief == 0:
            parts.append(f"{counts.retrieved} {noun} (none {in_brief_word})")
        elif in_brief_word:
            parts.append(f"{counts.retrieved} {noun} ({counts.in_brief} {in_brief_word})")
        else:
            parts.append(f"{counts.retrieved} {noun}")
    return parts


def _retrieved_summary(section_counts: dict[FhirResourceType, SectionCount]) -> str:
    return (
        "Answered only from what was retrieved for this visit: "
        + ", ".join(section_parts(section_counts))
        + ". Nothing else from the chart was retrieved."
    )


def render_follow_up(
    status: AnswerStatus,
    gaps: list[RecordGap],
    results: list[VerificationResult],
    retrieved: dict[FhirResourceType, list[Any]],
    section_counts: dict[FhirResourceType, SectionCount],
) -> str:
    """
    Plain code, no LLM, and the same trust model as render_brief:

    - Every claim renders, in drafted order. Verified ones plainly; failed
      ones as UNVERIFIED with the reason, never dropped or softened (§5).
    - When nothing verified, the answer opens with NOT_IN_RECORD — the
      same honest refusal as "not recorded" in the brief.
    - Gaps render as fixed sentences whenever the answer is not complete.
    - The closing line states the bounded set from retrieval counts, so
      an empty allergy retrieval reads "allergies not recorded" rather
      than implying none are on file (F-21).
    """
    lines = ["Follow-up answer", ""]
    if status is AnswerStatus.ANSWERED:
        lines.append("From the records retrieved for this visit:")
    elif status is AnswerStatus.PARTIALLY_ANSWERED:
        lines.append("Partly answered from the records retrieved for this visit:")
    else:
        lines.append(NOT_IN_RECORD)

    lines.extend(_claim_line(result, retrieved) for result in results)

    if status is not AnswerStatus.ANSWERED and gaps:
        if status is AnswerStatus.PARTIALLY_ANSWERED:
            lines.extend(
                ["", "The rest of that information isn't in the retrieved record for this visit."]
            )
        elif results:
            # Keep gap sentences apart from the UNVERIFIED lines above them.
            lines.extend(["", "Not in the retrieved record:"])
        lines.extend(f"  - {GAP_WORDING[gap]}" for gap in gaps)

    unverified = sum(1 for result in results if not result.passed)
    if unverified:
        lines.append("")
        lines.append(
            f"{unverified} item(s) above could not be verified against the "
            "chart and are marked UNVERIFIED."
        )

    lines.append("")
    lines.append(_retrieved_summary(section_counts))
    return "\n".join(lines)


async def run_follow_up(
    token: str,
    patient_id: str,
    question: str,
    patient_session_key: str,
) -> FollowUpResult:
    """
    Answers one follow-up question for one patient.

    `token` is the requesting clinician's own bearer token (§2).
    `patient_session_key` is the caller's (user, patient, session) key
    (§4), attributed to every event this run logs; the caller owns its
    construction.

    Uses the request's bound correlation_id if there is one, otherwise
    binds a fresh one for this run only. Exceptions from retrieval or
    drafting propagate; nothing renders and no latency_event is logged for
    a run that did not render.
    """
    for name, value in (
        ("token", token),
        ("patient_id", patient_id),
        ("question", question),
        ("patient_session_key", patient_session_key),
    ):
        if not value.strip():
            raise ValueError(f"{name} must be a non-empty string")

    trigger_ts = time.time()
    correlation_id = get_correlation_id() or str(uuid.uuid4())
    step_seconds: dict[str, float] = {}

    with correlation_scope(correlation_id):
        started = time.perf_counter()
        retrieved = await retrieve_bounded_set(token, patient_id, patient_session_key)
        step_seconds["retrieve"] = time.perf_counter() - started

        started = time.perf_counter()
        draft = await draft_follow_up(retrieved, question, patient_session_key=patient_session_key)
        step_seconds["draft"] = time.perf_counter() - started

        # The same verifier, over the same mapping the draft step saw.
        started = time.perf_counter()
        results = verify_claims(
            draft.claims,
            retrieved,
            use_case=_USE_CASE,
            patient_session_key=patient_session_key,
        )
        step_seconds["verify"] = time.perf_counter() - started

        started = time.perf_counter()
        status = answer_status(draft.coverage, draft.gaps, results)
        section_counts = _section_counts(retrieved)
        text = render_follow_up(status, draft.gaps, results, retrieved, section_counts)
        # After the render call, never inside it (findings.md F-59).
        audit_disclosure(
            results,
            text,
            lambda result: _claim_line(result, retrieved),
            use_case=_USE_CASE,
            patient_session_key=patient_session_key,
        )
        rendered_ts = time.time()
        log_event(
            LATENCY_EVENT,
            event_data=LatencyEvent(
                use_case=_USE_CASE,
                trigger_ts=trigger_ts,
                rendered_ts=rendered_ts,
                correlation_id=correlation_id,
            ).model_dump(mode="json"),
            use_case=_USE_CASE,
            patient_session_key=patient_session_key,
        )
        step_seconds["render"] = time.perf_counter() - started
        record_steps(
            Pipeline.FOLLOW_UP,
            step_seconds,
            use_case=_USE_CASE,
            patient_session_key=patient_session_key,
        )

    return FollowUpResult(
        text=text,
        answer_status=status,
        gaps=draft.gaps,
        correlation_id=correlation_id,
        patient_session_key=patient_session_key,
        trigger_ts=trigger_ts,
        rendered_ts=rendered_ts,
        step_seconds=step_seconds,
        verification_results=results,
        section_counts=section_counts,
    )
