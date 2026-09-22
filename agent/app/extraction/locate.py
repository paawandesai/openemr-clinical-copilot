"""
Stage 3 of D3: put an extracted value back on the page, or refuse to.

D3: "Deterministic locate step — code matches each extracted value back to
the word boxes from stage 1. A value that cannot be located on its source
page is flagged `unsupported`." And: "**The locate step is the verifier,
and it is the only source of `bbox`.**"

So this module answers one question — *where on this page is this text, if
anywhere* — and it answers it in three ways, never two:

* `located`, with one box, the words that matched and how they matched;
* `not_located`, with nothing;
* `ambiguous`, with **every** candidate box.

There is no fourth answer and no tie-break-and-hope. Returning the first
of several matches would mean a lab value of `5` could be boxed over an
unrelated `5` elsewhere on the page and rendered as fact, which is exactly
the failure D3's locate step exists to catch. `ambiguous` is a real,
useful answer: `locate_in_row` resolves most of them by asking a narrower
question, and what survives is disclosed rather than guessed.

**Evidence tiers.** Matching runs in order of how strong the evidence is,
and stops at the first tier that finds anything:

1. `exact` — the same characters, allowing for case, whitespace, unit
   spelling and hyphenation across a line break (`normalize`).
2. `numeric` — the same number and the same unit, written differently:
   `5`, `5.0` and `5.00` are one value.
3. `number_only` — the number is on the page but its unit is not beside
   it. This is the ordinary shape of a lab table, where a flag column sits
   between the result and its units, and the box it yields is the number
   alone. It fires only when what follows the number really is a unit
   (`normalize.is_unit`), so `70 - 99`, `2026-08-30` and `22D2118740` get
   no fallback to their leading number. Weaker evidence, and it says so.
4. `fuzzy` — a near match, for OCR. **Off by default on a text layer**: a
   text layer reports characters exactly, so a value that is not there
   character-for-character is not there, and allowing near matches on a
   digital PDF buys nothing but false locates. It is off *entirely* — on
   any page, under any `FuzzyMode` — for a value that is a number, a unit,
   a date or a lab flag; see `EXACT_ONLY_VALUE_CLASSES`.

A stricter tier that finds exactly one match therefore beats a looser tier
that finds several, which is where most real ambiguity goes away.

Every result says which tier produced it (`tier`, as `exact` | `numeric` |
`fuzzy`) and how well it matched (`score`, 1.0 for everything but a fuzzy
match). An extractor is expected to carry a fuzzy locate at lower
confidence than an exact one rather than treat the two alike.

**This module never logs and never raises on a miss.** A miss is a return
value, not an exception; and the reasons it returns name statuses and
counts, never the text being searched for (invariant 1 — no chart content
in logs, traces or error messages). `Candidate.matched_text` does carry
page text, because that is the value the citation contract quotes; it is
returned to the caller, never written anywhere.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
from enum import StrEnum

from app.extraction.geometry import BBox, union_all
from app.extraction.normalize import (
    ValueClass,
    classify_value,
    is_unit,
    split_number_and_unit,
    squash,
)
from app.extraction.words import PageWords, TextSource, Word

#: Punctuation a match may leave unconsumed at the edge of the first or
#: last word it covers, so that the value `Everett` is found in the drawn
#: word `Everett,`. Deliberately excludes `-`, `/` and `%`: those carry
#: meaning inside a value, and letting a match stop short of them would
#: find `70` inside `70-99` and `5` inside `5/10`.
_EDGE_PUNCTUATION = frozenset(".,;:!?()[]{}\"'*")

#: Longest run of words a numeric or fuzzy match may cover. Exact matching
#: is not bounded by this — it runs over the whole page stream, so a value
#: wrapped across several lines is still found.
MAX_SPAN_WORDS = 12

#: A fuzzy candidate must be at least this similar to the value. Chosen
#: against the factory's scanned samples: below about 0.80 the false-locate
#: rate on the decoy probes climbs, and above about 0.90 the heavy-scan
#: located rate falls off without buying any precision.
DEFAULT_FUZZY_THRESHOLD = 0.84

#: Two candidates in the same tier whose scores differ by no more than
#: this are both reported: the answer is `ambiguous`, not the better one.
#: Exact, numeric and number-only matches all score exactly 1.0, so any two
#: of them are always ambiguous; only fuzzy matching can separate a winner.
AMBIGUITY_MARGIN = 0.05

#: Words starting fractionally left of the label's right edge are still to
#: its right; a tenth of a point absorbs the rounding in a word box.
_ROW_X_EPSILON = 0.1


class LocateStatus(StrEnum):
    LOCATED = "located"
    NOT_LOCATED = "not_located"
    AMBIGUOUS = "ambiguous"


class MatchKind(StrEnum):
    EXACT = "exact"
    NUMERIC = "numeric"
    NUMBER_ONLY = "number_only"
    FUZZY = "fuzzy"


#: Tier order, strongest evidence first.
_TIER_ORDER = {
    MatchKind.EXACT: 0,
    MatchKind.NUMERIC: 1,
    MatchKind.NUMBER_ONLY: 2,
    MatchKind.FUZZY: 3,
}

#: The coarse three-value vocabulary an extractor records against a value:
#: `number_only` is a numeric match that found the number without its unit,
#: so it reports as numeric evidence rather than as a fourth kind.
_COARSE_TIER = {
    MatchKind.EXACT: "exact",
    MatchKind.NUMERIC: "numeric",
    MatchKind.NUMBER_ONLY: "numeric",
    MatchKind.FUZZY: "fuzzy",
}

#: Value classes the fuzzy tier is never used on, whatever `FuzzyMode` says.
#:
#: A near match is a claim that two strings are the same despite a
#: difference. For a name or a drug that is usually an OCR slip worth
#: forgiving. For a number, a unit, a date or a flag the difference *is*
#: the value: `3.1` and `8.1` differ by one character and are a normal and
#: a critical potassium; `mg/dL` and `mg/mL` differ by one character and
#: are a thousandfold apart. There is no version of "close enough" that is
#: safe there, so those four classes are matched by the exact and numeric
#: tiers or not at all.
#:
#: This is a policy, not a default: passing `FuzzyMode.ALWAYS` does not
#: lift it. A caller that wants a near match on a number wants a different
#: function.
EXACT_ONLY_VALUE_CLASSES = frozenset(
    {ValueClass.NUMERIC, ValueClass.UNIT, ValueClass.DATE, ValueClass.FLAG}
)


class FuzzyMode(StrEnum):
    """When near-matching is allowed."""

    #: On for OCR pages, off for text-layer pages. See the module docstring.
    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"


@dataclass(frozen=True, slots=True)
class Candidate:
    """One place on the page the value could be."""

    bbox: BBox
    #: One box per visual row the match covers, top to bottom. A
    #: single-row match has exactly one, equal to `bbox`. This is the
    #: doc-factory's `line_bboxes`.
    line_bboxes: tuple[BBox, ...]
    #: The words as they are drawn, joined by single spaces — what a
    #: citation quotes.
    matched_text: str
    kind: MatchKind
    #: Match quality within `kind`, 0-1. Comparable between two candidates
    #: of the same kind; not across kinds, which the tier order ranks.
    score: float
    #: Indices into `PageWords.words`, ascending and contiguous.
    word_indices: tuple[int, ...]
    #: Mean reader confidence over the matched words — 1.0 on a text layer.
    mean_confidence: float

    @property
    def page(self) -> int:
        return self.bbox.page


@dataclass(frozen=True, slots=True)
class LocateResult:
    """
    What the locate step decided. `status` is the whole answer; `best` is
    populated only when the status is `located`.
    """

    status: LocateStatus
    candidates: tuple[Candidate, ...]
    #: Why, in terms of statuses and counts only — never the value searched
    #: for, and never page content (invariant 1).
    reason: str
    #: Which tier produced `candidates`, or None when nothing matched.
    kind: MatchKind | None = None
    #: How the value was classified, and so whether the fuzzy tier was
    #: available at all (`EXACT_ONLY_VALUE_CLASSES`).
    value_class: ValueClass = ValueClass.FREE_TEXT

    @property
    def located(self) -> bool:
        return self.status is LocateStatus.LOCATED

    @property
    def tier(self) -> str | None:
        """
        The evidence tier, as `exact` | `numeric` | `fuzzy`, or None when
        nothing matched. This and `score` are what an extractor records
        beside the value: a `fuzzy` locate is a weaker claim than an
        `exact` one and should be carried at lower confidence.
        """
        return None if self.kind is None else _COARSE_TIER[self.kind]

    @property
    def score(self) -> float | None:
        """
        The match quality of the single match, or None unless the status is
        `located`. 1.0 for every exact and numeric match; for a fuzzy match
        it is the similarity, and never reaches 1.0.
        """
        best = self.best
        return None if best is None else best.score

    @property
    def best(self) -> Candidate | None:
        """The single match, or None unless the status is `located`."""
        if self.status is not LocateStatus.LOCATED:
            return None
        return self.candidates[0]

    @property
    def bbox(self) -> BBox | None:
        """
        The box, or None. There is no box for `ambiguous`: that is the
        point — D3 makes the locate step the only source of a bbox, so it
        has to be able to decline to produce one.
        """
        best = self.best
        return best.bbox if best is not None else None


@dataclass(frozen=True, slots=True)
class RowLocateResult:
    """
    `locate_in_row`'s answer: the value's result, plus where the label that
    anchored it was found.
    """

    value: LocateResult
    #: Every place the label was found. Empty when the label itself was not
    #: on the page, which is reported as `not_located` on `value` too.
    label_candidates: tuple[Candidate, ...]
    #: Visual rows searched, 0-based down the page.
    rows_searched: tuple[int, ...]

    @property
    def status(self) -> LocateStatus:
        return self.value.status

    @property
    def located(self) -> bool:
        return self.value.located

    @property
    def bbox(self) -> BBox | None:
        return self.value.bbox

    @property
    def kind(self) -> MatchKind | None:
        return self.value.kind

    @property
    def tier(self) -> str | None:
        return self.value.tier

    @property
    def score(self) -> float | None:
        return self.value.score


def _soft_hyphen_words(words: Sequence[Word], squashed: Sequence[str]) -> set[int]:
    """
    Indices of words whose trailing `-` is a line-break hyphen.

    A hyphen only breaks a word when the next word is on the next row: the
    hyphen in `70-99` or in `Okonjo-Reyes` is part of the text and stays.
    """
    soft: set[int] = set()
    for index, text in enumerate(squashed):
        if len(text) < 2 or not text.endswith("-"):
            continue
        following = index + 1
        if following < len(words) and words[following].row > words[index].row:
            soft.add(index)
    return soft


def _build_stream(
    squashed: Sequence[str], drop_trailing_hyphen: set[int]
) -> tuple[str, tuple[tuple[int, int], ...]]:
    """Concatenate the page into one string, recording each word's slice."""
    pieces: list[str] = []
    bounds: list[tuple[int, int]] = []
    offset = 0
    for index, text in enumerate(squashed):
        piece = text[:-1] if index in drop_trailing_hyphen else text
        pieces.append(piece)
        bounds.append((offset, offset + len(piece)))
        offset += len(piece)
    return "".join(pieces), tuple(bounds)


