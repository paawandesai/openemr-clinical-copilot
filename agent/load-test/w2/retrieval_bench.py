#!/usr/bin/env python3
"""
Guideline retrieval (hybrid RAG) at concurrency, in process.

The evidence-retriever worker is not behind an HTTP route on `main` (no
supervisor calls it yet), so this drives `run_evidence_retriever` directly
with the committed corpus, the local bge-small embedder and the local
cross-encoder reranker — the same objects `AGENT_PRELOAD_RETRIEVAL=true`
builds at startup (app/retrieval/preload.py).

    cd agent
    python load-test/w2/retrieval_bench.py --levels 1 10 50 --per-worker 4 \\
        --events logs/retrieval-events.jsonl > retrieval-bench.json

Queries are the 30 concept-only dev queries in
`evals/w2/staging/retrieval_dev.jsonl` — concepts, no patient field (D5).
Each level runs `concurrency x per-worker` retrievals from a thread pool of
`concurrency` threads, because the retriever is synchronous and ONNX
inference releases the GIL: that is how a supervisor would have to call it
from the event loop (`asyncio.to_thread`).

The reranker's circuit breaker (app/retrieval/retriever.py) is reset before
each level, so every level starts with reranking available and the
`reranked` share is what that level's own load did to it.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import resource
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

AGENT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AGENT))


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _cpu_seconds() -> float:
    own = resource.getrusage(resource.RUSAGE_SELF)
    return own.ru_utime + own.ru_stime


def _peak_rss_mib() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--levels", type=int, nargs="+", default=[1, 10, 50])
    parser.add_argument("--per-worker", type=int, default=4)
    parser.add_argument("--no-rerank", action="store_true")
    parser.add_argument("--events", required=True)
    args = parser.parse_args()

    from app.middleware.logging import configure_logging
    from app.models.w2.workers import ClinicalConcept, EvidenceRetrieverInput
    from app.retrieval.preload import preload_retrieval
    from app.retrieval.worker import run_evidence_retriever

    queries = [
        json.loads(line)["query"]
        for line in (AGENT / "evals/w2/staging/retrieval_dev.jsonl").read_text().splitlines()
        if line.strip()
    ]
    summary: dict = {"queries": len(queries), "cpu_count": os.cpu_count(), "levels": []}

    with open(args.events, "a", encoding="utf-8") as events, contextlib.redirect_stdout(events):
        configure_logging()
        load_started = time.perf_counter()
        retriever = preload_retrieval(with_reranker=not args.no_rerank)
        summary["preload_seconds"] = round(time.perf_counter() - load_started, 2)

        def one(query: str) -> tuple[float, bool, int]:
            request = EvidenceRetrieverInput(
                concepts=[ClinicalConcept(text=query[:120])],
                top_k=5,
                rerank=not args.no_rerank,
                correlation_id=str(uuid.uuid4()),
            )
            started = time.perf_counter()
            output = run_evidence_retriever(request, retriever)
            return time.perf_counter() - started, output.reranked, len(output.chunks)

        one(queries[0])  # warm
        for level in args.levels:
            retriever.reset_rerank_circuit()
            batch = [queries[i % len(queries)] for i in range(level * args.per_worker)]
            cpu_before, wall_before = _cpu_seconds(), time.perf_counter()
            with ThreadPoolExecutor(max_workers=level) as pool:
                results = list(pool.map(one, batch))
            wall = time.perf_counter() - wall_before
            cpu = _cpu_seconds() - cpu_before
            seconds = [r[0] for r in results]
            summary["levels"].append(
                {
                    "concurrency": level,
                    "retrievals": len(results),
                    "wall_seconds": round(wall, 3),
                    "throughput_per_second": round(len(results) / wall, 2),
                    "latency_ms": {
                        q: round(_percentile(seconds, v) * 1000, 1)
                        for q, v in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99))
                    },
                    "reranked": sum(1 for r in results if r[1]),
                    "reranked_rate": round(sum(1 for r in results if r[1]) / len(results), 3),
                    "circuit_open_after": retriever.rerank_circuit_open,
                    "zero_hit": sum(1 for r in results if r[2] == 0),
                    "mean_cores_busy": round(cpu / wall, 2),
                    "peak_rss_mib": round(_peak_rss_mib(), 1),
                }
            )
    json.dump(summary, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
