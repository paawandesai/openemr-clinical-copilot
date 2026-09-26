"""
D54: a completed lab extraction writes its rows back to the chart — and
everything that must never be written is not.

The extraction is real (text layer, locate, the checks) with the vision
step replaced by the sample's ground truth and the chart by the factory's
chart cache (tests/intake_extractor_support.py). The endpoint is a
recording writer; `tests/conftest.py` keeps every other path offline.

    cd agent && python -m pytest tests/test_w2_writeback_after_extraction.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from app.api import documents, upload_guard
from app.fhir import client as fhir_client
from app.main import app
from app.models.w2.documents import DocumentKind, ExtractionStatus
from app.models.w2.workers import ExtractionFailureReason, IntakeExtractorOutput
from app.models.w2.writeback import (
    LabWriteBackRequest,
    LabWriteBackResponse,
    LabWriteBackResultOutcome,
    WriteOutcome,
)
from app.workers import document_store
from app.workers.document_store import DocumentStore
from app.workers.intake_extractor.report import ExtractionReport
from app.writeback import after_extraction
from app.writeback.after_extraction import (
    WriteBackDecision,
    lab_write_back,
    write_back_after_extraction,
)
from app.writeback.exceptions import WriteBackAuthError, WriteBackRefused, WriteBackUnavailable
from tests.intake_extractor_support import chart_for, ground_truth_vision, patient_uuid, pdf_bytes

LAB = "lab-clean-001"
OTHER_LAB = "lab-multipage-003"
INTAKE = "intake-clean-004"
TOKEN = "SENTINEL-CLINICIAN-TOKEN-5e1a"
CORRELATION_ID = "0f0e0d0c-0b0a-4908-8706-050403020100"


class RecordingWriter:
    """The endpoint, answering every row as created."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, LabWriteBackRequest]] = []

    async def __call__(self, token: str, request: LabWriteBackRequest) -> LabWriteBackResponse:
        self.calls.append((token, request))
        return LabWriteBackResponse(
            patient_uuid=request.patient_uuid,
            document_id=2484,
            document_outcome=WriteOutcome.DUPLICATE_IGNORED,
            procedure_order_id=9001,
            results=[
                LabWriteBackResultOutcome(
                    test_code=row.test_code,
                    idempotency_key=row.idempotency_key,
                    extraction_id=row.extraction_id,
                    outcome=WriteOutcome.CREATED,
                    procedure_report_id=9100 + index,
                    procedure_result_id=9200 + index,
                )
                for index, row in enumerate(request.results)
            ],
            correlation_id=request.correlation_id,
        )


def reader_for(sample: str):  # noqa: ANN201 - a ChartReader
    async def read(uuid: str):  # noqa: ANN202
        return chart_for(sample)

    return read


async def extract(
    sample: str, *, chart: str | None = None, with_reader: bool = True
) -> ExtractionReport:
    store = DocumentStore(vision=ground_truth_vision(sample))
    kind = DocumentKind.LAB_PDF if sample.startswith("lab") else DocumentKind.INTAKE_FORM
    stored, _, _ = store.accept(
        patient_uuid(sample),
        pdf_bytes(sample),
        kind,
        chart_reader=reader_for(chart or sample) if with_reader else None,
    )
    report = await store.wait(stored.document.document_id)
    assert report is not None
    return report


def decide(report: ExtractionReport, writer: RecordingWriter, sample: str = LAB):
    return asyncio.run(
        write_back_after_extraction(
            report,
            token=TOKEN,
            patient_uuid=patient_uuid(sample),
            pdf=pdf_bytes(sample),
            writer=writer,
        )
    )


# --- what is written ----------------------------------------------------------


def test_a_confirmed_lab_report_writes_its_located_coded_rows_under_the_uploaders_token():
    report = asyncio.run(extract(LAB))
    assert report.identity is not None and not report.identity.hard_flag
    writer = RecordingWriter()

    assert decide(report, writer).decision is WriteBackDecision.WRITTEN

    assert len(writer.calls) == 1
    token, request = writer.calls[0]
    assert token == TOKEN
    assert request.patient_uuid == patient_uuid(LAB)
    assert request.extraction_id == report.output.extraction_id
    # The bytes travel so the endpoint can match the chart copy by digest
    # (or store it, if the chart copy is not there yet): one document.
    assert base64.b64decode(request.document.content_base64 or "") == pdf_bytes(LAB)
    assert request.document.category_path == "/Categories/Lab_Report"
    assert request.results, "lab-clean-001 has located, coded rows"
    for row in request.results:
        assert row.bbox is not None and row.page >= 1  # invariant 4 / D2 provenance
        assert row.test_code.strip()  # LOINC-coded
        assert row.extraction_id.startswith("w2x-")  # D1: what lands in comments