class PageIndex:
    """
    A page prepared for searching: normalised text, character offsets, and
    the within-row spans the numeric and fuzzy tiers scan.

    Building this costs roughly what one search costs, and everything in it
    is independent of the value being looked for — so a caller running many
    values against one page (which is every real caller, and the
    measurement harness) should build it once and pass it in.
    """

    __slots__ = (
        "_spans_by_length",
        "_spans_by_number",
        "origin",
        "row_spans",
        "source",
        "span_parsed",
        "squashed",
        "streams",
        "words",
    )

    def __init__(
        self,
        words: Sequence[Word],
        *,
        source: TextSource = TextSource.TEXT_LAYER,
        origin: Sequence[int] | None = None,
    ) -> None:
        self.words: tuple[Word, ...] = tuple(words)
        self.source = source
        self.origin: tuple[int, ...] = (
            tuple(origin) if origin is not None else tuple(range(len(self.words)))
        )
        if len(self.origin) != len(self.words):
            raise ValueError("origin must have one index per word")

        self.squashed: tuple[str, ...] = tuple(squash(word.text) for word in self.words)
        plain = _build_stream(self.squashed, set())
        dehyphenated = _build_stream(self.squashed, _soft_hyphen_words(self.words, self.squashed))
        #: The plain stream, and the de-hyphenated one when it differs.
        self.streams: tuple[tuple[str, tuple[tuple[int, int], ...]], ...] = (
            (plain,) if dehyphenated[0] == plain[0] else (plain, dehyphenated)
        )

        self.row_spans = _build_row_spans(self.words, self.squashed)
        self.span_parsed: tuple[tuple[Decimal, str] | None, ...] = tuple(
            split_number_and_unit(text) for _, _, text in self.row_spans
        )
        by_number: dict[Decimal, list[int]] = {}
        by_length: dict[int, list[int]] = {}
        for index, (_, _, text) in enumerate(self.row_spans):
            parsed = self.span_parsed[index]
            if parsed is not None:
                by_number.setdefault(parsed[0], []).append(index)
            by_length.setdefault(len(text), []).append(index)
        self._spans_by_number = {key: tuple(value) for key, value in by_number.items()}
        self._spans_by_length = {key: tuple(value) for key, value in by_length.items()}

    @classmethod
    def for_page(cls, page: PageWords) -> PageIndex:
        return cls(page.words, source=page.source)

    def restrict(self, indices: Iterable[int]) -> PageIndex:
        """A view over a subset of this page's words, keeping their origins."""
        kept = sorted(set(indices))
        return PageIndex(
            [self.words[index] for index in kept],
            source=self.source,
            origin=[self.origin[index] for index in kept],
        )

    def spans_with_number(self, number: Decimal) -> tuple[int, ...]:
        return self._spans_by_number.get(number, ())

    def spans_of_length(self, low: int, high: int) -> list[int]:
        found: list[int] = []
        for length in range(low, high + 1):
            found.extend(self._spans_by_length.get(length, ()))
        return found


