"""
The concept-only query guard — D5's hard constraint, enforced in code.

D5 (docs/w2/DECISIONS.md): "no patient identifiers in retrieval queries
sent to third parties. A query leaving this system carries clinical
concepts, never a name, MRN, date of birth, or any other identifier. This
constraint also applies to any local-vs-hosted comparison run during
evaluation."

That last sentence is why this guard sits in the retrieval layer rather
than only on the graph's input contract: the bake-off
(`scripts/retrieval_bakeoff.py`) sends dev queries to a local model and,
when a key is configured, to Cohere. Those queries never pass through the
graph, so a guard that only lived on `EvidenceRetrieverInput` would not
have covered the one run that actually leaves the machine.

**What this is not.** It is not a PHI detector, and a text that passes is
not thereby certified free of identifiers. It catches the shapes a
clinical concept never needs — a calendar date, an SSN, a phone number, an
email address, an MRN-shaped run of digits — which is what "the whole
question got passed through as a concept" looks like in practice. The
structural defence is that `EvidenceRetrieverInput` has no patient field
to begin with; this is the second layer, not the first.

The two patterns the contract's `ClinicalConcept` already rejects (dates
and SSNs) are reproduced here with the same meaning, so that a concept
accepted at the graph boundary is never rejected here for a reason the
contract does not share. `ClinicalConcept` itself is imported from the
contract and re-exported, not redefined: two validators for one idea is
how they diverge.
"""

from __future__ import annotations

import re

from app.models.w2.workers import ClinicalConcept

#: Longest text a single clinical concept needs. Read off the contract's
#: own `ClinicalConcept.text` constraint rather than repeated as a number,
#: so the two cannot drift.
MAX_CONCEPT_CHARS: int = next(
    m.max_length
    for m in ClinicalConcept.model_fields["text"].metadata
    if getattr(m, "max_length", None) is not None
)

__all__ = [
    "MAX_CONCEPT_CHARS",
    "ClinicalConcept",
    "IdentifierInQueryError",
    "assert_concept_only",
    "build_query",
    "find_identifier_shapes",
]


class IdentifierInQueryError(ValueError):
    """
    Raised when a query carries something identifier-shaped.

    The message names the *rule* that fired, never the offending text: an
    exception that quotes the identifier it caught writes that identifier
    into the traceback, the log and the error response (CLAUDE.md
    invariant 1).
    """


#: (rule name, pattern). Order is the order they are reported in.
IDENTIFIER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Shared with the contract's ClinicalConcept:
    ("iso_date", re.compile(r"\d{4}-\d{2}-\d{2}")),
    ("slashed_date", re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    # Additional to the contract, and strictly narrowing:
    ("email", re.compile(r"[^\s@]+@[^\s@]+\.[A-Za-z]{2,}")),
    ("phone", re.compile(r"\b(?:\+?1[-. ])?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b")),
    ("long_digit_run", re.compile(r"\b\d{6,}\b")),
    ("mrn_label", re.compile(r"\b(?:mrn|medical\s+record\s+(?:number|no\.?))\b", re.IGNORECASE)),
    ("dob_label", re.compile(r"\b(?:dob|date\s+of\s+birth)\b", re.IGNORECASE)),
    ("named_patient", re.compile(r"\b(?:patient|pt\.?)\s+(?:name|is)\b", re.IGNORECASE)),
    (
        "month_name_date",
        re.compile(
            r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
            r"[a-z]*\s+\d{1,2},?\s+\d{4}\b",
            re.IGNORECASE,
        ),
    ),
)


def find_identifier_shapes(text: str) -> list[str]:
    """Names of every rule that fires on `text`. Never returns the match."""
    return [name for name, pattern in IDENTIFIER_PATTERNS if pattern.search(text)]


def assert_concept_only(text: str) -> None:
    """Raise `IdentifierInQueryError` if `text` is not concept-shaped."""
    if len(text) > MAX_CONCEPT_CHARS:
        raise IdentifierInQueryError(
            f"concept text is {len(text)} characters, over the {MAX_CONCEPT_CHARS} "
            "limit; a concept this long is a question or a note, and D5 forbids "
            "sending either"
        )
    hits = find_identifier_shapes(text)
    if hits:
        raise IdentifierInQueryError(
            f"identifier-shaped text in a retrieval query (rules: {', '.join(hits)}); "
            "D5 forbids a patient identifier in a query that leaves this system"
        )


def build_query(concepts: list[ClinicalConcept] | list[str]) -> str:
    """
    Join concepts into the one string the retrievers see.

    Every concept is re-checked here even though `ClinicalConcept` already
    validated it, because this function also accepts bare strings — which
    is what the bake-off harness and any future caller with a hand-written
    query will reach for, and exactly the path that would otherwise skip
    the guard.
    """
    if not concepts:
        raise ValueError("a retrieval query needs at least one concept")
    parts: list[str] = []
    for concept in concepts:
        text = concept if isinstance(concept, str) else concept.text
        assert_concept_only(text)
        parts.append(text)
    return " ".join(parts)
