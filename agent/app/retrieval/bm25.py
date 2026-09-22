"""
BM25 (Okapi) over the chunk index — the sparse half of the hybrid.

Implemented here rather than pulled in as a dependency for two reasons.
Scoring has to be deterministic down to tie-breaking, because chunk ids
from a retrieval run end up in eval cases that a gate is scored against;
and the tokeniser has to be ours, because clinical retrieval lives or dies
on whether `A1C`, `eGFR`, `25(OH)D` and `TSH` survive it. A general-purpose
tokeniser that strips digits turns "A1C" into "a" and "hemoglobin A1c" into
a query about hemoglobin.

Ties are broken on `chunk_id`, so two chunks with identical scores always
come back in the same order on every machine.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from app.retrieval.chunking import GuidelineChunk

#: Okapi BM25 defaults. k1 bounds term-frequency saturation, b controls
#: length normalisation. Not tuned — tuning them on 28 documents would be
#: fitting noise, and the bake-off measures the configurations, not these.
K1 = 1.2
B = 0.75

#: Keep digits and letters together so `a1c`, `25ohd`, `ldl`, `egfr` and
#: `hba1c` stay whole. Splitting on anything else means `130/80` becomes
#: two useful numbers rather than one unsearchable token.
_TOKEN = re.compile(r"[a-z0-9]+")

#: A deliberately small stopword list. Clinical text is short and dense;
#: an aggressive list removes "high", "low", "no" and "not", each of which
#: changes what a guideline sentence means.
STOPWORDS = frozenset(
    """
    a an and are as at be been being but by for from had has have how i if in
    into is it its of on or that the their them then there these they this to
    was were what when where which while who will with you your
    """.split()
)


def normalise(token: str) -> str:
    """
    Conservative, deterministic morphology: strip a plural `s`/`es`/`ies`.

    Deliberately not a stemmer. Porter maps `statin` and `static` to the
    same stem's neighbourhood and mangles `analyses`/`analysis`; the only
    thing actually costing recall on this corpus is the plural, and this
    handles the plural.
    """
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith(("ses", "xes", "zes", "ches", "shes")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumerics, drop stopwords, de-pluralise."""
    return [normalise(t) for t in _TOKEN.findall(text.lower()) if t not in STOPWORDS]


@dataclass(frozen=True)
class ScoredChunk:
    """A chunk id and one retriever's score for it."""

    chunk_id: str
    score: float


class Bm25Index:
    """An in-memory BM25 index. Built once, queried many times."""

    def __init__(self, chunks: list[GuidelineChunk], k1: float = K1, b: float = B) -> None:
        if not chunks:
            raise ValueError("BM25 index needs at least one chunk")
        self.k1 = k1
        self.b = b
        self.chunk_ids: list[str] = [c.chunk_id for c in chunks]
        self._term_frequencies: list[Counter[str]] = []
        self._lengths: list[int] = []
        document_frequency: Counter[str] = Counter()

        for chunk in chunks:
            tokens = tokenize(chunk.indexed_text)
            counts = Counter(tokens)
            self._term_frequencies.append(counts)
            self._lengths.append(len(tokens))
            document_frequency.update(counts.keys())

        count = len(chunks)
        self.average_length = sum(self._lengths) / count
        # Okapi IDF with the +1 inside the log, which keeps it non-negative
        # for a term present in every document rather than letting a
        # ubiquitous term score negatively and push a chunk down.
        self._idf = {
            term: math.log(1 + (count - freq + 0.5) / (freq + 0.5))
            for term, freq in document_frequency.items()
        }
        self._postings: dict[str, list[int]] = {}
        for index, counts in enumerate(self._term_frequencies):
            for term in counts:
                self._postings.setdefault(term, []).append(index)

    def __len__(self) -> int:
        return len(self.chunk_ids)

    def search(self, query: str, top_k: int) -> list[ScoredChunk]:
        """Top `top_k` chunks for `query`, highest score first."""
        terms = tokenize(query)
        scores: dict[int, float] = {}
        for term in terms:
            idf = self._idf.get(term)
            if idf is None:
                continue
            for index in self._postings[term]:
                frequency = self._term_frequencies[index][term]
                length_norm = 1 - self.b + self.b * (self._lengths[index] / self.average_length)
                contribution = (
                    idf * (frequency * (self.k1 + 1)) / (frequency + self.k1 * length_norm)
                )
                scores[index] = scores.get(index, 0.0) + contribution

        ranked = sorted(
            scores.items(),
            key=lambda item: (-item[1], self.chunk_ids[item[0]]),
        )
        return [ScoredChunk(self.chunk_ids[i], score) for i, score in ranked[:top_k]]