def _build_row_spans(
    words: Sequence[Word], squashed: Sequence[str]
) -> tuple[tuple[int, int, str], ...]:
    """
    Every run of up to `MAX_SPAN_WORDS` consecutive words within one row,
    as `(start, stop, squashed_text)` with `stop` exclusive.
    """
    spans: list[tuple[int, int, str]] = []
    total = len(words)
    for start in range(total):
        text = ""
        for stop in range(start + 1, min(start + MAX_SPAN_WORDS, total) + 1):
            if words[stop - 1].row != words[start].row:
                break
            text += squashed[stop - 1]
            if text:
                spans.append((start, stop, text))
    return tuple(spans)


def _edge_ok(word_text: str, start: int, stop: int) -> bool:
    """Whatever the match left unconsumed at a word's edge is punctuation."""
    head, tail = word_text[:start], word_text[stop:]
    return all(character in _EDGE_PUNCTUATION for character in head + tail)


def _span_from_stream(
    stream: str,
    bounds: Sequence[tuple[int, int]],
    start: int,
    stop: int,
) -> tuple[int, int] | None:
    """
    Turn a `[start, stop)` slice of a stream into a word span, or None if
    the slice does not begin and end at a word edge — ignoring punctuation
    at those two edges, and nothing else.
    """
    first = last = None
    for word_index, (word_start, word_stop) in enumerate(bounds):
        if word_stop == word_start or word_stop <= start or word_start >= stop:
            continue
        if first is None:
            first = word_index
        last = word_index
    if first is None or last is None:
        return None

    head_start, head_stop = bounds[first]
    head_text = stream[head_start:head_stop]
    if not _edge_ok(head_text, start - head_start, len(head_text)):
        return None
    tail_start, tail_stop = bounds[last]
    tail_text = stream[tail_start:tail_stop]
    if not _edge_ok(tail_text, 0, stop - tail_start):
        return None
    return first, last + 1


