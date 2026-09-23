"""
docs/w2/DECISIONS.md keeps its numbering: no D-number twice, no gaps.

The log is append-only, and on 2026-09-23 two merges lost ten entries by
resolving its conflict to one side: the eval-gate merge dropped D41, the
answer-scope merge D42-D49. Both left a gap in the numbering, and
the renumbering before that was needed because D23 and D35 had each been
taken twice. Either failure is a line of arithmetic away from being seen,
so this test does the arithmetic on every run.

What is allowed, because the log's own rules produce it:

* `## Dn (superseding) — ...` repeats an existing n. That is how the log
  recorded superseding D1 before it moved to fresh numbers, and entries are
  never edited, so it stays.
* `## D-unassigned ...` has no number yet. It is not a D-number, so it can
  be neither a duplicate nor a gap.

What it cannot see: an entry dropped from the *end* of the log leaves no
gap. Nothing automatic guards that case; a reviewer reading the merge does.

A `merge=union` rule for this file was tried and removed on 2026-09-23.
Redoing both lossy merges with it kept every entry (51 of 51, no conflict).
But union applies to cherry-pick and rebase as well as merge, and it
resolves every conflict silently: a cherry-pick of a follow-up commit
without the commit it followed reported no conflict and landed a paragraph
claiming a rename that had not happened. That trades a loud loss for a
silent, incoherent merge. The losses we actually had both left gaps, so
this test catches them, and a conflict keeps stopping the person merging.

Numbers reserved ahead of merge (the header's reservation rule) must merge
in order, or `main` shows a gap until the lower range lands. That is
deliberate: a gap on `main` is indistinguishable from a lost entry.

    cd agent && python -m pytest tests/test_decisions_log.py
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

DECISIONS = Path(__file__).resolve().parents[2] / "docs" / "w2" / "DECISIONS.md"

_HEADING = re.compile(r"^## (D\S*)(?: \((superseding)\))?", re.MULTILINE)


def numbering_problems(text: str) -> list[str]:
    """Every duplicate and gap in the log's D-numbers, as sentences. Empty is good."""
    numbers: list[int] = []
    for label, superseding in _HEADING.findall(text):
        match = re.fullmatch(r"D(\d+)", label)
        if match is None or superseding:
            continue  # D-unassigned, or the log's own "Dn (superseding)" form
        numbers.append(int(match.group(1)))

    problems = [
        f"D{n} is used {count} times" for n, count in sorted(Counter(numbers).items()) if count > 1
    ]
    if numbers:
        missing = sorted(set(range(1, max(numbers) + 1)) - set(numbers))
        problems += [f"D{n} is missing (the log has D1-D{max(numbers)})" for n in missing]
    return problems


def test_the_committed_log_has_no_duplicate_and_no_gap() -> None:
    text = DECISIONS.read_text(encoding="utf-8")
    assert _HEADING.search(text), "no decision headings found; did the file move?"
    assert numbering_problems(text) == []


def entry(label: str, title: str = "Something decided") -> str:
    return f"## {label} — {title}\n\n**Status:** DECIDED\n\n---\n\n"


def log(*labels: str) -> str:
    return "# Week 2 — Decisions\n\n" + "".join(entry(label) for label in labels)


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        (("D1", "D2", "D3"), []),
        (("D1", "D1 (superseding)", "D2"), []),
        (("D1", "D2", "D-unassigned (work order 17)", "D3"), []),
        (("D1", "D2", "D2"), ["D2 is used 2 times"]),
        (("D1", "D3"), ["D2 is missing (the log has D1-D3)"]),
        (("D2", "D3"), ["D1 is missing (the log has D1-D3)"]),
    ],
)
def test_numbering_rules(labels: tuple[str, ...], expected: list[str]) -> None:
    assert numbering_problems(log(*labels)) == expected


def test_the_eval_gate_merge_loss_would_have_failed() -> None:
    """0b4ae04 dropped D41 from between D40 and D42."""
    labels = tuple(f"D{n}" for n in range(1, 51) if n != 41)
    assert numbering_problems(log(*labels)) == ["D41 is missing (the log has D1-D50)"]


def test_the_answer_scope_merge_loss_would_have_failed() -> None:
    """ea198c8 kept D1-D40 and D50 and dropped everything between."""
    labels = (*(f"D{n}" for n in range(1, 41)), "D50")
    assert numbering_problems(log(*labels)) == [
        f"D{n} is missing (the log has D1-D50)" for n in range(41, 50)
    ]


def test_the_old_double_d23_would_have_failed() -> None:
    labels = (*(f"D{n}" for n in range(1, 25)), "D23")
    assert numbering_problems(log(*labels)) == ["D23 is used 2 times"]
