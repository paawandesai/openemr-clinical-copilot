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

**In-process memory, like `ExtractionJobs`.** Lost on restart and not shared
between workers; recorded as an open item rather than hidden. The bytes are
held by the extraction task until it finishes and nowhere else.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass

import pymupdf

from app.models.w2.answer_view import PageGeometry
from app.models.w2.documents import DocumentChecksums, DocumentKind, SourceDocument
from app.models.w2.workers import IntakeExtractorOutput
from app.models.w2.writeback import document_idempotency_key
from app.workers.intake_extractor.chart import ChartReader
from app.workers.intake_extractor.report import ExtractionReport
from app.workers.intake_extractor.vision import VisionExtractor
from app.workers.intake_extractor.worker import AfterExtraction, ExtractionJobs

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
    ) -> None:
        self.jobs = jobs if jobs is not None else ExtractionJobs(vision=vision)
        self._documents: dict[str, StoredDocument] = {}
        # asyncio holds tasks weakly; these must outlive the request.
        self._watchers: set[asyncio.Task[object]] = set()

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

        `after_extraction` runs on the finished report inside the job, only
        for a job this call started — a duplicate upload starts none, so it
        runs nothing. The upload route passes the lab write-back (D54);
        nothing else does, so a store built in a test never writes.
        """
        handed_off = False
        try:
            stored, output, duplicate, started = self._accept(
                patient_uuid, pdf, kind, chart_reader, after_extraction
            )
            if started and on_finished is not None:
                self._call_when_done(stored.document.document_id, on_finished)
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
    ) -> tuple[StoredDocument, IntakeExtractorOutput, bool, bool]:
        """`accept` without the callback; the last element is whether a job started."""
        digest = hashlib.sha3_512(pdf).hexdigest()
        document_id = document_idempotency_key(digest)
        existing = self._documents.get(document_id)
        if existing is not None:
            if existing.patient_uuid != patient_uuid:
                raise DocumentBoundElsewhere("this file is attached to a different chart")
            status = self.jobs.status(document_id)
            if status is not None:
                return existing, status, True, False

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
        output = self.jobs.submit(
            patient_uuid,
            pdf,
            kind,
            document_id=document_id,
            chart_reader=chart_reader,
            after_extraction=after_extraction,
        )
        return stored, output, existing is not None, True

    def _call_when_done(self, document_id: str, callback: Callable[[], None]) -> None:
        """
        Run `callback` once the job for `document_id` has ended, however it
        ended. Watches through the public `jobs.wait`, so the job itself is
        not changed; a done-callback fires on cancellation too, which a
        `finally` inside the job would not if it were cancelled before it
        first ran.
        """

        def finished(watcher: asyncio.Task[object]) -> None:
            self._watchers.discard(watcher)
            if not watcher.cancelled():
                # Retrieved so asyncio does not report it; the job's own
                # outcome is its status, which the status route reads.
                watcher.exception()
            callback()

        watcher: asyncio.Task[object] = asyncio.get_running_loop().create_task(
            self.jobs.wait(document_id)
        )
        self._watchers.add(watcher)
        watcher.add_done_callback(finished)

    def lookup(self, document_id: str, patient_uuid: str) -> StoredDocument | None:
        stored = self._documents.get(document_id)
        if stored is None or stored.patient_uuid != patient_uuid:
            return None
        return stored

    def status(self, document_id: str) -> IntakeExtractorOutput | None:
        """Never waits (D7)."""
        return self.jobs.status(document_id)

    def report(self, document_id: str) -> ExtractionReport | None:
        return self.jobs.report(document_id)

    async def wait(self, document_id: str) -> ExtractionReport | None:
        """Upload path, scripts and tests only. The ask path never calls this (D7)."""
        return await self.jobs.wait(document_id)

    def clear(self) -> None:
        """Tests only."""
        self._documents.clear()
        self.jobs = ExtractionJobs(vision=self.jobs._vision)


#: The process's store. The upload route writes to it and the ask path reads it.
DOCUMENTS = DocumentStore()
