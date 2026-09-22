"""
Print pooled retrieval candidates for hand-labelling the dev query set.

Relevance labels for `agent/evals/w2/staging/retrieval_dev.jsonl` are
assigned by a person reading candidates, which is what "pooled judgment"
means in IR evaluation and the only honest way to get a ground truth that
is not just one retriever's opinion of itself. The pool is the union of
BM25's and the dense retriever's top-N, so a chunk only one of them finds
still gets seen and judged.

    python scripts/retrieval_pool_candidates.py --depth 10 --slice 0:8

Labelling a pool biases recall upward for every config that was pooled and
downward for one that was not — Cohere rerank reorders the pool rather
than adding to it, so it is unaffected. Stated in the bake-off write-up
rather than hidden here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.retrieval.bm25 import Bm25Index  # noqa: E402
from app.retrieval.build import load_index  # noqa: E402
from app.retrieval.dense import DenseIndex, FastEmbedEmbedder  # noqa: E402
from app.retrieval.query_guard import build_query  # noqa: E402

DEV_PATH = Path(__file__).resolve().parents[1] / "evals/w2/staging/retrieval_dev.jsonl"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--depth", type=int, default=10, help="top-N from each retriever")
    ap.add_argument("--slice", default=":", help="python slice over the query list, e.g. 0:8")
    ap.add_argument("--chars", type=int, default=150, help="snippet length")
    args = ap.parse_args(argv)

    index = load_index()
    lexical = Bm25Index(index.chunks)
    dense = DenseIndex(
        index.chunks, embedder=FastEmbedEmbedder(), fingerprint=index.documents_sha256
    )
    chunks = {c.chunk_id: c for c in index.chunks}

    rows = [json.loads(line) for line in DEV_PATH.read_text(encoding="utf-8").splitlines() if line]
    start, _, end = args.slice.partition(":")
    rows = rows[int(start or 0) : int(end) if end else None]

    for row in rows:
        query = build_query([row["query"]])
        pool: dict[str, list[str]] = {}
        for label, results in (
            ("B", lexical.search(query, args.depth)),
            ("D", dense.search(query, args.depth)),
        ):
            for rank, scored in enumerate(results, start=1):
                pool.setdefault(scored.chunk_id, []).append(f"{label}{rank}")

        print(f"\n### {row['query_id']}  —  {row['query']}")
        ordered = sorted(pool.items(), key=lambda kv: (min(int(t[1:]) for t in kv[1]), kv[0]))
        for chunk_id, tags in ordered:
            chunk = chunks[chunk_id]
            snippet = " ".join(chunk.text.split())[: args.chars]
            print(f"  [{','.join(tags):<7}] {chunk_id}")
            print(f"      {chunk.guideline_id} :: {chunk.section[:70]}")
            print(f"      {snippet}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
