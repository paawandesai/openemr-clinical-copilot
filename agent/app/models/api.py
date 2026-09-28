"""
HTTP contracts for the agent service — the source of truth for what
POST /pre-visit-brief, POST /pre-visit-brief/follow-up,
POST /pre-visit-brief/conversation (and /conversation/end) and GET /ready
accept and return.

Every model forbids unknown fields and coerces nothing (strict), so a
request that does not match the contract is rejected with 422 rather than
reinterpreted. The clinician's bearer token is deliberately not a field
anywhere here: it travels only in the Authorization header
(ARCHITECTURE.md §2), so it cannot end up in a logged or echoed body.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny, field_validator

from app.models.conversation import (
    ConversationGap,
    ConversationTransition,
    RefusalCategory,
    RetrievalRule,
    TurnStatus,
)
from app.models.fhir_resources import FhirResourceType
from app.models.follow_up import AnswerStatus, RecordGap

# OpenEMR's FHIR ids are lowercase UUIDs (confirmed live). A canonical form
# only: anything else is rejected at the boundary instead of being sent to
# FHIR, which answers a malformed id in a search with empty results.
PATIENT_ID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"

# Letters, digits and hyphens only: the id becomes the third part of the
# colon-separated patient_session_key, so it can never contain a colon.
SESSION_ID_PATTERN = r"^[A-Za-z0-9-]{8,64}$"

# One clarifying question (USERS.md §2), not a document.
FOLLOW_UP_QUESTION_MAX_LENGTH = 500


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class PreVisitBriefRequest(_Contract):
    patient_id: str = Field(
        pattern=PATIENT_ID_PATTERN,
        description="FHIR Patient id (lowercase UUID) of the chart being opened.",
        examples=["a2c0e096-23d5-4cf9-ad5b-4895597363a7"],
    )
    session_id: str | None = Field(
        default=None,
        pattern=SESSION_ID_PATTERN,
        description=(
            "The caller's session for this chart. Omit to have one generated; "
            "reuse the returned value to keep later calls in the same session."
        ),
    )


class VerificationSummary(_Contract):
    claims: int
    verified: int
    unverified: int


class ClaimOutcome(_Contract):
    resource_type: FhirResourceType
    source_id: str
    field: str
    value: str | float | int | None
    verified: bool
    unverified_reason: str | None = Field(
        description="Why verification failed; null when verified."
    )


class SectionSummary(_Contract):
    resource_type: FhirResourceType
    retrieved: int = Field(description="Records FHIR returned for this section.")
    in_brief: int = Field(description="Of those, the ones the brief reports on (active).")


class Timing(_Contract):
    retrieve_seconds: float
    draft_seconds: float
    verify_seconds: float
    render_seconds: float
    total_seconds: float = Field(
        description="Pipeline start to brief rendered; the logged latency_event interval."
    )


class PreVisitBriefResponse(_Contract):
    brief: str = Field(description="The physician-facing Pre-Visit Brief text.")
    patient_id: str
    session_id: str
    patient_session_key: str = Field(
        description="(user, patient, session) as user:patient:session — ARCHITECTURE.md §4."
    )
    correlation_id: str
    verification: VerificationSummary
    claims: list[ClaimOutcome]
    sections: list[SectionSummary]
    timing: Timing


class FollowUpRequest(_Contract):
    """
    One follow-up question about the brief just rendered. Deliberately no
    field for records, claims or earlier questions: the records are
    re-read from FHIR under the caller's token, so the set every claim is
    verified against is never caller-supplied, and nothing carries over
    between questions.
    """

    patient_id: str = Field(
        pattern=PATIENT_ID_PATTERN,
        description="FHIR Patient id (lowercase UUID) of the brief being followed up.",
        examples=["a2c0e096-23d5-4cf9-ad5b-4895597363a7"],
    )
    session_id: str = Field(
        pattern=SESSION_ID_PATTERN,
        description=(
            "The session_id the brief returned. Required: the follow-up's "
            "events are attributed to the brief's patient_session_key."
        ),
    )
    question: str = Field(
        min_length=1,
        max_length=FOLLOW_UP_QUESTION_MAX_LENGTH,
        description="The physician's single follow-up question. Never logged.",
        examples=["When was the lisinopril prescribed, and is it still active?"],
    )

    @field_validator("question")
    @classmethod
    def _question_has_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must contain text")
        return value


class FollowUpResponse(_Contract):
    answer: str = Field(
        description=(
            "Physician-facing answer text: verified claims stated plainly, "
            "failed claims marked UNVERIFIED, and what the retrieved record "
            "does not hold stated as such."
        )
    )
    answer_status: AnswerStatus = Field(
        description=(
            "answered: every claim verified and the question fully covered. "
            "partially_answered: some verified claims, but part of the question "
            "is not in the retrieved record or a claim failed verification. "
            "not_in_record: no claim verified."
        )
    )
    gaps: list[RecordGap] = Field(
        description="Kinds of information the question needed that the retrieved record does not hold."
    )
    question: str
    patient_id: str
    session_id: str
    patient_session_key: str = Field(
        description="(user, patient, session) as user:patient:session — ARCHITECTURE.md §4."
    )
    correlation_id: str
    verification: VerificationSummary
    claims: list[ClaimOutcome]
    sections: list[SectionSummary] = Field(
        description="The bounded record set the answer was drawn from and verified against."
    )
    timing: Timing


class ConversationTurnRequest(_Contract):
    """
    One turn of the multi-turn follow-up conversation. Exactly the
    single-turn request: the caller cannot send history, context, records
    or claims. What the conversation remembers is held server-side, as the
    claims earlier turns verified (app/conversation/state.py), and every
    turn re-reads the chart under the caller's token.
    """

    patient_id: str = Field(
        pattern=PATIENT_ID_PATTERN,
        description=(
            "FHIR Patient id (lowercase UUID) of the open chart. A session is bound to one "
            "patient: a turn naming a different patient discards the session's conversation "
            "before it runs."
        ),
        examples=["a2c0e096-23d5-4cf9-ad5b-4895597363a7"],
    )
    session_id: str = Field(
        pattern=SESSION_ID_PATTERN,
        description="The conversation's session: the brief's session_id, or any id the caller keeps.",
    )
    question: str = Field(
        min_length=1,
        max_length=FOLLOW_UP_QUESTION_MAX_LENGTH,
        description="The physician's question for this turn. Never logged, never stored.",
        examples=["When was that medication prescribed?"],
    )

    @field_validator("question")
    @classmethod
    def _question_has_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must contain text")
        return value


class TurnClaim(ClaimOutcome):
    text: str = Field(
        description=(
            "The physician-facing line rendered for this claim, as it appears in `answer`: "
            "plain when verified, prefixed UNVERIFIED with the reason when not."
        )
    )


class RetrievalOutcome(_Contract):
    resource_type: FhirResourceType
    rule: RetrievalRule = Field(
        description=(
            "Why this type was read: base_set (every turn), question_rule (the question's "
            "wording matched a defined retrieval), context_carry (an earlier turn verified "
            "claims on it). Never chosen by the model."
        )
    )
    retrieved: int = Field(description="Records FHIR returned.")
    considered: int = Field(
        description="Records in the turn's bounded set, which claims were drafted from and verified against."
    )


class ConversationInfo(_Contract):
    turn: int = Field(description="1-based turn number within this conversation.")
    transition: ConversationTransition = Field(
        description=(
            "started / continued, or why earlier context was discarded before this turn: "
            "reset_patient_changed (the session held another patient's conversation), "
            "reset_expired."
        )
    )
    context_turns: int = Field(
        description="Earlier turns whose verified claims this turn was given."
    )
    context_claims: int = Field(description="Verified claims in that context.")
    state_saved: bool = Field(
        description=(
            "Whether this turn's verified claims were stored. False when the conversation "
            "was invalidated while the turn was in flight."
        )
    )
    expires_in_seconds: int = Field(
        description="Until this conversation's state is discarded if idle."
    )


class ConversationTurnResponse(_Contract):
    answer: str = Field(
        description=(
            "Physician-facing answer text: verified claims stated plainly, failed claims "
            "marked UNVERIFIED, gaps and refusals in fixed wording."
        )
    )
    turn_status: TurnStatus = Field(
        description=(
            "answered / partially_answered / not_in_record as for the single-turn follow-up. "
            "refused_out_of_scope: the question asks for a part of the chart the co-pilot "
            "does not retrieve. refused_unverifiable: it asks for a judgment, comparison or "
            "trend the verifier cannot check. Both refusals are decided by rule; nothing is "
            "retrieved and no model is called."
        )
    )
    refusals: list[RefusalCategory]
    gaps: list[ConversationGap]
    question: str
    patient_id: str
    session_id: str
    patient_session_key: str = Field(
        description="(user, patient, session) as user:patient:session — ARCHITECTURE.md §4."
    )
    correlation_id: str
    conversation: ConversationInfo
    retrieval: list[RetrievalOutcome] = Field(
        description="What this turn read from the chart, and by which rule. Empty for a refusal."
    )
    verification: VerificationSummary
    claims: list[TurnClaim]
    sections: list[SectionSummary]
    timing: Timing


class EndConversationRequest(_Contract):
    patient_id: str = Field(pattern=PATIENT_ID_PATTERN)
    session_id: str = Field(pattern=SESSION_ID_PATTERN)


class EndConversationResponse(_Contract):
    ended: bool = Field(description="Whether a stored conversation existed and was discarded.")
    session_id: str
    correlation_id: str


class ErrorCode(str, Enum):
    MISSING_BEARER_TOKEN = "missing_bearer_token"
    MALFORMED_BEARER_TOKEN = "malformed_bearer_token"
    FHIR_TOKEN_REJECTED = "fhir_token_rejected"
    INVALID_REQUEST = "invalid_request"
    PATIENT_NOT_FOUND = "patient_not_found"
    FHIR_UNAVAILABLE = "fhir_unavailable"
    BRIEF_GENERATION_FAILED = "brief_generation_failed"
    FOLLOW_UP_GENERATION_FAILED = "follow_up_generation_failed"
    INTERNAL_ERROR = "internal_error"
    # /documents only (app/api/upload_guard.py).
    INSUFFICIENT_SCOPE = "insufficient_scope"
    RATE_LIMITED = "rate_limited"
    EXTRACTION_CAPACITY = "extraction_capacity"


class ErrorResponse(_Contract):
    error: ErrorCode
    message: str = Field(description="Safe to show; never contains exception text.")
    correlation_id: str


class DependencyCheck(_Contract):
    status: Literal["healthy", "unhealthy"]
    required: bool = Field(description="Whether this check decides readiness.")
    detail: str | None = None


class ObservabilityCheck(DependencyCheck):
    backend: Literal["stdout", "langfuse"] = Field(
        description=(
            "Where telemetry goes. `stdout` is the JSON event log, the production "
            "path when Langfuse export is not configured (D55)."
        ),
    )


class ReadyResponse(_Contract):
    # `degraded` (D58): every required check is healthy and a reported one
    # is not, so the service answers (200) while its traces reach stdout
    # only. `not_ready` (503) wins over it.
    status: Literal["ready", "degraded", "not_ready"]
    # SerializeAsAny: `observability_backend` is an ObservabilityCheck, and its
    # `backend` must survive serialisation through the base type.
    checks: dict[str, SerializeAsAny[DependencyCheck]]


class VersionResponse(_Contract):
    # The git commit the image was built from, stamped at build time
    # (agent/Dockerfile, AGENT_GIT_COMMIT). "unknown" when the build was not
    # given one, so a regression harness can refuse to anchor on it.
    commit: str
    # UTC ISO-8601 build time (AGENT_BUILT_AT); null when not given one.
    built_at: str | None
