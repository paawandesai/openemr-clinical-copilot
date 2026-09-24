"""
The intake-extractor's entry points.

    report = await attach_and_extract(patient_uuid, pdf, DocumentKind.LAB_PDF)

    jobs = ExtractionJobs()
    pending = jobs.submit(patient_uuid, pdf, DocumentKind.INTAKE_FORM)   # returns at once
    jobs.status(pending.document_id)      # pending | in_progress | complete | failed
    jobs.report(pending.document_id)      # the ExtractionReport, once terminal

**D7: extraction runs at upload, not at question time.** `ExtractionJobs`
is the upload-time path: `submit` starts the pipeline as an asyncio task
and returns a `pending` `IntakeExtractorOutput` immediately. The ask path
only ever calls `status` / `report`, which never wait and never start
work: before the task finishes they answer `pending` / `in_progress` with
no payload (the contract forbids one), and a question about that document
gets D7's explicit "still processing", never a partial extraction.
The store is in-process memory: it is lost on restart and not shared
between workers, which is recorded as an open item in the session report
rather than papered over.

**Graceful degradation.** `attach_and_extract` never raises. Every expected
failure — an unreadable file, a non-PDF, no text layer with OCR
unavailable, a failed or refused vision call, output that does not parse,
zero located fields, too many pages, a timeout — comes back as
`status=failed` with an `ExtractionFailureReason` the caller can show. An
unexpected exception comes back as `internal_error`, logged by exception
type only. There is never a payload on a failure, so nothing fabricated can
ride along with one.

**Observability.** One correlation id per extraction (the caller's, or a
new UUID4), bound for the whole run so every line the steps emit carries
it — the vision call's `llm_usage_event`, each step's
`intake_extraction_step`, and the closing `intake_extraction_completed` /
`intake_extraction_failed` summary. Those carry step sequence, per-step
latency, tokens and cost, located/unlocated counts, mean confidence and the
cross-check and discrepancy counts — and no document text, extracted
value, patient identifier or image (CLAUDE.md invariant 1;
`ExtractionReport.log_summary`).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from statistics import fmean

from app.graph.intake_extractor import (
    INTAKE_EXTRACTOR_GRAPH,
    IntakeExtractorContext,
    IntakeExtractorState,
)
from app.middleware.correlation import correlation_scope, get_correlation_id
from app.middleware.logging import log_event
from app.models.w2.documents import DocumentKind, ExtractionStatus
from app.models.w2.workers import ExtractionFailureReason, IntakeExtractorOutput
from app.workers.intake_extractor.chart import ChartReader
from app.workers.intake_extractor.report import (
    ExtractionReport,
    FieldConfidence,
    VisionCost,
    unsupported_fields,
)
from app.workers.intake_extractor.vision import VisionExtractor, extract_with_claude

#: A step run on the finished report, e.g. the upload's lab write-back (D54).
AfterExtraction = Callable[[ExtractionReport], Awaitable[object]]


def _new_correlation_id() -> str:
    return str(uuid.uuid4())


def _read_input(document: bytes | str | Path) -> tuple[bytes | None, str]:
    if isinstance(document, bytes):
        return document, "upload.pdf"
    path = Path(document)
    try:
        return path.read_bytes(), path.name
    except OSError:
        return None, path.name or "upload.pdf"


def _failed_output(
    kind: DocumentKind,
    document_id: str,
    reason: ExtractionFailureReason,
    correlation_id: str,
) -> IntakeExtractorOutput:
    return IntakeExtractorOutput(
        status=ExtractionStatus.FAILED,
        document_id=document_id,
        kind=kind,
        failure_reason=reason,
        correlation_id=correlation_id,
    )


def _report_from_state(
    state: IntakeExtractorState, kind: DocumentKind, fallback_id: str, correlation_id: str
) -> ExtractionReport:
    document = state.get("document")
    document_id = document.document_id if document else fallback_id
    step_ms = {
        name: round(seconds * 1000) for name, seconds in (state.get("node_seconds") or {}).items()
    }
    vision_result = state.get("vision_result")
    vision = VisionCost()
    if vision_result is not None:
        usage = vision_result.usage
        vision = VisionCost(
            calls=usage.calls,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_creation_input_tokens=usage.cache_creation_input_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens,
            latency_ms=usage.latency_ms,
            served_models=list(usage.served_models),
            cost_usd=usage.cost_usd,
        )
    assembled = state.get("assembled")
    fields = [
        FieldConfidence(
            field_path=record.field_path,
            schema_path=record.schema_path,
            value_class=record.value_class.value,
            outcome=record.outcome,
            match_tier=record.match_kind.value if record.match_kind else None,
            score=record.score,
            reader_confidence=record.reader_confidence,
            confidence=record.confidence,
        )
        for record in (assembled.records if assembled else [])
    ]
    scored = [field.confidence for field in fields if field.confidence is not None]
    common = {
        "fields": fields,
        "parse_failures": list(assembled.parse_failures) if assembled else [],
        "loinc": [(key, outcome.value) for key, outcome in assembled.loinc] if assembled else [],
        "mean_confidence": round(fmean(scored), 4) if scored else None,
        "vision": vision,
        "step_ms": step_ms,
        "page_count": document.page_count if document else None,
        "has_text_layer": document.has_text_layer if document else None,
    }

    failure = state.get("failure")
    if failure is not None:
        return ExtractionReport(
            output=_failed_output(kind, document_id, failure, correlation_id), **common
        )

    assert assembled is not None
    extraction = assembled.extraction
    return ExtractionReport(
        output=IntakeExtractorOutput(
            status=ExtractionStatus.COMPLETE,
            document_id=document_id,
            kind=kind,
            extraction_id=extraction.extraction_id,
            payload=extraction,
            correlation_id=correlation_id,
        ),
        unsupported=unsupported_fields(extraction),
        flag_cross_check=state.get("flag_cross_check"),
        identity=state.get("identity"),
        discrepancies=state.get("discrepancies"),
        **common,
    )


async def attach_and_extract(
    patient_uuid: str,
    document: bytes | str | Path,
    doc_type: DocumentKind,
    *,
    vision: VisionExtractor | None = None,
    chart_reader: ChartReader | None = None,
    document_id: str | None = None,
    correlation_id: str | None = None,
) -> ExtractionReport:
    """
    Extract one lab PDF or intake form for one patient. Never raises.

    `vision` defaults to the real Anthropic call; `chart_reader` is the
    Week 1 FHIR client under the uploading clinician's token
    (`chart.fhir_chart_reader(token)`) — without one, the identity check is
    `not_comparable` and the discrepancy check reports `not_run`.
    `document_id` is OpenEMR's id for the stored document when the upload
    has one; otherwise a SHA3-512-derived id stands in.
    """
    correlation_id = correlation_id or get_correlation_id() or _new_correlation_id()
    fallback_id = document_id or "unread-document"
    with correlation_scope(correlation_id):
        pdf, filename = _read_input(document)
        if pdf is None:
            report = ExtractionReport(
                output=_failed_output(
                    doc_type, fallback_id, ExtractionFailureReason.UNREADABLE_FILE, correlation_id
                )
            )
            _log_outcome(report)
            return report

        context = IntakeExtractorContext(
            patient_uuid=patient_uuid,
            pdf=pdf,
            kind=doc_type,
            vision=vision or extract_with_claude,
            chart_reader=chart_reader,
            document_id=document_id,
            filename=filename,
        )
        try:
            state = await INTAKE_EXTRACTOR_GRAPH.ainvoke({"node_seconds": {}}, context=context)
            report = _report_from_state(state, doc_type, fallback_id, correlation_id)
        except Exception as error:  # noqa: BLE001 - the caller gets a typed failure, never this
            log_event(
                "intake_extraction_internal_error",
                event_data={"correlation_id": correlation_id, "error_type": type(error).__name__},
            )
            report = ExtractionReport(
                output=_failed_output(
                    doc_type, fallback_id, ExtractionFailureReason.INTERNAL_ERROR, correlation_id
                )
            )
        _log_outcome(report)
        return report


def _log_outcome(report: ExtractionReport) -> None:
    summary = report.log_summary()
    for step, milliseconds in report.step_ms.items():
        log_event(
            "intake_extraction_step",
            event_data={
                "correlation_id": report.output.correlation_id,
                "step": step,
                "latency_ms": milliseconds,
            },
        )
    event = "intake_extraction_completed" if report.is_complete else "intake_extraction_failed"
    log_event(event, event_data={"correlation_id": report.output.correlation_id, **summary})


def _extraction_correlation_id(document_id: str) -> str:
    """
    The id the background extraction runs under.

    The uploading request's id when it is a UUID, so the extraction stays on
    the encounter's trace (docs/w2/OBSERVABILITY.md §2, D36).
    `IntakeExtractorOutput.correlation_id` must be a UUID, and the middleware
    also accepts other identifier-shaped ids (`corr-…`, k6's ids). For those,
    and outside any request, a fresh UUID. When one replaces the request's
    id, the link is logged under the request's id, so the two traces still
    join.
    """
    inherited = get_correlation_id()
    try:
        if inherited is not None and str(uuid.UUID(inherited)) == inherited.lower():
            return inherited
    except ValueError:
        pass
    fresh = _new_correlation_id()
    if inherited is not None:
        log_event(
            "w2_extraction_correlation_replaced",
            {"document_id": document_id, "extraction_correlation_id": fresh},
        )
    return fresh


class ExtractionJobs:
    """
    Upload-time extraction with a status the ask path can read (D7).

    Keyed by the caller's `document_id` (OpenEMR's id for the stored file),
    which `submit` therefore requires: the ask path looks a document up by
    the id the chart knows it under.
    """

    def __init__(
        self,
        *,
        vision: VisionExtractor | None = None,
        chart_reader: ChartReader | None = None,
    ) -> None:
        self._vision = vision
        self._chart_reader = chart_reader
        self._status: dict[str, IntakeExtractorOutput] = {}
        self._reports: dict[str, ExtractionReport] = {}
        self._tasks: dict[str, asyncio.Task[ExtractionReport]] = {}

    def submit(
        self,
        patient_uuid: str,
        document: bytes | str | Path,
        doc_type: DocumentKind,
        *,
        document_id: str,
        chart_reader: ChartReader | None = None,
        force_reextract: bool = False,
        after_extraction: AfterExtraction | None = None,
    ) -> IntakeExtractorOutput:
        """
        Start extraction and return at once. Must be called on a running event loop.

        `after_extraction` runs once the report is stored and the status is
        terminal, inside the same task and correlation id — the upload
        route's lab write-back (D54). It must not raise; the status a
        question reads is already final before it starts.
        """
        existing = self._status.get(document_id)
        if existing is not None and not force_reextract:
            return existing
        correlation_id = _extraction_correlation_id(document_id)
        pending = IntakeExtractorOutput(
            status=ExtractionStatus.PENDING,
            document_id=document_id,
            kind=doc_type,
            correlation_id=correlation_id,
        )
        self._status[document_id] = pending
        self._reports.pop(document_id, None)

        async def run() -> ExtractionReport:
            self._status[document_id] = pending.model_copy(
                update={"status": ExtractionStatus.IN_PROGRESS}
            )
            report = await attach_and_extract(
                patient_uuid,
                document,
                doc_type,
                vision=self._vision,
                chart_reader=chart_reader or self._chart_reader,
                document_id=document_id,
                correlation_id=correlation_id,
            )
            self._reports[document_id] = report
            self._status[document_id] = report.output
            if after_extraction is not None:
                with correlation_scope(correlation_id):
                    await after_extraction(report)
            return report

        self._tasks[document_id] = asyncio.get_running_loop().create_task(run())
        return pending

    def status(self, document_id: str) -> IntakeExtractorOutput | None:
        """Never waits. None for a document never submitted."""
        return self._status.get(document_id)

    def report(self, document_id: str) -> ExtractionReport | None:
        """The finished report, or None while pending / in progress."""
        return self._reports.get(document_id)

    async def wait(self, document_id: str) -> ExtractionReport | None:
        """For the upload path and tests only — the ask path must never call this (D7)."""
        task = self._tasks.get(document_id)
        return await task if task is not None else self._reports.get(document_id)
