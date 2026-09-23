"""
The hybrid guideline retriever.

BM25 and dense vectors are run independently, fused with RRF, and the top
candidates reordered by a cross-encoder. D25 (docs/w2/DECISIONS.md) settles
what those stages are: local bge-small embeddings, **equal-weight** RRF,
and a local cross-encoder over the top `DEFAULT_CANDIDATE_K`.

The types here are the contract types from `app.models.w2.workers` — this
module does not define its own. It used to, on this branch, while the
contracts were unmerged; that mirror is deleted.

Two invariants are structural rather than checked:

* **Every returned chunk carries its full source metadata** (CLAUDE.md
  invariant 2). `_source_for` is the only way an `EvidenceChunk` gets
  built, and it reads the registry, so a chunk whose guideline is not in
  `sources.yaml` raises rather than returning a citation-less result.
* **`source_type` is guideline, always.** Nothing here can produce a
  patient-record citation, because nothing here has ever seen a patient
  record (invariant 3).

Queries go through `query_guard.build_query` before they reach any
retriever, hosted or not (D5, settled by D25).
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from app.models.w2.workers import EvidenceChunk, GuidelineSource, RetrievalMethod
from app.retrieval.bm25 import Bm25Index, ScoredChunk
from app.retrieval.build import CorpusIndex, load_index
from app.retrieval.chunking import GuidelineChunk
from app.retrieval.corpus_meta import CorpusRegistry, SourceRecord, load_registry
from app.retrieval.dense import CACHE_DIR, DenseIndex, Embedder
from app.retrieval.query_guard import ClinicalConcept, build_query
from app.retrieval.rerank import Reranker

#: How many fused candidates the reranker sees (D25: "rerank of the top
#: 20"). Reranking is the expensive stage and the cross-encoder is ~25 ms
#: per candidate on the deployment host's class of CPU, so the pool size is
#: the latency budget.
DEFAULT_CANDIDATE_K = 20

#: How deep each half of the hybrid searches before the lists are fused.
#: Independent of `top_k` on purpose — see the note in fusion.py. Measured
#: on the dev set: depth 10 gives recall@5 0.663, depth 30 gives 0.692, and
#: 60 and 100 give 0.692 as well, so 30 is where it flattens.
DEFAULT_FUSION_DEPTH = 30

#: Per-retriever RRF weights, lexical first. **Equal, by decision** (D25):
#: weighting the dense half 2x scored better on the 30 dev queries it was
#: fitted on, and 30 queries is not enough to adopt a weight from.
EQUAL_FUSION_WEIGHTS = (1.0, 1.0)

#: The latency budget for the rerank stage: D25 set 2.5 s, D41 raised it to
#: 4 s after measuring the cross-encoder on the deployment host. A rerank
#: that takes longer than this does not get to decide the order: the result
#: is discarded, the fused order stands, and the output says
#: `reranked=false`. The clinician gets slightly worse ranking instead of a
#: slow answer, and the output does not claim a reranking that was thrown
#: away.
RERANK_BUDGET_SECONDS = 4.0

#: How many cross-encoder reranks may run at once, per retriever. A rerank
#: that finds every slot taken does not wait: it takes the budget fallback
#: at once (fused order, `reranked=false`, `busy=true`). Measured without a
#: bound (docs/w2/LOAD_TEST.md §5): 50 concurrent reranks thrashed ONNX's
#: threads for minutes (p95 335 s), because the budget is only checked once
#: a rerank *finishes* and the circuit cannot cancel one already running.
#: Bounding admission is what makes the budget a bound. Value and
#: measurements: DECISIONS.md, the rerank-admission entry.
MAX_CONCURRENT_RERANKS = 2

#: Suffix appended to `GuidelineSource.version` for a source older than the
#: registry's threshold. The contract has no `dated` field and forbids
#: extras, and `version` is the field the UI shows, so the flag rides
#: there. `parse_source_version` is the other half — nobody should be
#: splitting this string by hand.
DATED_VERSION_SUFFIX = " (dated)"


def parse_source_version(version: str | None) -> tuple[str | None, bool]:
    """Split `GuidelineSource.version` into (effective date, dated flag)."""
    if version is None:
        return None, False
    if version.endswith(DATED_VERSION_SUFFIX):
        return version[: -len(DATED_VERSION_SUFFIX)], True
    return version, False


@dataclass
class RerankOutcome:
    """What the rerank stage did, for the log line and for the output flag."""

    applied: bool
    elapsed_ms: float
    candidates: int
    over_budget: bool = False
    unavailable: bool = False
    #: Every rerank slot was taken, so this one was not attempted. The
    #: fused order stands, as for over-budget, but the circuit stays shut:
    #: a busy reranker is not evidence of a slow one.
    busy: bool = False


@dataclass
class RetrievalRun:
    """
    Per-stage timings and the rerank outcome for one `retrieve` call.

    Exists so that `app/retrieval/worker.py` can log where the time went
    without timing the retriever from outside and guessing at the split.
    Everything on it is a number or a flag — nothing here can carry chart
    content into a log line (CLAUDE.md invariant 1).
    """

    method: RetrievalMethod
    concept_count: int
    lexical_ms: float = 0.0
    dense_ms: float = 0.0
    fusion_ms: float = 0.0
    total_ms: float = 0.0
    rerank: RerankOutcome | None = None


class GuidelineRetriever:
    """Hybrid retrieval over the committed corpus."""

    def __init__(
        self,
        index: CorpusIndex,
        registry: CorpusRegistry,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
        cache_dir: Path | None = CACHE_DIR,
        rerank_budget_seconds: float = RERANK_BUDGET_SECONDS,
        rerank_slots: int = MAX_CONCURRENT_RERANKS,
    ) -> None:
        self.index = index
        self.registry = registry
        self.reranker = reranker
        self.rerank_budget_seconds = rerank_budget_seconds
        self._rerank_slots = threading.BoundedSemaphore(rerank_slots)
        #: The only threads that ever call the cross-encoder. ONNX Runtime
        #: keeps memory per calling thread: 100 reranks from one thread grew
        #: RSS by 38 MiB, the same 100 spread over 50 request threads (never
        #: more than two at once) by 2.6 GiB. A fixed pool caps that at the
        #: slot count, whatever thread the request arrived on.
        self._rerank_pool = ThreadPoolExecutor(
            max_workers=rerank_slots, thread_name_prefix="rerank"
        )
        #: Opened when a rerank blows the budget. Until it is reset, rerank
        #: is skipped rather than run-and-discarded: paying the budget to throw
        #: the answer away on every subsequent turn is strictly worse than
        #: not reranking at all.
        self.rerank_circuit_open = False
        self._chunks: dict[str, GuidelineChunk] = {c.chunk_id: c for c in index.chunks}
        self._sources: dict[str, SourceRecord] = {s.guideline_id: s for s in registry.sources}
        self.lexical = Bm25Index(index.chunks)
        self.dense: DenseIndex | None = None
        if embedder is not None:
            self.dense = DenseIndex(
                index.chunks,
                embedder=embedder,
                fingerprint=index.documents_sha256,
                cache_dir=cache_dir,
            )

    @classmethod
    def load(
        cls,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
        cache_dir: Path | None = CACHE_DIR,
    ) -> Self:
        """Build a retriever from the committed index and registry."""
        return cls(
            index=load_index(),
            registry=load_registry(),
            embedder=embedder,
            reranker=reranker,
            cache_dir=cache_dir,
        )

    def reset_rerank_circuit(self) -> None:
        """Let the reranker be tried again after it blew the budget."""
        self.rerank_circuit_open = False

    # -- internals -------------------------------------------------------
    def _source_for(self, chunk: GuidelineChunk) -> GuidelineSource:
        record = self._sources.get(chunk.guideline_id)
        if record is None:
            raise KeyError(
                f"{chunk.chunk_id} names guideline {chunk.guideline_id!r}, which is "
                "not in sources.yaml; a chunk with no bibliography entry cannot be "
                "cited (CLAUDE.md invariant 2)"
            )
        version = record.effective_date.isoformat()
        if record.is_dated(self.registry.as_of, self.registry.dated_after_years):
            version += DATED_VERSION_SUFFIX
        return GuidelineSource(
            guideline_id=record.guideline_id,
            title=record.title,
            section=chunk.section,
            publisher=record.publisher,
            version=version,
            url=record.url,
        )

    def _materialise(
        self, scored: list[ScoredChunk], method: RetrievalMethod
    ) -> list[EvidenceChunk]:
        results = []
        for item in scored:
            chunk = self._chunks[item.chunk_id]
            results.append(
                EvidenceChunk(
                    chunk_id=chunk.chunk_id,
                    text=chunk.text,
                    source=self._source_for(chunk),
                    retrieval_score=float(item.score),
                    method=method,
                )
            )
        return results

    def _apply_rerank(
        self, query: str, results: list[EvidenceChunk]
    ) -> tuple[list[EvidenceChunk], RerankOutcome]:
        if self.reranker is None:
            return results, RerankOutcome(False, 0.0, len(results), unavailable=True)
        if self.rerank_circuit_open:
            return results, RerankOutcome(False, 0.0, len(results), over_budget=True)

        # Admission, not a queue: waiting for a slot would spend the budget
        # before the rerank even started.
        if not self._rerank_slots.acquire(blocking=False):
            return results, RerankOutcome(False, 0.0, len(results), busy=True)
        try:
            started = time.perf_counter()
            scores = self._rerank_pool.submit(
                self.reranker.score, query, [r.text for r in results]
            ).result()
            elapsed = time.perf_counter() - started
        finally:
            self._rerank_slots.release()

        if elapsed > self.rerank_budget_seconds:
            # D25: over budget means the fused order stands and the output
            # says so. The result is discarded rather than used late.
            self.rerank_circuit_open = True
            return results, RerankOutcome(False, elapsed * 1000.0, len(results), over_budget=True)

        reranked = [
            r.model_copy(update={"rerank_score": float(score)})
            for r, score in zip(results, scores, strict=True)
        ]
        reranked.sort(key=lambda r: (-(r.rerank_score or 0.0), r.chunk_id))
        return reranked, RerankOutcome(True, elapsed * 1000.0, len(results))

    # -- public API ------------------------------------------------------
    def retrieve(
        self,
        concepts: list[ClinicalConcept] | list[str],
        top_k: int = 10,
        method: RetrievalMethod = RetrievalMethod.HYBRID,
        rerank: bool = False,
        candidate_k: int = DEFAULT_CANDIDATE_K,
        fusion_depth: int = DEFAULT_FUSION_DEPTH,
        fusion_weights: tuple[float, float] = EQUAL_FUSION_WEIGHTS,
    ) -> list[EvidenceChunk]:
        """Retrieve, discarding the rerank outcome. See `retrieve_with_outcome`."""
        chunks, _ = self.retrieve_with_outcome(
            concepts,
            top_k=top_k,
            method=method,
            rerank=rerank,
            candidate_k=candidate_k,
            fusion_depth=fusion_depth,
            fusion_weights=fusion_weights,
        )
        return chunks

    def retrieve_with_outcome(
        self,
        concepts: list[ClinicalConcept] | list[str],
        top_k: int = 10,
        method: RetrievalMethod = RetrievalMethod.HYBRID,
        rerank: bool = False,
        candidate_k: int = DEFAULT_CANDIDATE_K,
        fusion_depth: int = DEFAULT_FUSION_DEPTH,
        fusion_weights: tuple[float, float] = EQUAL_FUSION_WEIGHTS,
    ) -> tuple[list[EvidenceChunk], RetrievalRun]:
        """
        Retrieve for a set of clinical concepts, and say what each stage did.

        `concepts` are concepts, not a question: `build_query` re-runs the
        D5 guard over every one of them regardless of how they were built.
        The `RetrievalRun` is what the caller needs to set
        `EvidenceRetrieverOutput.reranked` honestly and to log the stages.
        """
        started = time.perf_counter()
        query = build_query(concepts)
        run = RetrievalRun(method=method, concept_count=len(concepts))

        if method is RetrievalMethod.DENSE and self.dense is None:
            raise ValueError("dense retrieval was requested but no embedder was given")

        pool = max(top_k, candidate_k) if rerank else top_k

        if method is RetrievalMethod.LEXICAL:
            stage = time.perf_counter()
            scored = self.lexical.search(query, pool)
            run.lexical_ms = (time.perf_counter() - stage) * 1000.0
        elif method is RetrievalMethod.DENSE:
            assert self.dense is not None  # narrowed by the check above
            stage = time.perf_counter()
            scored = self.dense.search(query, pool)
            run.dense_ms = (time.perf_counter() - stage) * 1000.0
        else:
            if self.dense is None:
                raise ValueError(
                    "hybrid retrieval needs an embedder; pass one, or ask for "
                    "RetrievalMethod.LEXICAL explicitly rather than getting "
                    "lexical results labelled hybrid"
                )
            # Each half searches to the same depth, so neither dominates the
            # fusion just by having been asked for more candidates — and that
            # depth is `fusion_depth`, not `pool`, because fusing two lists
            # only as long as the answer throws away the evidence that one
            # retriever found something the other missed.
            depth = max(pool, fusion_depth)
            stage = time.perf_counter()
            lexical_hits = self.lexical.search(query, depth)
            run.lexical_ms = (time.perf_counter() - stage) * 1000.0
            stage = time.perf_counter()
            dense_hits = self.dense.search(query, depth)
            run.dense_ms = (time.perf_counter() - stage) * 1000.0
            stage = time.perf_counter()
            scored = _fuse(lexical_hits, dense_hits, pool, fusion_weights)
            run.fusion_ms = (time.perf_counter() - stage) * 1000.0

        results = self._materialise(scored, method)
        if not rerank:
            run.rerank = RerankOutcome(False, 0.0, 0, unavailable=True)
            run.total_ms = (time.perf_counter() - started) * 1000.0
            return results[:top_k], run

        reranked, outcome = self._apply_rerank(query, results)
        run.rerank = outcome
        run.total_ms = (time.perf_counter() - started) * 1000.0
        return reranked[:top_k], run


def _fuse(
    lexical: list[ScoredChunk],
    dense: list[ScoredChunk],
    top_k: int,
    weights: tuple[float, float],
) -> list[ScoredChunk]:
    from app.retrieval.fusion import reciprocal_rank_fusion

    return reciprocal_rank_fusion([lexical, dense], top_k=top_k, weights=list(weights))
