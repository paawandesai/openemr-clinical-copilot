"""
The hybrid guideline retriever.

BM25 and dense vectors are run independently and fused with RRF, then an
optional reranker reorders the top candidates. Which reranker, and whether
the embedder is local or hosted, is D5 and still OPEN — both are injected,
and `scripts/retrieval_bakeoff.py` is what turns the question into
numbers.

Two invariants are structural here rather than checked:

* **Every returned chunk carries its full source metadata** (CLAUDE.md
  invariant 2). `_source_for` is the only way a `RetrievedChunk` gets
  built, and it reads the registry, so a chunk whose guideline is not in
  `sources.yaml` raises rather than returning a citation-less result.
* **`source_type` is guideline, always.** Nothing in this module can
  produce a patient-record citation, because nothing in it has ever seen a
  patient record (invariant 3).

Queries go through `query_guard.build_query` before they reach any
retriever, hosted or not (D5).
"""

from __future__ import annotations

from pathlib import Path
from typing import Self

from app.retrieval.bm25 import Bm25Index, ScoredChunk
from app.retrieval.build import CorpusIndex, load_index
from app.retrieval.chunking import GuidelineChunk
from app.retrieval.corpus_meta import CorpusRegistry, SourceRecord, load_registry
from app.retrieval.dense import CACHE_DIR, DenseIndex, Embedder
from app.retrieval.query_guard import ClinicalConcept, build_query
from app.retrieval.rerank import Reranker
from app.retrieval.types import GuidelineSource, RetrievalMethod, RetrievedChunk

#: How many fused candidates the reranker sees. Reranking is the expensive
#: stage, and past ~30 the cross-encoder is re-reading chunks the fusion
#: already ranked below anything that will be shown.
DEFAULT_CANDIDATE_K = 30

#: How deep each half of the hybrid searches before the lists are fused.
#: Independent of `top_k` on purpose — see the note in fusion.py. Measured
#: on the dev set: depth 10 gives recall@5 0.663, depth 30 gives 0.692, and
#: 60 and 100 give 0.692 as well, so 30 is where it flattens.
DEFAULT_FUSION_DEPTH = 30

#: Per-retriever RRF weights, lexical first. Equal by default: a weight is
#: a fitted parameter, and `scripts/retrieval_bakeoff.py` is where fitting
#: it is argued for with numbers rather than assumed here.
EQUAL_FUSION_WEIGHTS = (1.0, 1.0)


class GuidelineRetriever:
    """Hybrid retrieval over the committed corpus."""

    def __init__(
        self,
        index: CorpusIndex,
        registry: CorpusRegistry,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
        cache_dir: Path | None = CACHE_DIR,
    ) -> None:
        self.index = index
        self.registry = registry
        self.reranker = reranker
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

    # -- internals -------------------------------------------------------
    def _source_for(self, chunk: GuidelineChunk) -> GuidelineSource:
        record = self._sources.get(chunk.guideline_id)
        if record is None:
            raise KeyError(
                f"{chunk.chunk_id} names guideline {chunk.guideline_id!r}, which is "
                "not in sources.yaml; a chunk with no bibliography entry cannot be "
                "cited (CLAUDE.md invariant 2)"
            )
        return GuidelineSource(
            guideline_id=record.guideline_id,
            title=record.title,
            section=chunk.section,
            publisher=record.publisher,
            version=record.effective_date.isoformat(),
            url=record.url,
        )

    def _materialise(
        self, scored: list[ScoredChunk], method: RetrievalMethod
    ) -> list[RetrievedChunk]:
        results = []
        for item in scored:
            chunk = self._chunks[item.chunk_id]
            results.append(
                RetrievedChunk(
                    chunk_id=chunk.chunk_id,
                    text=chunk.text,
                    source=self._source_for(chunk),
                    retrieval_score=item.score,
                    method=method,
                )
            )
        return results

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
    ) -> list[RetrievedChunk]:
        """
        Retrieve for a set of clinical concepts.

        `concepts` are concepts, not a question: `build_query` re-runs the
        D5 guard over every one of them regardless of how they were built.
        """
        query = build_query(concepts)

        if method is RetrievalMethod.DENSE and self.dense is None:
            raise ValueError("dense retrieval was requested but no embedder was given")

        pool = max(top_k, candidate_k) if rerank else top_k

        if method is RetrievalMethod.LEXICAL:
            scored = self.lexical.search(query, pool)
        elif method is RetrievalMethod.DENSE:
            assert self.dense is not None  # narrowed by the check above
            scored = self.dense.search(query, pool)
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
            scored = _fuse(
                self.lexical.search(query, depth),
                self.dense.search(query, depth),
                pool,
                fusion_weights,
            )

        results = self._materialise(scored, method)
        if not rerank or self.reranker is None:
            return results[:top_k]

        scores = self.reranker.score(query, [r.text for r in results])
        reranked = [
            r.model_copy(update={"rerank_score": score})
            for r, score in zip(results, scores, strict=True)
        ]
        reranked.sort(key=lambda r: (-(r.rerank_score or 0.0), r.chunk_id))
        return reranked[:top_k]


def _fuse(
    lexical: list[ScoredChunk],
    dense: list[ScoredChunk],
    top_k: int,
    weights: tuple[float, float],
) -> list[ScoredChunk]:
    from app.retrieval.fusion import reciprocal_rank_fusion

    return reciprocal_rank_fusion([lexical, dense], top_k=top_k, weights=list(weights))
