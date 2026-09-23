"""
POST /w2/ask — one turn of the Week 2 ask path (app/graph/supervisor.py).
Contract: app/models/w2/ask.py.

Authorization is Week 1's, reused: the clinician's own token as
`Authorization: Bearer`, the Patient read under it before anything else,
and the conversation keyed by the token's `sub`
(app/api/conversation.py:_caller). The session is shared with
POST /pre-visit-brief/conversation: one session, one open chart.

Every clinical outcome is HTTP 200 with a typed `status` and `reason_code`
— an answer, a refusal, a document still processing, a document that failed
extraction, a guideline search that found nothing, a chart or model failure.
HTTP errors are for what is not a clinical outcome:
  401 missing_bearer_token / malformed_bearer_token / fhir_token_rejected
  404 patient_not_found
  422 invalid_request
  502 fhir_unavailable — the authorizing Patient read itself failed
  500 internal_error — anything unexpected; message fixed, never exception text

The question is never logged or stored.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials

from app.api.conversation import _caller, _failures_as_api_errors
from app.api.pre_visit_brief import _ERROR_RESPONSES, _bearer
from app.graph import supervisor
from app.models.w2.ask import AskRequest, AskResponse

router = APIRouter()


@router.post(
    "/w2/ask",
    response_model=AskResponse,
    responses=_ERROR_RESPONSES,
    summary="Week 2: supervisor -> intake-extractor | evidence-retriever -> verified answer",
)
async def ask(
    body: AskRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AskResponse:
    token, user = _caller(credentials)
    with _failures_as_api_errors("w2_ask_failed"):
        return await supervisor.run_ask_turn(
            token,
            user,
            body.patient_id,
            body.session_id,
            body.question,
            body.document_ids,
        )
