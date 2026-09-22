"""
The Week 2 citation contract — CLAUDE.md invariant 2, extending Week 1.

Invariant 2: "Every clinical claim carries the citation contract:
{source_type, source_id, page_or_section, field_or_chunk_id,
quote_or_value} - plus bbox when the source is a document."

Week 1 had no `Citation` type. What it had was `Claim`
(app/models/claims.py), which binds a stated value to `source_id` + `field`
+ `resource_type` — the same five facts under different names, for the one
source type Week 1 could read. `Citation` is that shape generalised over
three source types, and `citation_from_claim` / `claim_from_citation` below
are the lossless bridge in both directions. tests/test_w2_citations.py
round-trips every Week 1 `Claim` value shape through it, including the
`value=None` case that findings.md F-57 requires (a chart record with no
usable name).

Three things this type is deliberately strict about:

**Invariant 3 — chart and guideline never stand in for each other.** The
three `SourceType` members are disjoint and every consumer switches on
them. `PatientFactClaim` and `GuidelineClaim` (app/models/w2/answer.py)
each accept one side and reject the other, so an answer cannot cite a
guideline as if it were this patient's chart.

**`bbox` belongs to documents alone.** D3 makes the deterministic locate
step "the only source of `bbox`" — the model never supplies one. A chart or
guideline citation carrying a bbox would mean a coordinate arrived from
somewhere that has no pages, so it is rejected outright rather than
ignored.

**A document citation names its extraction.** D1 routes provenance through
`procedure_result.comments`, which carries an extraction id only; the
ledger maps that id to document, page and bbox. A document citation without
an extraction id could not be traced back to the run that produced it, so
`extraction_id` is required for `source_type=document` and forbidden for
the other two.

Where "a document citation for a located value requires bbox" is enforced:
`ExtractedField` (app/models/w2/extraction.py), not here. Locatedness is a
property of the extracted field, not of the citation — the same `Citation`
shape describes the ink region of an illegible field, which has a bbox and
is emphatically not a located value. What this module offers instead is
`Citation.located_document()`, a constructor that cannot be called without
a bbox, so the requirement is structural at every call site that means it.
"""

from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from app.models.api import ClaimOutcome
from app.models.claims import Claim
from app.models.fhir_resources import FhirResourceType
from app.models.w2.base import EXTRACTION_ID_PATTERN, W2Contract
from app.models.w2.source_types import SourceType

#: The set of `page_or_section` values a chart citation may carry. A chart
#: has no pages; its "section" is which part of the record the fact came
#: from, and that is exactly Week 1's five tool-scope resource types
#: (ARCHITECTURE.md §3). Constraining it to this set is what makes the
#: Week 1 round-trip lossless: `Claim.resource_type` survives the trip
#: through `Citation` without a sixth field.
CHART_SECTIONS = frozenset(member.value for member in FhirResourceType)


class BBox(W2Contract):
    """
    A rectangle on a page, in PDF points.

    Coordinate convention, matching the doc-factory's recorded convention
    exactly (`agent/evals/w2/factory/groundtruth.py` COORDINATE_CONVENTION)
    so that ground truth and extractor output are directly comparable:
    origin top-left, y increasing downward — PyMuPDF page space, on the
    original clean layout rather than on a degraded scan.

    `x1 >= x0` and `y1 >= y0`, not `>`: a zero-width box is real data. The
    factory emits one for every unflagged lab result (an empty flag column
    renders as `[306.0, 256.398, 306.0, 268.24]`), and rejecting it would
    make the contract unable to describe a normal lab row.
    """

    page: int = Field(ge=1, description="1-based page number within the source document.")
    x0: float = Field(ge=0, description="Left edge, PDF points from the page's left edge.")
    y0: float = Field(ge=0, description="Top edge, PDF points from the page's top edge (y down).")
    x1: float = Field(ge=0, description="Right edge; >= x0.")
    y1: float = Field(ge=0, description="Bottom edge; >= y0.")

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.x1 < self.x0:
            raise ValueError(f"bbox x1 ({self.x1}) is left of x0 ({self.x0})")
        if self.y1 < self.y0:
            raise ValueError(f"bbox y1 ({self.y1}) is above y0 ({self.y0})")
        return self

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


