"""
Per-field extraction confidence — one number, defined here and nowhere else.

    confidence = reader_confidence * tier_weight * match_score

* `reader_confidence` — the mean confidence stage 1 assigned to the words
  the locate step matched: 1.0 for every word of a PDF text layer, and
  Tesseract's per-word confidence (0-1) for an OCR page. It says how sure
  the *page reading* is.
* `tier_weight` — how strong the locate evidence is (D24):
  `exact` 1.0, `numeric` 0.95 (same number, written differently),
  `number_only` 0.90 (the number without its unit beside it), `fuzzy` 0.80.
  A checkbox mark found beside its label is `exact`.
* `match_score` — `LocateResult.score`: 1.0 for every exact and numeric
  match, the similarity (< 1.0, >= the fuzzy threshold) for a fuzzy one.

So a text-layer exact match is 1.0; an OCR word read at 0.91 and matched
exactly is 0.91; the same word matched fuzzily at similarity 0.88 is
0.91 * 0.80 * 0.88 = 0.64. The weights are ordinal judgements, not fitted
probabilities: they exist so that a fuzzy locate can never outrank an exact
one on the same page, which is what D24 asks a consumer to respect.

What the number is **not**: the vision model's opinion. The model reports
no confidence and none is asked for — a self-reported confidence is an
assertion about the source, the thing D3 refuses to take from the model.

Fields that are not located:

* `unlocated` — 0.0. The model read a value and code found no support for
  it on the page; there is no evidence to be partially confident in.
* `missing` / `illegible` — None, as the contract requires: nothing was
  read, and a confidence about nothing invites a threshold comparison.
"""

from __future__ import annotations

from app.extraction import MatchKind

TIER_WEIGHT: dict[MatchKind, float] = {
    MatchKind.EXACT: 1.0,
    MatchKind.NUMERIC: 0.95,
    MatchKind.NUMBER_ONLY: 0.90,
    MatchKind.FUZZY: 0.80,
}

UNLOCATED_CONFIDENCE = 0.0


def extraction_confidence(reader_confidence: float, kind: MatchKind, score: float) -> float:
    """See the module docstring. Clamped to [0, 1] and rounded to 4 places."""
    value = reader_confidence * TIER_WEIGHT[kind] * score
    return round(min(1.0, max(0.0, value)), 4)