def _make_candidate(
    index: PageIndex, start: int, stop: int, kind: MatchKind, score: float
) -> Candidate:
    words = index.words[start:stop]
    by_row: dict[int, list[BBox]] = {}
    for word in words:
        by_row.setdefault(word.row, []).append(word.bbox)
    return Candidate(
        bbox=union_all(word.bbox for word in words),
        line_bboxes=tuple(union_all(by_row[row]) for row in sorted(by_row)),
        matched_text=" ".join(word.text for word in words),
        kind=kind,
        score=score,
        word_indices=tuple(index.origin[start:stop]),
        mean_confidence=sum(word.confidence for word in words) / len(words),
    )


def _exact_candidates(index: PageIndex, needle: str) -> list[Candidate]:
    seen: set[tuple[int, int]] = set()
    found: list[Candidate] = []
    for stream, bounds in index.streams:
        position = stream.find(needle)
        while position != -1:
            span = _span_from_stream(stream, bounds, position, position + len(needle))
            if span is not None and span not in seen:
                seen.add(span)
                found.append(_make_candidate(index, span[0], span[1], MatchKind.EXACT, 1.0))
            position = stream.find(needle, position + 1)
    return found


def _numeric_candidates(index: PageIndex, value: str) -> tuple[list[Candidate], list[Candidate]]:
    """
    Number-and-unit matches and number-only matches, in that order.

    Both are restricted to a single visual row: a number and the unit
    written beside it are on one row, and letting a numeric match wrap
    would pair a result with the next row's units.
    """
    parsed = split_number_and_unit(value)
    if parsed is None:
        return [], []
    number, unit = parsed
    # The number-only fallback fires only for a real unit; see `is_unit`.
    fallback_allowed = is_unit(unit)

    with_unit: list[Candidate] = []
    number_only: list[Candidate] = []
    for span_index in index.spans_with_number(number):
        span_number, span_unit = index.span_parsed[span_index]
        if span_number != number:  # pragma: no cover - the bucket key is the number
            continue
        start, stop, _ = index.row_spans[span_index]
        if span_unit == unit:
            with_unit.append(_make_candidate(index, start, stop, MatchKind.NUMERIC, 1.0))
        elif fallback_allowed and not span_unit:
            number_only.append(_make_candidate(index, start, stop, MatchKind.NUMBER_ONLY, 1.0))
    return with_unit, number_only


