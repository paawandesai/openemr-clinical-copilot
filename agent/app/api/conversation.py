"""
POST /pre-visit-brief/conversation — one turn of the multi-turn follow-up
conversation (USERS.md §3 Use Case 3; app/graph/conversation.py).
POST /pre-visit-brief/conversation/end — discard a conversation's state now
rather than at its TTL. Contracts: app/models/api.py.

Authorization and patient_session_key are exactly as for
POST /pre-visit-brief (app/api/pre_visit_brief.py, whose helpers this module
reuses): the clinician's own token in `Authorization: Bearer`, forwarded to
FHIR unchanged; key = <token sub>:<patient_id>:<session_id>.

The request carries a question and nothing else about the conversation.
What earlier turns established is held server-side as verified claims,
keyed by the token's `sub` and the session and bound to one patient; a turn
that names a different patient discards it (app/conversation/state.py).
Because that state is keyed by `sub`, which is read from the token without
checking its signature, neither endpoint touches it until OpenEMR has
accepted the same token for a read of this patient.

A refusal is an answer, not an error: HTTP 200 with turn_status
refused_out_of_scope or refused_unverifiable.

Failures, each with an ErrorResponse body and never exception text — the
same set as POST /pre-visit-brief/follow-up:
  401 missing_bearer_token / malformed_bearer_token — before any FHIR call.
  401 fhir_token_rejected — OpenEMR answered 401 (F-08).
  404 patient_not_found — Patient read found no such patient.
  422 invalid_request — body does not match the contract (app/main.py).
  502 fhir_unavailable — FHIR unreachable or answered with an error.
  502 follow_up_generation_failed — the LLM call or its output failed.
  500 internal_error — anything else.
A failed turn stores nothing.

The question is never logged or stored, including on failure.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import anthropic
import httpx
import structlog
from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials

from app.api.pre_visit_brief import _ERROR_RESPONSES, ApiError, _bearer, _token_subject
from app.fhir import client as fhir_client
from app.fhir.exceptions import FhirAuthError, FhirClientError, PatientNotFoundError
from app.graph import conversation as graph
from app.middleware.correlation import get_correlation_id
from app.models.api import (
    ConversationInfo,
    ConversationTurnRequest,
    ConversationTurnResponse,
    EndConversationRequest,
    EndConversationResponse,
    ErrorCode,
    RetrievalOutcome,
    SectionSummary,
    Timing,
    TurnClaim,
    VerificationSummary,
)
from app.reasoning.draft_claims import ClaimDraftingError

router = APIRouter()


def _caller(credentials: HTTPAuthorizationCredentials | None) -> tuple[str, str]:
    """(token, user). `user` is unproven until OpenEMR accepts the token."""
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
    return token, user


@contextmanager
def _failures_as_api_errors(event: str) -> Iterator[None]:
    log = structlog.get_logger()
    try:
        yield
    except FhirAuthError:
        log.warning(event, error=ErrorCode.FHIR_TOKEN_REJECTED.value)
        raise ApiError(
            401,
            ErrorCode.FHIR_TOKEN_REJECTED,
            "OpenEMR rejected the bearer token. It may be expired or revoked, or it may "
            "lack a scope this conversation needs (user/Patient.read, user/Condition.read, "
            "user/MedicationRequest.read, user/AllergyIntolerance.read, and for lab or "
            "visit questions user/Observation.read, user/Encounter.read). OpenEMR answers "
            "all of these the same way, so which one applies cannot be determined here.",
            {"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from None
    except PatientNotFoundError:
        log.info(event, error=ErrorCode.PATIENT_NOT_FOUND.value)
        raise ApiError(
            404,
            ErrorCode.PATIENT_NOT_FOUND,
            "OpenEMR has no patient with this patient_id.",
        ) from None
    except (FhirClientError, httpx.TransportError) as exc:
        log.error(event, error=ErrorCode.FHIR_UNAVAILABLE.value, exc_info=exc)
        raise ApiError(
            502,
            ErrorCode.FHIR_UNAVAILABLE,
            "OpenEMR's FHIR API could not be reached or returned an error. Check GET /ready.",
        ) from None
    except (ClaimDraftingError, anthropic.APIError) as exc:
        log.error(event, error=ErrorCode.FOLLOW_UP_GENERATION_FAILED.value, exc_info=exc)
        raise ApiError(
            502,
            ErrorCode.FOLLOW_UP_GENERATION_FAILED,
            "The question could not be answered: the language model call failed. "
            "No partial answer is returned and nothing was added to the conversation.",
        ) from None
    except ApiError:
        raise
    except Exception as exc:
        log.error(event, error=ErrorCode.INTERNAL_ERROR.value, exc_info=exc)
        raise ApiError(
            500,
            ErrorCode.INTERNAL_ERROR,
            "The request could not be completed because of an internal error. "
            "Quote the correlation_id when reporting it.",
        ) from None


@router.post(
    "/pre-visit-brief/conversation",
    response_model=ConversationTurnResponse,
    responses=_ERROR_RESPONSES,
    summary="One turn of the multi-turn follow-up conversation, verified per turn",
)
async def conversation_turn(
    body: ConversationTurnRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> ConversationTurnResponse:
    token, user = _caller(credentials)

    with _failures_as_api_errors("conversation_turn_failed"):
        result = await graph.run_conversation_turn(
            token, user, body.patient_id, body.session_id, body.question
        )

    results = result.verification_results
    verified = sum(1 for r in results if r.passed)
    snapshot = result.snapshot
    return ConversationTurnResponse(
        answer=result.text,
        turn_status=result.status,
        refusals=result.refusals,
        gaps=result.gaps,
        question=body.question,
        patient_id=body.patient_id,
        session_id=body.session_id,
        patient_session_key=result.patient_session_key,
        correlation_id=result.correlation_id or get_correlation_id() or "no-request-context",
        conversation=ConversationInfo(
            turn=snapshot.turn,
            transition=snapshot.transition,
            context_turns=len(snapshot.context),
            context_claims=snapshot.context_claim_count,
            state_saved=result.state_saved,
            expires_in_seconds=int(snapshot.expires_in_seconds),
        ),
        retrieval=[
            RetrievalOutcome(
                resource_type=r.resource_type,
                rule=r.rule,
                retrieved=r.retrieved,
                considered=r.considered,
            )
            for r in result.retrieval
        ],
        verification=VerificationSummary(
            claims=len(results), verified=verified, unverified=len(results) - verified
        ),
        claims=[
            TurnClaim(
                resource_type=r.claim.resource_type,
                source_id=r.claim.source_id,
                field=r.claim.field,
                value=r.claim.value,
                verified=r.passed,
                unverified_reason=r.reason,
                text=line.removeprefix("  - "),
            )
            for r, line in zip(results, result.claim_lines, strict=True)
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


@router.post(
    "/pre-visit-brief/conversation/end",
    response_model=EndConversationResponse,
    responses=_ERROR_RESPONSES,
    summary="Discard a conversation's stored state now",
)
async def end_conversation(
    body: EndConversationRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> EndConversationResponse:
    token, user = _caller(credentials)

    with _failures_as_api_errors("conversation_end_failed"):
        # The same proof of `user` a turn requires, before state is touched.
        await fhir_client.ensure_patient_exists(token, body.patient_id)
        ended = graph.STORE.end(user, body.session_id)

    return EndConversationResponse(
        ended=ended,
        session_id=body.session_id,
        correlation_id=get_correlation_id() or "no-request-context",
    )
