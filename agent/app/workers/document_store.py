"""
Uploaded documents, as the ask path sees them — D7's upload-time extraction
with the two things `ExtractionJobs` does not keep: which patient a document
was uploaded to, and the page geometry the answer view draws boxes on.

    store = DocumentStore()
    stored, output, duplicate = store.accept(patient_uuid, pdf, kind, chart_reader=reader)
    store.lookup(document_id, patient_uuid)   # None for another patient's document
    store.status(document_id)                 # never waits (D7)
    store.report(document_id)                 # the ExtractionReport once terminal

**Which patient a document belongs to is decided by the upload**, not by
reading the paper (`DocumentPatientHeader`'s own rule). `lookup` refuses a
document for any other patient with the same `None` it returns for an
unknown id, so a caller cannot learn that an id exists on another chart —
the ui-integration 404 rule, applied again on the agent side.

**The same bytes are the same document** (D1: the id *is* the SHA3-512
digest). Re-uploading to the same patient returns the existing document and
its current status. Uploading them to a different patient is refused with
`DocumentBoundElsewhere`: rebinding would move an extraction — and every
citation into it — from one chart to another.

**Saved to disk, so a restart forgets nothing (D60).** With an
`ExtractionStore` (app/workers/extraction_store.py, configured by
`configure_from_env` at service start) every accepted upload is saved with
its patient binding, and saved again with its report when the extraction
ends. A document this process does not hold is read back from the store
the first time anything asks about it — `accept`, `lookup`, `status`,
`report` — so after a restart the same document answers with the same
report, stays bound to the same chart, and the same bytes for another
chart are still refused. An extraction the restart cut short comes back
`failed` / `interrupted`; uploading the file again starts it afresh.
Without a store (tests, scripts, a dev process with no path set) this is
in-process memory, as before D60.

Not shared between workers either way: a job running in one process is
invisible to another (D40 item 10). The bytes are held by the extraction
task until it finishes and nowhere else — never saved.

**A document ruled another patient's releases its binding (D60, amending
D40 item 8).** When a finished extraction's identity check found a name or
date of birth that contradicts the chart it was uploaded to (the hard flag
the module quarantines on), that chart's claim on the bytes is released:
the same bytes uploaded to another chart are accepted and extracted afresh
for that chart, whose own identity check then decides whether anything is
stated or written. A binding whose extraction matched, could not compare,
failed or is still running still refuses another chart.

**A write-back that did not complete is retried by a re-upload (D60).** The
lab write-back's outcome (D54) is kept with the document, on disk too. A
re-upload of the same bytes to the same chart, when the extraction is
complete and its write-back ended short of `written` or of a reason not to
write (the endpoint unavailable, the token or request refused, or never
finished), runs the write-back again on the saved report under the new
upload's token. The ledger's keys (D1) make a repeat write idempotent. One
write-back per document runs at a time.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import pymupdf

from app.middleware.logging import log_event
from app.models.w2.answer_view import PageGeometry
from app.models.w2.documents import (
    DocumentChecksums,
    DocumentKind,
    ExtractionStatus,
    SourceDocument,
)
from app.models.w2.workers import ExtractionFailureReason, IntakeExtractorOutput
from app.models.w2.writeback import document_idempotency_key
from app.workers.extraction_store import ExtractionStore, SavedExtraction, open_from_env
from app.workers.intake_extractor.chart import ChartReader
from app.workers.intake_extractor.report import ExtractionReport
from app.workers.intake_extractor.vision import VisionExtractor
from app.workers.intake_extractor.worker import AfterExtraction, ExtractionJobs
from app.writeback.after_extraction import write_back_owed

#: The file name the agent records. The uploaded name is frequently the
#: patient's name, so it is never kept (app/api/documents.py).
STORED_FILENAME = "upload.pdf"


class UnreadablePdf(ValueError):
    """The bytes do not open as a PDF. The message carries nothing from them."""


class DocumentBoundElsewhere(ValueError):
    """These bytes were already uploaded to a different patient."""


@dataclass(frozen=True)
class StoredDocument:
    document: SourceDocument
    patient_uuid: str
    pages: tuple[PageGeometry, ...]


def read_geometry(pdf: bytes) -> tuple[int, tuple[PageGeometry, ...], bool]:
    """Page count, per-page geometry, and whether every page has a text layer."""
    try:
        with pymupdf.open(stream=pdf, filetype="pdf") as handle:
            pages = tuple(
                PageGeometry(
                    page=index + 1,
                    width_pt=page.rect.width,
                    height_pt=page.rect.height,
                    rotation=page.rotation,
                )
                for index, page in enumerate(handle)
            )
            has_text = all(page.get_text("text").strip() for page in handle)
    except Exception as error:  # noqa: BLE001 - any PyMuPDF failure is an unreadable file
        raise UnreadablePdf("the upload could not be opened as a PDF") from error
    if not pages:
        raise UnreadablePdf("the upload has no pages")
    return len(pages), pages, has_text


class DocumentStore:
    def __init__(
        self,
        *,
        vision: VisionExtractor | None = None,
        jobs: ExtractionJobs | None = None,
        persistence: ExtractionStore | None = None,
    ) -> None:
        self.jobs = jobs if jobs is not None else ExtractionJobs(vision=vision)
        self.persistence = persistence
        self._documents: dict[str, StoredDocument] = {}
        # asyncio holds tasks weakly; these must outlive the request.
        self._watchers: set[asyncio.Task[object]] = set()
        #: Each document's last write-back outcome (a WriteBackDecision
        #: value), None when none has finished.
        self._writeback: dict[str, str | None] = {}
        #: Documents whose write-back is running now, first run or retry.
        self._writing: set[str] = set()

    def accept(
        self,
        patient_uuid: str,
        pdf: bytes,
        kind: DocumentKind,
        *,
        chart_reader: ChartReader | None = None,
        on_finished: Callable[[], None] | None = None,
        after_extraction: AfterExtraction | None = None,
    ) -> tuple[StoredDocument, IntakeExtractorOutput, bool]:
        """
        Register the upload and start extracting it (D7). Returns at once,
        on a running event loop. `(stored, status, duplicate)`.

        `on_finished` is called exactly once, when no extraction this call
        started is running any more: when the one it started ends — complete,
        failed, raised or cancelled — or before this returns or raises, if it
        started none (a duplicate, `DocumentBoundElsewhere`, `UnreadablePdf`).
        The upload route passes the release of its extraction slot
        (app/api/upload_guard.py), which is what makes the concurrency cap
        cover the background job and not only the body read.

        `after_extraction` runs on the finished report inside the job this
        call started. A duplicate upload starts no job; it runs
        `after_extraction` only to retry a write-back that did not complete
        (D60), and `on_finished` then waits for that retry. The upload route
        passes the lab write-back (D54); nothing else does, so a store built
        in a test never writes.
        """
        handed_off = False
        try:
            stored, output, duplicate, pending = self._accept(
                patient_uuid, pdf, kind, chart_reader, after_extraction
            )
            if pending is not None and on_finished is not None:
                self._call_when_done(pending, on_finished)
                handed_off = True
            return stored, output, duplicate
        finally:
            if not handed_off and on_finished is not None:
                on_finished()

    def _accept(
        self,
        patient_uuid: str,
        pdf: bytes,
        kind: DocumentKind,
        chart_reader: ChartReader | None,
        after_extraction: AfterExtraction | None = None,
    ) -> tuple[StoredDocument, IntakeExtractorOutput, bool, Callable[[], Awaitable[object]] | None]:
        """
        `accept` without the callback. The last element waits for whatever
        this call started — a job or a write-back retry — or is None.
        """
        digest = hashlib.sha3_512(pdf).hexdigest()
        document_id = document_idempotency_key(digest)
        existing = self._known(document_id)
        restart = False
        rebind = False
        if existing is not None:
            if existing.patient_uuid != patient_uuid:
                if not self._ruled_another_patients(document_id):
                    raise DocumentBoundElsewhere("this file is attached to a different chart")
                # D60: the first chart's claim is released; this chart's own
                # extraction and identity check decide from here.
                rebind = True
                log_event("w2_document_binding_released", {"document_id": document_id})
            else:
                status = self.jobs.status(document_id)
                if status is not None and not _interrupted(status):
                    retry = self._retry_write_back(existing, after_extraction)
                    return existing, status, True, retry
                # A job a restart cut short is started again by the re-upload.
                restart = status is not None

        page_count, pages, has_text = read_geometry(pdf)
        stored = StoredDocument(
            document=SourceDocument(
                document_id=document_id,
                kind=kind,
                filename=STORED_FILENAME,
                mime_type="application/pdf",
                page_count=page_count,
                byte_length=len(pdf),
                checksums=DocumentChecksums(sha3_512=digest),
                has_text_layer=has_text,
            ),
            patient_uuid=patient_uuid,
            pages=pages,
        )
        self._documents[document_id] = stored
        self._writeback.pop(document_id, None)
        output = self.jobs.submit(
            patient_uuid,
            pdf,
            kind,
            document_id=document_id,
            chart_reader=chart_reader,
            force_reextract=restart or rebind,
            after_extraction=(
                self._tracked(stored, after_extraction) if after_extraction is not None else None
            ),
            on_report=lambda report: self._save(stored, report.output, report),
        )
        # Saved before the job's first step can run: the binding is on disk
        # from the moment the upload is accepted (the 409 rule, D40 item 8).
        self._save(stored, output, None)
        return (
            stored,
            output,
            existing is not None and not rebind,
            lambda: self.jobs.wait(document_id),
        )

    def _ruled_another_patients(self, document_id: str) -> bool:
        """The finished extraction's identity check contradicts its chart: the hard flag."""
        report = self.jobs.report(document_id)
        return (
            report is not None
            and report.is_complete
            and report.identity is not None
            and report.identity.hard_flag
        )

    def _tracked(self, stored: StoredDocument, step: AfterExtraction) -> AfterExtraction:
        """`step`, recording its outcome with the document, in memory and on disk."""
        document_id = stored.document.document_id

        async def run(report: ExtractionReport) -> object:
            self._writing.add(document_id)
            try:
                decision = await step(report)
            finally:
                self._writing.discard(document_id)
            value = None if decision is None else str(getattr(decision, "value", decision))
            # Only while the document is still bound as it was: a binding
            # released meanwhile belongs to another chart's extraction now.
            if self._documents.get(document_id) is stored:
                self._writeback[document_id] = value
                self._save(stored, report.output, report, writeback=value)
            return decision

        return run

    def _retry_write_back(
        self, stored: StoredDocument, after_extraction: AfterExtraction | None
    ) -> Callable[[], Awaitable[object]] | None:
        """Start the write-back again on the saved report, when one is owed (D60)."""
        document_id = stored.document.document_id
        report = self.jobs.report(document_id)
        previous = self._writeback.get(document_id)
        if (
            after_extraction is None
            or report is None
            or not report.is_complete
            or document_id in self._writing
            or not write_back_owed(previous)
        ):
            return None
        log_event("w2_writeback_retry_started", {"document_id": document_id, "previous": previous})
        self._writing.add(document_id)  # held until the tracked step takes over
        step = self._tracked(stored, after_extraction)

        async def retry() -> object:
            self._writing.discard(document_id)
            return await step(report)

        task: asyncio.Task[object] = asyncio.get_running_loop().create_task(retry())
        self._watchers.add(task)
        task.add_done_callback(self._watchers.discard)
        return lambda: task

    def _known(self, document_id: str) -> StoredDocument | None:
        """
        The document, from memory or else from the store (D60). A saved one
        is put back in memory, with its status and report, the first time.
        """
        stored = self._documents.get(document_id)
        if stored is not None or self.persistence is None:
            return stored
        saved = self.persistence.load(document_id)
        if saved is None:
            return None
        stored = StoredDocument(
            document=saved.document, patient_uuid=saved.patient_uuid, pages=saved.pages
        )
        self._documents[document_id] = stored
        self.jobs.restore(document_id, _as_restored(saved), saved.report)
        self._writeback[document_id] = saved.writeback
        log_event(
            "w2_document_restored",
            {
                "document_id": document_id,
                "status": self.jobs.status(document_id).status.value,  # type: ignore[union-attr]
                "has_report": saved.report is not None,
            },
        )
        return stored

    def _save(
        self,
        stored: StoredDocument,
        output: IntakeExtractorOutput,
        report: ExtractionReport | None,
        writeback: str | None = None,
    ) -> None:
        """
        Write the document's row. Never raises: a failed write leaves this
        process correct and is logged; after a restart the document is then
        unknown, as it was before D60, and a re-upload extracts it again.
        """
        if self.persistence is None:
            return
        try:
            self.persistence.save(
                patient_uuid=stored.patient_uuid,
                document=stored.document,
                pages=stored.pages,
                output=output,
                report=report,
                writeback=writeback,
            )
        except Exception as error:  # noqa: BLE001 - the upload and the job carry on
            log_event(
                "extraction_store_save_failed",
                {
                    "document_id": stored.document.document_id,
                    "status": output.status.value,
                    "error_type": type(error).__name__,
                },
            )

    def _call_when_done(
        self, pending: Callable[[], Awaitable[object]], callback: Callable[[], None]
    ) -> None:
        """
        Run `callback` once what `pending` waits for has ended, however it
        ended: the job (through the public `jobs.wait`, so the job itself is
        not changed) or a write-back retry. A done-callback fires on
        cancellation too, which a `finally` inside the job would not if it
        were cancelled before it first ran.
        """

        async def watch() -> object:
            return await pending()

        def finished(watcher: asyncio.Task[object]) -> None:
            self._watchers.discard(watcher)
            if not watcher.cancelled():
                # Retrieved so asyncio does not report it; the job's own
                # outcome is its status, which the status route reads.
                watcher.exception()
            callback()

        watcher: asyncio.Task[object] = asyncio.get_running_loop().create_task(watch())
        self._watchers.add(watcher)
        watcher.add_done_callback(finished)

    def lookup(self, document_id: str, patient_uuid: str) -> StoredDocument | None:
        stored = self._known(document_id)
        if stored is None or stored.patient_uuid != patient_uuid:
            return None
        return stored

    def status(self, document_id: str) -> IntakeExtractorOutput | None:
        """Never waits (D7)."""
        self._known(document_id)
        return self.jobs.status(document_id)

    def report(self, document_id: str) -> ExtractionReport | None:
        self._known(document_id)
        return self.jobs.report(document_id)

    async def wait(self, document_id: str) -> ExtractionReport | None:
        """Upload path, scripts and tests only. The ask path never calls this (D7)."""
        return await self.jobs.wait(document_id)

    def clear(self) -> None:
        """Tests only. Forgets this process's memory; a store's rows stay on disk."""
        self._documents.clear()
        self._writeback.clear()
        self._writing.clear()
        self.jobs = ExtractionJobs(vision=self.jobs._vision)


def _interrupted(output: IntakeExtractorOutput) -> bool:
    return output.failure_reason is ExtractionFailureReason.INTERRUPTED


def _as_restored(saved: SavedExtraction) -> IntakeExtractorOutput:
    """
    The status a saved document answers with after a restart: its report's
    when it finished, otherwise `failed` / `interrupted` — the job that was
    running is gone, and saying `pending` would have the card poll forever.
    """
    if saved.report is not None:
        return saved.report.output
    if saved.output.status in (ExtractionStatus.COMPLETE, ExtractionStatus.FAILED):
        return saved.output
    return IntakeExtractorOutput(
        status=ExtractionStatus.FAILED,
        document_id=saved.output.document_id,
        kind=saved.output.kind,
        failure_reason=ExtractionFailureReason.INTERRUPTED,
        correlation_id=saved.output.correlation_id,
    )


#: The process's store. The upload route writes to it and the ask path reads it.
DOCUMENTS = DocumentStore()


def configure_from_env() -> None:
    """
    Give the process's store the on-disk store the environment configures
    (D60). Called once by app/main.py at import, so a misconfigured
    production service does not start; tests and scripts never call it and
    stay in memory.
    """
    DOCUMENTS.persistence = open_from_env()
