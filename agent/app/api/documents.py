"""
POST /documents and GET /documents/{document_id}/extraction — upload a
document and start extracting it; read how far the extraction has got.

Work order 11 shipped these as a stub (canned statuses, bytes discarded).
Work order 13 makes them real: the upload runs the intake-extractor worker
(app/workers/intake_extractor/, D3) as a background task on this process,
and the status route reads that task's state. The response shapes are
unchanged — the JavaScript on feat/w2-ui already parses them — except that
`extraction_id` and `failure_reason` are now real values rather than always
null, and both routes take `patient_id` (below).

CHECK ORDER — WHAT IS DECIDED BEFORE THE BODY IS READ
-----------------------------------------------------
The upload is the one route where an unauthenticated caller could make
this service do real work — read up to 25 MiB, then spend OCR CPU and a
vision call — so everything that can refuse a request without its body
runs first, in this order (`app/api/upload_guard.py`, carried over from
`chore/w2-deploy-rehearsal` onto this real route):

1. `authorize_document_request` — an OpenEMR token (401), carrying the
   scopes extraction reads with (403 `insufficient_scope`), accepted by
   OpenEMR for this `patient_id` (401 / 404 / 502);
2. `LIMITS.check_and_record` — per OAuth client, user and patient (429).
   A request counts once it passes (1), even if it is later refused as
   409 or 415: the caller made this service start work, and a refusal of
   the bytes does not undo that;
3. the declared `Content-Type` (415) and `Content-Length` (413, unread);
4. an extraction slot (503, never a queue).

Only then is the body read. The slot is held for the whole background
extraction, not just the read: `DocumentStore.accept` hands it back
exactly once, when the job ends however it ends, or at once when no job
started (a duplicate, a 409, an unreadable file). A leaked slot would be a
permanent 503 after `AGENT_EXTRACTION_MAX_CONCURRENT` leaks.

D7 — EXTRACTION RUNS AT UPLOAD TIME
-----------------------------------
`POST /documents` returns as soon as the job is started, with `pending`
(or, for bytes already uploaded, whatever that document's state is now).
The client polls `GET .../extraction`; nothing blocks on the extraction,
and a question asked against it before it finishes gets
`document_still_processing` from the supervisor, not a wait.

PATIENT BINDING — a change to the pinned agent hop (D40)
--------------------------------------------------------
Both routes take `patient_id`, the FHIR Patient id of the open chart. The
extractor needs it (the identity check and the intake-vs-chart discrepancy
check read that chart under the uploader's token), and the ask path needs to
know which chart a document was uploaded to. Both routes make the Patient
read first, so the token is proven before anything is stored or read — the
stub could not, and accepted any token-shaped string. A document asked for
under another patient is 404, the same answer as an unknown id
(docs/w2/ui-integration.md §2.3's non-disclosure rule).

IDENTITY, AND WHY IT IS THE DIGEST
----------------------------------
`document_id` is the file's SHA3-512 through `document_idempotency_key` —
D1's document-level dedup key, and the same digest OpenEMR stores.
Re-uploading identical bytes to the same chart is the same document, 200,
with its current status (invariant 6's shape, one step before the write).
The same bytes for a *different* chart are refused, 409: rebinding would
move an extraction and every citation into it to another patient.

NO PHI IN LOGS
--------------
The file name is never logged and never stored. What is logged is the
document id, the kind, the byte count and the correlation id — identifiers
and counts, per CLAUDE.md invariant 1.

WRITE-BACK — D54
----------------
A lab report's extraction ends by writing its located, verified,
LOINC-coded rows to the chart through the module's write-back endpoint,
under the uploader's token, unreviewed (`app/writeback/after_extraction.py`).
Nothing is written for an intake form (D2), a failed extraction, or a
document whose patient the identity check did not confirm. The status
route is final before the write starts: `status` never waits for it.

WRITE-BACK OUTCOME — duplicate results
--------------------------------------
Both responses carry `write_back`: null until the lab report's write-back
has ended (and always null for an intake form, which has none), then a
`WriteBackSummary` — the decision, the counts (created, duplicate_ignored,
already_in_chart, conflict, skipped) and each conflict with both values.
The card polls a lab report past `complete` until this is set, and shows
the conflicts. A conflict is the one place chart content crosses this
route: a value the chart holds and a value the document prints, for the
clinician who uploaded it, on the chart it was uploaded to, under the
same token that could read the chart. It is never logged.

IDENTITY — D60
--------------
Both responses carry `identity_check` once the extraction is complete:
`mismatch` when the name or date of birth printed on the document
contradicts the chart (the extractor's hard flag, D35 item 10), `confirmed`
when D54's rule confirms the patient, `not_confirmed` otherwise, and null
before completion or on a failure. It is the whole of what the module needs
to quarantine a wrong-patient document in the chart, and it carries no
value from the document: the outcome of a comparison, never the name.

STATE ACROSS RESTARTS — D60
---------------------------
The document store saves every upload and its extraction to an encrypted
SQLite file (app/workers/extraction_store.py), so after a restart a
document answers with the same status and report, stays bound to its
chart, and the same bytes for another chart are still 409. An extraction
the restart cut short is `failed` / `interrupted`, and uploading the file
again starts it afresh. Still not shared between workers (D40 item 10).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from enum import StrEnum

from fastapi import APIRouter, Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api import upload_guard
from app.api.pre_visit_brief import ApiError
from app.middleware.correlation import get_correlation_id
from app.middleware.logging import log_event
from app.middleware.telemetry import record_step
from app.models.api import PATIENT_ID_PATTERN, ErrorCode, ErrorResponse
from app.models.telemetry_events import Pipeline, StepOutcome
from app.models.w2.base import W2Contract
from app.models.w2.documents import DocumentKind, ExtractionStatus
from app.models.w2.workers import ExtractionFailureReason
from app.models.w2.writeback import WriteBackSummary
from app.workers import document_store
from app.workers.document_store import DocumentBoundElsewhere, UnreadablePdf
from app.workers.intake_extractor.chart import fhir_chart_reader
from app.workers.intake_extractor.report import ExtractionReport
from app.writeback.after_extraction import identity_confirmed, lab_write_back

router = APIRouter()

_bearer = HTTPBearer(
    auto_error=False,
    description="The clinician's own OpenEMR OAuth2 access token (ARCHITECTURE.md §2).",
)

#: Largest upload accepted, matching the browser-side cap in
#: public/assets/w2/upload.mjs. Enforced here too: a client-side limit is a
#: courtesy, not a control.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

PDF_MIME = "application/pdf"

#: The body is the PDF itself, not a multipart part: the only client is the
#: OpenEMR module, server-side (docs/w2/ui-integration.md §2.2).
_RAW_BODY_NOTE = "the PDF bytes, as the request body"

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    status: {"model": ErrorResponse}
    for status in (400, 401, 403, 404, 409, 413, 415, 422, 429, 502, 503)
}

_PATIENT_QUERY = Query(
    pattern=PATIENT_ID_PATTERN,
    description="FHIR Patient id (lowercase UUID) of the chart the document belongs to.",
)


class IdentityOutcome(StrEnum):
    """Whether the document is this chart's patient's, as far as the extractor could tell (D60)."""

    #: The name or date of birth on the document contradicts the chart: the
    #: extractor's hard flag. Nothing from it is stated or written back.
    MISMATCH = "mismatch"
    #: D54's rule: the name matched and neither comparison is a mismatch.
    CONFIRMED = "confirmed"
    #: Neither: the name could not be compared (not located, or the chart
    #: could not be read). Not a mismatch, and not a confirmation either.
    NOT_CONFIRMED = "not_confirmed"


def identity_outcome(report: ExtractionReport | None) -> IdentityOutcome | None:
    """None until the extraction is complete, and for a failed one."""
    if report is None or not report.is_complete:
        return None
    if report.identity is not None and report.identity.hard_flag:
        return IdentityOutcome.MISMATCH
    if identity_confirmed(report):
        return IdentityOutcome.CONFIRMED
    return IdentityOutcome.NOT_CONFIRMED


class UploadAccepted(W2Contract):
    """What `POST /documents` answers with. D7: already a status."""

    document_id: str
    kind: DocumentKind
    status: ExtractionStatus
    byte_length: int
    correlation_id: str
    #: Set only when the document had already finished extracting (a
    #: duplicate upload); see IdentityOutcome.
    identity_check: IdentityOutcome | None = None
    #: The lab write-back's outcome, once it has ended; see the module docstring.
    write_back: WriteBackSummary | None = None


class ExtractionState(W2Contract):
    """
    What `GET /documents/{id}/extraction` answers with: `IntakeExtractorOutput`
    minus `payload`. The payload reaches the clinician through the ask path
    (POST /w2/ask), as verified, cited claims — never as a raw extraction.
    """

    document_id: str
    kind: DocumentKind
    status: ExtractionStatus
    extraction_id: str | None = None
    failure_reason: ExtractionFailureReason | None = None
    correlation_id: str
    identity_check: IdentityOutcome | None = None
    write_back: WriteBackSummary | None = None


@router.post(
    "/documents",
    response_model=UploadAccepted,
    responses=_ERROR_RESPONSES,
    summary="Upload a document; extraction starts now, not at question time (D7)",
)
async def upload_document(
    request: Request,
    kind: DocumentKind = Query(description="Which schema applies: lab_pdf or intake_form."),
    patient_id: str = _PATIENT_QUERY,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> UploadAccepted:
    started = time.perf_counter()
    # Steps 1-4 of the module docstring: nothing below reads the body until
    # the caller, the rate, the declared size and a free slot are settled.
    caller = await upload_guard.authorize_document_request(credentials, patient_id)
    upload_guard.LIMITS.check_and_record(caller)
    correlation_id = get_correlation_id() or "no-request-context"

    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type not in (PDF_MIME, "application/x-pdf"):
        raise ApiError(
            415,
            ErrorCode.INVALID_REQUEST,
            f"Only {PDF_MIME} can be extracted ({_RAW_BODY_NOTE}).",
        )
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise ApiError(
            413,
            ErrorCode.INVALID_REQUEST,
            f"The uploaded file is larger than {MAX_UPLOAD_BYTES} bytes.",
        )

    upload_guard.SLOTS.try_acquire()
    release = _once(upload_guard.SLOTS.release)
    try:
        body = await request.body()
        if len(body) == 0:
            raise ApiError(400, ErrorCode.INVALID_REQUEST, "The uploaded file is empty.")
        if len(body) > MAX_UPLOAD_BYTES:
            raise ApiError(
                413,
                ErrorCode.INVALID_REQUEST,
                f"The uploaded file is larger than {MAX_UPLOAD_BYTES} bytes.",
            )
        if not body.startswith(b"%PDF-"):
            raise ApiError(415, ErrorCode.INVALID_REQUEST, "The uploaded file is not a PDF.")
    except BaseException:
        release()
        raise

    # From here `accept` owns the slot: it calls `release` exactly once,
    # whether it raises, finds a duplicate, or starts a job that later ends.
    try:
        stored, output, duplicate = document_store.DOCUMENTS.accept(
            patient_id,
            body,
            kind,
            chart_reader=fhir_chart_reader(caller.token),
            on_finished=release,
            # D54: a completed lab report's located, coded rows go to the
            # chart under the uploader's own token. Never an intake form (D2).
            after_extraction=(
                lab_write_back(caller.token, patient_id, body)
                if kind is DocumentKind.LAB_PDF
                else None
            ),
        )
    except UnreadablePdf:
        raise ApiError(
            415, ErrorCode.INVALID_REQUEST, "The uploaded file is not a readable PDF."
        ) from None
    except DocumentBoundElsewhere:
        raise ApiError(
            409,
            ErrorCode.INVALID_REQUEST,
            "This file is already attached to a different chart and cannot be attached here.",
        ) from None

    # Through log_event, not a bare logger line, so the upload is on the
    # encounter's trace with every later step (docs/w2/OBSERVABILITY.md §2).
    log_event(
        "w2_document_upload_accepted",
        {
            "document_id": stored.document.document_id,
            "kind": kind.value,
            "byte_length": len(body),
            "duplicate": duplicate,
            "status": output.status.value,
            "correlation_id": correlation_id,
        },
    )
    record_step(
        Pipeline.DOCUMENTS,
        "upload",
        time.perf_counter() - started,
        counts={"byte_length": len(body), "duplicate": duplicate},
    )
    return UploadAccepted(
        document_id=stored.document.document_id,
        kind=stored.document.kind,
        status=output.status,
        byte_length=len(body),
        correlation_id=correlation_id,
        identity_check=identity_outcome(
            document_store.DOCUMENTS.report(stored.document.document_id)
        ),
        write_back=document_store.DOCUMENTS.write_back(stored.document.document_id),
    )


@router.get(
    "/documents/{document_id}/extraction",
    response_model=ExtractionState,
    responses=_ERROR_RESPONSES,
    summary="Where an upload's extraction has got to (D7)",
)
async def extraction_state(
    document_id: str,
    patient_id: str = _PATIENT_QUERY,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> ExtractionState:
    started = time.perf_counter()
    # Checked exactly as the upload is (401 / 403 / 404 / 502); not counted
    # against the upload limits, because polling is not new work.
    await upload_guard.authorize_document_request(credentials, patient_id)
    correlation_id = get_correlation_id() or "no-request-context"

    store = document_store.DOCUMENTS
    stored = store.lookup(document_id, patient_id)
    output = store.status(document_id) if stored is not None else None
    if stored is None or output is None:
        # Unknown, or another patient's: one answer for both (ui-integration §2.3).
        raise ApiError(
            404, ErrorCode.INVALID_REQUEST, "No extraction is recorded for that document."
        )

    identity = identity_outcome(store.report(document_id))
    write_back = store.write_back(document_id)
    log_event(
        "w2_extraction_state_read",
        {
            "document_id": document_id,
            "status": output.status.value,
            "identity_check": identity.value if identity is not None else None,
            "write_back": write_back.decision.value if write_back is not None else None,
            "correlation_id": correlation_id,
        },
    )
    final = output.status in (ExtractionStatus.COMPLETE, ExtractionStatus.FAILED)
    record_step(
        Pipeline.DOCUMENTS,
        "extraction_status",
        time.perf_counter() - started,
        StepOutcome.OK if final else StepOutcome.PENDING,
        counts={"complete": output.status is ExtractionStatus.COMPLETE},
    )
    return ExtractionState(
        document_id=document_id,
        kind=output.kind,
        status=output.status,
        extraction_id=output.extraction_id,
        failure_reason=output.failure_reason,
        correlation_id=correlation_id,
        identity_check=identity,
        write_back=write_back,
    )


def _once(callback: Callable[[], None]) -> Callable[[], None]:
    """`callback`, made safe to call more than once: only the first call runs it."""
    called = False

    def run() -> None:
        nonlocal called
        if not called:
            called = True
            callback()

    return run


def _reset_for_tests() -> None:
    """Forget every upload, and reset the limits and slots. Used by tests only."""
    document_store.DOCUMENTS.clear()
    upload_guard.reset_for_tests()
