"""
FastAPI entrypoint — Clinical Co-Pilot agent service.

Scaffold only (see /agent/README.md). This wires together the load-bearing
instrumentation — correlation ID + structured logging, which
docs/KEY_METRICS.md states is a precondition for computing any of its six
metrics — and the two health endpoints. It does not yet call FHIR, call an
LLM, or run verification; see the `raise NotImplementedError` stubs in
app/health.py for exactly where the next task picks up. No LangGraph graph
is constructed here yet, for the same reason.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.health import router as health_router
from app.middleware.correlation import CorrelationIdMiddleware
from app.middleware.logging import configure_logging

configure_logging()

app = FastAPI(
    title="Clinical Co-Pilot Agent",
    description=(
        "Scaffold service for the Clinical Co-Pilot agent. See "
        "docs/ARCHITECTURE.md, docs/USERS.md, and docs/KEY_METRICS.md at "
        "the repository root for the design this implements."
    ),
    version="0.1.0",
)

# Load-bearing, not optional: every downstream metric in
# docs/KEY_METRICS.md depends on every log line being able to carry a
# correlation_id for its request's lifetime.
app.add_middleware(CorrelationIdMiddleware)

app.include_router(health_router)
