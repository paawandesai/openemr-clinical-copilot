"""
FastAPI entrypoint — Clinical Co-Pilot agent service.

Routes:
  GET  /health           liveness (app/health.py)
  GET  /ready            dependency readiness (app/health.py)
  POST /pre-visit-brief  the Pre-Visit Brief pipeline (app/api/pre_visit_brief.py)
  POST /pre-visit-brief/follow-up
                         one follow-up question on the brief's verified
                         record set (app/api/follow_up.py)
  POST /pre-visit-brief/conversation
                         one turn of the multi-turn follow-up conversation,
                         verified per turn (app/api/conversation.py)
  POST /pre-visit-brief/conversation/end
                         discard that conversation's stored state

Contracts for all of them live in app/models/api.py; /docs and /openapi.json
publish them. Deployment: agent/docs/deployment.md.

Correlation ID + structured logging are wired first because
docs/KEY_METRICS.md states they are a precondition for computing any of its
six metrics: every log line a request produces carries its correlation_id.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.conversation import router as conversation_router
from app.api.follow_up import router as follow_up_router
from app.api.pre_visit_brief import ApiError
from app.api.pre_visit_brief import router as pre_visit_brief_router
from app.health import router as health_router
from app.middleware.correlation import CorrelationIdMiddleware, get_correlation_id
from app.middleware.logging import configure_logging
from app.models.api import ErrorCode, ErrorResponse

configure_logging()

app = FastAPI(
    title="Clinical Co-Pilot Agent",
    description=(
        "Pre-Visit Brief (docs/USERS.md Use Case 1) and the multi-turn follow-up "
        "conversation (Use Case 3) over OpenEMR's FHIR API, with every drafted "
        "claim verified against the retrieved records before it renders, on "
        "every turn. Design: docs/ARCHITECTURE.md at the repository root."
    ),
    version="0.3.0",
)

# Load-bearing, not optional: every downstream metric in
# docs/KEY_METRICS.md depends on every log line being able to carry a
# correlation_id for its request's lifetime.
app.add_middleware(CorrelationIdMiddleware)


def _error(
    status_code: int, error: ErrorCode, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    body = ErrorResponse(
        error=error,
        message=message,
        correlation_id=get_correlation_id() or "no-request-context",
    )
    return JSONResponse(
        status_code=status_code, content=body.model_dump(mode="json"), headers=headers
    )


@app.exception_handler(ApiError)
async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
    return _error(exc.status_code, exc.error, exc.message, exc.headers)


@app.exception_handler(RequestValidationError)
async def _invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Field locations and rule messages only — never the submitted values.
    problems = "; ".join(
        f"{'.'.join(str(part) for part in err['loc'][1:]) or 'body'}: {err['msg']}"
        for err in exc.errors()
    )
    return _error(
        422, ErrorCode.INVALID_REQUEST, f"Request does not match the contract: {problems}"
    )


app.include_router(health_router)
app.include_router(pre_visit_brief_router)
app.include_router(follow_up_router)
app.include_router(conversation_router)
