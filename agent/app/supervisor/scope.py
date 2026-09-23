"""
Answer scope — which lab rows a document answer itemises, decided by rule
before the model sees anything (D50).

**Why.** A question about a whole lab report handed every located value to
the drafting call and got every one of them back as a citation: 22 rows was
~33k tokens in and ~11.5k out, 88-92 s; 44 rows was ~49k in and ~25k out,
187-196 s (docs/w2/LOAD_TEST.md §7). The physician has ~90 seconds between rooms. The
time is the model writing citations, so the fix is fewer citations to write
— chosen here, in code, not by asking the model to be brief.

**The rule** (`SCOPE_RULE` is the same rule in the clinician's words):

1. **Critical** — beyond the critical-value table (critical.py), located or
   not. Always itemised, never counted against the cap: this must not hide
   a panic value any more than the lab cap may (D40 item 9). The critical
   block states them whatever the scope says; the scope only decides
   whether the model is also offered the row to cite in full.
2. **Abnormal** — the laboratory printed high, low, abnormal or a mark the
   co-pilot could not read; or the value is outside its printed range by the
   co-pilot's own arithmetic (`compute_abnormal_flag`); or the printed and
   computed flags disagree.
3. **Not compared** — the co-pilot cannot say the row is inside its range:
   no located value, no printed range, a censored value that straddles it,
   or a printed flag the locate step could not place. Not the same as
   normal, so never summarised as if it were (D20).
4. **Named** — the question names the test or its panel: Week 1's analyte
   rules (`ANALYTE_RULES`, by LOINC code or by the rule's wording applied to
   the printed name and panel), or the printed name itself.
5. **In range** — a located value inside its printed range, no printed
   high/low/abnormal flag, not named. *Counted, not itemised*, unless the
   question asks for every result. It is summarised as one statement that
   cites each row's located value and range, so the count is a cited claim
   (invariant 2), and it names the panels so the physician knows what to
   ask for.

**The cap.** At most `MAX_ITEMISED_ROWS` non-critical rows are offered to
the model, in the priority order above (abnormal, not compared, named, in
range when asked for). Rows beyond it are not dropped silently: they are
named in a cited statement of their own, abnormal ones first, and the
answer says the cap bound. `ANSWER_MAX_TOKENS` bounds the drafting call; if
it binds, the answer is rendered from what code states on its own (critical
values, this scope's statements, unsupported fields) and says so.

Both numbers are chosen from the measurement in LOAD_TEST.md §7, not
guessed: see D50.

Intake forms are not scoped: a form's catalog is a few medications and
allergies, and none of it is "in range".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from app.conversation.lab_codes import ANALYTE_RULES
from app.models.w2.citations import Citation
from app.models.w2.lab_pdf import LabPdfExtraction, LabResult
from app.models.w2.lab_values import AbnormalFlag, CrossCheckOutcome, ReportedFlag
from app.supervisor.critical import document_criticals
from app.supervisor.facts import DocumentFact
from app.workers.intake_extractor.report import ExtractionReport

#: Non-critical lab rows offered to the drafting call per turn. Measured:
#: see D50 and LOAD_TEST.md §7.
MAX_ITEMISED_ROWS = 8

#: The drafting call's output ceiling on the ask path (thinking included).
#: Measured: see D50 and LOAD_TEST.md §7 — ~131 output tokens/s, so this is
#: ~76 s of writing; the cap-bound worst case wrote ~5,100.
ANSWER_MAX_TOKENS = 10_000

SCOPE_RULE = (
    "Scope of this answer: a lab report result is listed individually when it is beyond "
    "the critical-value table, flagged high, low or abnormal by the laboratory, outside the "
    "reference range printed beside it, could not be compared with a range, or named in "
    f"the question — at most {MAX_ITEMISED_ROWS} besides critical values, which are always "
    "listed. Every other result is counted below, not listed."
)
OUTPUT_LIMIT_WORDING = (
    "The drafted answer reached its length limit and was not used. Only what the co-pilot "
    "states by rule is shown: critical values, the counts above, and unsupported fields."
)


class RowReason(StrEnum):
    """Why a lab row is, or is not, itemised. Priority order is list order."""

    CRITICAL = "critical"
    ABNORMAL = "abnormal"
    NOT_COMPARED = "not_compared"
    NAMED = "named"
    IN_RANGE = "in_range"


_PRIORITY = {reason: rank for rank, reason in enumerate(RowReason)}

_OUT_OF_RANGE = frozenset({AbnormalFlag.HIGH, AbnormalFlag.LOW, AbnormalFlag.ABNORMAL})
_PRINTED_NOT_NORMAL = frozenset(
    {ReportedFlag.HIGH, ReportedFlag.LOW, ReportedFlag.ABNORMAL, ReportedFlag.UNRECOGNISED}
)

#: "all the results", "every value", "the full report", "including normal".
_EVERY_RESULT = re.compile(
    r"\b(all|every|each|full|complete|entire)\b(\W+\w+){0,3}?\W+"
    r"(results?|values?|tests?|labs?|report|rows?|numbers?)\b"
    r"|\b(normal|in[- ]range)\s+(results?|values?|ones|tests?)\b"
    r"|\bincluding\s+(the\s+)?normal\b",
    re.IGNORECASE,
)

_PANEL_NOISE = frozenset({"panel", "continued", "with", "differential", "studies"})


def asks_for_every_result(question: str) -> bool:
    return _EVERY_RESULT.search(question) is not None


def _normalise(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())


def _name_aliases(printed: str) -> set[str]:
    """
    "BUN (Urea Nitrogen)" -> {"bun urea nitrogen", "bun", "urea nitrogen"};
    "Iron, Total" -> {"iron total", "iron"}. Short aliases are not kept:
    a two-letter word matches too much.
    """
    aliases = {_normalise(printed)}
    outside = re.sub(r"\([^)]*\)", " ", printed)
    aliases.add(_normalise(outside))
    aliases.add(_normalise(outside.split(",")[0]))
    aliases.update(_normalise(inner) for inner in re.findall(r"\(([^)]*)\)", printed))
    return {alias for alias in aliases if len(alias) >= 3}


def _mentions(question_words: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", question_words) is not None


def row_is_named(question: str, result: LabResult) -> bool:
    """The question names this row's test or its panel."""
    name_fact = result.test_name.as_presented_fact()
    printed = name_fact.value if name_fact is not None else result.test_key.replace("_", " ")
    panel = (result.panel or "").replace("_", " ")
    for _, pattern, codes in ANALYTE_RULES:
        if not pattern.search(question):
            continue
        # A coded row is named by its code, so "glucose" does not also name
        # "Est. Average Glucose"; an uncoded row, by the rule's wording.
        by_name = result.loinc_code is None and pattern.search(printed)
        if result.loinc_code in codes or by_name or pattern.search(panel):
            return True
    words = _normalise(question)
    if any(_mentions(words, alias) for alias in _name_aliases(printed)):
        return True
    panel_core = " ".join(w for w in _normalise(panel).split() if w not in _PANEL_NOISE)
    return len(panel_core) >= 3 and _mentions(words, panel_core)


