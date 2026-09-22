"""
Section-aware chunking with stable chunk ids.

A chunk id is a hash of `(guideline_id, section path, chunk text)` and
nothing else — not the chunk's position in the document, not the build
time, not the number of chunks around it. That is the whole point: a
chunk id appears in an eval case and in a rendered citation
(`Citation.field_or_chunk_id`), so re-fetching a guideline that gained a
paragraph in section 9 must not renumber the chunk ids in section 2. It
also means a chunk that genuinely changed gets a new id rather than
quietly changing what an old id points at.

Headings are kept, twice over:

* `section_path` records the full heading trail, and becomes the
  citation's `page_or_section`.
* `indexed_text` prefixes the trail to the body for retrieval, because
  "Treatment" as a bare heading and a body that never repeats the drug
  class is a chunk that neither BM25 nor a dense model can find.

The chunk's `text` is the body alone, with no heading prefix, because it
is what gets quoted back to a clinician as `quote_or_value`. A quote that
silently includes a breadcrumb we assembled is not a quote.
"""

from __future__ import annotations

import hashlib
import re
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Chunks are sized for a 512-token embedding window (bge-small and the
#: MiniLM cross-encoder both truncate there). ~1400 characters of English
#: prose is ~330 tokens, which leaves room for the heading trail that
#: `indexed_text` prepends.
MAX_CHUNK_CHARS = 1400

#: A section shorter than this is packed together with the sections that
#: follow it — but only with its own SUBSECTIONS, never with a sibling.
#: Government health pages are full of two-sentence `####` sections, and
#: alone each is too short to retrieve on; folding one into its parent is
#: honest because the parent heading does cover it. Folding "Symptoms" into
#: "Complications" because they happen to be adjacent is not: the chunk's
#: `section_path` becomes the citation's `page_or_section`, so a merge
#: across a heading boundary points a clinician at a heading the quote did
#: not come from (CLAUDE.md invariant 2).
MIN_CHUNK_CHARS = 350

#: Hard floor. Anything shorter than this after packing is dropped — it is a
#: stray caption or a "Get email updates" widget, not evidence.
DROP_BELOW_CHARS = 60

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class GuidelineChunk(BaseModel):
    """
    One retrievable passage of a guideline. Bibliographic and clinical
    content only — there is no field here a patient fact could be put in,
    which is how CLAUDE.md invariant 3 is kept structurally rather than by
    convention.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str = Field(min_length=1)
    guideline_id: str = Field(min_length=1)
    section_path: list[str] = Field(
        min_length=1,
        description="Heading trail, document title first. Becomes page_or_section.",
    )
    text: str = Field(min_length=1, description="Body text only — no heading prefix.")
    char_start: int = Field(ge=0, description="Offset of this chunk's body in the source Markdown.")
    char_end: int = Field(gt=0)

    @model_validator(mode="after")
    def _span_is_sane(self) -> Self:
        if self.char_end <= self.char_start:
            raise ValueError(f"{self.chunk_id}: char_end must follow char_start")
        return self

    @property
    def section(self) -> str:
        """The heading trail below the document title, as one string."""
        trail = self.section_path[1:] or self.section_path
        return " > ".join(trail)

    @property
    def indexed_text(self) -> str:
        """What the retrievers index: heading trail, then body."""
        return f"{' > '.join(self.section_path)}\n{self.text}"


def compute_chunk_id(guideline_id: str, section_path: list[str], text: str) -> str:
    """
    The stable id. `\\x1f` (unit separator) joins the parts so that a
    section literally named "a > b" cannot collide with the two-level path
    ["a", "b"] — a separator that can occur in the data is not a separator.
    """
    payload = "\x1f".join([guideline_id, *section_path, text])
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{guideline_id}#{digest[:16]}"


class _Section:
    """A heading and the body directly beneath it."""

    __slots__ = ("path", "paragraphs", "start")

    def __init__(self, path: list[str], start: int) -> None:
        self.path = path
        self.paragraphs: list[tuple[str, int, int]] = []
        self.start = start


def _parse_sections(markdown: str, fallback_title: str) -> list[_Section]:
    """Split a document into sections, tracking heading depth and offsets."""
    sections: list[_Section] = []
    trail: list[tuple[int, str]] = []  # (level, heading)
    current: _Section | None = None
    offset = 0

    for line in markdown.splitlines(keepends=True):
        length = len(line)
        match = _HEADING.match(line.rstrip("\n"))
        if match:
            level = len(match.group(1))
            heading = match.group(2).strip()
            while trail and trail[-1][0] >= level:
                trail.pop()
            trail.append((level, heading))
            path = [h for _, h in trail]
            current = _Section(path=path, start=offset + length)
            sections.append(current)
        else:
            body = line.strip()
            if body:
                if current is None:
                    current = _Section(path=[fallback_title], start=offset)
                    sections.append(current)
                current.paragraphs.append((body, offset, offset + len(line.rstrip("\n"))))
        offset += length

    return sections


def _pack_paragraphs(
    paragraphs: list[tuple[str, int, int]],
) -> list[tuple[str, int, int]]:
    """
    Greedily pack paragraphs up to MAX_CHUNK_CHARS. A single paragraph over
    the limit is split on sentence boundaries; a single sentence over the
    limit is passed through whole, because cutting mid-sentence produces a
    quote no clinician should be shown.
    """
    packed: list[tuple[str, int, int]] = []
    buffer: list[str] = []
    start = end = 0

    def flush() -> None:
        nonlocal buffer, start, end
        if buffer:
            packed.append(("\n".join(buffer), start, end))
            buffer = []

    for text, para_start, para_end in paragraphs:
        pieces: list[tuple[str, int, int]]
        if len(text) > MAX_CHUNK_CHARS:
            pieces = []
            cursor = para_start
            for sentence in _SENTENCE_END.split(text):
                if sentence:
                    pieces.append((sentence, cursor, cursor + len(sentence)))
                    cursor += len(sentence) + 1
        else:
            pieces = [(text, para_start, para_end)]

        for piece, piece_start, piece_end in pieces:
            current_len = sum(len(b) + 1 for b in buffer)
            if buffer and current_len + len(piece) > MAX_CHUNK_CHARS:
                flush()
            if not buffer:
                start = piece_start
            buffer.append(piece)
            end = piece_end

    flush()
    return packed


def chunk_document(markdown: str, guideline_id: str, title: str) -> list[GuidelineChunk]:
    """
    Chunk one guideline document. Deterministic: same bytes in, same chunk
    ids out, on any machine and in any order.
    """
    sections = _parse_sections(markdown, fallback_title=title)

    # Pack a short section together with its own subsections, and stop at
    # the first heading that is not beneath it. The group's anchor path is
    # what the chunk is attributed to, so everything in the group has to sit
    # under that heading for the attribution to be true.
    groups: list[tuple[list[str], list[tuple[str, int, int]]]] = []
    anchor: list[str] | None = None
    pending: list[tuple[str, int, int]] = []

    def is_descendant(path: list[str], of: list[str]) -> bool:
        return len(path) > len(of) and path[: len(of)] == of

    for section in sections:
        if not section.paragraphs:
            continue
        full_enough = sum(len(p) for p, _, _ in pending) >= MIN_CHUNK_CHARS
        if anchor is not None and (full_enough or not is_descendant(section.path, anchor)):
            groups.append((anchor, pending))
            anchor, pending = None, []
        if anchor is None:
            anchor = section.path
        else:
            # A subsection folded into its parent keeps its own heading as a
            # line of the body, so the text still says where it came from.
            pending.append((section.path[-1], section.start, section.start))
        pending.extend(section.paragraphs)
    if anchor is not None and pending:
        groups.append((anchor, pending))

    chunks: list[GuidelineChunk] = []
    seen: set[str] = set()
    for path, paragraphs in groups:
        for text, start, end in _pack_paragraphs(paragraphs):
            if len(text) < DROP_BELOW_CHARS:
                continue
            chunk_id = compute_chunk_id(guideline_id, path, text)
            if chunk_id in seen:
                # Identical body under an identical heading trail: the same
                # chunk, not two. Emitting it twice would put two rows with
                # one id into the index.
                continue
            seen.add(chunk_id)
            chunks.append(
                GuidelineChunk(
                    chunk_id=chunk_id,
                    guideline_id=guideline_id,
                    section_path=path,
                    text=text,
                    char_start=start,
                    char_end=end,
                )
            )
    return chunks
