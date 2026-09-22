"""
`AnswerView` — a `FinalAnswer` plus everything the source-grounded answer
view needs to render it, and nothing it does not.

A `FinalAnswer` (app/models/w2/answer.py) is complete as an accountability
record and incomplete as something to draw. Three things are missing, and
each of them is a way the view could otherwise lie:

**A document citation points at a page the view may not have.** `bbox.page`
is a page number inside `source_id`, and the view has to fetch those bytes
and know how big the page is before it can put a rectangle anywhere on it.
`ViewerDocument` carries the document's identity and its page geometry, and
this model refuses an answer whose document citations name a document it
does not carry — a chip with no page behind it is a chip that renders as
"click to see the source" and then cannot show one.

**A guideline citation carries its snippet but not its provenance.**
`Citation.quote_or_value` is the passage and `page_or_section` is the
section heading; the publisher, version and URL live on `GuidelineSource`
(app/models/w2/workers.py), which the retriever produced and the citation
has no room for (invariant 2 fixes the citation at five fields). Without
them a guideline chip shows a quotation with no attribution, which is the
half of invariant 3 that matters in the other direction: the reader has to
be able to see that this is *guidance*, from a named source, and not a fact
about the patient in front of them.

**A lab flag has to be shown as what was printed, not as what was meant.**
D20: a blank flag column reports nothing, and never means `normal`.
`FlagCrossCheck` already refuses to record agreement over a non-comparable
reported flag; `ReportedFlagView` is that check carried to the surface,
with the row it belongs to, so the view has a `ReportedFlag` to render
rather than an empty string to interpret. A view that received "" and chose
a label would be re-deciding D12 in the presentation layer.

**What this model deliberately does not carry.** No verification state:
`FinalAnswer` cannot assert its own correctness and neither can its view.
No prose beyond `FinalAnswer.answer`. No URL for the document bytes — how
the viewer fetches them is the caller's business (in OpenEMR it is a
server-side proxy that holds the clinician's token; in fixture mode it is a
file on disk), and putting a URL in the contract would invite one that the
browser could dereference on its own.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from app.models.w2.answer import FinalAnswer
from app.models.w2.base import W2Contract
from app.models.w2.citations import Citation
from app.models.w2.documents import SourceDocument
from app.models.w2.lab_values import FlagCrossCheck
from app.models.w2.source_types import SourceType
from app.models.w2.workers import EvidenceChunk

#: Bumped when the shape changes in a way a rendered page would notice. The
#: view checks it before drawing anything, so an agent and a chart embed at
#: different versions fail loudly instead of drawing boxes in the wrong
#: places.
ANSWER_VIEW_SCHEMA_VERSION = "w2-ui/answer-view/1.0.0"

#: Page rotations a PDF may declare, and the only ones the overlay maps.
#: PDF.js itself rejects anything else (`PageViewport`), so accepting one
#: here would only move the failure into the browser.
SUPPORTED_ROTATIONS = frozenset({0, 90, 180, 270})


class PageGeometry(W2Contract):
    """
    One page's size, as the overlay needs it.

    In PDF points, which is the unit `BBox` is recorded in, so that the
    scale factor between a recorded box and a rendered one is a single
    number the view can compute. `rotation` is the page's `/Rotate`: it
    changes where a box lands without changing the box.
    """

    page: int = Field(ge=1, description="1-based page number within the document.")
    width_pt: float = Field(gt=0, description="CropBox width in PDF points.")
    height_pt: float = Field(gt=0, description="CropBox height in PDF points.")
    rotation: int = Field(default=0, description="Page /Rotate: 0, 90, 180 or 270.")

    @model_validator(mode="after")
    def _rotation_is_a_quarter_turn(self) -> Self:
        if self.rotation not in SUPPORTED_ROTATIONS:
            raise ValueError(
                f"page rotation must be one of {sorted(SUPPORTED_ROTATIONS)}, got {self.rotation}"
            )
        return self


class ViewerDocument(W2Contract):
    """
    A document the view can open, with the geometry of every page in it.

    Every page is listed, not only the cited ones: a reader who follows a
    chip to page 2 of a six-page report can page through the rest, and a
    view that only knew about cited pages would have to guess at the others.
    """

    document: SourceDocument
    pages: list[PageGeometry] = Field(min_length=1)

    @model_validator(mode="after")
    def _pages_match_the_document(self) -> Self:
        numbers = [page.page for page in self.pages]
        if len(set(numbers)) != len(numbers):
            raise ValueError(f"document {self.document.document_id} lists a page twice")
        if sorted(numbers) != list(range(1, self.document.page_count + 1)):
            raise ValueError(
                f"document {self.document.document_id} declares page_count "
                f"{self.document.page_count} but carries geometry for pages {sorted(numbers)}"
            )
        return self

    def page(self, number: int) -> PageGeometry | None:
        return next((page for page in self.pages if page.page == number), None)


class ReportedFlagView(W2Contract):
    """
    One lab row's abnormal flag, in the shape D20 requires it be shown in.

    `cross_check` holds both halves — what the laboratory printed and what
    the value and reference range imply — because showing either alone is
    the failure mode D20 was written against. `reported` is a `ReportedFlag`
    and therefore always a value: `not_reported` for a blank column and
    `unrecognised` for a glyph the table does not know, never an empty
    string for the view to interpret.
    """

    test_name: str = Field(min_length=1, description="The test, as the document prints it.")
    panel: str | None = Field(default=None, description="The panel it sits under, if any.")
    cross_check: FlagCrossCheck
    citation: Citation | None = Field(
        default=None,
        description=(
            "The flag cell on the page, when the locate step placed it. The "
            "box is often zero-width — an empty flag column is real data "
            "(citations.py, BBox) — and the view draws it as a point."
        ),
    )

    @model_validator(mode="after")
    def _citation_is_a_document_citation(self) -> Self:
        if self.citation is not None and self.citation.source_type is not SourceType.DOCUMENT:
            raise ValueError("a printed flag is cited from the document that printed it")
        return self


class AnswerView(W2Contract):
    """
    One answer, with its sources attached. See the module docstring.

    The validators below are the whole point of the type: each one is a
    thing the view would otherwise have to do at render time, in JavaScript,
    on a page a clinician is already reading.
    """

    schema_version: Literal["w2-ui/answer-view/1.0.0"] = ANSWER_VIEW_SCHEMA_VERSION
    answer: FinalAnswer
    documents: list[ViewerDocument] = Field(default_factory=list)
    evidence_chunks: list[EvidenceChunk] = Field(
        default_factory=list,
        description=(
            "The retrieved chunks the guideline citations point at, by "
            "`chunk_id`. Carries the `GuidelineSource` a chip needs to name "
            "its publisher."
        ),
    )
    lab_flags: list[ReportedFlagView] = Field(default_factory=list)

    @model_validator(mode="after")
    def _every_citation_has_its_source(self) -> Self:
        documents = {viewer.document.document_id: viewer for viewer in self.documents}
        if len(documents) != len(self.documents):
            raise ValueError("two viewer documents share a document_id")
        chunks = {chunk.chunk_id for chunk in self.evidence_chunks}
        if len(chunks) != len(self.evidence_chunks):
            raise ValueError("two evidence chunks share a chunk_id")

        cited: list[Citation] = list(self.answer.all_citations())
        cited.extend(flag.citation for flag in self.lab_flags if flag.citation is not None)
        cited.extend(
            field.citation for field in self.answer.unsupported if field.citation is not None
        )

        for citation in cited:
            if citation.source_type is SourceType.GUIDELINE:
                if citation.field_or_chunk_id not in chunks:
                    raise ValueError(
                        f"guideline citation names chunk {citation.field_or_chunk_id!r}, "
                        "which this view does not carry; a chip cannot show a snippet "
                        "it has no chunk for"
                    )
                continue
            if citation.source_type is not SourceType.DOCUMENT:
                continue
            viewer = documents.get(citation.source_id)
            if viewer is None:
                raise ValueError(
                    f"document citation names document {citation.source_id!r}, which "
                    "this view does not carry; a chip cannot open a page it has no "
                    "document for"
                )
            if citation.bbox is not None and viewer.page(citation.bbox.page) is None:
                raise ValueError(
                    f"document {citation.source_id} has {viewer.document.page_count} "
                    f"page(s); a citation points at page {citation.bbox.page}"
                )
        return self

    def document(self, document_id: str) -> ViewerDocument | None:
        return next((v for v in self.documents if v.document.document_id == document_id), None)

    def chunk(self, chunk_id: str) -> EvidenceChunk | None:
        return next((c for c in self.evidence_chunks if c.chunk_id == chunk_id), None)