def row_reason(result: LabResult, critical_keys: frozenset[str]) -> RowReason:
    """Rule 1-3 and 5 of the module docstring; naming is decided separately."""
    if result.test_key in critical_keys:
        return RowReason.CRITICAL
    printed = result.reported_flag.as_presented_fact()
    if printed is not None and printed.value in _PRINTED_NOT_NORMAL:
        return RowReason.ABNORMAL
    if printed is None and result.reported_flag.value in _PRINTED_NOT_NORMAL:
        # Read, but not placed on the page: not a fact to state (invariant
        # 4), and not something to summarise away as in range either.
        return RowReason.NOT_COMPARED
    cross_check = result.cross_check_flag()
    if cross_check is None:
        return RowReason.NOT_COMPARED
    if cross_check.computed in _OUT_OF_RANGE or cross_check.outcome is CrossCheckOutcome.DISAGREE:
        return RowReason.ABNORMAL
    if cross_check.computed is AbnormalFlag.NORMAL:
        return RowReason.IN_RANGE
    return RowReason.NOT_COMPARED


@dataclass(frozen=True)
class ScopedRow:
    document_id: str
    result: LabResult
    reason: RowReason
    named: bool
    #: This row's located facts, in catalog order.
    facts: tuple[DocumentFact, ...]

    @property
    def rank(self) -> int:
        """Lower is kept first under the cap. A named in-range row ranks as NAMED."""
        if self.reason is RowReason.IN_RANGE and self.named:
            return _PRIORITY[RowReason.NAMED]
        return _PRIORITY[self.reason]

    @property
    def panel(self) -> str:
        # "lipid_panel_continued" is the same panel carried over a page break.
        words = (self.result.panel or "ungrouped").split("_")
        return " ".join(w for w in words if w != "continued")

    @property
    def printed_name(self) -> str:
        name = self.result.test_name.as_presented_fact()
        return name.value if name is not None else self.result.test_key

    def citations(self, *parts: str) -> list[Citation]:
        return [fact.citation for fact in self.facts if fact.part in parts]


@dataclass(frozen=True)
class AnswerScope:
    #: The catalog the drafter is shown, by document.
    offered: dict[str, list[DocumentFact]]
    itemised: list[ScopedRow] = field(default_factory=list)
    #: In range, not named, not asked for: counted in one cited statement.
    summarised: list[ScopedRow] = field(default_factory=list)
    #: In scope, but beyond MAX_ITEMISED_ROWS: named in a statement of their own.
    capped: list[ScopedRow] = field(default_factory=list)
    every_result_asked: bool = False
    question_names_rows: bool = False

    @property
    def binds(self) -> bool:
        """Something on a report is not itemised: the answer must say so."""
        return bool(self.summarised or self.capped)

    def counts(self) -> dict[str, int]:
        """For the turn event. Counts only (invariant 1)."""
        counts = {f"itemised_{reason.value}": 0 for reason in RowReason}
        for row in self.itemised:
            counts[f"itemised_{row.reason.value}"] += 1
        counts["summarised"] = len(self.summarised)
        counts["capped"] = len(self.capped)
        counts["capped_abnormal"] = sum(
            1 for row in self.capped if row.reason is not RowReason.IN_RANGE
        )
        return counts


