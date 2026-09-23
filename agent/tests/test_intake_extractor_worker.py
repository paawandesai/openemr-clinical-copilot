"""
The worker end to end: typed failures, D7 status, observability without PHI,
and the tracing guard. No network: the vision step is a stub and the chart
is the factory cache.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pymupdf
import pytest
from structlog.testing import capture_logs

from app.extraction import OcrUnavailableError
from app.graph.intake_extractor import INTAKE_EXTRACTOR_GRAPH, IntakeExtractorContext
from app.models.telemetry_events import TELEMETRY_EVENT_TYPES
from app.models.w2.documents import DocumentKind, ExtractionStatus
from app.models.w2.extraction import FieldStatus
from app.models.w2.workers import ExtractionFailureReason
from app.workers.intake_extractor import ExtractionJobs, attach_and_extract
from app.workers.intake_extractor.vision import VisionCallError, VisionResult, VisionUsage
from app.workers.intake_extractor.vision_schema import RawField
from tests.intake_extractor_support import (
    chart_for,
    ground_truth_vision,
    patient_uuid,
    pdf_bytes,
    raw_from_ground_truth,
)

LAB = "lab-clean-001"
INTAKE = "intake-clean-004"


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def reader(sample: str):  # noqa: ANN201
    async def read(_uuid: str):  # noqa: ANN202
        return chart_for(sample)

    return read


def failing_vision(reason: ExtractionFailureReason):  # noqa: ANN201
    async def extract(kind: DocumentKind, pdf: bytes, pages: int) -> VisionResult:
        raise VisionCallError(reason, "stubbed failure")

    return extract


def extract(sample: str, **kwargs: Any):  # noqa: ANN201
    kind = DocumentKind.LAB_PDF if sample.startswith("lab") else DocumentKind.INTAKE_FORM
    kwargs.setdefault("vision", ground_truth_vision(sample))
    return run(
        attach_and_extract(
            patient_uuid(sample), kwargs.pop("pdf", pdf_bytes(sample)), kind, **kwargs
        )
    )


def assert_failed(report: Any, reason: ExtractionFailureReason) -> None:
    assert report.output.status is ExtractionStatus.FAILED
    assert report.output.failure_reason is reason
    assert report.output.payload is None  # D7: never a partial extraction


# -- the happy path ------------------------------------------------------------


def test_complete_lab_extraction_is_the_contract_output() -> None:
    report = extract(LAB, chart_reader=reader(LAB), document_id="doc-77")
    output = report.output
    assert output.status is ExtractionStatus.COMPLETE
    assert output.document_id == "doc-77"
    assert output.extraction_id == output.payload.extraction_id
    assert output.payload.document.checksums.sha3_512 is not None
    assert output.payload.document.has_text_layer
    assert report.flag_cross_check is not None
    assert report.identity is not None and not report.identity.hard_flag
    assert {field.outcome for field in report.fields} >= {"located", "missing"}
    # Every non-presentable field is disclosed — the accession printed twice.
    assert any(u.field_path.startswith("order.accession") for u in report.unsupported)


def test_extraction_id_is_stable_for_the_same_bytes() -> None:
    first = extract(LAB).output.extraction_id
    second = extract(LAB).output.extraction_id
    assert first == second


def test_path_input_is_accepted(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "report.pdf"
    path.write_bytes(pdf_bytes(LAB))
    report = run(
        attach_and_extract(
            patient_uuid(LAB), path, DocumentKind.LAB_PDF, vision=ground_truth_vision(LAB)
        )
    )
    assert report.output.status is ExtractionStatus.COMPLETE
    assert report.output.payload.document.filename == "report.pdf"


# -- graceful degradation --------------------------------------------------------


def test_not_a_pdf_is_unsupported_mime_type() -> None:
    assert_failed(
        extract(LAB, pdf=b"\x89PNG\r\n\x1a\n...."), ExtractionFailureReason.UNSUPPORTED_MIME_TYPE
    )


def test_corrupt_pdf_is_unreadable_file() -> None:
    assert_failed(extract(LAB, pdf=b"%PDF-1.7\n garbage"), ExtractionFailureReason.UNREADABLE_FILE)


def test_missing_path_is_unreadable_file() -> None:
    report = run(attach_and_extract(patient_uuid(LAB), "/nonexistent/x.pdf", DocumentKind.LAB_PDF))
    assert_failed(report, ExtractionFailureReason.UNREADABLE_FILE)


def test_image_only_pdf_without_ocr_is_no_text_layer_and_ocr_failed(monkeypatch) -> None:  # noqa: ANN001
    def no_ocr(pdf: bytes):  # noqa: ANN202
        raise OcrUnavailableError("tesseract is not installed")

    monkeypatch.setattr(IntakeExtractorContext, "__init__", _with_reader(no_ocr))
    assert_failed(
        extract("lab-scanned-heavy-abnormal-002"),
        ExtractionFailureReason.NO_TEXT_LAYER_AND_OCR_FAILED,
    )


def _with_reader(read_words):  # noqa: ANN001, ANN202
    original = IntakeExtractorContext.__init__

    def init(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        kwargs["read_words"] = read_words
        original(self, *args, **kwargs)

    return init


def test_blank_pdf_is_no_text_layer_and_ocr_failed() -> None:
    document = pymupdf.open()
    document.new_page()
    blank = document.tobytes()
    assert_failed(extract(LAB, pdf=blank), ExtractionFailureReason.NO_TEXT_LAYER_AND_OCR_FAILED)


def test_too_many_pages_fails_before_any_model_call() -> None:
    document = pymupdf.open()
    for _ in range(31):
        document.new_page().insert_text((72, 72), "Page text for the limit test")
    calls: list[int] = []

    async def counting(kind, pdf, pages):  # noqa: ANN001, ANN202
        calls.append(pages)
        raise AssertionError("no call should be made")

    assert_failed(
        extract(LAB, pdf=document.tobytes(), vision=counting),
        ExtractionFailureReason.PAGE_LIMIT_EXCEEDED,
    )
    assert calls == []


@pytest.mark.parametrize(
    "reason",
    [
        ExtractionFailureReason.VISION_CALL_FAILED,
        ExtractionFailureReason.MODEL_OUTPUT_INVALID,
        ExtractionFailureReason.TIMED_OUT,
    ],
)
def test_vision_failures_are_typed(reason: ExtractionFailureReason) -> None:
    assert_failed(extract(LAB, vision=failing_vision(reason)), reason)


def test_wrong_document_kind_from_the_model_is_model_output_invalid() -> None:
    assert_failed(
        extract(LAB, vision=ground_truth_vision(INTAKE)),
        ExtractionFailureReason.MODEL_OUTPUT_INVALID,
    )


def test_zero_located_fields_is_a_failure_not_an_empty_success() -> None:
    raw = raw_from_ground_truth(LAB)
    nowhere = RawField(state="present", text="Nothing Like This", page=1)
    invented = raw.model_copy(
        update={
            "patient": raw.patient.model_copy(
                update={name: nowhere for name in type(raw.patient).model_fields}
            ),
            "performing_lab": raw.performing_lab.model_copy(
                update={name: nowhere for name in type(raw.performing_lab).model_fields}
            ),
            "order": raw.order.model_copy(
                update={name: nowhere for name in type(raw.order).model_fields}
            ),
            "results": [],
        }
    )

    async def stub(kind, pdf, pages):  # noqa: ANN001, ANN202
        return VisionResult(raw=invented, usage=VisionUsage(calls=1, cost_usd=0.01))

    report = extract(LAB, vision=stub)
    assert_failed(report, ExtractionFailureReason.NO_FIELDS_LOCATED)
    assert report.vision.calls == 1  # the spend is still reported


def test_unexpected_error_is_internal_error_never_raised() -> None:
    async def broken(kind, pdf, pages):  # noqa: ANN001, ANN202
        raise RuntimeError("SENTINEL-PHI-IN-EXCEPTION")

    with capture_logs() as logs:
        report = extract(LAB, vision=broken)
    assert_failed(report, ExtractionFailureReason.INTERNAL_ERROR)
    assert "SENTINEL-PHI-IN-EXCEPTION" not in json.dumps(logs, default=str)


def test_chart_outage_does_not_fail_the_extraction() -> None:
    async def down(_uuid: str):  # noqa: ANN202
        raise ConnectionError("FHIR down")

    report = extract(INTAKE, chart_reader=down)
    assert report.output.status is ExtractionStatus.COMPLETE
    assert report.discrepancies.status.value == "chart_unavailable"


def test_wrong_patient_blocks_the_discrepancy_check() -> None:
    raw = raw_from_ground_truth(INTAKE)
    other = chart_for("intake-missing-allergy-006")  # a different patient's chart

    async def other_reader(_uuid: str):  # noqa: ANN202
        return other

    async def stub(kind, pdf, pages):  # noqa: ANN001, ANN202
        return VisionResult(raw=raw, usage=VisionUsage())

    report = extract(INTAKE, vision=stub, chart_reader=other_reader)
    assert report.output.status is ExtractionStatus.COMPLETE
    assert report.identity.hard_flag
    assert report.discrepancies.status.value == "blocked_identity_mismatch"
    assert report.discrepancies.discrepancies == []


# -- D7: upload time, and a status the ask path reads --------------------------------


def test_jobs_report_pending_then_complete_without_waiting() -> None:
    gate = asyncio.Event()

    async def slow(kind, pdf, pages):  # noqa: ANN001, ANN202
        await gate.wait()
        return await ground_truth_vision(LAB)(kind, pdf, pages)

    async def scenario() -> None:
        jobs = ExtractionJobs(vision=slow)
        pending = jobs.submit(
            patient_uuid(LAB), pdf_bytes(LAB), DocumentKind.LAB_PDF, document_id="d1"
        )
        assert pending.status is ExtractionStatus.PENDING
        assert pending.payload is None
        await asyncio.sleep(0.05)
        in_flight = jobs.status("d1")
        assert in_flight.is_still_processing and in_flight.payload is None
        assert jobs.report("d1") is None  # the ask path gets nothing partial
        gate.set()
        report = await jobs.wait("d1")
        assert report.output.status is ExtractionStatus.COMPLETE
        assert jobs.status("d1").status is ExtractionStatus.COMPLETE
        assert jobs.report("d1") is report
        # Re-submitting a finished document is a lookup, not a second run.
        assert (
            jobs.submit(patient_uuid(LAB), b"", DocumentKind.LAB_PDF, document_id="d1")
            is report.output
        )

    run(scenario())


def test_jobs_report_a_failure_as_failed() -> None:
    async def scenario() -> None:
        jobs = ExtractionJobs(vision=failing_vision(ExtractionFailureReason.VISION_CALL_FAILED))
        jobs.submit(patient_uuid(LAB), pdf_bytes(LAB), DocumentKind.LAB_PDF, document_id="d2")
        await jobs.wait("d2")
        status = jobs.status("d2")
        assert status.status is ExtractionStatus.FAILED
        assert status.failure_reason is ExtractionFailureReason.VISION_CALL_FAILED

    run(scenario())


# -- observability ----------------------------------------------------------------------


def test_every_event_carries_one_correlation_id_and_the_summary_fields() -> None:
    correlation_id = str(uuid.uuid4())
    with capture_logs() as logs:
        report = extract(LAB, chart_reader=reader(LAB), correlation_id=correlation_id)
    assert report.output.correlation_id == correlation_id
    every = [log for log in logs if log.get("event") == "agent_event"]
    # The node step_events share the id; the sequence below is the worker's own.
    assert all(e["event_data"]["correlation_id"] == correlation_id for e in every)
    events = [e for e in every if e["event_type"] not in TELEMETRY_EVENT_TYPES]
    assert [e["event_type"] for e in events] == [
        "intake_extraction_step",
        "intake_extraction_step",
        "intake_extraction_step",
        "intake_extraction_step",
        "intake_extraction_completed",
    ]
    assert all(e["event_data"]["correlation_id"] == correlation_id for e in events)
    summary = events[-1]["event_data"]
    assert summary["steps"] == ["read_document", "vision", "locate", "verify"]
    for key in (
        "step_ms",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "field_outcomes",
        "mean_confidence",
        "cross_check",
        "identity",
    ):
        assert key in summary


CANARIES = ("Merlyn225", "Mueller846", "1960-09-25", "890 Mills Well", "Westwood", "a2c373ec")


def test_no_phi_reaches_any_log_line() -> None:
    # The chart and the page are full of the patient's name, DOB, address
    # and uuid; none of it may appear in any event, including the canary
    # control below that proves the scan can see a leak.
    with capture_logs() as logs:
        extract("intake-scanned-light-med-005", chart_reader=reader("intake-scanned-light-med-005"))
        extract(LAB, chart_reader=reader(LAB))
    dumped = json.dumps(logs, default=str)
    assert [canary for canary in CANARIES if canary in dumped] == []
    assert "amLODIPine" not in dumped and "Aspirin" not in dumped


def test_the_canary_scan_can_see_a_leak() -> None:
    from app.middleware.logging import log_event

    with capture_logs() as logs:
        log_event("control", event_data={"leak": "Merlyn225"})
    assert "Merlyn225" in json.dumps(logs, default=str)


# -- tracing (the contained LangSmith tests are in test_intake_extractor_tracing.py) --------------------------------------------------------------------------


def test_the_exported_graph_has_no_other_way_to_run() -> None:
    for name in ("invoke", "stream", "astream", "astream_events", "batch", "with_config"):
        assert not hasattr(INTAKE_EXTRACTOR_GRAPH, name)


def test_illegible_field_stays_valueless_end_to_end() -> None:
    # W2-B4: the factory scribbles out city/state/zip; the chart knows it.
    report = extract(
        "intake-missing-allergy-006", chart_reader=reader("intake-missing-allergy-006")
    )
    city = report.output.payload.demographics.city_state_zip
    assert city.status is FieldStatus.ILLEGIBLE and city.value is None
    dob = report.output.payload.demographics.date_of_birth
    assert dob.status is FieldStatus.MISSING and dob.value is None
