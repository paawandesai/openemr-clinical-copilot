"""
The extractor's `LabPdfExtraction` is what `app/writeback/plan.py` expects.

These tests pin the handoff between the worker and the planner
(feat/w2-writeback) so a change on either side that breaks it fails a test:

* rows come out coded by the committed table (`loinc.py`), so every
  located clean-sample row is writable except eGFR, which is deliberately
  uncoded and skipped as `no_loinc_code`; the request's extraction id is
  the extraction's own when the caller passes the worker's
  `EXTRACTOR_VERSION`;
* a printed flag that disagrees with the computed one is the planner's
  `flag_disagrees_with_page`; a blank flag column is not a disagreement.
"""

from __future__ import annotations

import asyncio
import uuid

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


def _plan(extraction: LabPdfExtraction, sha: str, content: bytes):  # noqa: ANN202
    return plan_write_back(
        extraction,
        patient_uuid=patient_uuid(SAMPLE),
        document_sha3_512=sha,
        extractor_version=EXTRACTOR_VERSION,
        correlation_id=str(uuid.uuid4()),
        content=content,
    )


def test_as_emitted_every_clean_row_but_egfr_is_coded_and_writable() -> None:
    # loinc.py codes every analyte the factory draws except eGFR, which it
    # refuses on purpose (the equation is not printed). That row is skipped
    # cleanly as `no_loinc_code`; everything else is written.
    extraction = _extracted(SAMPLE)
    plan = _plan(extraction, extraction.document.checksums.sha3_512, pdf_bytes(SAMPLE))
    assert [(s.test_key, s.reason) for s in plan.skipped] == [("egfr", SkipReason.NO_LOINC_CODE)]
    assert plan.writable_count == 21
    assert plan.request.extraction_id == extraction.extraction_id
    glucose = next(r for r in plan.request.results if r.test_name == "Glucose")
    assert glucose.test_code == "2345-7"
    assert glucose.bbox == extraction.results[0].value.citation.bbox


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
    # Glucose in mg/dL is coded by the table (2345-7), so the flag rules decide.
    return assemble_lab(raw, words(page), source_document(), EXTRACTION_ID).extraction


def test_a_disagreeing_printed_flag_is_the_planners_flag_disagrees_with_page() -> None:
    plan = _plan(_one_row("H"), SHA, b"%PDF-1.7")
    assert [s.reason for s in plan.skipped] == [SkipReason.FLAG_DISAGREES_WITH_PAGE]


def test_a_blank_flag_column_is_not_a_disagreement() -> None:
    plan = _plan(_one_row(""), SHA, b"%PDF-1.7")
    assert plan.skipped == []
    assert plan.request.results[0].abnormal.value == "normal"
