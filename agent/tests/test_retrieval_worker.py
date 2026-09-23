"""
The evidence-retriever worker: the contract boundary, the rerank budget,
the citation, and the log line.

Offline. The dense half runs on the deterministic fake embedder from
tests/test_retrieval_corpus.py and the reranker is a stub, because what is
under test here is the wiring and the honesty of the flags, not whether a
cross-encoder ranks well — the bake-off measures that.
"""

from __future__ import annotations

import inspect
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError
from structlog.testing import capture_logs

from app.models.w2.citations import Citation
from app.models.w2.source_types import SourceType
from app.models.w2.workers import (
    ClinicalConcept,
    EvidenceRetrieverInput,
    EvidenceRetrieverOutput,
    RetrievalMethod,
)
from app.retrieval.build import load_index
from app.retrieval.corpus_meta import load_registry
from app.retrieval.retriever import (
    DATED_VERSION_SUFFIX,
    RERANK_BUDGET_SECONDS,
    GuidelineRetriever,
)
from app.retrieval.worker import (
    RETRIEVAL_EVENT,
    ExcerptNotInChunkError,
    citation_for,
    dated_chunk_ids,
    run_evidence_retriever,
)
from tests.test_retrieval_corpus import FakeEmbedder

CORRELATION_ID = str(uuid.uuid4())


class StubReranker:
    """Scores by position, so the reordering is exact and checkable."""

    name = "stub:reverse"

    def __init__(self, delay_seconds: float = 0.0) -> None:
        self.delay_seconds = delay_seconds
        self.calls = 0

    def score(self, query: str, passages: list[str]) -> list[float]:
        self.calls += 1
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        # Reverse the incoming order: the last candidate becomes the best.
        return [float(i) for i in range(len(passages))]


def build_retriever(reranker=None, budget: float = 2.5) -> GuidelineRetriever:
    return GuidelineRetriever(
        load_index(),
        load_registry(),
        embedder=FakeEmbedder(),
        reranker=reranker,
        cache_dir=None,
        rerank_budget_seconds=budget,
    )


def request(rerank: bool = False, top_k: int = 4) -> EvidenceRetrieverInput:
    return EvidenceRetrieverInput(
        concepts=[ClinicalConcept(text="chronic kidney disease staging by eGFR")],
        top_k=top_k,
        method=RetrievalMethod.HYBRID,
        rerank=rerank,
        correlation_id=CORRELATION_ID,
    )


# --------------------------------------------------------------------------
# contract in, contract out
# --------------------------------------------------------------------------


def test_input_contract_goes_in_and_output_contract_comes_out():
    output = run_evidence_retriever(request(), build_retriever())
    assert isinstance(output, EvidenceRetrieverOutput)
    assert output.correlation_id == CORRELATION_ID
    assert output.method is RetrievalMethod.HYBRID
    assert 0 < len(output.chunks) <= 4


def test_the_input_contract_has_no_patient_field_to_fill_in():
    """
    D5's structural half, asserted from the retrieval side: there is no
    field on the request for an identifier, so this worker cannot receive
    one however its caller is written.
    """
    with pytest.raises(ValidationError):
        EvidenceRetrieverInput(
            concepts=[ClinicalConcept(text="eGFR")],
            patient_id="a2c37430-0a29-4c57-8195-2c924c2b654a",
            correlation_id=CORRELATION_ID,
        )


def test_top_k_is_honoured():
    output = run_evidence_retriever(request(top_k=2), build_retriever())
    assert len(output.chunks) == 2


# --------------------------------------------------------------------------
# reranked says what actually happened
# --------------------------------------------------------------------------


def test_without_a_reranker_the_output_says_it_was_not_reranked():
    output = run_evidence_retriever(request(rerank=True), build_retriever(reranker=None))
    assert output.reranked is False
    assert all(c.rerank_score is None for c in output.chunks)


def test_with_a_reranker_every_chunk_carries_a_rerank_score():
    output = run_evidence_retriever(request(rerank=True), build_retriever(StubReranker()))
    assert output.reranked is True
    assert all(c.rerank_score is not None for c in output.chunks)
    scores = [c.rerank_score for c in output.chunks]
    assert scores == sorted(scores, reverse=True)


def test_rerank_is_not_run_when_the_request_does_not_ask_for_it():
    stub = StubReranker()
    output = run_evidence_retriever(request(rerank=False), build_retriever(stub))
    assert stub.calls == 0
    assert output.reranked is False


