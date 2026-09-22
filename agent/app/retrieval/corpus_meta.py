"""
The corpus source registry: who published each guideline, under what
licence, when, and when we took a copy.

The registry is `corpus/sources.yaml`; the text it describes is
`corpus/documents/<guideline_id>.md`. They are kept apart so that the
bibliography a citation is built from is reviewable on its own — a
reviewer checking "is this corpus actually openly licensed?" reads one
file, not twenty-eight.

**Only public-domain or expressly redistributable material belongs here.**
No ADA Standards of Care, no KDIGO, nothing paywalled — those are
copyrighted with no public licence and would make the corpus
undistributable regardless of how useful they are.

Two bases are in use, and the distinction is not cosmetic. Most sources are
works of the United States federal government, which are not subject to
copyright in the US (17 U.S.C. section 105). **USPSTF recommendation
statements are not.** They are published by JAMA (or, for the 2015 thyroid
statement, Annals of Internal Medicine) under arrangement with AHRQ and are
jointly copyrighted by the AMA and the US Government — the page says so, in
the words "All rights reserved". What makes them usable here is the express
permission that follows it: AHRQ "permits members of the public to
reproduce, redistribute, publicly display, and incorporate USPSTF work into
other materials provided that it is reproduced without any changes to the
work or portions thereof". Verbatim chunks with attribution are portions
reproduced without changes. Paraphrasing one is not, and neither is
presenting one as an AHRQ or HHS endorsement — see
`USPSTF_ENDORSEMENT_CONSTRAINT`.

`scripts/audit_corpus_licensing.py` is what found this; it re-fetches every
page and reads the raw HTML, because a rights notice lives in the chrome
that extraction strips.

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


#: The condition the USPSTF permission attaches, carried here because it is
#: a product requirement and not only a licensing footnote: AHRQ and HHS
#: "cannot endorse, or appear to endorse, derivative or excerpted
#: materials". A UI that renders a retrieved USPSTF excerpt must present it
#: as a quotation from the recommendation, never as AHRQ/HHS endorsing the
#: co-pilot's answer.
USPSTF_ENDORSEMENT_CONSTRAINT = (
    "Verbatim excerpts only, with attribution. AHRQ/HHS must not be shown as "
    "endorsing any answer built from them."
)


class LicenseBasis(StrEnum):
    """
    Why this document may be redistributed inside this repository.

    Two members, and adding a third is a licensing decision that should be
    visible in a diff and argued for in the session report.
    """

    #: 17 U.S.C. section 105 — a work of the US federal government, not
    #: subject to copyright in the United States.
    US_GOV_PUBLIC_DOMAIN = "us-gov-public-domain"

    #: Jointly copyrighted (AMA/Annals + US Government) and expressly
    #: licensed by AHRQ for reproduction and redistribution of the work "or
    #: portions thereof" without changes. Not public domain — see the module
    #: docstring and `USPSTF_ENDORSEMENT_CONSTRAINT`.
    USPSTF_AHRQ_REDISTRIBUTION_PERMITTED = "uspstf-ahrq-redistribution-permitted"


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
    def verbatim_only(self) -> bool:
        """
        True when the licence permits reproduction only without changes.

        A chunk from such a source may be quoted; it may not be paraphrased
        into an answer and presented as the source's words.
        """
        return self.license_basis is LicenseBasis.USPSTF_AHRQ_REDISTRIBUTION_PERMITTED

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
