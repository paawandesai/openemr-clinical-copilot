"""
Turning an extraction into a write-back request — and saying what it left out.

The interesting part of this module is the refusals. An extraction has
rows that must not reach a chart, and each one is refused here for a
stated reason rather than being written and disclosed afterwards:

* **A value the locate step could not place** is `unsupported` and is
  never rendered as fact (CLAUDE.md invariant 4). A value nobody could
  find on the page has no business being written into the chart as a
  result, so `presentable_results()` is the only source of rows.
* **A row with no LOINC code.** `procedure_result.result_code` and
  `result_text` must both be populated or OpenEMR drops the code and
  substitutes a null flavor (spike §9). Coding is a step outside the
  extraction contract (`LabResult.loinc_code` is assigned, never read off
  the page), so an uncoded row is simply not writable yet.
* **A row with no collection date.** It is the third component of D1's
  idempotency key; without it there is no key, and without a key there is
  no idempotency.
* **A row whose flag cannot be computed** — `indeterminate`. There is no
  `procedure_result.abnormal` value meaning "undecidable", and writing
  `no` would put a normal in a chart that the data does not support.
* **A row whose printed flag and computed flag disagree.** One of the two
  misread the page and which is not knowable from here, so it is a
  verification failure (D3, ARCHITECTURE.md §5). Week 1's rule is that a
  claim which fails verification is disclosed, never silently dropped —
  and never *written*. This is the same rule applied to a chart write,
  which is the strictest place it applies.

Everything refused comes back in `skipped`, so the caller can disclose it.
A plan with no writable rows is a real outcome, not an error.
"""

from __future__ import annotations

import base64
from datetime import datetime
from enum import StrEnum

from pydantic import Field

from app.models.w2.base import W2Contract
from app.models.w2.documents import DocumentKind
from app.models.w2.lab_pdf import LabPdfExtraction, LabResult
from app.models.w2.lab_values import AbnormalFlag, CrossCheckOutcome
from app.models.w2.writeback import (
    LabWriteBackRequest,
    LabWriteBackResult,
    WriteBackDocument,
    document_extraction_id,
    lab_result_extraction_id,
    lab_result_idempotency_key,
)


class SkipReason(StrEnum):
    """Why a row was left out. See the module docstring for each."""

    VALUE_NOT_LOCATED = "value_not_located"
    NO_LOINC_CODE = "no_loinc_code"
    NO_COLLECTION_DATE = "no_collection_date"
    NO_TEST_NAME = "no_test_name"
    FLAG_INDETERMINATE = "flag_indeterminate"
    FLAG_DISAGREES_WITH_PAGE = "flag_disagrees_with_page"


class SkippedResult(W2Contract):
    """
    One row that will not be written, named by its `test_key` — the
    extraction's own handle for it — and never by its value.
    """

    test_key: str = Field(min_length=1)
    reason: SkipReason


class WriteBackPlan(W2Contract):
    """
    What would be sent, and what would not.

    `request` is None when nothing in the extraction is writable. That is
    an outcome to disclose, not an error to raise: a scan too degraded to
    locate a single value produces exactly this.
    """

    request: LabWriteBackRequest | None = None
    skipped: list[SkippedResult] = Field(default_factory=list)

    @property
    def writable_count(self) -> int:
        return 0 if self.request is None else len(self.request.results)