def _fuzzy_candidates(index: PageIndex, needle: str, threshold: float) -> list[Candidate]:
    low = max(1, int(len(needle) * 0.6))
    high = max(low, int(len(needle) * 1.6) + 1)
    matcher = SequenceMatcher(autojunk=False)
    matcher.set_seq2(needle)

    scored: list[tuple[float, int, int]] = []
    for span_index in index.spans_of_length(low, high):
        start, stop, text = index.row_spans[span_index]
        matcher.set_seq1(text)
        if matcher.real_quick_ratio() < threshold or matcher.quick_ratio() < threshold:
            continue
        ratio = matcher.ratio()
        if ratio >= threshold:
            scored.append((ratio, start, stop))

    # Overlapping spans around the same ink are one candidate, not several:
    # keep the best-scoring span of each overlapping group, so a near match
    # does not report itself ambiguous with its own neighbours.
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    kept: list[tuple[float, int, int]] = []
    for ratio, start, stop in scored:
        if any(start < other_stop and other_start < stop for _, other_start, other_stop in kept):
            continue
        kept.append((ratio, start, stop))
    return [
        _make_candidate(index, start, stop, MatchKind.FUZZY, ratio) for ratio, start, stop in kept
    ]


def _decide(
    candidates: Sequence[Candidate],
    kind: MatchKind,
    value_class: ValueClass = ValueClass.FREE_TEXT,
) -> LocateResult:
    ranked = sorted(candidates, key=lambda candidate: (-candidate.score, candidate.word_indices))
    if len(ranked) == 1:
        return LocateResult(
            LocateStatus.LOCATED, (ranked[0],), f"single {kind.value} match", kind, value_class
        )
    if ranked[0].score - ranked[1].score > AMBIGUITY_MARGIN:
        return LocateResult(
            LocateStatus.LOCATED,
            tuple(ranked),
            f"{len(ranked)} {kind.value} matches, one clearly best",
            kind,
            value_class,
        )
    return LocateResult(
        LocateStatus.AMBIGUOUS,
        tuple(ranked),
        f"{len(ranked)} {kind.value} matches, none clearly best",
        kind,
        value_class,
    )


