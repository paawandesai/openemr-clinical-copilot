"""
Two D60 amendments to how an upload is accepted (app/workers/document_store.py):

- **A document ruled another patient's releases its binding** (D40 item 8,
  amended). Wrong chart -> the identity check's hard flag -> the same bytes
  on the right chart are accepted and extracted for it. Anything short of
  that hard flag still refuses another chart.
- **A re-upload retries a write-back that did not complete** (D54, amended),
  on the saved report, under the new upload's token; a completed one is not
  run again, and one runs at a time.

The writer is the real `write_back_after_extraction` step over a scripted
endpoint, so what is retried is the step the upload route passes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.models.w2.documents import DocumentKind, ExtractionStatus
from app.models.w2.writeback import (
    LabWriteBackRequest,
    LabWriteBackResponse,
    LabWriteBackResultOutcome,
    WriteOutcome,
)
from app.workers.document_store import DocumentBoundElsewhere, DocumentStore
from app.workers.extraction_store import ExtractionStore
from app.writeback.after_extraction import WriteBackDecision, write_back_after_extraction
from app.writeback.exceptions import WriteBackUnavailable
from tests.intake_extractor_support import chart_for, ground_truth_vision, pdf_bytes
from tests.w2_graph_support import LAB_SAMPLE, OTHER_PATIENT, PATIENT

KEY = bytes(range(32))
PDF = pdf_bytes(LAB_SAMPLE)
#: A chart whose patient is not the one printed on lab-clean-001.
WRONG_CHART = "intake-clean-004"


def reader(sample: str):  # noqa: ANN201 - a ChartReader
    async def read(uuid: str):  # noqa: ANN202
        return chart_for(sample)

    return read


class CountingVision:
    def __init__(self) -> None:
        self._inner = ground_truth_vision(LAB_SAMPLE)
        self.calls = 0

    async def __call__(self, kind, pdf, page_count):  # noqa: ANN001, ANN204
        self.calls += 1
        return await self._inner(kind, pdf, page_count)


class FlakyEndpoint:
    """Unavailable for the first `failures` calls, then answers every row as created."""

    def __init__(self, failures: int = 1) -> None:
        self.failures = failures
        self.calls: list[tuple[str, LabWriteBackRequest]] = []

    async def __call__(self, token: str, request: LabWriteBackRequest) -> LabWriteBackResponse:
        self.calls.append((token, request))
        if len(self.calls) <= self.failures:
            raise WriteBackUnavailable("connection reset")
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


def write_back(endpoint: FlakyEndpoint, token: str, patient: str = PATIENT):  # noqa: ANN201
    """The upload route's step (lab_write_back), with the endpoint scripted."""

    async def step(report):  # noqa: ANN001, ANN202
        return await write_back_after_extraction(
            report, token=token, patient_uuid=patient, pdf=PDF, writer=endpoint
        )

    return step


async def upload(
    documents: DocumentStore,
    patient: str,
    chart: str,
    step=None,  # noqa: ANN001
):  # noqa: ANN202
    """accept, then wait for everything the call started (job or retry)."""
    done = asyncio.Event()
    stored, output, duplicate = documents.accept(
        patient,
        PDF,
        DocumentKind.LAB_PDF,
        chart_reader=reader(chart),
        after_extraction=step,
        on_finished=done.set,
    )
    await asyncio.wait_for(done.wait(), 30)
    return stored.document.document_id, output, duplicate


def store_on(path: Path, vision: CountingVision | None = None) -> DocumentStore:
    return DocumentStore(vision=vision or CountingVision(), persistence=ExtractionStore(path, KEY))


# --------------------------------------------------------------------------
# releasing the binding
# --------------------------------------------------------------------------


