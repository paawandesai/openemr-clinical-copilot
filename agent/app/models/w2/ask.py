"""
The Week 2 ask path's HTTP contracts — `POST /w2/ask` (app/api/ask.py).

One request shape and one response shape for every outcome. A refusal, a
document still processing, a failed tool and an answer are all HTTP 200 with
a typed `status` and a `reason_code` from the supervisor's closed set
(`RoutingReasonCode`): the clinician-facing text is fixed wording chosen by
that code, never exception text and never chart content the verifier has not
passed. HTTP errors are kept for what is not a clinical outcome at all — no
token, a token OpenEMR rejects, no such patient, a malformed request.

**What the response is accountable to.** `answer_view.answer` is a
`FinalAnswer`, and every claim in its two lists passed the deterministic
verifier (app/verification/w2_checker.py). A drafted claim that failed is
not in those lists — a renderer that drew them would draw it as fact — and
is not dropped either: it is in `unverified`, and its line is in `message`
(invariant 4; Week 1's disclosure rule, ARCHITECTURE.md §5).

**The handoffs are inspectable.** `routing` is every supervisor decision in
order, exactly as logged; `handoffs` is what each worker was given and what
it handed back, as ids, statuses and counts. Neither carries free text.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from app.models.conversation import ConversationGap, ConversationTransition
from app.models.w2.answer_view import AnswerView
from app.models.w2.base import CORRELATION_ID_PATTERN, PATIENT_UUID_PATTERN, W2Contract
from app.models.w2.citations import Citation
from app.models.w2.intake_form import Discrepancy
from app.models.w2.source_types import SourceType
from app.models.w2.workers import RoutingDecision, RoutingReasonCode

#: Same rule as Week 1's SESSION_ID_PATTERN (app/models/api.py): the id is the
#: third part of a colon-separated patient_session_key.
SESSION_ID_PATTERN = r"^[A-Za-z0-9-]{8,64}$"

#: Same bound as Week 1's follow-up question.
QUESTION_MAX_LENGTH = 500

#: A turn names at most this many documents. More is not a question a
#: clinician asks in 90 seconds, and each one adds to the drafting prompt.
MAX_DOCUMENTS_PER_TURN = 4


class AskRequest(W2Contract):
    """One turn. The caller sends no records, claims or history."""

    patient_id: str = Field(
        pattern=PATIENT_UUID_PATTERN,
        description="FHIR Patient id of the open chart. A session is bound to one patient.",
    )
    session_id: str = Field(pattern=SESSION_ID_PATTERN)
    question: str = Field(
        min_length=1,
        max_length=QUESTION_MAX_LENGTH,
        description="The physician's question. Never logged, never stored.",
    )
    document_ids: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(
        default_factory=list,
        max_length=MAX_DOCUMENTS_PER_TURN,
        description=(
            "Uploaded documents this question is about, by the id POST /documents "
            "returned. Empty for a chart or guideline question."
        ),
    )


class AskStatus(StrEnum):
    ANSWERED = "answered"
    PARTIALLY_ANSWERED = "partially_answered"
    #: Nothing the sources hold answers it. Not a refusal: the question was
    #: in scope and was looked up.
    NOT_IN_RECORD = "not_in_record"
    REFUSED = "refused"


class AnswerLimitation(StrEnum):
    """Typed caveats, each rendered as fixed wording in `message`."""

    #: Present whenever guideline evidence is. The verifier confirms that a
    #: guideline passage says what is quoted; it cannot confirm that the
    #: passage applies to this patient (w2_checker.py, "Known limitation").
    GUIDELINE_APPLICABILITY_NOT_VERIFIED = "guideline_applicability_not_verified"
    #: Some values on a document in this turn could not be located on the
    #: page and are listed as unsupported, not stated (invariant 4).
    UNSUPPORTED_DOCUMENT_VALUES = "unsupported_document_values"
    #: A drafted claim failed verification and is shown as UNVERIFIED.
    UNVERIFIED_CLAIMS = "unverified_claims"
    #: Retrieval ran without the reranker (D25 latency budget or circuit).
    RERANK_SKIPPED = "rerank_skipped"
    #: A cited guideline source is flagged dated by the corpus registry.
    DATED_GUIDELINE_SOURCE = "dated_guideline_source"
    #: Lab results the scope rule did not itemise are counted in a cited
    #: statement instead (app/supervisor/scope.py, D49).
    ANSWER_SCOPED = "answer_scoped"
    #: More lab results were in scope than the answer itemises; the rest are
    #: named, not stated (D49).
    ANSWER_CAPPED = "answer_capped"
    #: The drafting call reached its output ceiling; only what code states by
    #: rule is shown (D49).
    OUTPUT_LIMIT_REACHED = "output_limit_reached"


class UnverifiedClaim(W2Contract):
    """A drafted claim the verifier did not pass, as it is disclosed."""

    source_type: SourceType
    source_id: str
    field_or_chunk_id: str
    reason: str = Field(description="A closed-set failure reason (w2_checker.W2FailureReason).")
    line: str = Field(description="The UNVERIFIED line exactly as it appears in `message`.")


class HandoffRecord(W2Contract):
    """
    One worker visit: what the supervisor handed over and what came back.
    Ids, statuses and counts only (invariant 1); `node` names the worker.
    """

    sequence: int = Field(ge=1)
    node: str
    handed: str = Field(description="The contract handed to the worker, by type name.")
    returned: str = Field(description="The contract handed back, by type name.")
    document_ids: list[str] = Field(default_factory=list)
    extraction_statuses: list[str] = Field(default_factory=list)
    concept_count: int = 0
    hit_count: int = 0
    reranked: bool | None = None
    guideline_ids: list[str] = Field(default_factory=list)
    failure: RoutingReasonCode | None = None
    latency_ms: int = 0


class CriticalValue(W2Contract):
    """
    A value beyond the critical-value table (app/supervisor/critical.py),
    surfaced at the top of the answer whatever the question was about.

    `stated=False` is a *possible* critical value: the model read it on the
    document but the locate step could not place it, so the number is not
    stated (invariant 4) and nothing is cited as fact — the line names the
    test and page and says to open the document.
    """

    source_type: Literal[SourceType.CHART, SourceType.DOCUMENT]
    source_id: str = Field(min_length=1)
    analyte: str = Field(min_length=1, description="The table's analyte label.")
    direction: Literal["high", "low"]
    critical_limit: float
    unit: str
    stated: bool
    page: int | None = Field(default=None, ge=1)
    citations: list[Citation] = Field(default_factory=list)
    line: str = Field(description="The line exactly as it appears in `message`.")

    @model_validator(mode="after")
    def _stated_means_cited(self) -> Self:
        if self.stated and not self.citations:
            raise ValueError("a stated critical value cites its source")
        if not self.stated and self.citations:
            raise ValueError("a possible critical value states nothing, so cites nothing")
        for citation in self.citations:
            if citation.source_type is not self.source_type:
                raise ValueError("a critical value cites its own source type only")
            if citation.source_type is SourceType.DOCUMENT and citation.bbox is None:
                raise ValueError("a stated document value is a located one (invariant 4)")
        return self


class AskConversation(W2Contract):
    turn: int
    transition: ConversationTransition
    context_turns: int
    state_saved: bool


class AskResponse(W2Contract):
    status: AskStatus
    reason_code: RoutingReasonCode = Field(
        description="The supervisor's last decision: why the turn ended where it did."
    )
    message: str = Field(description="Clinician-facing text. Fixed wording plus verified lines.")
    answer_view: AnswerView | None = Field(
        default=None, description="Present when the turn reached the answer step."
    )
    unverified: list[UnverifiedClaim] = Field(default_factory=list)
    critical_values: list[CriticalValue] = Field(
        default_factory=list,
        description="Panic values in the sources this turn read, surfaced first (H6).",
    )
    discrepancies: list[Discrepancy] = Field(
        default_factory=list,
        description="Intake form vs chart, surfaced for a human; never merged (invariant 5).",
    )
    limitations: list[AnswerLimitation] = Field(default_factory=list)
    gaps: list[ConversationGap] = Field(default_factory=list)
    routing: list[RoutingDecision] = Field(min_length=1)
    handoffs: list[HandoffRecord] = Field(default_factory=list)
    conversation: AskConversation
    patient_session_key: str
    correlation_id: str = Field(pattern=CORRELATION_ID_PATTERN)
    node_ms: dict[str, int] = Field(default_factory=dict)
    total_ms: int = 0
