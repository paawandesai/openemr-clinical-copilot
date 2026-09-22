"""
Rerankers — the second half of D5's open question.

Two implementations, deliberately behind one protocol so the bake-off can
swap them and the answer is a number rather than a preference:

* `LocalCrossEncoder` — Xenova/ms-marco-MiniLM-L-6-v2 through ONNX
  Runtime. Apache-2.0, 80 MB, no torch, no network after download, and no
  query text leaves the process.
* `CohereReranker` — the hosted option. It is the one path in this package
  that sends text to a third party, so it runs the D5 query guard on the
  query before the request is built, and it will not construct at all
  without a key.

The Cohere client is written against `urllib` rather than the `cohere`
SDK on purpose: one HTTP POST does not justify a dependency, and a hand-
rolled request makes it obvious at review time exactly what leaves the
machine.

**The key is never logged, never printed, never put in an exception
message.** `_read_api_key` returns it or returns None; nothing else in
this module holds it.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.retrieval.dense import model_cache_dir
from app.retrieval.query_guard import assert_concept_only

#: Apache-2.0, 80 MB, ONNX. The MiniLM cross-encoders are the standard
#: baseline for this comparison and the L-6 variant is the cheap one.
DEFAULT_CROSS_ENCODER = "Xenova/ms-marco-MiniLM-L-6-v2"

COHERE_MODEL = "rerank-v3.5"
COHERE_ENDPOINT = "https://api.cohere.com/v2/rerank"
COHERE_KEY_NAME = "COHERE_API_KEY"

#: Where a key may be configured, in order. The work order names
#: `agent/.env`; the environment is checked first so a CI run can supply
#: one without writing a file.
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


@runtime_checkable
class Reranker(Protocol):
    """Score (query, passage) pairs. Higher is more relevant."""

    @property
    def name(self) -> str: ...

    def score(self, query: str, passages: list[str]) -> list[float]: ...


def _read_api_key(env_file: Path = ENV_FILE) -> str | None:
    """
    The key from the environment, else from `agent/.env`. Returns None when
    there is none — callers branch on that, they do not see a reason.
    """
    from_env = os.environ.get(COHERE_KEY_NAME)
    if from_env:
        return from_env.strip() or None
    if not env_file.exists():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip() == COHERE_KEY_NAME:
            return value.strip().strip("'\"") or None
    return None


def cohere_key_available(env_file: Path = ENV_FILE) -> bool:
    """Whether a Cohere key is configured. Says nothing about its value."""
    return _read_api_key(env_file) is not None


class LocalCrossEncoder:
    """Local ONNX cross-encoder. Nothing leaves the process."""

    def __init__(
        self, model_name: str = DEFAULT_CROSS_ENCODER, cache_dir: str | None = None
    ) -> None:
        # Lazy, for the same reason as FastEmbedEmbedder: the offline unit
        # tests must not need onnxruntime or a downloaded model.
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self._model_name = model_name
        self._model = TextCrossEncoder(model_name, cache_dir=cache_dir or model_cache_dir())

    @property
    def name(self) -> str:
        return f"local:{self._model_name}"

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        return [float(s) for s in self._model.rerank(query, passages)]


class CohereReranker:
    """
    Hosted rerank. Constructing one without a key is an error rather than a
    silent no-op, so a bake-off row labelled "cohere" is never secretly a
    row that did nothing.
    """

    def __init__(self, model: str = COHERE_MODEL, env_file: Path = ENV_FILE) -> None:
        key = _read_api_key(env_file)
        if key is None:
            raise RuntimeError(
                f"{COHERE_KEY_NAME} is not set in the environment or agent/.env; "
                "the hosted reranker cannot run"
            )
        self._key = key
        self._model = model

    @property
    def name(self) -> str:
        return f"cohere:{self._model}"

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        # D5: this is the one call in this package that leaves the machine.
        assert_concept_only(query)

        body = json.dumps(
            {
                "model": self._model,
                "query": query,
                "documents": passages,
                "top_n": len(passages),
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            COHERE_ENDPOINT,
            data=body,
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            # Deliberately not echoing the response body: an auth error
            # from some providers quotes the key back.
            raise RuntimeError(f"Cohere rerank failed with HTTP {exc.code}") from None

        scores = [0.0] * len(passages)
        for result in payload.get("results", []):
            scores[int(result["index"])] = float(result["relevance_score"])
        return scores