def test_only_rows_the_planner_accepts_are_sent_and_the_rest_are_counted_not_named():
    report = asyncio.run(extract(LAB))
    writer = RecordingWriter()
    with capture_logs() as logs:
        decide(report, writer)

    sent = len(writer.calls[0][1].results)
    [event] = [e for e in logs if e.get("event_type") == "lab_writeback_after_extraction"]
    data = event["event_data"]
    assert data["decision"] == "written"
    assert data["results_writable"] == sent
    assert data["results_total"] == sent + sum(data["results_skipped_by_reason"].values())


# --- what is never written ----------------------------------------------------


def test_a_document_naming_another_patient_is_not_written():
    report = asyncio.run(extract(LAB, chart=OTHER_LAB))
    assert report.identity is not None and report.identity.hard_flag
    writer = RecordingWriter()

    assert decide(report, writer).decision is WriteBackDecision.IDENTITY_NOT_CONFIRMED
    assert writer.calls == []


def test_a_document_whose_patient_could_not_be_checked_is_not_written():
    # No chart read: the identity check is `not_comparable`, which has not
    # ruled out a report filed on the wrong chart.
    report = asyncio.run(extract(LAB, with_reader=False))
    assert report.identity is not None and report.identity.name.value == "not_comparable"
    writer = RecordingWriter()

    assert decide(report, writer).decision is WriteBackDecision.IDENTITY_NOT_CONFIRMED
    assert writer.calls == []


def test_an_intake_form_is_never_written():
    report = asyncio.run(extract(INTAKE))
    assert report.is_complete
    writer = RecordingWriter()

    assert decide(report, writer, INTAKE).decision is WriteBackDecision.NOT_LAB_REPORT
    assert writer.calls == []


def test_a_failed_extraction_is_not_written():
    report = ExtractionReport(
        output=IntakeExtractorOutput(
            status=ExtractionStatus.FAILED,
            document_id="d" * 128,
            kind=DocumentKind.LAB_PDF,
            failure_reason=ExtractionFailureReason.NO_FIELDS_LOCATED,
            correlation_id=CORRELATION_ID,
        )
    )
    writer = RecordingWriter()

    assert decide(report, writer).decision is WriteBackDecision.EXTRACTION_NOT_COMPLETE
    assert writer.calls == []


@pytest.mark.parametrize(
    ("error", "decision"),
    [
        (WriteBackAuthError("no scope"), WriteBackDecision.TOKEN_REFUSED),
        (
            WriteBackRefused("bad field", field="results", status_code=400),
            WriteBackDecision.REQUEST_REFUSED,
        ),
        (WriteBackUnavailable("503"), WriteBackDecision.ENDPOINT_UNAVAILABLE),
        (RuntimeError("bug"), WriteBackDecision.ENDPOINT_UNAVAILABLE),
    ],
)
def test_a_failed_write_is_reported_and_never_raised(error: Exception, decision: WriteBackDecision):
    report = asyncio.run(extract(LAB))

    async def failing(token: str, request: LabWriteBackRequest) -> LabWriteBackResponse:
        raise error

    outcome = asyncio.run(
        write_back_after_extraction(
            report, token=TOKEN, patient_uuid=patient_uuid(LAB), pdf=pdf_bytes(LAB), writer=failing
        )
    )
    assert outcome.decision is decision


# --- when it runs -------------------------------------------------------------


