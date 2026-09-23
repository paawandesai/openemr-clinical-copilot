"""
The Week 2 verifier — Week 1's deterministic check (checker.py) extended to
documents and guideline evidence. Plain comparison code: no model, no
network, nothing with its own uncertainty (ARCHITECTURE.md §5).
tests/test_w2_checker.py enforces the import side, as test_verifier.py does
for checker.py.

One drafted claim cites exactly one source, and is checked against what
*this turn* holds for that source:

- **chart** — `checker.verify_claims`, unchanged: the source_id was
  retrieved this turn, under the claimed resource type, and holds the
  claimed value in the claimed field. Same events, same failure reasons.
- **document** — the document is one this turn resolved for this patient,
  the `field_path` is a field its extraction *located* on the page (D3/D13:
  `as_presented_fact()` is not None, so there is a bbox), and the claimed
  value equals the text the locate step found there, compared exactly after
  case and whitespace folding. No near match, no substring: "16" is in
  "162" (D24). A field the extraction has but did not locate fails as
  `field_not_located` — it is already disclosed as unsupported, and it is
  never presented as fact (invariant 4).
- **guideline** — the chunk is one the retriever returned this turn, and the
  excerpt is a verbatim passage of it: at least `MIN_EXCERPT_WORDS` words,
  found in the chunk text with only whitespace differences, and cited as the
  chunk's *own* characters (the rule of `retrieval.worker.citation_for`,
  restated here so the verifier does not import the retrieval stack), so the quote the
  clinician reads is the source's, not the model's copy of it (D26).

**The citation a passed claim carries is built from the source, never from
the draft.** A document claim carries the locate step's citation, with its
bbox and extraction id; a guideline claim carries a guideline citation
quoting the chunk's own slice. So `quote_or_value` is a substring of its source
by construction, and the model has no field through which to put a bbox or
a quote on the page.

**Separation (invariant 3, D17).** Each drafted item has one source type, so
a claim cannot mix chart with document or either with guideline; the
`FinalAnswer` validators check the same thing again on the way out.

**Known limitation — stated because it is the one that matters.** This
verifier can confirm that a guideline chunk *says* X. It cannot confirm that
X *applies to this patient*: that the patient is in the population the
recommendation covers, that no exclusion applies, that the evidence is
current for them. Nothing here reads the guideline's population criteria
against the chart, and a check that claimed to would be a second model's
opinion wearing a verifier's name. Every answer that carries guideline
evidence therefore carries `AnswerLimitation.GUIDELINE_APPLICABILITY_NOT_
VERIFIED` and its fixed sentence, and the guideline section is headed as
"not about this patient". The same holds for chart and document facts in
the other direction: a verified value is what the source says, not a claim
that the source is right (Week 1's §5 "source-data accuracy" limitation).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.middleware.correlation import get_correlation_id
from app.middleware.logging import log_event
from app.models.claims import Claim, VerificationResult
from app.models.fhir_resources import FhirResourceType
from app.models.metrics_events import UseCase
from app.models.w2.citations import Citation, citation_from_claim
from app.models.w2.source_types import SourceType
from app.models.w2.workers import EvidenceChunk
from app.supervisor.facts import DocumentFact
from app.verification.checker import FhirResource, verify_claims

#: A guideline excerpt shorter than this is not evidence of anything: "the"
#: is a substring of every chunk.
MIN_EXCERPT_WORDS = 4

W2_CLAIM_VERIFIED_EVENT = "w2_claim_verified_event"


class W2FailureReason(StrEnum):
    """Why a document or guideline claim failed. Closed set, loggable."""

    DOCUMENT_NOT_IN_TURN = "document_not_in_turn"
    FIELD_NOT_IN_DOCUMENT = "field_not_in_document"
    FIELD_NOT_LOCATED = "field_not_located"
    VALUE_MISMATCH = "value_mismatch"
    CHUNK_NOT_RETRIEVED = "chunk_not_retrieved"
    EXCERPT_TOO_SHORT = "excerpt_too_short"
    EXCERPT_NOT_IN_CHUNK = "excerpt_not_in_chunk"


@dataclass(frozen=True)
class DraftedDocumentFact:
    document_id: str
    field_path: str
    value: str


@dataclass(frozen=True)
class DraftedGuideline:
    chunk_id: str
    excerpt: str


@dataclass(frozen=True)
class DocumentInTurn:
    """One document this turn resolved: its located facts and every field path it has."""

    document_id: str
    facts: Mapping[str, DocumentFact]
    all_field_paths: frozenset[str]


@dataclass(frozen=True)
class W2Verification:
    """
    One drafted claim's outcome. `citation` is the source-built citation for
    a passed claim and None for a failed one; `chart` is Week 1's own result
    for a chart claim.
    """

    source_type: SourceType
    source_id: str
    field_or_chunk_id: str
    passed: bool
    reason: str | None
    citation: Citation | None = None
    chart: VerificationResult | None = None
    fact: DocumentFact | None = None
    chunk: EvidenceChunk | None = None
    drafted_value: str | None = None


def _fold(text: str) -> str:
    return " ".join(text.split()).casefold()


def verbatim_slice(chunk_text: str, excerpt: str) -> str | None:
    """
    The chunk's own characters for `excerpt`, allowing only whitespace to
    differ, or None if the chunk does not contain it. Case is not folded:
    a quotation that changes case has changed the words (D26).
    """
    words = excerpt.split()
    if not words:
        return None
    pattern = r"\s+".join(re.escape(word) for word in words)
    match = re.search(pattern, chunk_text)
    return match.group(0) if match else None


def verify_document_fact(
    drafted: DraftedDocumentFact, documents: Mapping[str, DocumentInTurn]
) -> W2Verification:
    def failed(reason: W2FailureReason) -> W2Verification:
        return W2Verification(
            SourceType.DOCUMENT,
            drafted.document_id,
            drafted.field_path,
            passed=False,
            reason=reason.value,
            drafted_value=drafted.value,
        )

    document = documents.get(drafted.document_id)
    if document is None:
        return failed(W2FailureReason.DOCUMENT_NOT_IN_TURN)
    fact = document.facts.get(drafted.field_path)
    if fact is None:
        if drafted.field_path in document.all_field_paths:
            return failed(W2FailureReason.FIELD_NOT_LOCATED)
        return failed(W2FailureReason.FIELD_NOT_IN_DOCUMENT)
    if _fold(drafted.value) != _fold(fact.quote):
        return failed(W2FailureReason.VALUE_MISMATCH)
    return W2Verification(
        SourceType.DOCUMENT,
        drafted.document_id,
        drafted.field_path,
        passed=True,
        reason=None,
        citation=fact.citation,
        fact=fact,
        drafted_value=drafted.value,
    )


def verify_guideline(
    drafted: DraftedGuideline, chunks: Mapping[str, EvidenceChunk]
) -> W2Verification:
    chunk = chunks.get(drafted.chunk_id)

    def failed(reason: W2FailureReason) -> W2Verification:
        return W2Verification(
            SourceType.GUIDELINE,
            chunk.source.guideline_id if chunk else drafted.chunk_id,
            drafted.chunk_id,
            passed=False,
            reason=reason.value,
            chunk=chunk,
            drafted_value=drafted.excerpt,
        )

    if chunk is None:
        return failed(W2FailureReason.CHUNK_NOT_RETRIEVED)
    if len(drafted.excerpt.split()) < MIN_EXCERPT_WORDS:
        return failed(W2FailureReason.EXCERPT_TOO_SHORT)
    own = verbatim_slice(chunk.text, drafted.excerpt)
    if own is None:
        return failed(W2FailureReason.EXCERPT_NOT_IN_CHUNK)
    return W2Verification(
        SourceType.GUIDELINE,
        chunk.source.guideline_id,
        chunk.chunk_id,
        passed=True,
        reason=None,
        citation=_guideline_citation(chunk, own),
        chunk=chunk,
        drafted_value=drafted.excerpt,
    )


def _guideline_citation(chunk: EvidenceChunk, own: str) -> Citation:
    """
    `retrieval.worker.citation_for` without importing the retrieval stack
    (which carries a network client) into the verifier. Same rule: the
    quote is literally in the chunk, which `verbatim_slice` guarantees.
    """
    if own not in chunk.text:
        raise AssertionError("verbatim_slice returned text that is not in the chunk")
    return Citation(
        source_type=SourceType.GUIDELINE,
        source_id=chunk.source.guideline_id,
        page_or_section=chunk.source.section,
        field_or_chunk_id=chunk.chunk_id,
        quote_or_value=own,
    )


def verify_answer(
    chart_claims: list[Claim],
    document_claims: list[DraftedDocumentFact],
    guideline_claims: list[DraftedGuideline],
    *,
    chart: Mapping[FhirResourceType, Sequence[FhirResource]],
    documents: Mapping[str, DocumentInTurn],
    chunks: Mapping[str, EvidenceChunk],
    use_case: UseCase,
    patient_session_key: str,
) -> list[W2Verification]:
    """
    Every drafted claim, checked; one result each, chart first, then
    document, then guideline, each in drafted order. Chart claims emit
    Week 1's claim_verified_event; the other two emit
    `w2_claim_verified_event` with ids and a reason code only.
    """
    results: list[W2Verification] = []
    for result in verify_claims(
        chart_claims, chart, use_case=use_case, patient_session_key=patient_session_key
    ):
        results.append(
            W2Verification(
                SourceType.CHART,
                result.claim.source_id,
                result.claim.field,
                passed=result.passed,
                reason=result.reason,
                citation=citation_from_claim(result.claim) if result.passed else None,
                chart=result,
            )
        )
    for drafted in document_claims:
        results.append(
            _emit(verify_document_fact(drafted, documents), use_case, patient_session_key)
        )
    for drafted in guideline_claims:
        results.append(_emit(verify_guideline(drafted, chunks), use_case, patient_session_key))
    return results


def _emit(result: W2Verification, use_case: UseCase, patient_session_key: str) -> W2Verification:
    log_event(
        W2_CLAIM_VERIFIED_EVENT,
        event_data={
            "correlation_id": get_correlation_id() or "no-request-context",
            "source_type": result.source_type.value,
            # A document id is a digest; a guideline id is bibliography.
            "source_id": result.source_id,
            "passed": result.passed,
            "reason": result.reason,
        },
        use_case=use_case,
        patient_session_key=patient_session_key,
    )
    return result