def _not_located(reason: str, value_class: ValueClass = ValueClass.FREE_TEXT) -> LocateResult:
    return LocateResult(LocateStatus.NOT_LOCATED, (), reason, None, value_class)


def locate(
    value: str,
    page: PageWords | PageIndex,
    *,
    value_class: ValueClass | None = None,
    fuzzy: FuzzyMode = FuzzyMode.AUTO,
    fuzzy_threshold: float = DEFAULT_FUZZY_THRESHOLD,
    allow_number_only: bool = True,
) -> LocateResult:
    """
    Find `value` on one page.

    Returns `located` with exactly one box, `ambiguous` with every box it
    could be, or `not_located`. It never picks one of several equally good
    matches — see the module docstring. The result carries `tier` and
    `score` so a caller can hold a fuzzy match at lower confidence than an
    exact one.

    `value` is the text an extractor says is on the page. Pass what the
    document *draws*: where a document deliberately re-expresses a chart
    value — a patient writing a drug's common name, a letterhead set in
    caps — the drawn text is what is on the page and the chart's wording is
    not (the doc-factory records this as `rendering: derived`).

    `value_class` declares what kind of value this is, which decides
    whether the fuzzy tier is available at all
    (`EXACT_ONLY_VALUE_CLASSES`). Leave it out and it is inferred from the
    string's shape; **declare it whenever the caller knows**, because
    inference sees only the characters. A model that misreads `mg/dL` as
    `md/gL` hands over something still recognisably unit-shaped, but one
    that misreads `ratio` as `rtaio` does not, and an inferred
    classification would let that be near-matched back onto a units column.
    """
    index = page if isinstance(page, PageIndex) else PageIndex.for_page(page)
    resolved_class = classify_value(value) if value_class is None else value_class
    needle = squash(value)
    if not needle:
        return _not_located("value is empty once normalised; nothing to find", resolved_class)
    if not index.words:
        return _not_located("page has no words", resolved_class)

    exact = _exact_candidates(index, needle)
    if exact:
        return _decide(exact, MatchKind.EXACT, resolved_class)

    with_unit, number_only = _numeric_candidates(index, value)
    if with_unit:
        return _decide(with_unit, MatchKind.NUMERIC, resolved_class)
    if number_only and allow_number_only:
        return _decide(number_only, MatchKind.NUMBER_ONLY, resolved_class)

    exact_only = resolved_class in EXACT_ONLY_VALUE_CLASSES
    use_fuzzy = not exact_only and (
        fuzzy is FuzzyMode.ALWAYS or (fuzzy is FuzzyMode.AUTO and index.source is TextSource.OCR)
    )
    if use_fuzzy:
        near = _fuzzy_candidates(index, needle, fuzzy_threshold)
        if near:
            return _decide(near, MatchKind.FUZZY, resolved_class)

    if exact_only:
        return _not_located(
            f"no exact or numeric match; the fuzzy tier is not used on a "
            f"{resolved_class.value} value",
            resolved_class,
        )
    return _not_located("no span of this page's words matches the value", resolved_class)