def _row_prefix(result: LabResult) -> str | None:
    for part in (result.test_name, result.value, result.unit, result.reference_range):
        if part.citation is not None:
            return part.citation.field_or_chunk_id.rpartition(".")[0]
    return None


def scope_answer(
    question: str,
    reports: dict[str, ExtractionReport],
    facts: dict[str, list[DocumentFact]],
) -> AnswerScope:
    """
    Decide, by rule, which lab rows the drafter is offered. Facts that are
    not lab rows (the specimen, every intake-form fact) are always offered.
    """
    every = asks_for_every_result(question)
    offered: dict[str, list[DocumentFact]] = {}
    candidates: list[ScopedRow] = []
    summarised: list[ScopedRow] = []
    names_rows = False
    for document_id, report in reports.items():
        payload = report.output.payload
        document_facts = facts.get(document_id, [])
        if not isinstance(payload, LabPdfExtraction):
            offered[document_id] = list(document_facts)
            continue
        critical_keys = frozenset(found.result.test_key for found in document_criticals(payload))
        by_prefix: dict[str, list[DocumentFact]] = {}
        for fact in document_facts:
            by_prefix.setdefault(fact.group_key[1], []).append(fact)
        row_prefixes: set[str] = set()
        for result in payload.results:
            prefix = _row_prefix(result)
            if prefix is None or prefix not in by_prefix:
                continue  # nothing located: disclosed as unsupported, not scoped
            row_prefixes.add(prefix)
            named = row_is_named(question, result)
            names_rows = names_rows or named
            row = ScopedRow(
                document_id=document_id,
                result=result,
                reason=row_reason(result, critical_keys),
                named=named,
                facts=tuple(by_prefix[prefix]),
            )
            if row.reason is RowReason.IN_RANGE and not (named or every):
                summarised.append(row)
            else:
                candidates.append(row)
        offered[document_id] = [f for f in document_facts if f.group_key[1] not in row_prefixes]

    critical = [row for row in candidates if row.reason is RowReason.CRITICAL]
    ranked = sorted(
        (row for row in candidates if row.reason is not RowReason.CRITICAL),
        key=lambda row: row.rank,  # stable: document order within a rank
    )
    kept, capped = ranked[:MAX_ITEMISED_ROWS], ranked[MAX_ITEMISED_ROWS:]
    keep = {id(row) for row in (*critical, *kept)}
    itemised = [row for row in candidates if id(row) in keep]  # document order
    for row in itemised:
        offered.setdefault(row.document_id, []).extend(row.facts)
    return AnswerScope(
        offered=offered,
        itemised=itemised,
        summarised=summarised,
        capped=capped,
        every_result_asked=every,
        question_names_rows=names_rows,
    )


def _panel_counts(rows: list[ScopedRow]) -> str:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.panel] = counts.get(row.panel, 0) + 1
    return ", ".join(f"{panel} {count}" for panel, count in counts.items())


def summary_statement(rows: list[ScopedRow]) -> tuple[str, list[Citation]] | None:
    """
    One statement for the in-range rows, citing each row's located value
    and printed range. Worded so a blank flag column is never "normal" (D20):
    the claim is the co-pilot's arithmetic against the printed range.
    """
    if not rows:
        return None
    citations = [c for row in rows for c in row.citations("result", "reference range")]
    if not citations:
        return None
    statement = (
        f"Not listed individually: {len(rows)} result(s) on the lab report "
        f"({_panel_counts(rows)}). Each has a value located on the page that is inside the "
        "reference range printed beside it, by the co-pilot's arithmetic (not a clinical "
        "judgment), and none carries a high, low or abnormal flag from the laboratory. Ask "
        "about a test or panel by name, or for all results, to see them."
    )
    return statement, citations


def capped_statement(rows: list[ScopedRow]) -> tuple[str, list[Citation]] | None:
    """
    Rows the cap left out, by printed name, out-of-range ones first. Names
    only — each is a located test name, cited — never a value the drafter
    did not cite and the verifier did not check.
    """
    if not rows:
        return None
    flagged = [row for row in rows if row.reason in (RowReason.ABNORMAL, RowReason.NOT_COMPARED)]
    rest = [row for row in rows if row not in flagged]
    parts = []
    if flagged:
        parts.append(
            "flagged, outside the printed range, or not comparable with it: "
            + "; ".join(row.printed_name for row in flagged)
        )
    if rest:
        # Semicolons: printed names carry commas ("Protein, Total").
        parts.append("also asked for: " + "; ".join(row.printed_name for row in rest))
    citations = [c for row in rows for c in row.citations("test")]
    if not citations:
        return None
    statement = (
        f"{len(rows)} more result(s) are in scope but past this answer's limit of "
        f"{MAX_ITEMISED_ROWS} and are not listed — " + "; ".join(parts) + ". Ask about them by "
        "name."
    )
    return statement, citations
