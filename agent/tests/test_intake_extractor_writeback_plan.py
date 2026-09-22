"""
The extractor's `LabPdfExtraction` is what `app/writeback/plan.py` expects.

The planner (feat/w2-writeback) is not changed here; these tests pin the
handoff so a change on either side that breaks it fails a test:

* rows come out uncoded — `loinc_code` is assigned, never extracted — so as
  emitted every located row is `no_loinc_code`, and nothing else;
* once coded, every located clean-sample row is writable, and the request's
  extraction id is the extraction's own when the caller passes the
  worker's `EXTRACTOR_VERSION`;
* a printed flag that disagrees with the computed one is the planner's
  `flag_disagrees_with_page`; a blank flag column is not a disagreement.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter

from structlog.testing import capture_logs

from app.graph.intake_extractor import EXTRACTOR_VERSION
from app.models.w2.lab_pdf import LabPdfExtraction
from app.workers.intake_extractor import attach_and_extract
from app.workers.intake_extractor.assemble import assemble_lab
from app.writeback.plan import SkipReason, plan_write_back
from tests.intake_extractor_support import doc_kind, ground_truth_vision, patient_uuid, pdf_bytes
from tests.test_intake_extractor_assemble import (
    EXTRACTION_ID,
    page_of,
    present,
    raw_lab,
    raw_row,
    source_document,
    words,
)

SAMPLE = "lab-clean-001"
SHA = "b" * 128


def _extracted(sample: str) -> LabPdfExtraction:
    with capture_logs():
        report = asyncio.run(
            attach_and_extract(
                patient_uuid(sample),
                pdf_bytes(sample),
                doc_kind(sample),
                vision=ground_truth_vision(sample),
            )
        )
    payload = report.output.payload
    assert isinstance(payload, LabPdfExtraction)
    return payload


def _coded(extraction: LabPdfExtraction) -> LabPdfExtraction:
    return extraction.model_copy(
        update={
            "results": [
                row.model_copy(update={"loinc_code": f"{1000 + i}-0"})
                for i, row in enumerate(extraction.results)
            ]
        }
    )


def _plan(extraction: LabPdfExtraction, sha: str, content: bytes):  # noqa: ANN202
    return plan_write_back(
        extraction,
        patient_uuid=patient_uuid(SAMPLE),
        document_sha3_512=sha,
        extractor_version=EXTRACTOR_VERSION,
        correlation_id=str(uuid.uuid4()),
        content=content,
    )


def test_as_emitted_every_located_row_is_uncoded_and_nothing_else() -> None:
    extraction = _extracted(SAMPLE)
    plan = _plan(extraction, extraction.document.checksums.sha3_512, pdf_bytes(SAMPLE))
    assert plan.request is None
    assert Counter(s.reason for s in plan.skipped) == {SkipReason.NO_LOINC_CODE: 22}


def test_once_coded_every_clean_row_is_writable_under_the_extractions_own_id() -> None:
    extraction = _coded(_extracted(SAMPLE))
    plan = _plan(extraction, extraction.document.checksums.sha3_512, pdf_bytes(SAMPLE))
    assert plan.skipped == []
    assert plan.writable_count == 22
    assert plan.request.extraction_id == extraction.extraction_id
    first = plan.request.results[0]
    assert first.bbox == extraction.results[0].value.citation.bbox


def _one_row(flag: str) -> LabPdfExtraction:
    page = page_of(
        [
            ["Collected", "2026-08-30", "07:12"],
            ["Glucose", "89", *([flag] if flag else []), "mg/dL", "70", "-", "99"],
        ]
    )
    raw = raw_lab(
        [
            raw_row(
                "glucose",
                name="Glucose",
                value="89",
                unit="mg/dL",
                reference_range="70 - 99",
                flag=flag,
            )
        ],
        collected_at=present("2026-08-30 07:12"),
    )
    return _coded(assemble_lab(raw, words(page), source_document(), EXTRACTION_ID).extraction)


def test_a_disagreeing_printed_flag_is_the_planners_flag_disagrees_with_page() -> None:
    plan = _plan(_one_row("H"), SHA, b"%PDF-1.7")
    assert [s.reason for s in plan.skipped] == [SkipReason.FLAG_DISAGREES_WITH_PAGE]


def test_a_blank_flag_column_is_not_a_disagreement() -> None:
    plan = _plan(_one_row(""), SHA, b"%PDF-1.7")
    assert plan.skipped == []
    assert plan.request.results[0].abnormal.value == "normal"