def locate_in_row(
    label: str,
    value: str,
    page: PageWords | PageIndex,
    *,
    value_class: ValueClass | None = None,
    after_label: bool = True,
    fuzzy: FuzzyMode = FuzzyMode.AUTO,
    fuzzy_threshold: float = DEFAULT_FUZZY_THRESHOLD,
    allow_number_only: bool = True,
) -> RowLocateResult:
    """
    Find `value` on the same visual row as `label` — the lab-table case.

    A lab report is a grid, and a result means nothing without the test
    name beside it. Searching the whole page for `5` finds the `5` in a
    reference interval, in an accession number and in a date; searching the
    row that says `Potassium` finds the potassium result. This is what
    turns most `ambiguous` answers into `located` ones without ever
    tie-breaking: the question asked is narrower, so the honest answer is
    narrower too.

    `label` may itself appear more than once — the same analyte can be
    ordered on two panels. Every row it heads is searched, and if the value
    is found on more than one of them the answer is still `ambiguous`.

    `after_label` keeps candidates at or right of the label's right edge,
    which is how every table in these documents is laid out, and stops the
    label from matching inside its own words. Set it False for a layout
    that puts the value first.

    `value_class` applies to the **value**, not the label. A row label is a
    test name, a drug or an allergen — free text, which the fuzzy tier may
    still near-match, and on a degraded scan that is often the only way the
    row is found at all. The value it anchors keeps whatever policy its own
    class carries.
    """
    index = page if isinstance(page, PageIndex) else PageIndex.for_page(page)
    resolved_class = classify_value(value) if value_class is None else value_class
    if not index.words:
        return RowLocateResult(_not_located("page has no words", resolved_class), (), ())

    label_result = locate(
        label, index, fuzzy=fuzzy, fuzzy_threshold=fuzzy_threshold, allow_number_only=False
    )
    label_candidates = label_result.candidates
    if not label_candidates:
        return RowLocateResult(
            _not_located("the row label is not on this page", resolved_class), (), ()
        )

    position = {origin: offset for offset, origin in enumerate(index.origin)}
    candidates: list[Candidate] = []
    kinds: list[MatchKind] = []
    rows_searched: set[int] = set()
    seen: set[tuple[int, ...]] = set()

    for anchor in label_candidates:
        anchor_offsets = {position[origin] for origin in anchor.word_indices}
        rows = {index.words[offset].row for offset in anchor_offsets}
        rows_searched |= rows
        row_word_offsets = [
            offset
            for offset, word in enumerate(index.words)
            if word.row in rows
            and offset not in anchor_offsets
            and not (after_label and word.bbox.x0 < anchor.bbox.x1 - _ROW_X_EPSILON)
        ]
        if not row_word_offsets:
            continue
        result = locate(
            value,
            index.restrict(row_word_offsets),
            value_class=resolved_class,
            fuzzy=fuzzy,
            fuzzy_threshold=fuzzy_threshold,
            allow_number_only=allow_number_only,
        )
        if result.kind is None:
            continue
        for candidate in result.candidates:
            if candidate.word_indices in seen:
                continue
            seen.add(candidate.word_indices)
            candidates.append(candidate)
            kinds.append(result.kind)

    rows = tuple(sorted(rows_searched))
    if not candidates:
        return RowLocateResult(
            _not_located("the value is not on any row headed by this label", resolved_class),
            label_candidates,
            rows,
        )

    # Candidates found under two different occurrences of the label can
    # come from different tiers. Report the strongest tier present; a
    # weaker one counts towards nothing, not even ambiguity, because
    # stronger evidence for one row is exactly what settles it.
    strongest = min(kinds, key=_TIER_ORDER.__getitem__)
    in_tier = [
        candidate for candidate, kind in zip(candidates, kinds, strict=True) if kind is strongest
    ]
    return RowLocateResult(_decide(in_tier, strongest, resolved_class), label_candidates, rows)