def test_the_store_runs_it_once_after_the_status_is_final_and_not_for_a_duplicate():
    async def run() -> tuple[list[ExtractionStatus], int]:
        store = DocumentStore(vision=ground_truth_vision(LAB))
        seen: list[ExtractionStatus] = []

        async def after(report: ExtractionReport) -> WriteBackDecision:
            status = store.status(report.output.document_id)
            assert status is not None
            seen.append(status.status)
            # Completed: a duplicate owes no retry (D60 retries only a
            # write-back that did not complete; see the retry tests below).
            return WriteBackDecision.WRITTEN

        stored, _, _ = store.accept(
            patient_uuid(LAB),
            pdf_bytes(LAB),
            DocumentKind.LAB_PDF,
            chart_reader=reader_for(LAB),
            after_extraction=after,
        )
        await store.wait(stored.document.document_id)
        # The same bytes again: the extraction exists and its write-back
        # completed, so nothing runs.
        _, _, duplicate = store.accept(
            patient_uuid(LAB),
            pdf_bytes(LAB),
            DocumentKind.LAB_PDF,
            chart_reader=reader_for(LAB),
            after_extraction=after,
        )
        await asyncio.sleep(0)
        return seen, int(duplicate)

    seen, duplicate = asyncio.run(run())
    assert seen == [ExtractionStatus.COMPLETE]
    assert duplicate == 1


def test_a_store_without_the_step_never_writes(offline_writeback):
    # The gate and the ask-path tests build stores this way.
    asyncio.run(extract(LAB))
    assert offline_writeback.requests == []


# --- the route ----------------------------------------------------------------


