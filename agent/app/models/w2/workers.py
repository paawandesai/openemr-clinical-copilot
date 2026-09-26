"""
Worker and supervisor contracts — the intake-extractor, the
evidence-retriever, and the routing decision between them.

Three constraints from docs/w2/DECISIONS.md are enforced here by the shape
of the types rather than by review:

**D5 — no patient identifiers leave this system in a retrieval query.**
"A query leaving this system carries clinical concepts, never a name, MRN,
date of birth, or any other identifier." `EvidenceRetrieverInput` therefore
has no patient field: not `patient_id`, not `patient_uuid`, not a free-text
`question`. Combined with `extra="forbid"` from `W2Contract`, that is not a
convention — a caller that passes `patient_id=` gets a `ValidationError`,
and there is no field to put a name in even by accident.
tests/test_w2_workers.py asserts the absence by name against a denylist, so
adding one later fails a test rather than passing review.

The one thing that could still carry an identifier is the text of a concept
itself. `ClinicalConcept` rejects text containing a calendar date or an
SSN-shaped number, which are the two identifier forms D5 names that a
clinical concept never needs. It is a narrow guard and it is not a PHI
detector; it exists because the cheapest place to catch "the whole question
got passed as a concept" is at the type.

**D4 — routing is deterministic and always logged with its reason.**
`RoutingDecision` carries a `reason_code` from a closed enum and has no
free-text field at all. That is deliberate twice over: it keeps the reason
loggable under CLAUDE.md invariant 1 (a free-text reason written by a model
that has just read a chart is a PHI leak waiting for its first incident),
and it makes "explain why this route was taken" a lookup rather than prose.

**D7 — a document that has not finished extracting says so.**
`IntakeExtractorOutput` pairs a status with a payload and refuses a payload
for any non-terminal status, so "still processing" cannot be returned
alongside a partial extraction.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, field_validator, model_validator

from app.models.w2.base import CORRELATION_ID_PATTERN, W2Contract
from app.models.w2.citations import Citation
from app.models.w2.documents import DocumentKind, ExtractionStatus, SourceDocument
from app.models.w2.intake_form import IntakeFormExtraction
from app.models.w2.lab_pdf import LabPdfExtraction
from app.models.w2.source_types import SourceType

# --------------------------------------------------------------------------
# intake-extractor
# --------------------------------------------------------------------------


class IntakeExtractorInput(W2Contract):
    """
    What the extractor is asked to do: one stored document, by id.

    No page text and no page images in the contract — the worker fetches the
    bytes itself from the document store. That keeps chart content out of
    the value that gets passed between graph nodes, which is the value most
    likely to end up in a trace (invariant 1).
    """

    document: SourceDocument
    correlation_id: str = Field(pattern=CORRELATION_ID_PATTERN)
    force_reextract: bool = Field(
        default=False,
        description=(
            "Re-run even if an extraction already exists for this document. "
            "Extraction is at upload time and cached (D7); this is the "
            "operator escape hatch, not the normal path."
        ),
    )


class ExtractionFailureReason(StrEnum):
    """Closed set, so a failure is loggable without chart content in it."""

    UNREADABLE_FILE = "unreadable_file"
    UNSUPPORTED_MIME_TYPE = "unsupported_mime_type"
    NO_TEXT_LAYER_AND_OCR_FAILED = "no_text_layer_and_ocr_failed"
    MODEL_OUTPUT_INVALID = "model_output_invalid"
    PAGE_LIMIT_EXCEEDED = "page_limit_exceeded"
    TIMED_OUT = "timed_out"
    #: The vision call itself failed — an API error, a refusal, a truncated
    #: output. Distinct from MODEL_OUTPUT_INVALID, where a response arrived
    #: and did not parse (D35).
    VISION_CALL_FAILED = "vision_call_failed"
    #: The model read fields but the locate step placed none of them on a
    #: page. Nothing in such an extraction could be presented as fact
    #: (invariant 4), so it is a failure rather than an empty success (D35).
    NO_FIELDS_LOCATED = "no_fields_located"
    #: An unexpected error inside the worker. Logged by exception type only;
    #: the caller still gets a typed failure, never an exception (D35).
    INTERNAL_ERROR = "internal_error"
    #: The agent restarted while the extraction was running. Nothing was
    #: extracted; uploading the same file again starts it afresh (D60).
    INTERRUPTED = "interrupted"


class IntakeExtractorOutput(W2Contract):
    """
    What the extractor produced, or why it has not produced it yet.

    `payload` is a discriminated union on `schema_version`, which differs
    between the two extraction models, so a lab report cannot be returned
    where an intake form was expected without failing validation.
    """

    status: ExtractionStatus
    document_id: str = Field(min_length=1)
    kind: DocumentKind
    extraction_id: str | None = Field(
        default=None,
        description="Assigned once extraction completes; None before that.",
    )
    payload: LabPdfExtraction | IntakeFormExtraction | None = Field(
        default=None,
        description="The extraction. Present only when status is `complete` (D7).",
    )
    failure_reason: ExtractionFailureReason | None = None
    correlation_id: str = Field(pattern=CORRELATION_ID_PATTERN)

    @model_validator(mode="after")
    def _status_rules(self) -> Self:
        if self.status is ExtractionStatus.COMPLETE:
            if self.payload is None:
                raise ValueError("a complete extraction has a payload")
            if self.extraction_id is None:
                raise ValueError("a complete extraction has an extraction_id")
            if self.failure_reason is not None:
                raise ValueError("a complete extraction has no failure reason")
            expected = (
                LabPdfExtraction if self.kind is DocumentKind.LAB_PDF else IntakeFormExtraction
            )
            if not isinstance(self.payload, expected):
                raise ValueError(
                    f"kind is {self.kind.value} but payload is {type(self.payload).__name__}"
                )
            if self.payload.extraction_id != self.extraction_id:
                raise ValueError("payload extraction_id disagrees with the output's")
            if self.payload.document.document_id != self.document_id:
                raise ValueError("payload document_id disagrees with the output's")
            return self

        if self.payload is not None:
            raise ValueError(
                f"status is {self.status.value}; D7 forbids returning a partial "
                "extraction alongside a non-complete state"
            )
        if self.status is ExtractionStatus.FAILED and self.failure_reason is None:
            raise ValueError("a failed extraction says why")
        if self.status is not ExtractionStatus.FAILED and self.failure_reason is not None:
            raise ValueError(f"status {self.status.value} has no failure reason")
        return self

    @property
    def is_still_processing(self) -> bool:
        """D7's explicit 'still processing' state."""
        return self.status not in (ExtractionStatus.COMPLETE, ExtractionStatus.FAILED)


