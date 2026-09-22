"""
The D5 bake-off: measure each retrieval configuration on the dev queries.

    python scripts/retrieval_bakeoff.py
    python scripts/retrieval_bakeoff.py --repeats 5 --json out.json

Reads `agent/evals/w2/staging/retrieval_dev.jsonl` — 30 concept-only
queries with hand-assigned relevant chunk ids — and reports recall@5, MRR
and latency for each configuration. Nothing here writes to
`agent/evals/w2/cases/`; staging is where generated material lives, and
promoting it is a gate-session step (CLAUDE.md, "The staging rule").

**D5's constraint applies to this script, not just to production.** Every
query goes through `query_guard.build_query` before it reaches any
retriever, and the Cohere configuration is skipped rather than degraded
when no key is configured, so a row labelled `hybrid+cohere` is never
secretly a row that reran the local reranker.

Two things the numbers do not say, and the write-up must:

* Relevance was judged from a pool of BM25's and the dense retriever's
  top-8. A configuration that only reorders that pool (any reranker) is
  unaffected; a hypothetical retriever that finds chunks outside it would
  be scored unfairly low.
* Latency is this Mac, warm cache, 696 chunks in memory. It is a floor for
  the deployment host, not a prediction of it.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.w2.workers import RetrievalMethod  # noqa: E402
from app.retrieval.build import load_index  # noqa: E402
from app.retrieval.corpus_meta import load_registry  # noqa: E402
from app.retrieval.dense import FastEmbedEmbedder  # noqa: E402
from app.retrieval.rerank import (  # noqa: E402
    CohereReranker,
    LocalCrossEncoder,
    cohere_key_available,
)
from app.retrieval.retriever import EQUAL_FUSION_WEIGHTS, GuidelineRetriever  # noqa: E402

#: The one fitted parameter in this comparison. Weighting the dense half
#: twice as heavily is worth ~4 points of recall@5 on the dev set and is
#: stable across fusion depths 30-100 and RRF k 20-60 — but it is fitted on
#: 30 queries, and the write-up says so rather than presenting it as a
#: property of the method.
DENSE_HEAVY_WEIGHTS = (1.0, 2.0)

DEV_PATH = Path(__file__).resolve().parents[1] / "evals/w2/staging/retrieval_dev.jsonl"

#: recall@5 is the comparison number across configurations. recall@4 is
#: what actually ships: D25 passes the top 4 chunks on to the answer step.
RECALL_AT = 5
SHIPPED_TOP_K = 4
#: MRR is computed over the full returned list.
TOP_K = 10


@dataclass
class ConfigResult:
    name: str
    recall_at_5: float
    mrr: float
    recall_at_4: float = 0.0
    latencies_ms: list[float] = field(default_factory=list)
    per_query: dict[str, float] = field(default_factory=dict)
    skipped: str | None = None

    @property
    def p50(self) -> float:
        return statistics.median(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def p95(self) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        # Nearest-rank p95: with 90 samples this is the 86th, which is a
        # real measurement rather than an interpolation between two.
        index = max(0, min(len(ordered) - 1, round(0.95 * len(ordered) + 0.5) - 1))
        return ordered[index]


def load_queries() -> list[dict]:
    rows = [json.loads(line) for line in DEV_PATH.read_text(encoding="utf-8").splitlines() if line]
    unlabelled = [r["query_id"] for r in rows if not r["relevant_chunk_ids"]]
    if unlabelled:
        raise SystemExit(
            f"unlabelled queries: {', '.join(unlabelled)}; "
            "run scripts/retrieval_label_dev_set.py first"
        )
    return rows


def score(retrieved_ids: list[str], relevant: set[str]) -> tuple[float, float, float]:
    """(recall@5, recall@4, reciprocal rank). 0.0 when nothing is found."""
    recall5 = len(relevant.intersection(retrieved_ids[:RECALL_AT])) / len(relevant)
    recall4 = len(relevant.intersection(retrieved_ids[:SHIPPED_TOP_K])) / len(relevant)
    reciprocal = 0.0
    for rank, chunk_id in enumerate(retrieved_ids, start=1):
        if chunk_id in relevant:
            reciprocal = 1.0 / rank
            break
    return recall5, recall4, reciprocal


def run_config(
    name: str,
    retriever: GuidelineRetriever,
    queries: list[dict],
    method: RetrievalMethod,
    rerank: bool,
    repeats: int,
    fusion_weights: tuple[float, float] = EQUAL_FUSION_WEIGHTS,
) -> ConfigResult:
    recalls: list[float] = []
    recalls4: list[float] = []
    reciprocals: list[float] = []
    latencies: list[float] = []
    per_query: dict[str, float] = {}

    for pass_index in range(repeats):
        for row in queries:
            started = time.perf_counter()
            results = retriever.retrieve(
                [row["query"]],
                top_k=TOP_K,
                method=method,
                rerank=rerank,
                fusion_weights=fusion_weights,
            )
            latencies.append((time.perf_counter() - started) * 1000.0)
            if pass_index == 0:
                recall, recall4, reciprocal = score(
                    [r.chunk_id for r in results], set(row["relevant_chunk_ids"])
                )
                recalls.append(recall)
                recalls4.append(recall4)
                reciprocals.append(reciprocal)
                per_query[row["query_id"]] = round(recall, 4)

    return ConfigResult(
        name=name,
        recall_at_5=statistics.mean(recalls),
        recall_at_4=statistics.mean(recalls4),
        mrr=statistics.mean(reciprocals),
        latencies_ms=latencies,
        per_query=per_query,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Retrieval bake-off for D5.")
    ap.add_argument("--repeats", type=int, default=3, help="timing passes over the query set")
    ap.add_argument("--json", type=Path, default=None, help="also write results as JSON")
    args = ap.parse_args(argv)

    queries = load_queries()
    index = load_index()
    registry = load_registry()

    print(f"corpus   : {index.document_count} documents, {index.chunk_count} chunks")
    print(
        f"queries  : {len(queries)} ({sum(len(q['relevant_chunk_ids']) for q in queries)} judgments)"
    )
    print(f"repeats  : {args.repeats}\n")

    embed_started = time.perf_counter()
    embedder = FastEmbedEmbedder()
    retriever = GuidelineRetriever(index, registry, embedder=embedder)
    print(f"dense index ready in {time.perf_counter() - embed_started:.1f}s ({embedder.name})\n")

    results: list[ConfigResult] = []
    results.append(
        run_config("bm25", retriever, queries, RetrievalMethod.LEXICAL, False, args.repeats)
    )
    results.append(
        run_config("dense", retriever, queries, RetrievalMethod.DENSE, False, args.repeats)
    )
    results.append(
        run_config("hybrid-rrf", retriever, queries, RetrievalMethod.HYBRID, False, args.repeats)
    )
    results.append(
        run_config(
            "hybrid-rrf-dense2x",
            retriever,
            queries,
            RetrievalMethod.HYBRID,
            False,
            args.repeats,
            DENSE_HEAVY_WEIGHTS,
        )
    )

    local = LocalCrossEncoder()
    retriever.reranker = local
    short = local.name.split("/")[-1]
    results.append(
        run_config(
            f"hybrid-rrf + {short}  <- D25",
            retriever,
            queries,
            RetrievalMethod.HYBRID,
            True,
            args.repeats,
        )
    )
    results.append(
        run_config(
            f"hybrid-rrf-dense2x + {short}",
            retriever,
            queries,
            RetrievalMethod.HYBRID,
            True,
            args.repeats,
            DENSE_HEAVY_WEIGHTS,
        )
    )

    if cohere_key_available():
        cohere = CohereReranker()
        retriever.reranker = cohere
        results.append(
            run_config(
                f"hybrid-rrf-dense2x + {cohere.name}",
                retriever,
                queries,
                RetrievalMethod.HYBRID,
                True,
                args.repeats,
                DENSE_HEAVY_WEIGHTS,
            )
        )
    else:
        results.append(
            ConfigResult(
                name="hybrid-rrf-dense2x + cohere:rerank-v3.5",
                recall_at_5=0.0,
                mrr=0.0,
                skipped="COHERE_API_KEY not set in the environment or agent/.env",
            )
        )

    width = max(len(r.name) for r in results)
    print(f"{'config':<{width}}  recall@5  recall@4     MRR   p50 ms   p95 ms")
    print("-" * (width + 44))
    for result in results:
        if result.skipped:
            print(f"{result.name:<{width}}  SKIPPED — {result.skipped}")
            continue
        print(
            f"{result.name:<{width}}  {result.recall_at_5:>8.3f}  {result.recall_at_4:>8.3f}  "
            f"{result.mrr:>6.3f}  {result.p50:>7.1f}  {result.p95:>7.1f}"
        )

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "corpus": {
                        "documents": index.document_count,
                        "chunks": index.chunk_count,
                        "documents_sha256": index.documents_sha256,
                    },
                    "queries": len(queries),
                    "repeats": args.repeats,
                    "embed_model": embedder.name,
                    "configs": [
                        {
                            "name": r.name,
                            "skipped": r.skipped,
                            "recall_at_5": round(r.recall_at_5, 4),
                            "recall_at_4": round(r.recall_at_4, 4),
                            "mrr": round(r.mrr, 4),
                            "p50_ms": round(r.p50, 2),
                            "p95_ms": round(r.p95, 2),
                            "per_query_recall": r.per_query,
                        }
                        for r in results
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
