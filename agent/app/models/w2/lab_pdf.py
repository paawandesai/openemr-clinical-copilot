"""
The `lab_pdf` extraction schema — what a lab report becomes after D3.

Shape follows the doc-factory's `field_path` vocabulary
(`agent/evals/w2/factory/groundtruth.py`) wherever the factory has a path,
so ground truth and extractor output line up without a translation layer
for most of the schema. `app/models/w2/factory_mapping.py` holds the
mapping table and the six-sample conversion proof; the places where this
schema deliberately says more than the factory does are listed there too.

Everything a clinician could read is an `ExtractedField`, so every one of
them can be `unlocated`, `missing` or `illegible` and none of them can be
read as a fact without going through `as_presented_fact()` (invariant 4).

**Each result is cross-checked against itself.** A lab report prints both
the number and a flag. The vision model reads both (D3 stage 2); code
recomputes the flag from the number and the range
(`app/models/w2/lab_values.py`); `LabResult.cross_check_flag()` compares
them. A disagreement means one of the two misread the page, and this
contract's position is that neither side wins — it is a verification
failure, disclosed like any other (ARCHITECTURE.md §5).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from app.models.w2.base import W2Contract
from app.models.w2.citations import Citation
from app.models.w2.documents import DocumentKind, DocumentPatientHeader, SourceDocument
from app.models.w2.extraction import ExtractedField, PresentedFact
from app.models.w2.lab_values import (
    AbnormalFlag,
    FlagCrossCheck,
    LabValue,
    ReferenceRange,
    cross_check_abnormal_flag,
)

#: Bumped when a field changes meaning, not when one is added. Pinned as a
#: Literal so a file written by an older extractor fails validation instead
#: of being read under the new meanings.
LAB_PDF_SCHEMA_VERSION = "w2-contracts/lab_pdf/1.0.0"


class PerformingLab(W2Contract):
    """The laboratory letterhead — factory paths `lab.*`."""

    name: ExtractedField[str]
    address: ExtractedField[str] = Field(default_factory=lambda: ExtractedField[str].missing())
    clia: ExtractedField[str] = Field(
        default_factory=lambda: ExtractedField[str].missing(),
        description="CLIA certificate number, as printed.",
    )
    director: ExtractedField[str] = Field(default_factory=lambda: ExtractedField[str].missing())


class LabOrderHeader(W2Contract):
    """
    The order block — factory paths `order.*`.

    Timestamps are `ExtractedField[datetime]` and parsed by the extractor,
    not by this schema: a date that could not be parsed is `unlocated`, not
    a silently shifted one. `LabResult.collection_date` carries the date the
    idempotency key uses, at day resolution, because that is what D1's
    UNIQUE key is over.
    """

    accession: ExtractedField[str] = Field(default_factory=lambda: ExtractedField[str].missing())
    specimen: ExtractedField[str] = Field(default_factory=lambda: ExtractedField[str].missing())
    collected_at: ExtractedField[datetime] = Field(
        default_factory=lambda: ExtractedField[datetime].missing()
    )
    received_at: ExtractedField[datetime] = Field(
        default_factory=lambda: ExtractedField[datetime].missing()
    )
    reported_at: ExtractedField[datetime] = Field(
        default_factory=lambda: ExtractedField[datetime].missing()
    )
    ordering_provider: ExtractedField[str] = Field(
        default_factory=lambda: ExtractedField[str].missing()
    )


class LabResult(W2Contract):
    """
    One row of a lab report.

    `test_key` and `panel` come from where the row sits on the page (the
    factory encodes both in its field paths, `results.<panel>.<test>.*`) and
    are the schema's stable handle on the row. `test_name` is what was
    printed, which is a different thing and can itself be illegible.

    `loinc_code` is optional and is *not* extracted from the page: lab PDFs
    rarely print one, and the write-back path needs it
    (`procedure_order_code.procedure_code`, spike §9). Whatever assigns it
    is a coding step outside this contract; a result without one simply
    cannot be written back, which `LabWriteBackResult` enforces by requiring
    it.
    """

    panel: str | None = Field(
        default=None,
        description="Panel the row sits under, e.g. `cmp`, `lipid`, `a1c`. None when ungrouped.",
    )
    test_key: str = Field(
        min_length=1,
        description="Stable handle for the row within this document, e.g. `glucose`.",
    )
    test_name: ExtractedField[str] = Field(description="The test name as printed.")
    value: ExtractedField[LabValue]
    unit: ExtractedField[str] = Field(default_factory=lambda: ExtractedField[str].missing())
    reference_range: ExtractedField[ReferenceRange] = Field(
        default_factory=lambda: ExtractedField[ReferenceRange].missing()
    )
    collection_date: ExtractedField[date] = Field(
        default_factory=lambda: ExtractedField[date].missing(),
        description=(
            "The date this specimen was collected — the third component of "
            "D1's idempotency key. Usually copied from the order header; "
            "on reports that date rows individually, from the row."
        ),
    )
    reported_flag: ExtractedField[AbnormalFlag] = Field(
        default_factory=lambda: ExtractedField[AbnormalFlag].missing(),
        description=(
            "The flag the document printed, parsed by "
            "`lab_values.parse_reported_flag`. Never the computed flag — "
            "keeping them apart is the whole point of the cross-check."
        ),
    )
    loinc_code: str | None = Field(
        default=None,
        description="Assigned, not extracted. See the class docstring.",
    )

    @property
    def citation(self) -> Citation | None:
        """
        The citation a clinical claim about this row cites: the one on the
        *value*. The other fields carry their own citations because they sit
        in different places on the page, but a claim is about the number.
        """
        return self.value.citation

    def presented_value(self) -> PresentedFact[LabValue] | None:
        """The value as a fact, or None if the locate step could not place it."""
        return self.value.as_presented_fact()

    def cross_check_flag(self) -> FlagCrossCheck | None:
        """
        Compare the printed flag with the one the value and range imply.

        None when there is no value to compute from — an `unlocated`,
        `missing` or `illegible` value is already disclosed as unsupported,
        and running a safety check on a number nobody could find would
        manufacture a second, confusing failure for one underlying fault.
        """
        value_fact = self.value.as_presented_fact()
        if value_fact is None:
            return None
        range_fact = self.reference_range.as_presented_fact()
        return cross_check_abnormal_flag(
            self.reported_flag.value,
            value_fact.value,
            range_fact.value if range_fact else None,
        )


class LabPdfExtraction(W2Contract):
    """
    A whole lab report, extracted.

    `results` may be empty. An extraction that found no rows is a real
    outcome — a scan too degraded to read is exactly the `scanned_heavy`
    case the doc-factory generates — and the contract represents it as zero
    presentable facts rather than refusing to exist, so the "still
    processing / nothing found" states stay distinguishable (D7).
    """

    schema_version: Literal[LAB_PDF_SCHEMA_VERSION] = LAB_PDF_SCHEMA_VERSION
    extraction_id: str = Field(
        description=(
            "The run that produced this. Every citation below repeats it, "
            "so a value carries its provenance even after it is separated "
            "from the extraction it came from (D1)."
        )
    )
    document: SourceDocument
    patient_header: DocumentPatientHeader
    performing_lab: PerformingLab | None = None
    order: LabOrderHeader = Field(default_factory=LabOrderHeader)
    results: list[LabResult] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.document.kind is not DocumentKind.LAB_PDF:
            raise ValueError(
                f"a lab_pdf extraction describes a {DocumentKind.LAB_PDF.value} "
                f"document, not {self.document.kind.value}"
            )
        keys = [result.test_key for result in self.results]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise ValueError(
                f"test_key is the handle for a row and must be unique; repeated: {duplicates}"
            )
        for citation in self._citations():
            if citation.extraction_id != self.extraction_id:
                raise ValueError(
                    f"citation extraction_id {citation.extraction_id!r} does not "
                    f"belong to this extraction ({self.extraction_id!r})"
                )
            if citation.bbox is not None and citation.bbox.page > self.document.page_count:
                raise ValueError(
                    f"citation points at page {citation.bbox.page} of a "
                    f"{self.document.page_count}-page document"
                )
        return self

    def _citations(self) -> list[Citation]:
        found: list[Citation] = []
        for field in (
            *self.patient_header.__dict__.values(),
            *(self.performing_lab.__dict__.values() if self.performing_lab else ()),
            *self.order.__dict__.values(),
        ):
            if isinstance(field, ExtractedField) and field.citation is not None:
                found.append(field.citation)
        for result in self.results:
            for field in result.__dict__.values():
                if isinstance(field, ExtractedField) and field.citation is not None:
                    found.append(field.citation)
        return found

    def presentable_results(self) -> list[LabResult]:
        """The rows whose value the locate step actually placed on a page."""
        return [result for result in self.results if result.value.is_presentable]

    def flag_verification_failures(self) -> list[tuple[LabResult, FlagCrossCheck]]:
        """
        Every row where the printed flag and the computed flag disagree.
        A non-empty list is a verification failure for the extraction, to be
        disclosed — not repaired by preferring one side.
        """
        failures: list[tuple[LabResult, FlagCrossCheck]] = []
        for result in self.results:
            check = result.cross_check_flag()
            if check is not None and check.is_verification_failure:
                failures.append((result, check))
        return failures