def _jwt() -> str:
    import json

    def part(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    scopes = [
        "openid",
        "user/Patient.read",
        "user/Condition.read",
        "user/MedicationRequest.read",
        "user/AllergyIntolerance.read",
    ]
    claims = {
        "aud": "client-1",
        "sub": "user-1",
        "scopes": scopes,
        "exp": int(datetime.now(UTC).timestamp()) + 600,
    }
    return f"{part({'alg': 'RS256'})}.{part(claims)}.c2ln"


@pytest.mark.parametrize(("sample", "writes"), [(LAB, True), (INTAKE, False)])
def test_the_upload_route_writes_back_a_lab_report_and_never_an_intake_form(
    monkeypatch: pytest.MonkeyPatch, sample: str, writes: bool
):
    async def patient_exists(token: str, patient_id: str) -> None:
        return None

    writer = RecordingWriter()
    monkeypatch.setattr(fhir_client, "ensure_patient_exists", patient_exists)
    monkeypatch.setattr(
        document_store, "DOCUMENTS", DocumentStore(vision=ground_truth_vision(sample))
    )
    monkeypatch.setattr(documents, "fhir_chart_reader", lambda token, **_: reader_for(sample))
    monkeypatch.setattr(after_extraction, "write_lab_results", writer)
    upload_guard.reset_for_tests()
    token = _jwt()
    kind = "lab_pdf" if sample.startswith("lab") else "intake_form"
    try:
        with TestClient(app) as client:
            response = client.post(
                "/documents",
                params={"kind": kind, "patient_id": patient_uuid(sample)},
                content=pdf_bytes(sample),
                headers={"Content-Type": "application/pdf", "Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200, response.text
            # The job, and the write-back step inside it, run on the app's
            # loop in the client's thread; wait for the task to end.
            task = document_store.DOCUMENTS.jobs._tasks[response.json()["document_id"]]
            deadline = time.monotonic() + 60
            while not task.done() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert task.done()
    finally:
        upload_guard.reset_for_tests()

    assert [call[0] for call in writer.calls] == ([token] if writes else [])


def test_lab_write_back_binds_the_uploaders_token_patient_and_bytes():
    report = asyncio.run(extract(LAB))
    step = lab_write_back(TOKEN, patient_uuid(LAB), pdf_bytes(LAB))
    writer = RecordingWriter()
    original = after_extraction.write_lab_results
    after_extraction.write_lab_results = writer  # type: ignore[assignment]
    try:
        assert asyncio.run(step(report)).decision is WriteBackDecision.WRITTEN
    finally:
        after_extraction.write_lab_results = original  # type: ignore[assignment]
    [(token, request)] = writer.calls
    assert token == TOKEN and request.patient_uuid == patient_uuid(LAB)


# --- results the chart already holds (work order 34) ----------------------------


class ChartAwareWriter:
    """
    The endpoint after work order 34: the first row is already in the chart
    with a different value (a conflict), the second is already there with
    the same value, the rest are created.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, LabWriteBackRequest]] = []

    async def __call__(self, token: str, request: LabWriteBackRequest) -> LabWriteBackResponse:
        self.calls.append((token, request))
        results = []
        for index, row in enumerate(request.results):
            common = {
                "test_code": row.test_code,
                "idempotency_key": row.idempotency_key,
                "extraction_id": row.extraction_id,
            }
            if index == 0:
                results.append(
                    LabWriteBackResultOutcome(
                        **common,
                        outcome=WriteOutcome.CONFLICT,
                        matched_procedure_result_id=777,
                        chart_value="98765.4321",
                        chart_unit="mg/dL",
                    )
                )
            elif index == 1:
                results.append(
                    LabWriteBackResultOutcome(
                        **common,
                        outcome=WriteOutcome.ALREADY_IN_CHART,
                        procedure_report_id=600,
                        procedure_result_id=778,
                        matched_procedure_result_id=778,
                    )
                )
            else:
                results.append(
                    LabWriteBackResultOutcome(
                        **common,
                        outcome=WriteOutcome.CREATED,
                        procedure_report_id=9100 + index,
                        procedure_result_id=9200 + index,
                    )
                )
        return LabWriteBackResponse(
            patient_uuid=request.patient_uuid,
            document_id=2484,
            document_outcome=WriteOutcome.DUPLICATE_IGNORED,
            procedure_order_id=9001,
            results=results,
            correlation_id=request.correlation_id,
        )


def test_the_summary_counts_every_outcome_and_carries_each_conflict_with_both_values():
    report = asyncio.run(extract(LAB))
    writer = ChartAwareWriter()

    summary = decide(report, writer)

    [(_, request)] = writer.calls
    sent = len(request.results)
    assert summary.decision is WriteBackDecision.WRITTEN
    assert (summary.conflict, summary.already_in_chart, summary.created) == (1, 1, sent - 2)
    assert summary.duplicate_ignored == 0
    assert summary.results_total == summary.skipped + sent
    [conflict] = summary.conflicts
    first = request.results[0]
    # The document side with its provenance (invariant 2), the chart side
    # with its row: "chart has X, this document has Y", never overwritten.
    assert conflict.test_code == first.test_code and conflict.test_name == first.test_name
    assert conflict.document_value == first.value.as_text()
    assert conflict.document_unit == first.unit
    assert (conflict.page, conflict.bbox) == (first.page, first.bbox)
    assert conflict.collection_date == first.collection_date
    assert (conflict.chart_value, conflict.chart_unit) == ("98765.4321", "mg/dL")
    assert conflict.chart_procedure_result_id == 777


def test_the_summary_logs_counts_and_never_a_conflicts_values():
    report = asyncio.run(extract(LAB))
    writer = ChartAwareWriter()
    with capture_logs() as logs:
        summary = decide(report, writer)

    text = json.dumps(logs, default=str)
    [conflict] = summary.conflicts
    # The chart value is distinctive by construction; the document value is
    # a real lab number that could appear as some latency or count, so the
    # keys that would carry either are what is asserted absent.
    assert conflict.chart_value not in text
    assert conflict.test_name not in text
    for field in ("chart_value", "document_value", "conflicts", "test_name"):
        assert f'"{field}"' not in text, field
    [event] = [e for e in logs if e.get("event_type") == "lab_writeback_after_extraction"]
    data = event["event_data"]
    assert (data["results_conflict"], data["results_already_in_chart"]) == (1, 1)
    assert data["results_created"] == summary.created


def test_lab_clean_001_sends_its_collection_time_for_the_chart_match():
    # The report prints "Collected 2026-08-30 07:12": the endpoint matches an
    # existing chart result on that time, not only on the day.
    report = asyncio.run(extract(LAB))
    writer = RecordingWriter()
    decide(report, writer)
    [(_, request)] = writer.calls
    assert {row.collected_at for row in request.results} == {datetime(2026, 8, 30, 7, 12)}


def test_a_refused_write_still_reports_the_rows_it_would_have_sent():
    report = asyncio.run(extract(LAB))

    async def refused(token: str, request: LabWriteBackRequest) -> LabWriteBackResponse:
        raise WriteBackAuthError("no scope")

    summary = asyncio.run(
        write_back_after_extraction(
            report, token=TOKEN, patient_uuid=patient_uuid(LAB), pdf=pdf_bytes(LAB), writer=refused
        )
    )
    assert summary.decision is WriteBackDecision.TOKEN_REFUSED
    assert summary.results_total > 0 and summary.created == 0 and summary.conflicts == []


def test_the_status_route_reports_the_write_back_once_it_has_ended(
    monkeypatch: pytest.MonkeyPatch,
):
    async def patient_exists(token: str, patient_id: str) -> None:
        return None

    writer = ChartAwareWriter()
    monkeypatch.setattr(fhir_client, "ensure_patient_exists", patient_exists)
    monkeypatch.setattr(document_store, "DOCUMENTS", DocumentStore(vision=ground_truth_vision(LAB)))
    monkeypatch.setattr(documents, "fhir_chart_reader", lambda token, **_: reader_for(LAB))
    monkeypatch.setattr(after_extraction, "write_lab_results", writer)
    upload_guard.reset_for_tests()
    token = _jwt()
    try:
        with TestClient(app) as client:
            headers = {"Content-Type": "application/pdf", "Authorization": f"Bearer {token}"}
            params = {"kind": "lab_pdf", "patient_id": patient_uuid(LAB)}
            accepted = client.post(
                "/documents", params=params, content=pdf_bytes(LAB), headers=headers
            )
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["write_back"] is None  # nothing has ended yet
            document_id = accepted.json()["document_id"]
            task = document_store.DOCUMENTS.jobs._tasks[document_id]
            deadline = time.monotonic() + 60
            while not task.done() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert task.done()

            status = client.get(
                f"/documents/{document_id}/extraction",
                params={"patient_id": patient_uuid(LAB)},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert status.status_code == 200, status.text
            body = status.json()
            assert body["status"] == "complete"
            write_back = body["write_back"]
            assert write_back["decision"] == "written"
            assert (write_back["conflict"], write_back["already_in_chart"]) == (1, 1)
            assert write_back["created"] == len(writer.calls[0][1].results) - 2
            [conflict] = write_back["conflicts"]
            assert conflict["chart_value"] == "98765.4321"
            assert conflict["chart_procedure_result_id"] == 777
            assert conflict["bbox"]["page"] == conflict["page"]

            # A re-upload of the same bytes is a duplicate that owes no
            # retry: the upload response carries the same summary at once.
            again = client.post(
                "/documents", params=params, content=pdf_bytes(LAB), headers=headers
            )
            assert again.status_code == 200 and again.json()["write_back"] == write_back
            assert len(writer.calls) == 1
    finally:
        upload_guard.reset_for_tests()


def test_an_intake_form_has_no_write_back_to_report(monkeypatch: pytest.MonkeyPatch):
    async def patient_exists(token: str, patient_id: str) -> None:
        return None

    monkeypatch.setattr(fhir_client, "ensure_patient_exists", patient_exists)
    monkeypatch.setattr(
        document_store, "DOCUMENTS", DocumentStore(vision=ground_truth_vision(INTAKE))
    )
    monkeypatch.setattr(documents, "fhir_chart_reader", lambda token, **_: reader_for(INTAKE))
    upload_guard.reset_for_tests()
    token = _jwt()
    try:
        with TestClient(app) as client:
            headers = {"Content-Type": "application/pdf", "Authorization": f"Bearer {token}"}
            accepted = client.post(
                "/documents",
                params={"kind": "intake_form", "patient_id": patient_uuid(INTAKE)},
                content=pdf_bytes(INTAKE),
                headers=headers,
            )
            assert accepted.status_code == 200, accepted.text
            document_id = accepted.json()["document_id"]
            task = document_store.DOCUMENTS.jobs._tasks[document_id]
            deadline = time.monotonic() + 60
            while not task.done() and time.monotonic() < deadline:
                time.sleep(0.05)
            status = client.get(
                f"/documents/{document_id}/extraction",
                params={"patient_id": patient_uuid(INTAKE)},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert status.json()["status"] == "complete"
            assert status.json()["write_back"] is None
    finally:
        upload_guard.reset_for_tests()
