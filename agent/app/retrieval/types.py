"""
Retrieval result types, shaped to the evidence-retriever contract.

These mirror `EvidenceChunk`, `GuidelineSource` and `RetrievalMethod` on
`feat/w2-contracts` (`agent/app/models/w2/workers.py`) field for field.
They are duplicated rather than imported because that branch is not merged
yet; when it lands, this module becomes a thin adapter and the duplicate
definitions go. The field names are identical on purpose so that the
adapter is a rename of nothing.

What is deliberately NOT mirrored here is `EvidenceRetrieverInput`. That
type's whole job is to have no patient field (D5), and re-declaring it on
this branch would create a second thing to keep honest. The retrieval
layer takes concepts (`app/retrieval/query_guard.py`) and the contract
type wraps it at the graph boundary.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RetrievalMethod(StrEnum):
    """Which half of the hybrid produced a chunk, or both."""

    LEXICAL = "lexical"
    DENSE = "dense"
    HYBRID = "hybrid"


class GuidelineSource(BaseModel):
    """
    Where a chunk came from. Bibliography, never patient data.

    `version` carries the publisher's effective date (the later of
    publication and last review). The contract types it as a free string,
    and a date is the only version these publishers actually have.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    guideline_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    section: str = Field(
        min_length=1,
        description="Heading trail; becomes the citation's page_or_section.",
    )
    publisher: str | None = None
    version: str | None = None
    url: str | None = None


class RetrievedChunk(BaseModel):
    """
    One retrieved passage with its scores. Maps 1:1 onto the contract's
    `EvidenceChunk`.

    Retrieval score and rerank score are kept apart because they are
    different quantities on different scales from different models:
    collapsing them makes "was that the retriever or the reranker?"
    unanswerable the first time a result looks wrong. `rerank_score` is
    `None` when no reranker ran — never zero, which is a real score.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source: GuidelineSource
    retrieval_score: float
    rerank_score: float | None = None
    method: RetrievalMethod

    @property
    def effective_score(self) -> float:
        """What the final ordering is by: the reranker's word if it spoke."""
        return self.rerank_score if self.rerank_score is not None else self.retrieval_score