class Citation(W2Contract):
    """
    One citation. CLAUDE.md invariant 2's five fields, plus the two the
    document case needs. See the module docstring for the rules.
    """

    source_type: SourceType = Field(
        description=(
            "Which kind of source this points at. Invariant 3: chart facts "
            "and guideline evidence are distinct kinds and never substitute "
            "for one another."
        )
    )

    source_id: str = Field(
        min_length=1,
        description=(
            "chart: the FHIR resource id, exactly Week 1's Claim.source_id. "
            "document: the stored document's id. guideline: the guideline "
            "document's id."
        ),
    )

    page_or_section: str | None = Field(
        default=None,
        description=(
            "chart: the tool-scope resource type this fact came from, one "
            "of CHART_SECTIONS — a chart has no pages, so its 'section' is "
            "which part of the record it is. document: the page, as a "
            "string; when it parses as an integer it must equal bbox.page. "
            "guideline: the section heading the chunk sits under. Required "
            "for document and guideline."
        ),
    )

    field_or_chunk_id: str = Field(
        min_length=1,
        description=(
            "chart: the field name on the source resource (Week 1's "
            "Claim.field). document: the extracted field's path, in the "
            "doc-factory's field_path vocabulary. guideline: the retrieved "
            "chunk's id."
        ),
    )

    quote_or_value: str | bool | int | float | None = Field(
        default=None,
        description=(
            "What the source holds. For a chart citation this is Week 1's "
            "Claim.value and may be None — findings.md F-57: a record with "
            "no usable name is cited as nameless and renders UNVERIFIED, "
            "never under a placeholder. For a document or guideline "
            "citation it is what is actually on the page, and may not be "
            "None: a citation that quotes nothing cites nothing."
        ),
    )

    bbox: BBox | None = Field(
        default=None,
        description=(
            "Where on the page the quoted text sits. Document sources only "
            "— D3 makes the deterministic locate step the only source of a "
            "bbox, and neither a chart nor a guideline has pixels to point "
            "at. Present on an illegible field too, where it marks the ink "
            "region; a bbox alone does not make a value located (see "
            "ExtractedField)."
        ),
    )

    extraction_id: str | None = Field(
        default=None,
        pattern=EXTRACTION_ID_PATTERN,
        description=(
            "Which extraction run produced this value. Required for "
            "document sources and forbidden for the others. D1 puts this "
            "id, alone, into procedure_result.comments; the write-back "
            "ledger maps it to document id, page and bbox."
        ),
    )

    @model_validator(mode="after")
    def _source_type_rules(self) -> Self:
        if self.source_type is SourceType.DOCUMENT:
            if self.extraction_id is None:
                raise ValueError(
                    "document citation requires extraction_id: without it the "
                    "value cannot be traced back to the extraction run (D1)"
                )
            if self.page_or_section is None:
                raise ValueError("document citation requires page_or_section")
            if self.quote_or_value is None:
                raise ValueError("document citation requires quote_or_value")
            if self.bbox is not None and self.page_or_section.strip().isdigit():
                declared = int(self.page_or_section.strip())
                if declared != self.bbox.page:
                    raise ValueError(
                        f"page_or_section says page {declared} but bbox is on page {self.bbox.page}"
                    )
            return self

        # chart and guideline: no pixels, no extraction run.
        if self.bbox is not None:
            raise ValueError(
                f"bbox is document-only; a {self.source_type.value} citation has "
                "no page to point at (D3: the locate step is the only source of a bbox)"
            )
        if self.extraction_id is not None:
            raise ValueError(
                f"extraction_id is document-only; a {self.source_type.value} "
                "citation is not produced by a document extraction"
            )

        if self.source_type is SourceType.GUIDELINE:
            if self.page_or_section is None:
                raise ValueError("guideline citation requires page_or_section (the section)")
            if self.quote_or_value is None:
                raise ValueError("guideline citation requires quote_or_value")
            return self

        # chart
        if self.page_or_section is None:
            raise ValueError(
                "chart citation requires page_or_section: the tool-scope "
                "resource type the fact came from"
            )
        if self.page_or_section not in CHART_SECTIONS:
            raise ValueError(
                f"chart citation page_or_section must be one of "
                f"{sorted(CHART_SECTIONS)}, got {self.page_or_section!r}"
            )
        return self

    @property
    def is_located_document(self) -> bool:
        """
        True only for a document citation that carries a bbox — i.e. one the
        deterministic locate step actually placed on a page. This is the
        predicate `PresentedFact` and `ExtractedField` gate on.
        """
        return self.source_type is SourceType.DOCUMENT and self.bbox is not None

    @classmethod
    def located_document(
        cls,
        *,
        source_id: str,
        field_path: str,
        quote: str | bool | int | float,
        bbox: BBox,
        extraction_id: str,
    ) -> Citation:
        """
        A document citation the locate step placed on a page. `bbox` has no
        default and no None: a caller that means "located" cannot express
        "located, nowhere".
        """
        return cls(
            source_type=SourceType.DOCUMENT,
            source_id=source_id,
            page_or_section=str(bbox.page),
            field_or_chunk_id=field_path,
            quote_or_value=quote,
            bbox=bbox,
            extraction_id=extraction_id,
        )

    @classmethod
    def unlocated_document(
        cls,
        *,
        source_id: str,
        field_path: str,
        quote: str | bool | int | float,
        page: int,
        extraction_id: str,
    ) -> Citation:
        """
        A document citation for a value the locate step could not place —
        the model read it, code could not find it. Structurally incapable of
        carrying a bbox, so it can never be mistaken for a located value.
        """
        return cls(
            source_type=SourceType.DOCUMENT,
            source_id=source_id,
            page_or_section=str(page),
            field_or_chunk_id=field_path,
            quote_or_value=quote,
            extraction_id=extraction_id,
        )