def test_wrong_chart_then_quarantine_then_right_chart_is_accepted_and_extracted(
    tmp_path: Path,
) -> None:
    path = tmp_path / "x.sqlite3"
    vision = CountingVision()
    documents = store_on(path, vision)

    async def flow() -> None:
        # 1. Uploaded to the wrong chart: the identity check's hard flag,
        #    which is what the module quarantines on. Nothing is written.
        wrong = FlakyEndpoint(failures=0)
        document_id, _, _ = await upload(
            documents, OTHER_PATIENT, WRONG_CHART, write_back(wrong, "t-1", OTHER_PATIENT)
        )
        report = documents.report(document_id)
        assert report is not None and report.identity is not None and report.identity.hard_flag
        assert wrong.calls == []

        # 2. The same bytes on the chart whose patient they name: accepted,
        #    extracted again for that chart, not treated as a duplicate.
        right = FlakyEndpoint(failures=0)
        again, output, duplicate = await upload(
            documents, PATIENT, LAB_SAMPLE, write_back(right, "t-2")
        )
        assert again == document_id and not duplicate
        assert output.status is ExtractionStatus.PENDING
        report = documents.report(document_id)
        assert report is not None and report.is_complete
        assert report.identity is not None and not report.identity.hard_flag
        assert len(right.calls) == 1 and right.calls[0][1].patient_uuid == PATIENT

        # 3. Bound to the right chart now: the wrong chart no longer sees it,
        #    and a third chart is refused again.
        assert documents.lookup(document_id, PATIENT) is not None
        assert documents.lookup(document_id, OTHER_PATIENT) is None
        with pytest.raises(DocumentBoundElsewhere):
            documents.accept(OTHER_PATIENT, PDF, DocumentKind.LAB_PDF)

    asyncio.run(flow())
    assert vision.calls == 2

    # And the new binding is what a restart sees.
    after = store_on(path)
    document_id = next(iter(after_ids(path)))
    assert after.lookup(document_id, PATIENT) is not None
    assert after.lookup(document_id, OTHER_PATIENT) is None


def after_ids(path: Path) -> list[str]:
    import sqlite3

    with sqlite3.connect(path) as db:
        return [row[0] for row in db.execute("SELECT document_id FROM extraction")]


def test_the_release_survives_a_restart_between_the_two_uploads(tmp_path: Path) -> None:
    path = tmp_path / "x.sqlite3"
    asyncio.run(upload(store_on(path), OTHER_PATIENT, WRONG_CHART))

    async def right_chart() -> bool:
        _, _, duplicate = await upload(store_on(path), PATIENT, LAB_SAMPLE)
        return duplicate

    assert asyncio.run(right_chart()) is False


@pytest.mark.parametrize(
    ("first_chart", "why"),
    [
        (OTHER_PATIENT, "the identity check matched"),
        (None, "the identity check could not compare"),
    ],
)
def test_anything_short_of_a_hard_flag_still_refuses_another_chart(
    first_chart: str | None, why: str
) -> None:
    documents = DocumentStore(vision=CountingVision())

    async def flow() -> None:
        if first_chart is None:
            # No chart reader: identity not_comparable, not a mismatch.
            stored, _, _ = documents.accept(OTHER_PATIENT, PDF, DocumentKind.LAB_PDF)
            await documents.wait(stored.document.document_id)
        else:
            # lab-clean-001 on its own patient's chart: a match.
            await upload(documents, PATIENT, LAB_SAMPLE)
        with pytest.raises(DocumentBoundElsewhere):
            documents.accept(
                PATIENT if first_chart is None else OTHER_PATIENT, PDF, DocumentKind.LAB_PDF
            )

    asyncio.run(flow())


def test_a_running_extraction_still_refuses_another_chart() -> None:
    gate = asyncio.Event()

    async def held(kind, pdf, pages):  # noqa: ANN001, ANN202
        await gate.wait()
        return await ground_truth_vision(LAB_SAMPLE)(kind, pdf, pages)

    documents = DocumentStore(vision=held)

    async def flow() -> None:
        stored, _, _ = documents.accept(
            OTHER_PATIENT, PDF, DocumentKind.LAB_PDF, chart_reader=reader(WRONG_CHART)
        )
        with pytest.raises(DocumentBoundElsewhere):
            documents.accept(PATIENT, PDF, DocumentKind.LAB_PDF)
        gate.set()
        await documents.wait(stored.document.document_id)

    asyncio.run(flow())


# --------------------------------------------------------------------------
# retrying a write-back
# --------------------------------------------------------------------------


def test_a_transient_failure_is_retried_by_re_uploading_to_the_same_chart() -> None:
    endpoint = FlakyEndpoint(failures=1)
    vision = CountingVision()
    documents = DocumentStore(vision=vision)

    async def flow() -> None:
        await upload(documents, PATIENT, LAB_SAMPLE, write_back(endpoint, "t-first"))
        assert len(endpoint.calls) == 1  # unavailable

        _, output, duplicate = await upload(
            documents, PATIENT, LAB_SAMPLE, write_back(endpoint, "t-second")
        )
        assert duplicate and output.status is ExtractionStatus.COMPLETE
        assert len(endpoint.calls) == 2
        # The retry runs under the re-upload's token, on the same rows and
        # keys: the ledger answers a repeat as duplicate_ignored (D1).
        (first_token, first), (second_token, second) = endpoint.calls
        assert (first_token, second_token) == ("t-first", "t-second")
        assert [r.idempotency_key for r in second.results] == [
            r.idempotency_key for r in first.results
        ]

        # Completed now: a third upload writes nothing.
        await upload(documents, PATIENT, LAB_SAMPLE, write_back(endpoint, "t-third"))
        assert len(endpoint.calls) == 2

    asyncio.run(flow())
    assert vision.calls == 1  # no extraction was repeated