# --------------------------------------------------------------------------
# evidence-retriever
# --------------------------------------------------------------------------

#: Text shapes a clinical concept never needs and an identifier often is.
#: Narrow on purpose — see the module docstring. `\d{4}-\d{2}-\d{2}` and
#: `\d{1,2}/\d{1,2}/\d{2,4}` are calendar dates (D5 names date of birth);
#: `\d{3}-\d{2}-\d{4}` is an SSN.
_IDENTIFIER_SHAPES = (
    re.compile(r"\d{4}-\d{2}-\d{2}"),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
)

#: Field names that must never appear on a retrieval input. Asserted by
#: tests/test_w2_workers.py against the model's actual fields, so this list
#: is a test fixture with teeth rather than a comment.
FORBIDDEN_RETRIEVAL_FIELD_NAMES = frozenset(
    {
        "patient",
        "patient_id",
        "patient_uuid",
        "patient_name",
        "patient_session_key",
        "name",
        "mrn",
        "dob",
        "date_of_birth",
        "birth_date",
        "ssn",
        "address",
        "phone",
        "email",
        "question",
        "question_text",
        "chart",
        "chart_context",
        "note",
        "free_text",
    }
)


class ClinicalConcept(W2Contract):
    """
    One concept a retrieval query is built from. Text, and optionally a code.

    The code fields exist so that a concept can be pinned to a terminology
    where one is available (`snomed`, `rxnorm`, `loinc`), which is both
    better retrieval and less text leaving the process.
    """

    text: str = Field(min_length=1, max_length=120)
    code_system: str | None = Field(default=None, min_length=1)
    code: str | None = Field(default=None, min_length=1)

    @field_validator("text")
    @classmethod
    def _no_identifier_shapes(cls, value: str) -> str:
        for pattern in _IDENTIFIER_SHAPES:
            if pattern.search(value):
                raise ValueError(
                    "a clinical concept carries no dates or identifier-shaped "
                    "numbers; D5 forbids a patient identifier in a query that "
                    "leaves this system"
                )
        return value

    @model_validator(mode="after")
    def _code_pairs(self) -> Self:
        if (self.code is None) != (self.code_system is None):
            raise ValueError("code and code_system are given together or not at all")
        return self


