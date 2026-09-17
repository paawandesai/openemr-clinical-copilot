"""
POST /pre-visit-brief/follow-up — one physician question about the brief
just rendered, answered only from the records the brief retrieves and
verified against them (app/graph/follow_up.py). Contracts:
app/models/api.py.

Authorization and patient_session_key are exactly as for
POST /pre-visit-brief (app/api/pre_visit_brief.py, whose helpers this
module reuses): the clinician's own token in `Authorization: Bearer`,
forwarded to FHIR unchanged; key = <token sub>:<patient_id>:<session_id>.
Here session_id is required — it is the brief's — so the follow-up's
events join the brief's under one key.

Failures, each with an ErrorResponse body and never exception text:
  401 missing_bearer_token / malformed_bearer_token — before any FHIR call.
  401 fhir_token_rejected — OpenEMR answered 401 (expired, revoked or out
      of scope; F-08 makes these indistinguishable).
  404 patient_not_found — Patient read found no such patient.
  422 invalid_request — body does not match the contract (app/main.py).
  502 fhir_unavailable — FHIR unreachable or answered with an error.
  502 follow_up_generation_failed — the LLM call or its output failed.
  500 internal_error — anything else.

The question is never logged, including on failure.
"""

from __future__ import annotations

import anthropic
import httpx
import structlog
from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials

from app.api.pre_visit_brief import _ERROR_RESPONSES, ApiError, _bearer, _token_subject
from app.fhir.exceptions import FhirAuthError, FhirClientError, PatientNotFoundError
from app.graph.follow_up import run_follow_up
from app.middleware.correlation import get_correlation_id
from app.models.api import (
    ClaimOutcome,
    ErrorCode,
    FollowUpRequest,
    FollowUpResponse,
    SectionSummary,
    Timing,
    VerificationSummary,
)
from app.reasoning.draft_claims import ClaimDraftingError

router = APIRouter()


@router.post(
    "/pre-visit-brief/follow-up",
    response_model=FollowUpResponse,
    responses=_ERROR_RESPONSES,
    summary="Answer one follow-up question from the brief's verified record set",
)
async def follow_up(
    body: FollowUpRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> FollowUpResponse:
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

    patient_session_key = f"{user}:{body.patient_id}:{body.session_id}"
    log = structlog.get_logger()

    try:
        result = await run_follow_up(token, body.patient_id, body.question, patient_session_key)
    except FhirAuthError:
        log.warning("follow_up_failed", error=ErrorCode.FHIR_TOKEN_REJECTED.value)
        raise ApiError(
            401,
            ErrorCode.FHIR_TOKEN_REJECTED,
            "OpenEMR rejected the bearer token. It may be expired or revoked, or it may "
            "lack a scope this follow-up needs (user/Patient.read, user/Condition.read, "
            "user/MedicationRequest.read, user/AllergyIntolerance.read). OpenEMR answers "
            "all of these the same way, so which one applies cannot be determined here.",
            {"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from None
    except PatientNotFoundError:
        log.info("follow_up_failed", error=ErrorCode.PATIENT_NOT_FOUND.value)
        raise ApiError(
            404,
            ErrorCode.PATIENT_NOT_FOUND,
            "OpenEMR has no patient with this patient_id.",
        ) from None
    except (FhirClientError, httpx.TransportError) as exc:
        log.error("follow_up_failed", error=ErrorCode.FHIR_UNAVAILABLE.value, exc_info=exc)
        raise ApiError(
            502,
            ErrorCode.FHIR_UNAVAILABLE,
            "OpenEMR's FHIR API could not be reached or returned an error. Check GET /ready.",
        ) from None
    except (ClaimDraftingError, anthropic.APIError) as exc:
        log.error("follow_up_failed", error=ErrorCode.FOLLOW_UP_GENERATION_FAILED.value, exc_info=exc)
        raise ApiError(
            502,
            ErrorCode.FOLLOW_UP_GENERATION_FAILED,
            "The follow-up could not be answered: the language model call failed. "
            "No partial answer is returned.",
        ) from None
    except Exception as exc:
        log.error("follow_up_failed", error=ErrorCode.INTERNAL_ERROR.value, exc_info=exc)
        raise ApiError(
            500,
            ErrorCode.INTERNAL_ERROR,
            "The follow-up could not be answered because of an internal error. "
            "Quote the correlation_id when reporting it.",
        ) from None

    results = result.verification_results
    verified = sum(1 for r in results if r.passed)
    return FollowUpResponse(
        answer=result.text,
        answer_status=result.answer_status,
        gaps=result.gaps,
        question=body.question,
        patient_id=body.patient_id,
        session_id=body.session_id,
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
            retrieve_seconds=result.step_seconds["retrieve"],
            draft_seconds=result.step_seconds["draft"],
            verify_seconds=result.step_seconds["verify"],
            render_seconds=result.step_seconds["render"],
            total_seconds=result.total_seconds,
        ),
    )
