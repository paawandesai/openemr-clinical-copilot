"""
FastAPI entrypoint — Clinical Co-Pilot agent service.

Routes:
  GET  /health           liveness (app/health.py)
  GET  /ready            dependency readiness (app/health.py)
  GET  /version          the commit this image was built from (app/version.py)
  POST /documents        upload a document for a patient; the intake-extractor
                         starts at upload, not at question time (D7,
                         app/api/documents.py)
  GET  /documents/{id}/extraction
                         where that extraction has got to
  POST /w2/ask           one Week 2 turn: supervisor -> intake-extractor |
                         evidence-retriever -> verified, cited answer
                         (app/api/ask.py, app/graph/supervisor.py)
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

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.ask import router as ask_router
from app.api.conversation import router as conversation_router
from app.api.documents import router as documents_router
from app.api.follow_up import router as follow_up_router
from app.api.pre_visit_brief import ApiError
from app.api.pre_visit_brief import router as pre_visit_brief_router
from app.graph.supervisor import set_retriever
from app.health import router as health_router
from app.middleware.correlation import CorrelationIdMiddleware, get_correlation_id
from app.middleware.logging import configure_logging, log_event
from app.middleware.telemetry_key import require_secret
from app.models.api import ErrorCode, ErrorResponse
from app.process_state import warn_if_multi_process
from app.retrieval.preload import preload_enabled, preload_retrieval
from app.version import router as version_router
from app.workers import document_store

configure_logging()
# At import, not in the lifespan, so `--lifespan off` cannot skip it: with
# AGENT_ENV=production and no usable AGENT_TELEMETRY_HMAC_SECRET the service
# does not start (D56, app/middleware/telemetry_key.py).
require_secret()
# Likewise: uploaded documents' extractions are kept on disk so a restart
# does not forget them, and production does not start without the file and
# its key (D60, app/workers/extraction_store.py).
document_store.configure_from_env()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """
    Warm the guideline retriever before the first request, when asked to.

    The ONNX weights and the chunk vectors are cold on a fresh process, and
    a clinician's first question should not be what pays for them
    (docs/w2/retrieval-deploy.md). Off by default so a dev process and the
    test suite do not load a model they are not going to use; the container
    sets AGENT_PRELOAD_RETRIEVAL=true.

    A preload failure does not stop the service: the rest of the API does
    not depend on retrieval, and refusing to serve a Pre-Visit Brief
    because a guideline index would not warm is the wrong trade. It is
    logged loudly instead.
    """
    if preload_enabled():
        try:
            app.state.guideline_retriever = preload_retrieval()
        except Exception as exc:  # noqa: BLE001 - startup must not be fatal here
            app.state.guideline_retriever = None
            log_event(
                "retrieval_preload_failed_event",
                event_data={"error_type": type(exc).__name__},
            )
    else:
        app.state.guideline_retriever = None
    if app.state.guideline_retriever is not None:
        # The ask path's retriever. When preload is off it loads on first use.
        set_retriever(app.state.guideline_retriever)
    # Conversation and document state are in this process's memory: warn,
    # loudly and at startup, if the server was told to run more than one.
    warn_if_multi_process()
    yield


app = FastAPI(
    lifespan=lifespan,
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
app.include_router(version_router)
app.include_router(pre_visit_brief_router)
app.include_router(follow_up_router)
app.include_router(conversation_router)
app.include_router(documents_router)
app.include_router(ask_router)