def test_a_rerank_over_budget_falls_back_to_the_fused_order():
    """
    D25: "if rerank exceeds 2.5 s, fall back to fused order and mark the
    output reranked=false". The slow result is discarded rather than used
    late, and the output does not claim a reranking it threw away.
    """
    slow = StubReranker(delay_seconds=0.05)
    retriever = build_retriever(slow, budget=0.01)
    fused = run_evidence_retriever(request(rerank=False), build_retriever(None))
    output = run_evidence_retriever(request(rerank=True), retriever)

    assert slow.calls == 1
    assert output.reranked is False
    assert all(c.rerank_score is None for c in output.chunks)
    assert [c.chunk_id for c in output.chunks] == [c.chunk_id for c in fused.chunks]


def test_a_rerank_over_budget_opens_the_circuit_so_the_next_turn_is_not_slow_too():
    """
    Paying the budget and discarding the answer on every subsequent turn
    would be strictly worse than not reranking at all.
    """
    slow = StubReranker(delay_seconds=0.05)
    retriever = build_retriever(slow, budget=0.01)
    run_evidence_retriever(request(rerank=True), retriever)
    assert retriever.rerank_circuit_open

    run_evidence_retriever(request(rerank=True), retriever)
    assert slow.calls == 1, "the reranker was called again after blowing the budget"

    retriever.reset_rerank_circuit()
    run_evidence_retriever(request(rerank=True), retriever)
    assert slow.calls == 2


class ConcurrencyProbe(StubReranker):
    """A reranker that records how many calls were inside it at once."""

    def __init__(self, delay_seconds: float) -> None:
        super().__init__(delay_seconds)
        self._lock = threading.Lock()
        self.inside = 0
        self.peak = 0

    def score(self, query: str, passages: list[str]) -> list[float]:
        with self._lock:
            self.inside += 1
            self.peak = max(self.peak, self.inside)
        try:
            return super().score(query, passages)
        finally:
            with self._lock:
                self.inside -= 1


def test_no_more_than_the_slot_count_rerank_at_once_and_the_rest_do_not_wait():
    """
    The admission bound (docs/w2/LOAD_TEST.md §5): with every slot taken a
    rerank is not queued. It returns the fused order at once, marked busy,
    and the circuit stays shut because nothing was slow.
    """
    probe = ConcurrencyProbe(delay_seconds=0.3)
    retriever = GuidelineRetriever(
        load_index(),
        load_registry(),
        embedder=FakeEmbedder(),
        reranker=probe,
        cache_dir=None,
        rerank_budget_seconds=5.0,
        rerank_slots=2,
    )

    def one(_: int) -> tuple[bool, float]:
        started = time.perf_counter()
        output = run_evidence_retriever(request(rerank=True), retriever)
        return output.reranked, time.perf_counter() - started

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(one, range(10)))

    assert probe.peak <= 2
    reranked = [r for r in results if r[0]]
    turned_away = [r for r in results if not r[0]]
    assert reranked and turned_away
    # Turned away means not waited: well under one rerank's 0.3 s.
    assert max(seconds for _, seconds in turned_away) < 0.25
    assert not retriever.rerank_circuit_open


def test_the_log_line_says_a_turned_away_rerank_was_busy():
    retriever = build_retriever(StubReranker())
    assert retriever._rerank_slots.acquire(blocking=False)
    assert retriever._rerank_slots.acquire(blocking=False)  # default: two slots
    try:
        with capture_logs() as logs:
            output = run_evidence_retriever(request(rerank=True), retriever)
    finally:
        retriever._rerank_slots.release()
        retriever._rerank_slots.release()
    data = [log for log in logs if log.get("event_type") == RETRIEVAL_EVENT][0]["event_data"]
    assert output.reranked is False
    assert (data["rerank_busy"], data["rerank_over_budget"]) == (True, False)
    assert not retriever.rerank_circuit_open


def test_the_default_rerank_budget_is_the_one_d41_set():
    """
    D41 raised D25's 2.5 s to 4 s after measuring the cross-encoder on the
    deployment host. A default that drifted from the decision would change
    how often a clinician's turn loses its reranking, silently.
    """
    assert RERANK_BUDGET_SECONDS == 4.0
    default = inspect.signature(GuidelineRetriever).parameters["rerank_budget_seconds"].default
    assert default == RERANK_BUDGET_SECONDS


# --------------------------------------------------------------------------
# the citation contract
# --------------------------------------------------------------------------


def test_every_chunk_yields_a_guideline_citation_with_all_five_fields():
    output = run_evidence_retriever(request(), build_retriever())
    for chunk in output.chunks:
        citation = citation_for(chunk)
        assert isinstance(citation, Citation)
        assert citation.source_type is SourceType.GUIDELINE
        assert citation.source_id == chunk.source.guideline_id
        assert citation.page_or_section == chunk.source.section
        assert citation.field_or_chunk_id == chunk.chunk_id
        assert citation.quote_or_value
        # invariant 3 / D3: a guideline has no pixels and no extraction run.
        assert citation.bbox is None
        assert citation.extraction_id is None


