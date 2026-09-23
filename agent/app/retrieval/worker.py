"""
The evidence-retriever worker: `EvidenceRetrieverInput` in,
`EvidenceRetrieverOutput` out.

This is the boundary between the retrieval stack and the graph. Everything
above it speaks the Week 2 contracts (`app/models/w2/workers.py`);
everything below it speaks chunks and scores.

Three things this module is responsible for and the retriever is not:

**Saying honestly whether reranking happened.** D25 gives the rerank stage
a latency budget; over it, the fused order stands and the output says
`reranked=false`. `EvidenceRetrieverOutput` enforces the other half —
`reranked=true` requires a `rerank_score` on every chunk — so the two
cannot drift apart.

**Turning a chunk into a citation.** `EvidenceChunk.to_citation()` quotes
the whole chunk. `citation_for()` is the version that takes a shorter
excerpt, and it refuses an excerpt that is not literally in the chunk —
which is the only property that makes a quote a quote rather than a
paraphrase the model liked better. That matters twice over here: eight of
the twenty-eight sources are USPSTF, whose licence permits reproduction
"without any changes to the work or portions thereof"
(`corpus_meta.USPSTF_ENDORSEMENT_CONSTRAINT`), so an altered quote is a
licensing problem as well as an honesty one.

**Logging the retrieval without logging the question.** The log line
carries counts, scores, flags and per-stage milliseconds. It does not
carry the query, the concepts, or any chunk text — CLAUDE.md invariant 1.
Guideline ids are in it, because a guideline id is bibliography, not chart
content, and "which sources did this answer draw on" is unanswerable
without them.
"""

from __future__ import annotations

from app.middleware.logging import log_event
from app.middleware.telemetry import step, tool_call
from app.models.telemetry_events import Pipeline, Tool
from app.models.w2.citations import Citation
from app.models.w2.source_types import SourceType
from app.models.w2.workers import (
    EvidenceChunk,
    EvidenceRetrieverInput,
    EvidenceRetrieverOutput,
)
from app.retrieval.retriever import (
    DEFAULT_CANDIDATE_K,
    GuidelineRetriever,
    RetrievalRun,
    parse_source_version,
)

#: Event type for the retrieval log line. snake_case of what it describes,
#: like every other event in app/middleware/logging.py.
RETRIEVAL_EVENT = "evidence_retrieval_event"

#: How many scores go into the log line, in returned order. Enough to see
#: whether the result set was strong or scraped the barrel; not so many
#: that the line becomes a data dump. Note the retrieval scores are in
#: *returned* order, which after a rerank is not descending — that is the
#: point of logging both.
LOGGED_SCORES = 5


class ExcerptNotInChunkError(ValueError):
    """
    Raised when a proposed quote is not literally present in its chunk.

    The message carries neither the excerpt nor the chunk text: a citation
    error that prints both writes guideline text into the logs, and the
    habit of printing "the value that failed" is exactly how chart content
    ends up there too (invariant 1).
    """


def citation_for(chunk: EvidenceChunk, excerpt: str | None = None) -> Citation:
    """
    The guideline citation for a retrieved chunk.

    `excerpt` defaults to the whole chunk. Whatever is passed must appear
    verbatim in `chunk.text`, so `quote_or_value` is always something the
    source actually says.
    """
    if excerpt is None:
        return chunk.to_citation()
    if not excerpt.strip():
        raise ExcerptNotInChunkError(f"{chunk.chunk_id}: an empty excerpt cites nothing")
    if excerpt not in chunk.text:
        raise ExcerptNotInChunkError(
            f"{chunk.chunk_id}: the proposed quote is not a substring of the chunk; "
            "a citation quotes the source, it does not paraphrase it"
        )
    return Citation(
        source_type=SourceType.GUIDELINE,
        source_id=chunk.source.guideline_id,
        page_or_section=chunk.source.section,
        field_or_chunk_id=chunk.chunk_id,
        quote_or_value=excerpt,
    )


def dated_chunk_ids(chunks: list[EvidenceChunk]) -> list[str]:
    """Chunk ids whose source the registry flagged as dated."""
    return [c.chunk_id for c in chunks if parse_source_version(c.source.version)[1]]