def plan_write_back(
    extraction: LabPdfExtraction,
    *,
    patient_uuid: str,
    document_sha3_512: str,
    extractor_version: str,
    correlation_id: str,
    content: bytes | None = None,
    existing_document_id: int | None = None,
) -> WriteBackPlan:
    """
    Build the request for `extraction`, or explain why there is none.

    `document_sha3_512` is OpenEMR's digest of the stored file and is
    passed in rather than computed from `content`: on a retry there are no
    bytes to compute it from, and the digest is what every idempotency key
    in the request is built on. `SourceDocument.checksums.sha3_512` is
    where it comes from when the document has been stored.

    Raises ValueError for a non-lab extraction. That is not a skip — D2
    forbids intake data reaching the chart at all, and a caller that got
    here with an intake form has a routing bug, not a data problem.
    """
    if extraction.document.kind is not DocumentKind.LAB_PDF:
        raise ValueError(
            "only lab documents are written back; D2 makes intake medications "
            "and allergies discrepancies against the chart, never chart entries"
        )

    # The report's timestamp, which OpenEMR turns into both
    # `Observation.effectiveDateTime` and `meta.lastUpdated` (spike §9). It
    # sits on the order header, not the row; a report that did not print one
    # leaves it None and the endpoint falls back to the collection date.
    reported = extraction.order.reported_at.as_presented_fact()
    reported_at = None if reported is None else reported.value

    writable: list[LabWriteBackResult] = []
    skipped: list[SkippedResult] = []
    for result in extraction.results:
        built = _build_result(result, document_sha3_512, reported_at)
        if isinstance(built, SkipReason):
            skipped.append(SkippedResult(test_key=result.test_key, reason=built))
        else:
            writable.append(built)

    if not writable:
        return WriteBackPlan(request=None, skipped=skipped)

    document = WriteBackDocument(
        filename=extraction.document.filename,
        mime_type=extraction.document.mime_type,
        sha3_512=document_sha3_512,
        byte_length=extraction.document.byte_length,
        content_base64=None if content is None else base64.b64encode(content).decode("ascii"),
        existing_document_id=existing_document_id,
    )
    request = LabWriteBackRequest(
        patient_uuid=patient_uuid,
        document=document,
        results=writable,
        extraction_id=document_extraction_id(
            document_sha3_512=document_sha3_512, extractor_version=extractor_version
        ),
        extractor_version=extractor_version,
        correlation_id=correlation_id,
    )
    return WriteBackPlan(request=request, skipped=skipped)


def _build_result(
    result: LabResult, document_sha3_512: str, reported_at: datetime | None
) -> LabWriteBackResult | SkipReason:
    """One row, or the reason it is not one."""
    value = result.value.as_presented_fact()
    if value is None or result.value.citation is None or result.value.citation.bbox is None:
        # No located value means no bbox, and D2 requires the persisted
        # value to point at the region of the page it came from.
        return SkipReason.VALUE_NOT_LOCATED
    if result.loinc_code is None or not result.loinc_code.strip():
        return SkipReason.NO_LOINC_CODE

    collected = result.collection_date.as_presented_fact()
    if collected is None:
        return SkipReason.NO_COLLECTION_DATE

    name = result.test_name.as_presented_fact()
    if name is None or not name.value.strip():
        # `result_text` is required alongside `result_code` or OpenEMR
        # drops both. The extraction's `test_key` is our handle, not the
        # laboratory's name for the test, so it is not a substitute.
        return SkipReason.NO_TEST_NAME

    cross_check = result.cross_check_flag()
    if cross_check is None or cross_check.computed is AbnormalFlag.INDETERMINATE:
        return SkipReason.FLAG_INDETERMINATE
    if cross_check.outcome is CrossCheckOutcome.DISAGREE:
        return SkipReason.FLAG_DISAGREES_WITH_PAGE

    unit = result.unit.as_presented_fact()
    reference_range = result.reference_range.as_presented_fact()
    bbox = result.value.citation.bbox

    return LabWriteBackResult(
        test_code=result.loinc_code.strip(),
        test_name=name.value.strip(),
        value=value.value,
        unit=None if unit is None else unit.value,
        reference_range=None if reference_range is None else reference_range.value,
        collection_date=collected.value,
        reported_at=reported_at,
        abnormal=cross_check.computed,
        page=bbox.page,
        bbox=bbox,
        idempotency_key=lab_result_idempotency_key(
            document_sha3_512=document_sha3_512,
            test_code=result.loinc_code.strip(),
            collection_date=collected.value,
        ),
        extraction_id=lab_result_extraction_id(
            document_sha3_512=document_sha3_512,
            test_code=result.loinc_code.strip(),
            collection_date=collected.value,
        ),
    )