def citation_from_claim(claim: Claim) -> Citation:
    """
    Week 1 `Claim` -> Week 2 `Citation`, losslessly.

    `resource_type` lands in `page_or_section` because that is honestly what
    it is — which part of the chart the fact came from — and because keeping
    it out of a sixth field is what lets invariant 2's five-field contract
    describe a Week 1 claim unchanged. `claim_from_citation` is the exact
    inverse; tests/test_w2_citations.py round-trips both ways.
    """
    return Citation(
        source_type=SourceType.CHART,
        source_id=claim.source_id,
        page_or_section=claim.resource_type.value,
        field_or_chunk_id=claim.field,
        quote_or_value=claim.value,
    )


def claim_from_citation(citation: Citation) -> Claim:
    """
    Week 2 `Citation` -> Week 1 `Claim`. Chart citations only: a document or
    guideline citation has no Week 1 equivalent, and inventing a
    `resource_type` for it would be exactly the invariant-3 confusion this
    contract exists to prevent.
    """
    if citation.source_type is not SourceType.CHART:
        raise ValueError(
            f"only a chart citation maps back to a Week 1 Claim; "
            f"{citation.source_type.value} has no Claim equivalent (invariant 3)"
        )
    if isinstance(citation.quote_or_value, bool):
        # Week 1's Claim.value is str | float | int | None and has no bool
        # member. bool is an int subclass in Python, so this would pass
        # validation and silently become 0/1 rather than failing here.
        raise ValueError("Week 1 Claim.value has no boolean form; this citation is not a Claim")
    return Claim(
        source_id=citation.source_id,
        field=citation.field_or_chunk_id,
        value=citation.quote_or_value,
        resource_type=FhirResourceType(citation.page_or_section),
    )


def citation_from_claim_outcome(outcome: ClaimOutcome) -> Citation:
    """
    Week 1's rendered `ClaimOutcome` -> `Citation`, one way only.

    A `ClaimOutcome` is a claim plus its verification state (`verified`,
    `unverified_reason`). The citation half maps exactly; the verification
    half deliberately does not, because a citation says where a value came
    from, not whether anyone confirmed it. Round-tripping it would mean a
    citation could assert its own verification, which is the property
    ARCHITECTURE.md §5 keeps in a separate step on purpose.
    """
    return Citation(
        source_type=SourceType.CHART,
        source_id=outcome.source_id,
        page_or_section=outcome.resource_type.value,
        field_or_chunk_id=outcome.field,
        quote_or_value=outcome.value,
    )
