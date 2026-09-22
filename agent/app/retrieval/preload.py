"""
Build the retriever once, at process start, instead of on a clinician's
first question.

Three things are cold on a fresh process and none of them should be paid
for by a request:

* the ONNX weights (67 MB embedder + 80 MB cross-encoder), which in the
  container are baked into the image and must never be fetched at runtime;
* the 693 chunk vectors, which take seconds to compute and are cached per
  (model, corpus fingerprint);
* the BM25 postings, which are cheap but not free.

`preload_retrieval()` does all three and returns the retriever to hold on
to. It reports what it found rather than guessing: if the vector cache was
not in the image, the log line says so, because that is a deployment
defect that otherwise shows up only as a slow first request.

Wired into the FastAPI lifespan in `app/main.py`, behind
`AGENT_PRELOAD_RETRIEVAL` so that a dev process and the test suite do not
load ONNX they are not going to use.
"""

from __future__ import annotations

import os
import time

from app.middleware.logging import log_event
from app.retrieval.build import load_index
from app.retrieval.corpus_meta import load_registry
from app.retrieval.dense import CACHE_DIR, FastEmbedEmbedder, model_cache_dir
from app.retrieval.rerank import LocalCrossEncoder
from app.retrieval.retriever import GuidelineRetriever

PRELOAD_ENV = "AGENT_PRELOAD_RETRIEVAL"

PRELOAD_EVENT = "retrieval_preload_event"


def preload_enabled() -> bool:
    return os.environ.get(PRELOAD_ENV, "").strip().lower() in {"1", "true", "yes"}


def preload_retrieval(with_reranker: bool = True) -> GuidelineRetriever:
    """
    Construct the retriever and warm every cache it has. Never raises for a
    missing vector cache — a slow start is not a reason to refuse to start.
    """
    started = time.perf_counter()
    index = load_index()
    registry = load_registry()
    cache_present = CACHE_DIR.exists() and any(CACHE_DIR.glob("*.npz"))

    embedder = FastEmbedEmbedder()
    reranker = LocalCrossEncoder() if with_reranker else None
    retriever = GuidelineRetriever(index, registry, embedder=embedder, reranker=reranker)

    # One real query through both stages, so the first clinician question
    # is not also the first ONNX inference of the process.
    retriever.retrieve(["guideline evidence warm up"], top_k=3, rerank=with_reranker)

    log_event(
        PRELOAD_EVENT,
        event_data={
            "documents": index.document_count,
            "chunks": index.chunk_count,
            "embed_model": embedder.name,
            "reranker": reranker.name if reranker else None,
            "model_cache_dir": model_cache_dir(),
            "vector_cache_prebuilt": cache_present,
            "dated_sources": registry.dated_ids(),
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1),
        },
    )
    return retriever