def test_the_quote_is_always_a_substring_of_the_chunk():
    output = run_evidence_retriever(request(), build_retriever())
    for chunk in output.chunks:
        assert str(citation_for(chunk).quote_or_value) in chunk.text
        excerpt = chunk.text.split(". ")[0]
        assert str(citation_for(chunk, excerpt).quote_or_value) in chunk.text


def test_an_excerpt_that_is_not_in_the_chunk_is_refused():
    chunk = run_evidence_retriever(request(), build_retriever()).chunks[0]
    with pytest.raises(ExcerptNotInChunkError):
        citation_for(chunk, "a fluent paraphrase the model preferred")
    with pytest.raises(ExcerptNotInChunkError):
        citation_for(chunk, "   ")


def test_the_excerpt_error_does_not_quote_the_text_it_rejected():
    """An error that echoes the text writes it into the log (invariant 1)."""
    chunk = run_evidence_retriever(request(), build_retriever()).chunks[0]
    bad = "zzz not in the chunk zzz"
    with pytest.raises(ExcerptNotInChunkError) as caught:
        citation_for(chunk, bad)
    assert bad not in str(caught.value)
    assert chunk.text[:40] not in str(caught.value)


# --------------------------------------------------------------------------
# dated sources
# --------------------------------------------------------------------------


def test_a_dated_source_is_flagged_on_the_version_the_ui_shows():
    registry = load_registry()
    dated = set(registry.dated_ids())
    assert dated, "the corpus has no dated source to flag"

    output = run_evidence_retriever(
        EvidenceRetrieverInput(
            concepts=[
                ClinicalConcept(text="screening asymptomatic adults for thyroid dysfunction")
            ],
            top_k=10,
            method=RetrievalMethod.LEXICAL,
            rerank=False,
            correlation_id=CORRELATION_ID,
        ),
        build_retriever(),
    )
    for chunk in output.chunks:
        expected = chunk.source.guideline_id in dated
        assert chunk.source.version.endswith(DATED_VERSION_SUFFIX) is expected
    assert dated_chunk_ids(output.chunks) == [
        c.chunk_id for c in output.chunks if c.source.guideline_id in dated
    ]


# --------------------------------------------------------------------------
# observability
# --------------------------------------------------------------------------


def test_the_log_line_carries_counts_scores_flags_and_per_stage_latency():
    with capture_logs() as logs:
        run_evidence_retriever(request(rerank=True), build_retriever(StubReranker()))
    events = [log for log in logs if log.get("event_type") == RETRIEVAL_EVENT]
    assert len(events) == 1
    data = events[0]["event_data"]

    assert data["hit_count"] == 4
    assert len(data["retrieval_scores"]) == 4
    assert len(data["rerank_scores"]) == 4
    assert data["reranked"] is True
    assert data["rerank_candidates"] == 20
    assert data["concept_count"] == 1
    for stage in ("lexical_ms", "dense_ms", "fusion_ms", "rerank_ms", "total_ms"):
        assert isinstance(data[stage], float)
    assert data["total_ms"] >= data["rerank_ms"]
    assert data["guideline_ids"]
    assert isinstance(data["dated_hit_count"], int)


def test_the_log_line_never_carries_the_question_or_the_evidence_text():
    """
    CLAUDE.md invariant 1. The concept text is nonsense on purpose: a
    clinical marker word would collide with a guideline id, which the log
    line carries deliberately (bibliography is not chart content), and the
    test would then be asserting the wrong thing.
    """
    marker = "zebra mackerel lighthouse quernstone"
    with capture_logs() as logs:
        output = run_evidence_retriever(
            EvidenceRetrieverInput(
                concepts=[ClinicalConcept(text=marker)],
                top_k=4,
                method=RetrievalMethod.HYBRID,
                rerank=False,
                correlation_id=CORRELATION_ID,
            ),
            build_retriever(),
        )
    rendered = repr([log for log in logs if log.get("event_type") == RETRIEVAL_EVENT])
    assert marker not in rendered
    for word in marker.split():
        assert word not in rendered
    for chunk in output.chunks:
        assert chunk.text[:60] not in rendered


def test_the_log_line_reports_the_budget_fallback():
    slow = StubReranker(delay_seconds=0.05)
    with capture_logs() as logs:
        run_evidence_retriever(request(rerank=True), build_retriever(slow, budget=0.01))
    data = [log for log in logs if log.get("event_type") == RETRIEVAL_EVENT][0]["event_data"]
    assert data["reranked"] is False
    assert data["rerank_over_budget"] is True
    assert data["rerank_ms"] > 0
