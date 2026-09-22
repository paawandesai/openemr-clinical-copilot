"""
Reciprocal Rank Fusion — how the sparse and dense halves are combined.

RRF rather than a weighted score blend because BM25 scores and cosine
similarities are not on the same scale, are not on a *stable* scale
between queries, and a weight tuned on 28 documents is a weight fitted to
28 documents. RRF only reads rank, so it needs no calibration and no
per-query normalisation.

    score(chunk) = sum over retrievers of weight / (k + rank)

`k` damps the top of each list: with k=60, rank 1 contributes 0.0164 and
rank 2 contributes 0.0161, so a chunk ranked 2nd by both retrievers beats
one ranked 1st by a single retriever. That is the behaviour we want from a
hybrid — agreement counts for more than one retriever's confidence.

**Fusion depth is not `top_k`.** Each retriever must contribute a list
much deeper than the number of results wanted, or a chunk that one
retriever ranks 1st and the other does not have in its top-10 loses to
whatever the two happen to agree on near the bottom. On the dev set,
fusing top-10 lists scores recall@5 0.663; fusing top-30 lists and
returning the same 5 scores 0.692, with no change to anything else. That
is the difference between a hybrid that beats its dense half and one that
does not.

`weights` exists because an equal-weight fusion assumes the two
retrievers are equally good, and on a 28-document guideline corpus they
are not. It defaults to equal weights — a weight is a tuned parameter and
a default should not be one.
"""

from __future__ import annotations

from app.retrieval.bm25 import ScoredChunk

#: Cormack et al. (2009) use 60 and it has held up as the default. Not
#: tuned here for the same reason the BM25 constants are not.
RRF_K = 60


def reciprocal_rank_fusion(
    rankings: list[list[ScoredChunk]],
    top_k: int,
    k: int = RRF_K,
    weights: list[float] | None = None,
) -> list[ScoredChunk]:
    """
    Fuse ranked lists. Ties break on `chunk_id`, so the output order is
    identical on every machine and every run.
    """
    if weights is None:
        weights = [1.0] * len(rankings)
    if len(weights) != len(rankings):
        raise ValueError(f"{len(weights)} weights for {len(rankings)} rankings")

    fused: dict[str, float] = {}
    for ranking, weight in zip(rankings, weights, strict=True):
        for rank, scored in enumerate(ranking, start=1):
            fused[scored.chunk_id] = fused.get(scored.chunk_id, 0.0) + weight / (k + rank)

    ranked = sorted(fused.items(), key=lambda item: (-item[1], item[0]))
    return [ScoredChunk(chunk_id, score) for chunk_id, score in ranked[:top_k]]
