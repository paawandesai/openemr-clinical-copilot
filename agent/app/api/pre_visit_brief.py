"""
POST /pre-visit-brief — the Pre-Visit Brief pipeline (USERS.md §3 Use Case
1) over HTTP. Contracts: app/models/api.py.

Authorization. The caller sends the clinician's own OpenEMR access token as
`Authorization: Bearer <token>`. The agent forwards it to FHIR unchanged and
holds no credential of its own (ARCHITECTURE.md §2); OpenEMR decides what
the token may read.

patient_session_key = <user>:<patient>:<session> (ARCHITECTURE.md §4):
- user: the token's `sub` claim (OpenEMR issues JWTs; `sub` is the user's
  UUID, confirmed live). Read without checking the signature. That is safe
  for attribution only because nothing is retrieved or logged under the key
  until OpenEMR has accepted the same token: a forged `sub` fails the first
  FHIR read, before any event is emitted.
- patient: the request's patient_id.
- session: the request's session_id, or a new UUID, returned in the
  response so the caller can reuse it.
The caller cannot supply the key, or the user part, directly.

Failures, each with an ErrorResponse body and never exception text:
  401 missing_bearer_token / malformed_bearer_token — rejected before any
      FHIR call.
  401 fhir_token_rejected — OpenEMR answered 401. It does so identically
      for an expired, revoked or out-of-scope token (F-08, §2), so the
      message names all three and does not claim to know which.
  404 patient_not_found — Patient read found no such patient.
  422 invalid_request — body does not match the contract (app/main.py).
  502 fhir_unavailable — FHIR unreachable or answered with an error.
  502 brief_generation_failed — the LLM call or its output failed.
  500 internal_error — anything else.
"""

from __future__ import annotations

import base64
import json
import re
import uuid

import anthropic
import httpx
import structlog
from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.fhir.exceptions import FhirAuthError, FhirClientError, PatientNotFoundError
from app.graph.pre_visit_brief import run_pre_visit_brief
from app.middleware.correlation import get_correlation_id
from app.models.api import (
    ClaimOutcome,
    ErrorCode,
    ErrorResponse,
    PreVisitBriefRequest,
    PreVisitBriefResponse,
    SectionSummary,
    Timing,
    VerificationSummary,
)
from app.reasoning.draft_claims import ClaimDraftingError

router = APIRouter()

_bearer = HTTPBearer(
    auto_error=False,
    description="The clinician's own OpenEMR OAuth2 access token (ARCHITECTURE.md §2).",
)

# `sub` becomes the first part of a colon-separated key.
_SUBJECT = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    status: {"model": ErrorResponse} for status in (401, 404, 422, 500, 502)
}


class ApiError(Exception):
    """Raised by this router; rendered as ErrorResponse by app/main.py."""

    def __init__(
        self,
        status_code: int,
        error: ErrorCode,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error = error
        self.message = message
        self.headers = headers


def _token_subject(token: str) -> str | None:
    """The JWT's `sub` claim, or None if the token is not a JWT carrying one."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except ValueError:
        return None
    subject = claims.get("sub") if isinstance(claims, dict) else None
    if not isinstance(subject, str) or not _SUBJECT.fullmatch(subject):
        return None
    return subject


@router.post(
    "/pre-visit-brief",
    response_model=PreVisitBriefResponse,
    responses=_ERROR_RESPONSES,
    summary="Run the Pre-Visit Brief for one patient",
)
async def pre_visit_brief(
    body: PreVisitBriefRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> PreVisitBriefResponse:
    if credentials is None or not credentials.credentials:
        raise ApiError(
            401,
            ErrorCode.MISSING_BEARER_TOKEN,
            "Send the clinician's OpenEMR access token as 'Authorization: Bearer <token>'.",
            {"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    user = _token_subject(token)
    if user is None:
        raise ApiError(
            401,
            ErrorCode.MALFORMED_BEARER_TOKEN,
            "The bearer token is not an OpenEMR access token (a JWT with a subject claim).",
            {"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )

    session_id = body.session_id or str(uuid.uuid4())
    patient_session_key = f"{user}:{body.patient_id}:{session_id}"
    log = structlog.get_logger()

    try:
        result = await run_pre_visit_brief(token, body.patient_id, patient_session_key)
    except FhirAuthError:
        log.warning("pre_visit_brief_failed", error=ErrorCode.FHIR_TOKEN_REJECTED.value)
        raise ApiError(
            401,
            ErrorCode.FHIR_TOKEN_REJECTED,
            "OpenEMR rejected the bearer token. It may be expired or revoked, or it may "
            "lack a scope this brief needs (user/Patient.read, user/Condition.read, "
            "user/MedicationRequest.read, user/AllergyIntolerance.read). OpenEMR answers "
            "all of these the same way, so which one applies cannot be determined here.",
            {"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from None
    except PatientNotFoundError:
        log.info("pre_visit_brief_failed", error=ErrorCode.PATIENT_NOT_FOUND.value)
        raise ApiError(
            404,
            ErrorCode.PATIENT_NOT_FOUND,
            "OpenEMR has no patient with this patient_id.",
        ) from None
    except (FhirClientError, httpx.TransportError) as exc:
        log.error("pre_visit_brief_failed", error=ErrorCode.FHIR_UNAVAILABLE.value, exc_info=exc)
        raise ApiError(
            502,
            ErrorCode.FHIR_UNAVAILABLE,
            "OpenEMR's FHIR API could not be reached or returned an error. Check GET /ready.",
        ) from None
    except (ClaimDraftingError, anthropic.APIError) as exc:
        log.error(
            "pre_visit_brief_failed", error=ErrorCode.BRIEF_GENERATION_FAILED.value, exc_info=exc
        )
        raise ApiError(
            502,
            ErrorCode.BRIEF_GENERATION_FAILED,
            "The brief could not be generated: the language model call failed. "
            "No partial brief is returned.",
        ) from None
    except Exception as exc:
        log.error("pre_visit_brief_failed", error=ErrorCode.INTERNAL_ERROR.value, exc_info=exc)
        raise ApiError(
            500,
            ErrorCode.INTERNAL_ERROR,
            "The brief could not be generated because of an internal error. "
            "Quote the correlation_id when reporting it.",
        ) from None

    results = result.verification_results
    verified = sum(1 for r in results if r.passed)
    return PreVisitBriefResponse(
        brief=result.text,
        patient_id=body.patient_id,
        session_id=session_id,
        patient_session_key=patient_session_key,
        correlation_id=result.correlation_id or get_correlation_id() or "no-request-context",
        verification=VerificationSummary(
            claims=len(results), verified=verified, unverified=len(results) - verified
        ),
        claims=[
            ClaimOutcome(
                resource_type=r.claim.resource_type,
                source_id=r.claim.source_id,
                field=r.claim.field,
                value=r.claim.value,
                verified=r.passed,
                unverified_reason=r.reason,
            )
            for r in results
        ],
        sections=[
            SectionSummary(resource_type=rt, retrieved=c.retrieved, in_brief=c.in_brief)
            for rt, c in result.section_counts.items()
        ],
        timing=Timing(
            retrieve_seconds=result.node_seconds["retrieve"],
            draft_seconds=result.node_seconds["draft"],
            verify_seconds=result.node_seconds["verify"],
            render_seconds=result.node_seconds["render"],
            total_seconds=result.total_seconds,
        ),
    )
