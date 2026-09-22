"""
POST /documents and GET /documents/{document_id}/extraction — the upload
and extraction-status endpoints, as a **stub**.

WHAT IS REAL HERE AND WHAT IS NOT
---------------------------------
Real: the request and response contracts, the authorization, the
document id, and the state machine the client polls. Work order 11 allows
"a stub returning canned statuses" for part 1, and everything the browser
depends on is exercised end to end against this.

Not real: the extraction. No text layer is read, no vision model is called
and no locate step runs, because none of them exists yet (D3 is the design;
the extractor is a later work order). `POST /documents` therefore accepts
the bytes, derives the document's identity from them, **discards them**,
and hands back a document that moves `pending -> in_progress -> complete`
on a timer. `GET .../extraction` never returns a `payload`: a stub that
synthesised a `LabPdfExtraction` would be putting field values on a
clinician's screen that no document ever contained, which is invariant 4
broken by the scaffolding.

D7 — EXTRACTION RUNS AT UPLOAD TIME
-----------------------------------
So the upload response already carries a status, and the client polls this
endpoint rather than blocking a question on it. A question asked against a
document that has not finished gets `still_processing` from the supervisor
(`RoutingReasonCode.DOCUMENT_STILL_PROCESSING`), not a wait.

IDENTITY, AND WHY IT IS THE DIGEST
----------------------------------
`document_id` is derived from the file's SHA3-512, through
`document_idempotency_key` — D1's document-level dedup key, and the same
digest OpenEMR stores. Re-uploading an identical file is therefore the same
document and not a second one, which is invariant 6's shape applied one
step before the write: nothing here writes to OpenEMR, but the id that a
later write will be keyed on is fixed at upload, by the bytes.

NO PHI IN LOGS
--------------
The file name is never logged and never stored. What is logged is the
document id, the kind, the byte count and the correlation id — identifiers
and counts, per CLAUDE.md invariant 1. The name of an uploaded document is
frequently a patient's name.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field

import structlog
from fastapi import APIRouter, Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.pre_visit_brief import ApiError
from app.middleware.correlation import get_correlation_id
from app.models.api import ErrorCode, ErrorResponse
from app.models.w2.base import W2Contract
from app.models.w2.documents import DocumentKind, ExtractionStatus
from app.models.w2.workers import ExtractionFailureReason
from app.models.w2.writeback import document_idempotency_key

router = APIRouter()
logger = structlog.get_logger(__name__)

_bearer = HTTPBearer(
    auto_error=False,
    description="The clinician's own OpenEMR OAuth2 access token (ARCHITECTURE.md §2).",
)

#: Largest upload the stub accepts, matching the browser-side cap in
#: public/assets/w2/upload.mjs. Enforced here too: a client-side limit is a
#: courtesy, not a control.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

#: Accepted content type. The real extractor will care about the text layer
#: (D3); the stub only has to refuse what could never be read at all.
PDF_MIME = "application/pdf"

#: The body is the PDF itself, not a multipart part.
#:
#: The browser never talks to this endpoint: it posts a multipart form to
#: the OpenEMR module, which checks the session and the ACL and then makes
#: this call server-side (docs/w2/ui-integration.md). Since the only client
#: is a server, the simplest body that can carry a PDF is the PDF — which
#: also keeps `python-multipart` out of the agent's runtime dependencies
#: for one stub route.
_RAW_BODY_NOTE = "the PDF bytes, as the request body"

#: How long the canned extraction "takes". Long enough that the client's
#: polling path is actually exercised, short enough not to be tedious.
STUB_QUEUE_SECONDS = 1.0
STUB_WORK_SECONDS = 3.0

#: Stub extractions are forgotten after this, so the process does not grow
#: without bound. A real one persists; this one is a timer.
STUB_TTL_SECONDS = 30 * 60

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    status: {"model": ErrorResponse} for status in (400, 401, 404, 413, 415, 422)
}


class UploadAccepted(W2Contract):
    """What `POST /documents` answers with. D7: already a status."""

    document_id: str
    kind: DocumentKind
    status: ExtractionStatus
    byte_length: int
    correlation_id: str


class ExtractionState(W2Contract):
    """
    What `GET /documents/{id}/extraction` answers with.

    Shaped like `IntakeExtractorOutput` (app/models/w2/workers.py) minus
    `payload`, which this stub will not invent. When the real extractor
    lands, this becomes that type and the browser does not change.
    """

    document_id: str
    kind: DocumentKind
    status: ExtractionStatus
    extraction_id: str | None = None
    failure_reason: ExtractionFailureReason | None = None
    correlation_id: str


@dataclass
class _StubExtraction:
    """One accepted upload, in memory, with no bytes and no content."""

    document_id: str
    kind: DocumentKind
    accepted_at: float = field(default_factory=time.monotonic)

    def status(self, now: float) -> ExtractionStatus:
        elapsed = now - self.accepted_at
        if elapsed < STUB_QUEUE_SECONDS:
            return ExtractionStatus.PENDING
        if elapsed < STUB_QUEUE_SECONDS + STUB_WORK_SECONDS:
            return ExtractionStatus.IN_PROGRESS
        return ExtractionStatus.COMPLETE


_ACCEPTED: dict[str, _StubExtraction] = {}


def _evict(now: float) -> None:
    for document_id in [
        key for key, value in _ACCEPTED.items() if now - value.accepted_at > STUB_TTL_SECONDS
    ]:
        _ACCEPTED.pop(document_id, None)


def _require_token(credentials: HTTPAuthorizationCredentials | None) -> None:
    """
    Same gate as every other route: the clinician's own token, or nothing.

    The stub does not call FHIR, so it cannot check the token the way
    `/pre-visit-brief` does — which means a valid-looking token is accepted
    here that OpenEMR might reject. That is a property of the stub and is
    listed in the work order report; it is not a reason to leave the route
    open, because an open route is the one that survives into production.
    """
    if credentials is None or not credentials.credentials:
        raise ApiError(
            401,
            ErrorCode.MISSING_BEARER_TOKEN,
            "This endpoint needs the clinician's OpenEMR access token as a bearer token.",
            {"WWW-Authenticate": "Bearer"},
        )


@router.post(
    "/documents",
    response_model=UploadAccepted,
    responses=_ERROR_RESPONSES,
    summary="Upload a document; extraction starts now, not at question time (D7)",
)
async def upload_document(
    request: Request,
    kind: DocumentKind = Query(description="Which schema applies: lab_pdf or intake_form."),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> UploadAccepted:
    _require_token(credentials)
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

    # D1's document-level key, from the same algorithm OpenEMR stores. The
    # bytes go out of scope here and are never written anywhere.
    digest = hashlib.sha3_512(body).hexdigest()
    document_id = document_idempotency_key(digest)

    now = time.monotonic()
    _evict(now)
    existing = _ACCEPTED.get(document_id)
    if existing is None:
        _ACCEPTED[document_id] = _StubExtraction(document_id=document_id, kind=kind)
        status = ExtractionStatus.PENDING
    else:
        # The same bytes are the same document. Not a second extraction.
        status = existing.status(now)

    logger.info(
        "w2_document_upload_accepted",
        document_id=document_id,
        kind=kind.value,
        byte_length=len(body),
        duplicate=existing is not None,
        correlation_id=correlation_id,
        stub=True,
    )
    return UploadAccepted(
        document_id=document_id,
        kind=kind,
        status=status,
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
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> ExtractionState:
    _require_token(credentials)
    correlation_id = get_correlation_id() or "no-request-context"

    now = time.monotonic()
    _evict(now)
    accepted = _ACCEPTED.get(document_id)
    if accepted is None:
        raise ApiError(
            404,
            ErrorCode.INVALID_REQUEST,
            "No extraction is recorded for that document.",
        )

    status = accepted.status(now)
    # A stub that produced an extraction id for a run that never happened
    # would let a citation claim an extraction to trace back to.
    logger.info(
        "w2_extraction_state_read",
        document_id=document_id,
        status=status.value,
        correlation_id=correlation_id,
        stub=True,
    )
    return ExtractionState(
        document_id=document_id,
        kind=accepted.kind,
        status=status,
        extraction_id=None,
        failure_reason=None,
        correlation_id=correlation_id,
    )


def _reset_for_tests() -> None:
    """Clear the in-memory stub state. Used by tests only."""
    _ACCEPTED.clear()