class RetrievalMethod(StrEnum):
    """
    Which half of the hybrid produced a chunk, or both. D5 leaves the
    embedding and reranker choice OPEN; this enum describes the retrieval
    *shape*, which the hybrid design fixes regardless of that choice.
    """

    LEXICAL = "lexical"
    DENSE = "dense"
    HYBRID = "hybrid"


class EvidenceRetrieverInput(W2Contract):
    """
    A retrieval request. **Concepts only** — see the module docstring on D5.

    If you are here because you want to pass the patient's question through
    for better recall: that is the request D5 forbids. Build concepts from
    the question on this side of the boundary.
    """

    concepts: list[ClinicalConcept] = Field(min_length=1)
    top_k: int = Field(default=10, ge=1, le=100)
    method: RetrievalMethod = RetrievalMethod.HYBRID
    rerank: bool = Field(
        default=True,
        description="Whether to run the reranker over the candidate set (D5, OPEN).",
    )
    correlation_id: str = Field(pattern=CORRELATION_ID_PATTERN)


class GuidelineSource(W2Contract):
    """Where a chunk came from. Bibliography, not patient data."""

    guideline_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    section: str = Field(
        min_length=1,
        description="The heading the chunk sits under; becomes the citation's page_or_section.",
    )
    publisher: str | None = None
    version: str | None = None
    url: str | None = None


class EvidenceChunk(W2Contract):
    """
    One retrieved passage, with both scores kept separately.

    Retrieval score and rerank score are different quantities on different
    scales from different models, and collapsing them into one `score`
    makes "was this the retriever or the reranker?" unanswerable the first
    time a result looks wrong. D5 is OPEN on which reranker, so
    `rerank_score` is optional: `None` means not reranked, never zero.
    """

    chunk_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source: GuidelineSource
    retrieval_score: float
    rerank_score: float | None = None
    method: RetrievalMethod

    def to_citation(self) -> Citation:
        """
        The citation this chunk supports. `source_type` is GUIDELINE and
        nothing can change that here — invariant 3 lives or dies on this
        method not being reusable for chart facts.
        """
        return Citation(
            source_type=SourceType.GUIDELINE,
            source_id=self.source.guideline_id,
            page_or_section=self.source.section,
            field_or_chunk_id=self.chunk_id,
            quote_or_value=self.text,
        )


class EvidenceRetrieverOutput(W2Contract):
    """The chunks, in the order they should be considered."""

    chunks: list[EvidenceChunk] = Field(default_factory=list)
    method: RetrievalMethod
    reranked: bool
    correlation_id: str = Field(pattern=CORRELATION_ID_PATTERN)

    @model_validator(mode="after")
    def _rerank_consistent(self) -> Self:
        if self.reranked and any(chunk.rerank_score is None for chunk in self.chunks):
            raise ValueError("a reranked result set has a rerank_score on every chunk")
        if not self.reranked and any(chunk.rerank_score is not None for chunk in self.chunks):
            raise ValueError("rerank_score present on a result set marked not reranked")
        return self


# --------------------------------------------------------------------------
# supervisor
# --------------------------------------------------------------------------


class Route(StrEnum):
    """Where the supervisor sends a turn."""

    EXTRACT = "extract"
    RETRIEVE = "retrieve"
    ANSWER = "answer"
    REFUSE = "refuse"


