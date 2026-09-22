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

IN-PROCESS STATE
----------------
The document store is this process's memory (app/workers/document_store.py):
lost on restart, not shared between workers. Recorded as an open item.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.conversation import _failures_as_api_errors
from app.api.pre_visit_brief import ApiError
from app.fhir import client as fhir_client
from app.middleware.correlation import get_correlation_id
from app.middleware.logging import log_event
from app.middleware.telemetry import record_step
from app.models.api import PATIENT_ID_PATTERN, ErrorCode, ErrorResponse
from app.models.telemetry_events import Pipeline, StepOutcome
from app.models.w2.base import W2Contract
from app.models.w2.documents import DocumentKind, ExtractionStatus
from app.models.w2.workers import ExtractionFailureReason
from app.workers import document_store
from app.workers.document_store import DocumentBoundElsewhere, UnreadablePdf
from app.workers.intake_extractor.chart import fhir_chart_reader

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
    status: {"model": ErrorResponse} for status in (400, 401, 404, 409, 413, 415, 422, 502)
}

_PATIENT_QUERY = Query(
    pattern=PATIENT_ID_PATTERN,
    description="FHIR Patient id (lowercase UUID) of the chart the document belongs to.",
)


class UploadAccepted(W2Contract):
    """What `POST /documents` answers with. D7: already a status."""

    document_id: str
    kind: DocumentKind
    status: ExtractionStatus
    byte_length: int
    correlation_id: str


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


def _require_token(credentials: HTTPAuthorizationCredentials | None) -> str:
    if credentials is None or not credentials.credentials:
        raise ApiError(
            401,
            ErrorCode.MISSING_BEARER_TOKEN,
            "This endpoint needs the clinician's OpenEMR access token as a bearer token.",
            {"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


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
    token = _require_token(credentials)
    correlation_id = get_correlation_id() or "no-request-context"

    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type not in (PDF_MIME, "application/x-pdf"):
        raise ApiError(
            415,
            ErrorCode.INVALID_REQUEST,
            f"Only {PDF_MIME} can be extracted ({_RAW_BODY_NOTE}).",
        )

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

    with _failures_as_api_errors("w2_document_upload_failed"):
        await fhir_client.ensure_patient_exists(token, patient_id)

    try:
        stored, output, duplicate = document_store.DOCUMENTS.accept(
            patient_id, body, kind, chart_reader=fhir_chart_reader(token)
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
    token = _require_token(credentials)
    correlation_id = get_correlation_id() or "no-request-context"

    with _failures_as_api_errors("w2_extraction_state_failed"):
        await fhir_client.ensure_patient_exists(token, patient_id)

    store = document_store.DOCUMENTS
    stored = store.lookup(document_id, patient_id)
    output = store.status(document_id) if stored is not None else None
    if stored is None or output is None:
        # Unknown, or another patient's: one answer for both (ui-integration §2.3).
        raise ApiError(
            404, ErrorCode.INVALID_REQUEST, "No extraction is recorded for that document."
        )

    log_event(
        "w2_extraction_state_read",
        {
            "document_id": document_id,
            "status": output.status.value,
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
    )


def _reset_for_tests() -> None:
    """Forget every upload. Used by tests only."""
    document_store.DOCUMENTS.clear()
