"""
Dense retrieval — the other half of the hybrid.

D5 is OPEN on local vs. hosted embeddings, so the embedder is an
injectable protocol and nothing below it assumes which side won. What is
not open is the constraint attached to D5: a hosted embedder would send
query text to a third party, so any hosted implementation added here must
route through `app/retrieval/query_guard.py` first. The local
implementation below sends nothing anywhere, which is most of its
argument.

`FastEmbedEmbedder` runs BAAI/bge-small-en-v1.5 through ONNX Runtime
(fastembed), specifically to avoid a torch dependency: torch is ~2.5 GB
installed and the agent image does not otherwise need it. bge-small is
384-dimensional, 67 MB, MIT-licensed, and its 512-token window is what
`chunking.MAX_CHUNK_CHARS` is sized against.

Document vectors are cached on disk keyed by (model, corpus fingerprint),
because embedding 629 chunks takes a few seconds and the corpus changes
roughly never. A stale cache cannot be read: the key changes when the
corpus does.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np

from app.retrieval.bm25 import ScoredChunk
from app.retrieval.chunking import GuidelineChunk
from app.retrieval.corpus_meta import CORPUS_DIR

#: MIT-licensed, 384-dim, 512-token window, 67 MB on disk.
DEFAULT_EMBED_MODEL = "BAAI/bge-small-en-v1.5"

#: Not committed — derived data, rebuilt from chunks.json in seconds.
CACHE_DIR = CORPUS_DIR / ".cache"


@runtime_checkable
class Embedder(Protocol):
    """
    What the dense index needs from an embedding model.

    Queries and documents are embedded by separate methods because
    asymmetric models (bge among them) prepend an instruction to the query
    and not to the document. Collapsing the two into one `embed()` is the
    single easiest way to lose several points of recall without any error
    appearing anywhere.
    """

    @property
    def name(self) -> str: ...

    def embed_documents(self, texts: list[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


def l2_normalise(matrix: np.ndarray) -> np.ndarray:
    """Row-wise L2 normalisation, so cosine similarity is a dot product."""
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    return matrix / np.maximum(norms, 1e-12)


class FastEmbedEmbedder:
    """Local ONNX embeddings. No network after the first model download."""

    def __init__(self, model_name: str = DEFAULT_EMBED_MODEL) -> None:
        # Imported lazily so that BM25-only use, and the offline unit
        # tests, neither import onnxruntime nor look for a model on disk.
        from fastembed import TextEmbedding

        self._model_name = model_name
        self._model = TextEmbedding(model_name)

    @property
    def name(self) -> str:
        return self._model_name

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return l2_normalise(np.asarray(list(self._model.embed(texts)), dtype=np.float32))

    def embed_query(self, text: str) -> np.ndarray:
        vector = np.asarray(list(self._model.query_embed([text]))[0], dtype=np.float32)
        return l2_normalise(vector)


class DenseIndex:
    """Cosine-similarity search over cached chunk vectors."""

    def __init__(
        self,
        chunks: list[GuidelineChunk],
        embedder: Embedder,
        fingerprint: str,
        cache_dir: Path | None = CACHE_DIR,
    ) -> None:
        if not chunks:
            raise ValueError("dense index needs at least one chunk")
        self.chunk_ids = [c.chunk_id for c in chunks]
        self.embedder = embedder
        self._vectors = self._load_or_embed(chunks, fingerprint, cache_dir)

    def _cache_path(self, fingerprint: str, cache_dir: Path) -> Path:
        key = hashlib.sha256(f"{self.embedder.name}\x1f{fingerprint}".encode()).hexdigest()[:20]
        safe_model = self.embedder.name.replace("/", "_")
        return cache_dir / f"{safe_model}.{key}.npz"

    def _load_or_embed(
        self, chunks: list[GuidelineChunk], fingerprint: str, cache_dir: Path | None
    ) -> np.ndarray:
        path = self._cache_path(fingerprint, cache_dir) if cache_dir else None
        if path is not None and path.exists():
            with np.load(path, allow_pickle=False) as data:
                cached_ids = [str(x) for x in data["chunk_ids"]]
                if cached_ids == self.chunk_ids:
                    return data["vectors"]
                # Same fingerprint, different ids is a hash collision or a
                # hand-edited cache. Either way, re-embed rather than
                # return vectors for the wrong chunks.

        vectors = self.embedder.embed_documents([c.indexed_text for c in chunks])
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(
                path, vectors=vectors, chunk_ids=np.array(self.chunk_ids, dtype=object).astype(str)
            )
        return vectors

    def __len__(self) -> int:
        return len(self.chunk_ids)

    def search(self, query: str, top_k: int) -> list[ScoredChunk]:
        similarities = self._vectors @ self.embedder.embed_query(query)
        ranked = sorted(
            enumerate(similarities.tolist()),
            key=lambda item: (-item[1], self.chunk_ids[item[0]]),
        )
        return [ScoredChunk(self.chunk_ids[i], float(score)) for i, score in ranked[:top_k]]