class RoutingReasonCode(StrEnum):
    """
    Why. A closed set with no free-text companion, so a routing decision is
    loggable in full (D4: "Every routing decision is logged with its
    reason") without any chance of chart content riding along (invariant 1).
    """

    # -> EXTRACT
    DOCUMENT_NOT_YET_EXTRACTED = "document_not_yet_extracted"
    DOCUMENT_REEXTRACT_REQUESTED = "document_reextract_requested"
    # -> RETRIEVE
    GUIDELINE_QUESTION = "guideline_question"
    EVIDENCE_NEEDED_FOR_RECOMMENDATION = "evidence_needed_for_recommendation"
    # -> ANSWER
    CHART_ONLY_QUESTION = "chart_only_question"
    EXTRACTION_AVAILABLE = "extraction_available"
    EVIDENCE_IN_HAND = "evidence_in_hand"
    # -> REFUSE
    OUT_OF_SCOPE_RESOURCE = "out_of_scope_resource"
    UNVERIFIABLE_JUDGMENT = "unverifiable_judgment"
    EXTRACTION_FAILED = "extraction_failed"
    #: D7's "still processing": an explicit non-answer, never a wait.
    DOCUMENT_STILL_PROCESSING = "document_still_processing"
    #: The turn names a document this agent holds no upload of for this
    #: patient. Deliberately the same code for "no such document" and
    #: "another patient's document" (the ui-integration 404 rule, D40).
    DOCUMENT_NOT_FOUND = "document_not_found"
    #: The extractor's wrong-patient flag: the name or date of birth the
    #: document prints does not match the chart it was uploaded to (D40).
    DOCUMENT_IDENTITY_MISMATCH = "document_identity_mismatch"
    #: The question needs guideline evidence and retrieval found none, or
    #: no clinical concept could be built from it to search on (D40).
    NO_GUIDELINE_EVIDENCE = "no_guideline_evidence"
    #: Tool failures and model failure, each its own code so a refusal
    #: says which dependency failed without quoting the error (D40).
    RETRIEVAL_UNAVAILABLE = "retrieval_unavailable"
    CHART_UNAVAILABLE = "chart_unavailable"
    MODEL_UNAVAILABLE = "model_unavailable"
    #: The supervisor's node-visit bound was reached: fail closed (D40).
    VISIT_LIMIT_EXCEEDED = "visit_limit_exceeded"


#: Which reasons may justify which route. A decision whose pair is not in
#: here is rejected at construction, so the routing table cannot drift away
#: from the reasons it claims to act on.
ALLOWED_REASONS: dict[Route, frozenset[RoutingReasonCode]] = {
    Route.EXTRACT: frozenset(
        {
            RoutingReasonCode.DOCUMENT_NOT_YET_EXTRACTED,
            RoutingReasonCode.DOCUMENT_REEXTRACT_REQUESTED,
        }
    ),
    Route.RETRIEVE: frozenset(
        {
            RoutingReasonCode.GUIDELINE_QUESTION,
            RoutingReasonCode.EVIDENCE_NEEDED_FOR_RECOMMENDATION,
        }
    ),
    Route.ANSWER: frozenset(
        {
            RoutingReasonCode.CHART_ONLY_QUESTION,
            RoutingReasonCode.EXTRACTION_AVAILABLE,
            RoutingReasonCode.EVIDENCE_IN_HAND,
        }
    ),
    Route.REFUSE: frozenset(
        {
            RoutingReasonCode.OUT_OF_SCOPE_RESOURCE,
            RoutingReasonCode.UNVERIFIABLE_JUDGMENT,
            RoutingReasonCode.EXTRACTION_FAILED,
            RoutingReasonCode.DOCUMENT_STILL_PROCESSING,
            RoutingReasonCode.DOCUMENT_NOT_FOUND,
            RoutingReasonCode.DOCUMENT_IDENTITY_MISMATCH,
            RoutingReasonCode.NO_GUIDELINE_EVIDENCE,
            RoutingReasonCode.RETRIEVAL_UNAVAILABLE,
            RoutingReasonCode.CHART_UNAVAILABLE,
            RoutingReasonCode.MODEL_UNAVAILABLE,
            RoutingReasonCode.VISIT_LIMIT_EXCEEDED,
        }
    ),
}


class RoutingDecision(W2Contract):
    """
    One supervisor decision, in the form it is logged in.

    Three fields, all of them safe to log. There is no `reason` string, no
    `detail`, no `matched_text`: D4 wants the decision explainable, and a
    code plus the rule table above explains it without quoting the question
    that produced it.
    """

    decision: Route
    reason_code: RoutingReasonCode
    correlation_id: str = Field(pattern=CORRELATION_ID_PATTERN)

    @model_validator(mode="after")
    def _reason_fits_decision(self) -> Self:
        if self.reason_code not in ALLOWED_REASONS[self.decision]:
            raise ValueError(
                f"{self.reason_code.value} is not a reason for routing to "
                f"{self.decision.value}; see ALLOWED_REASONS"
            )
        return self


SupervisorWorkerInput = Annotated[
    IntakeExtractorInput | EvidenceRetrieverInput,
    Field(description="Whatever the supervisor hands to a worker."),
]