def test_the_owed_retry_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "x.sqlite3"
    endpoint = FlakyEndpoint(failures=1)
    asyncio.run(upload(store_on(path), PATIENT, LAB_SAMPLE, write_back(endpoint, "t-1")))
    assert ExtractionStore(path, KEY).load(after_ids(path)[0]).writeback == (  # type: ignore[union-attr]
        WriteBackDecision.ENDPOINT_UNAVAILABLE.value
    )

    vision = CountingVision()
    asyncio.run(upload(store_on(path, vision), PATIENT, LAB_SAMPLE, write_back(endpoint, "t-2")))
    assert len(endpoint.calls) == 2 and vision.calls == 0
    assert ExtractionStore(path, KEY).load(after_ids(path)[0]).writeback == "written"  # type: ignore[union-attr]

    asyncio.run(upload(store_on(path), PATIENT, LAB_SAMPLE, write_back(endpoint, "t-3")))
    assert len(endpoint.calls) == 2


def test_two_re_uploads_at_once_start_one_retry() -> None:
    endpoint = FlakyEndpoint(failures=1)
    documents = DocumentStore(vision=CountingVision())

    async def flow() -> None:
        await upload(documents, PATIENT, LAB_SAMPLE, write_back(endpoint, "t-1"))
        first, second = asyncio.Event(), asyncio.Event()
        for done in (first, second):
            documents.accept(
                PATIENT,
                PDF,
                DocumentKind.LAB_PDF,
                chart_reader=reader(LAB_SAMPLE),
                after_extraction=write_back(endpoint, "t-2"),
                on_finished=done.set,
            )
        await asyncio.wait_for(asyncio.gather(first.wait(), second.wait()), 30)

    asyncio.run(flow())
    assert len(endpoint.calls) == 2


@pytest.mark.parametrize(
    "decision",
    [
        WriteBackDecision.WRITTEN,
        WriteBackDecision.IDENTITY_NOT_CONFIRMED,
        WriteBackDecision.NOTHING_WRITABLE,
        WriteBackDecision.NOT_LAB_REPORT,
    ],
)
def test_an_outcome_that_completed_or_had_a_reason_not_to_write_is_not_retried(
    decision: WriteBackDecision,
) -> None:
    runs: list[str] = []

    async def step(report):  # noqa: ANN001, ANN202
        runs.append("ran")
        return decision

    documents = DocumentStore(vision=CountingVision())

    async def flow() -> None:
        await upload(documents, PATIENT, LAB_SAMPLE, step)
        await upload(documents, PATIENT, LAB_SAMPLE, step)

    asyncio.run(flow())
    assert runs == ["ran"]


@pytest.mark.parametrize(
    "decision",
    [
        WriteBackDecision.ENDPOINT_UNAVAILABLE,
        WriteBackDecision.TOKEN_REFUSED,
        WriteBackDecision.REQUEST_REFUSED,
    ],
)
def test_every_outcome_that_did_not_complete_is_retried(decision: WriteBackDecision) -> None:
    runs: list[str] = []

    async def step(report):  # noqa: ANN001, ANN202
        runs.append("ran")
        return decision if len(runs) == 1 else WriteBackDecision.WRITTEN

    documents = DocumentStore(vision=CountingVision())

    async def flow() -> None:
        for _ in range(3):
            await upload(documents, PATIENT, LAB_SAMPLE, step)

    asyncio.run(flow())
    assert runs == ["ran", "ran"]


def test_a_re_upload_with_no_write_back_step_retries_nothing() -> None:
    """An intake form's upload passes no step (D2), so there is nothing to retry."""
    endpoint = FlakyEndpoint(failures=1)
    documents = DocumentStore(vision=CountingVision())

    async def flow() -> None:
        await upload(documents, PATIENT, LAB_SAMPLE, write_back(endpoint, "t-1"))
        await upload(documents, PATIENT, LAB_SAMPLE, None)

    asyncio.run(flow())
    assert len(endpoint.calls) == 1
