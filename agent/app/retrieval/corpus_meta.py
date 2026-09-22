"""
The corpus source registry: who published each guideline, under what
licence, when, and when we took a copy.

The registry is `corpus/sources.yaml`; the text it describes is
`corpus/documents/<guideline_id>.md`. They are kept apart so that the
bibliography a citation is built from is reviewable on its own — a
reviewer checking "is this corpus actually openly licensed?" reads one
file, not twenty-eight.

**Only public-domain or openly licensed material belongs here.** Every
source is a work of the United States federal government, which is not
subject to copyright in the US (17 U.S.C. section 105). No ADA Standards of
Care, no KDIGO, nothing paywalled — those are copyrighted and would make
the corpus undistributable regardless of how useful they are.

`as_of` in the registry is what "dated" is measured against, rather than
today's date, so that a build run tomorrow produces byte-identical output
to a build run today. `SourceRecord.is_dated()` takes the reference date
as an argument for callers that want the live answer instead.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

CORPUS_DIR = Path(__file__).resolve().parent / "corpus"
DOCUMENTS_DIR = CORPUS_DIR / "documents"
SOURCES_PATH = CORPUS_DIR / "sources.yaml"

#: A source whose most recent publication or review is older than this is
#: flagged `dated`. Ten years is the work order's threshold; it is stored in
#: the registry too, so the committed corpus says what it was built against.
DEFAULT_DATED_AFTER_YEARS = 10


class LicenseBasis(StrEnum):
    """
    Why this document may be redistributed inside this repository.

    One member today, and that is the point: adding a second one is a
    licensing decision that should be visible in a diff.
    """

    US_GOV_PUBLIC_DOMAIN = "us-gov-public-domain"


class SourceRecord(BaseModel):
    """One guideline document's bibliography entry. No patient data."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    guideline_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    url: str = Field(min_length=1, pattern=r"^https://")
    section_urls: list[str] = Field(
        default_factory=list,
        description=(
            "Further pages that are part of the same document. NHLBI splits a "
            "health topic across tabbed sub-pages (symptoms, diagnosis, "
            "treatment, living-with); each is a URL but not a separate "
            "guideline, so they are fetched in order and concatenated into one "
            "document under the canonical `url`."
        ),
    )
    license_basis: LicenseBasis
    license_note: str = Field(min_length=1)
    published: date | None = Field(
        default=None, description="First publication date, where the publisher states one."
    )
    last_reviewed: date | None = Field(
        default=None,
        description=(
            "Most recent review/update the publisher states. This is what "
            "`effective_date` prefers, because a 2008 page reviewed in 2025 "
            "is current guidance, not a seventeen-year-old document."
        ),
    )
    retrieved: date = Field(description="When this repository took its copy.")
    topics: list[str] = Field(min_length=1)

    @property
    def all_urls(self) -> list[str]:
        """Canonical URL first, then any continuation pages, in order."""
        return [self.url, *self.section_urls]

    @model_validator(mode="after")
    def _has_a_date(self) -> Self:
        if self.published is None and self.last_reviewed is None:
            raise ValueError(
                f"{self.guideline_id}: a source with neither a publication nor a "
                "review date cannot be age-checked, and an un-age-checkable "
                "guideline is not citable"
            )
        return self

    @property
    def effective_date(self) -> date:
        """The date the source's currency is judged on."""
        candidates = [d for d in (self.last_reviewed, self.published) if d is not None]
        return max(candidates)

    def is_dated(self, as_of: date, years: int = DEFAULT_DATED_AFTER_YEARS) -> bool:
        """True when this source is older than `years` at `as_of`."""
        cutoff = date(as_of.year - years, as_of.month, as_of.day)
        return self.effective_date < cutoff

    @property
    def document_path(self) -> Path:
        return DOCUMENTS_DIR / f"{self.guideline_id}.md"


class CorpusRegistry(BaseModel):
    """`sources.yaml` in full."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    as_of: date = Field(
        description="Reference date for the `dated` flag; fixed so builds are reproducible."
    )
    dated_after_years: int = Field(default=DEFAULT_DATED_AFTER_YEARS, ge=1)
    sources: list[SourceRecord] = Field(min_length=1)

    @model_validator(mode="after")
    def _ids_unique(self) -> Self:
        seen: set[str] = set()
        for source in self.sources:
            if source.guideline_id in seen:
                raise ValueError(f"duplicate guideline_id: {source.guideline_id}")
            seen.add(source.guideline_id)
        return self

    def dated_ids(self) -> list[str]:
        """Sources the age check flags, in registry order."""
        return [
            s.guideline_id for s in self.sources if s.is_dated(self.as_of, self.dated_after_years)
        ]

    def by_id(self, guideline_id: str) -> SourceRecord:
        for source in self.sources:
            if source.guideline_id == guideline_id:
                return source
        raise KeyError(guideline_id)


def load_registry(path: Path | None = None) -> CorpusRegistry:
    """Parse `sources.yaml`. Raises if the registry does not validate."""
    raw = yaml.safe_load((path or SOURCES_PATH).read_text(encoding="utf-8"))
    return CorpusRegistry.model_validate(raw)


def load_sources(path: Path | None = None) -> list[SourceRecord]:
    return load_registry(path).sources
