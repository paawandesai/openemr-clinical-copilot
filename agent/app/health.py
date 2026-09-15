"""
Health and readiness endpoints — kept deliberately separate.

/health: always 200. Answers "is the process up," nothing more.
/ready:  answers "can this service actually do its job right now." Each
         dependency check below is a stub — this task scaffolds the
         service; it does not wire a real FHIR client, LLM provider, or
         observability backend. The observability backend check in
         particular cannot be implemented yet, not just left unimplemented:
         ARCHITECTURE.md §6 lists that backend's placement as an
         explicitly open, undecided risk, so there is nothing concrete to
         check against until that decision is made. Stated here rather
         than faked.

Kept as two separate endpoints, and /ready returns 503 when any dependency
isn't actually verified healthy, on purpose: this project's own audit
(ARCHITECTURE.md §1, F-48) documents OpenEMR's own readyz endpoint always
returning HTTP 200 regardless of actual install state — a pattern this
service deliberately does not repeat.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

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
    TODO: this cannot be implemented yet, not just left unimplemented.
    ARCHITECTURE.md §6 lists the observability backend's placement
    (on-droplet vs. elsewhere; which service shape) as an explicitly OPEN,
    undecided risk — there is nothing concrete to check against until that
    decision is made.
    """
    raise NotImplementedError(
        "Observability backend dependency check — ARCHITECTURE.md §6 "
        "(placement not yet decided)"
    )


# Every entry here must stay a stub until the corresponding real
# dependency exists — see the TODOs above for exactly what each one still
# needs and which ARCHITECTURE.md section governs it.
_DEPENDENCY_CHECKS = {
    "fhir": _check_fhir,
    "llm_provider": _check_llm_provider,
    "observability_backend": _check_observability_backend,
}


@router.get("/ready")
async def ready() -> JSONResponse:
    """
    Runs each dependency stub above and reports its status. Every check is
    currently `not_implemented` by construction, so this endpoint honestly
    reports `not_ready` (HTTP 503) rather than repeating the
    unconditional-200 pattern this project's own audit flagged as a defect
    in OpenEMR's own readiness endpoint.
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
        except Exception as exc:  # pragma: no cover - defensive; not yet reachable
            all_healthy = False
            checks[name] = {"status": "unhealthy", "detail": str(exc)}

    status_code = 200 if all_healthy else 503
    body = {
        "status": "ready" if all_healthy else "not_ready",
        "checks": checks,
    }
    return JSONResponse(status_code=status_code, content=body)