def _log_retrieval(
    run: RetrievalRun,
    chunks: list[EvidenceChunk],
    reranked: bool,
    correlation_id: str,
) -> None:
    """
    One structured line per retrieval. Counts, scores, flags, milliseconds.

    `correlation_id` is passed for the caller's benefit only — `log_event`
    merges the real one from structlog's contextvars and deliberately takes
    no parameter for it, so it goes in `event_data` where it is data rather
    than a competing source of truth.
    """
    scores = [round(c.retrieval_score, 6) for c in chunks[:LOGGED_SCORES]]
    rerank_scores = [
        round(c.rerank_score, 6) for c in chunks[:LOGGED_SCORES] if c.rerank_score is not None
    ]
    rerank = run.rerank
    log_event(
        RETRIEVAL_EVENT,
        event_data={
            "correlation_id": correlation_id,
            "method": run.method.value,
            "concept_count": run.concept_count,
            "hit_count": len(chunks),
            "retrieval_scores": scores,
            "rerank_scores": rerank_scores,
            "reranked": reranked,
            "rerank_candidates": rerank.candidates if rerank else 0,
            "rerank_over_budget": bool(rerank and rerank.over_budget),
            "rerank_unavailable": bool(rerank and rerank.unavailable),
            "rerank_busy": bool(rerank and rerank.busy),
            "lexical_ms": round(run.lexical_ms, 2),
            "dense_ms": round(run.dense_ms, 2),
            "fusion_ms": round(run.fusion_ms, 2),
            "rerank_ms": round(rerank.elapsed_ms, 2) if rerank else 0.0,
            "total_ms": round(run.total_ms, 2),
            # Bibliography, not chart content: which guidelines were drawn
            # on, and how many of them the age check flagged.
            "guideline_ids": sorted({c.source.guideline_id for c in chunks}),
            "dated_hit_count": len(dated_chunk_ids(chunks)),
        },
    )


def run_evidence_retriever(
    request: EvidenceRetrieverInput,
    retriever: GuidelineRetriever,
    candidate_k: int = DEFAULT_CANDIDATE_K,
) -> EvidenceRetrieverOutput:
    """
    Run one retrieval request end to end.

    The request carries concepts and no patient field — that is D5 enforced
    by the shape of the type, and this function never adds one back.
    """
    with (
        step(Pipeline.EVIDENCE_RETRIEVER, "retrieval") as retrieval,
        tool_call(Tool.GUIDELINE_RETRIEVER, request.method.value),
    ):
        chunks, run = retriever.retrieve_with_outcome(
            list(request.concepts),
            top_k=request.top_k,
            method=request.method,
            rerank=request.rerank,
            candidate_k=candidate_k,
        )
        reranked = bool(run.rerank and run.rerank.applied)
        # Numbers and flags only; the stage timings are the "by step" view
        # of a retrieval, which is one step to the supervisor.
        retrieval.counts.update(
            hits=len(chunks),
            reranked=reranked,
            rerank_requested=bool(run.rerank),
            rerank_over_budget=bool(run.rerank and run.rerank.over_budget),
            rerank_unavailable=bool(run.rerank and run.rerank.unavailable),
            rerank_busy=bool(run.rerank and run.rerank.busy),
            lexical_ms=round(run.lexical_ms, 2),
            dense_ms=round(run.dense_ms, 2),
            fusion_ms=round(run.fusion_ms, 2),
            rerank_ms=round(run.rerank.elapsed_ms, 2) if run.rerank else 0.0,
        )
    if not reranked:
        # A chunk that kept a rerank_score from somewhere would make the
        # output contradict its own flag; EvidenceRetrieverOutput rejects
        # that, and clearing here is what makes the fallback path valid
        # rather than an exception.
        chunks = [c.model_copy(update={"rerank_score": None}) for c in chunks]

    _log_retrieval(run, chunks, reranked, request.correlation_id)

    return EvidenceRetrieverOutput(
        chunks=chunks,
        method=request.method,
        reranked=reranked,
        correlation_id=request.correlation_id,
    )
