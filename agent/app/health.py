"""
Health and readiness endpoints — kept deliberately separate.

/health: always 200. Answers "is the process up," nothing more.
/ready:  answers "can this service actually do its job right now." The
         FHIR and LLM provider checks below are still stubs. The
         observability backend check is real: it reports whether the
         self-hosted Langfuse export (agent/docs/observability.md) is
         configured, reachable, and accepting this service's API keys.

Kept as two separate endpoints, and /ready returns 503 when any dependency
isn't actually verified healthy, on purpose: this project's own audit
(ARCHITECTURE.md §1, F-48) documents OpenEMR's own readyz endpoint always
returning HTTP 200 regardless of actual install state — a pattern this
service deliberately does not repeat.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.middleware.langfuse_export import check_export_health

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    """Always 200. Process-liveness only — no dependency checks."""
    return {"status": "alive"}


def _check_fhir() -> None:
    """
    TODO: implement a real FHIR reachability/token check.
    Not implemented in this scaffold — ARCHITECTURE.md §2: the agent reads
    exclusively through OpenEMR's FHIR R4 API under the requesting
    clinician's own OAuth2 token. No FHIR client exists yet in this task.
    """
    raise NotImplementedError("FHIR dependency check — ARCHITECTURE.md §2")


def _check_llm_provider() -> None:
    """
    TODO: implement a real LLM provider reachability check.
    Not implemented in this scaffold — ARCHITECTURE.md §5: the reasoning
    model that drafts structured claims. No LLM client exists yet in this
    task.
    """
    raise NotImplementedError(
        "LLM provider dependency check — ARCHITECTURE.md §5"
    )


def _check_observability_backend() -> None:
    """
    Self-hosted Langfuse (agent/docs/observability.md). Raises
    ObservabilityExportUnavailable — reported below as unhealthy — when
    export is not configured, Langfuse is unreachable, or it rejects the
    API keys. Never makes a network call on this thread: it reads the
    result of a background auth check refreshed every 15 s, and a result
    older than 60 s is never reported as healthy
    (app/middleware/langfuse_export.py).
    """
    check_export_health()


# FHIR and LLM provider stay stubs until the corresponding real dependency
# exists — see the TODOs above for exactly what each one still needs and
# which ARCHITECTURE.md section governs it.
_DEPENDENCY_CHECKS = {
    "fhir": _check_fhir,
    "llm_provider": _check_llm_provider,
    "observability_backend": _check_observability_backend,
}


@router.get("/ready")
async def ready() -> JSONResponse:
    """
    Runs each dependency check above and reports its status. While the FHIR
    and LLM provider checks are `not_implemented`, this endpoint honestly
    reports `not_ready` (HTTP 503) even when the observability backend is
    healthy, rather than repeating the unconditional-200 pattern this
    project's own audit flagged as a defect in OpenEMR's own readiness
    endpoint.
    """
    checks: dict[str, dict[str, str]] = {}
    all_healthy = True
    for name, check in _DEPENDENCY_CHECKS.items():
        try:
            check()
            checks[name] = {"status": "healthy"}
        except NotImplementedError as exc:
            all_healthy = False
            checks[name] = {"status": "not_implemented", "detail": str(exc)}
        except Exception as exc:  # e.g. ObservabilityExportUnavailable; messages are F-37-safe
            all_healthy = False
            checks[name] = {"status": "unhealthy", "detail": str(exc)}

    status_code = 200 if all_healthy else 503
    body = {
        "status": "ready" if all_healthy else "not_ready",
        "checks": checks,
    }
    return JSONResponse(status_code=status_code, content=body)
