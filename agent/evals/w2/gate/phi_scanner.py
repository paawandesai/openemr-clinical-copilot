"""
The PHI canary scanner.

Two detectors over every captured log and trace field, walked to the leaves:

1. **Canaries.** Sentinel values seeded into one synthetic patient — an MRN,
   a name, a date of birth, a free-text finding. No legitimate log field
   needs any of them, so an exact match is a leak and not a judgement call.
2. **Patterns.** Identifier shapes that are PHI wherever they appear: SSN,
   telephone, email, calendar dates.

Two things this module deliberately does not do.

**It does not report what it found.** A hit carries the field path, the
detector's name and the length of the match — never the matched text.
A scanner that prints the PHI it caught into CI output has moved the leak
rather than found it (CLAUDE.md invariant 1 applies to the gate too).

**It is not an NER pipeline.** The literature's state of the art pairs regex
with a name/date model; this is regex plus exact canaries. That is why the
suite carries both a no-canary control arm (a quiet scanner proves nothing
unless it has been shown to be quiet on clean input) and a positive-control
arm (a blatant plant it must catch). A free-text clinical narrative with no
seeded canary in it would pass this scanner, and W2-J is honest about that
rather than claiming detection it does not have.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Identifier shapes. Kept tight on purpose: every pattern here is checked
#: against the no-canary control arm, and a pattern that fires there is a
#: false positive that would eventually get the whole scanner switched off.
PATTERNS: dict[str, re.Pattern[str]] = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "date_iso": re.compile(r"\b(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b"),
    "date_us": re.compile(r"\b(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01])/(?:19|20)\d{2}\b"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "phone": re.compile(r"\b\d{3}-\d{3,4}-\d{4}\b|\(\d{3}\)\s*\d{3}-\d{4}"),
}


@dataclass(frozen=True)
class Hit:
    """One detection. Carries no matched text — see the module docstring."""

    field_path: str
    detector: str
    name: str
    match_length: int

    def __str__(self) -> str:
        return f"{self.field_path} [{self.detector}:{self.name}, {self.match_length} chars]"


def _walk(node: Any, path: str) -> list[tuple[str, str]]:
    """Every string leaf in `node`, with its dotted path."""
    if isinstance(node, str):
        return [(path, node)]
    if isinstance(node, dict):
        found: list[tuple[str, str]] = []
        for key, value in node.items():
            found.extend(_walk(value, f"{path}.{key}" if path else str(key)))
        return found
    if isinstance(node, (list, tuple)):
        found = []
        for index, value in enumerate(node):
            found.extend(_walk(value, f"{path}[{index}]"))
        return found
    return []


def scan(
    captured: Any,
    canaries: dict[str, str] | None = None,
    *,
    patterns: dict[str, re.Pattern[str]] | None = None,
) -> list[Hit]:
    """
    Every canary and pattern hit in `captured`, in field order.

    `captured` is whatever a run recorded: log lines, trace fields, tool
    inputs and outputs, retrieval queries, error messages, ledger rows. The
    scanner does not care which — it walks to the leaves, which is the point,
    because the leak sites the literature ranks highest are the ones nobody
    listed.
    """
    canaries = canaries or {}
    patterns = PATTERNS if patterns is None else patterns

    hits: list[Hit] = []
    for field_path, text in _walk(captured, ""):
        lowered = text.lower()
        for name, canary in canaries.items():
            if not canary:
                continue
            if canary.lower() in lowered:
                hits.append(
                    Hit(
                        field_path=field_path,
                        detector="canary",
                        name=name,
                        match_length=len(canary),
                    )
                )
        for name, pattern in patterns.items():
            match = pattern.search(text)
            if match:
                hits.append(
                    Hit(
                        field_path=field_path,
                        detector="pattern",
                        name=name,
                        match_length=len(match.group(0)),
                    )
                )
    return hits
