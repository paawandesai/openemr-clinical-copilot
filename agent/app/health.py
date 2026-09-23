"""
Health and readiness endpoints — kept deliberately separate.

/health: always 200. Answers "is the process up," nothing more.
/ready:  200 only when the service can produce a Pre-Visit Brief right now;
         otherwise 503, naming the dependency that is down. Contract:
         ReadyResponse in app/models/api.py.

Required checks (decide readiness):
  fhir          GET {FHIR_BASE_URL}/metadata answers with a
                CapabilityStatement. Unauthenticated by necessity: the agent
                holds no token of its own (ARCHITECTURE.md §2), so this
                proves OpenEMR's FHIR API is up, not that any clinician's
                token will be accepted.
  llm_provider  Two signals, either of which can make it unhealthy:
                - The Anthropic API accepts this service's API key and
                  serves the drafting model (models.retrieve — no tokens
                  consumed). Proactive: catches a revoked key, a missing
                  model or an unreachable API before any brief fails.
                - The service's own recent model calls are not failing in a
                  run (app/reasoning/provider_health.py). Reactive, and the
                  only signal that can see an exhausted credit balance:
                  that leaves the key valid and the model retrievable while
                  every completion fails, which is exactly what happened on
                  2026-09-16 (findings.md F-60). Read uncached, so an
                  outage is not hidden for the cache TTL.

Reported, not required:
  observability_backend  Where telemetry goes, named in `backend`.
                "stdout" when Langfuse export is not configured: the JSON
                event log is then the production record, which is the
                intended state for this submission (ARCHITECTURE.md §1:
                metrics come from stdout via scripts/compute_metrics.py), so
                it is reported as a working backend, not as "disabled" (D55,
                amending D37's wording). "langfuse" when export is
                configured, with the self-hosted Langfuse auth check's
                result (agent/docs/observability.md); every event still goes
                to stdout first. Export failures never affect a brief
                (app/middleware/langfuse_export.py), so they do not make the
                service unready either.

Each required probe's result is cached for _CACHE_TTL_SECONDS, so polling
/ready — it is publicly reachable — cannot turn into load on OpenEMR or
calls against the API key. Probes time out after _PROBE_TIMEOUT_SECONDS and
never retry. Details returned are fixed strings: no URLs, paths or
exception text (F-37).

Separate endpoints, and 503 when a dependency is not verified healthy, on
purpose: this project's own audit (ARCHITECTURE.md §1, F-48) documents
OpenEMR's readyz endpoint returning HTTP 200 regardless of actual state — a
pattern this service does not repeat.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import cache
from typing import Literal

import anthropic
import httpx
import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.fhir import client as fhir_client
from app.fhir.exceptions import FhirClientError
from app.middleware import langfuse_export
from app.middleware.langfuse_export import ObservabilityExportUnavailable, check_export_health
from app.models.api import DependencyCheck, ObservabilityCheck, ReadyResponse
from app.reasoning import provider_health
from app.reasoning.draft_claims import MODEL

router = APIRouter()

_CACHE_TTL_SECONDS = 15.0
_PROBE_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class _Outcome:
    status: Literal["healthy", "unhealthy"]
    detail: str | None = None
    backend: Literal["stdout", "langfuse"] | None = None


_HEALTHY = _Outcome("healthy")


class _CachedProbe:
    """A probe whose last result is reused for _CACHE_TTL_SECONDS."""

    def __init__(self, probe: Callable[[], Awaitable[_Outcome]]) -> None:
        self._probe = probe
        self._outcome: _Outcome | None = None
        self._checked_at = float("-inf")

    async def outcome(self) -> _Outcome:
        if self._outcome is None or time.monotonic() - self._checked_at > _CACHE_TTL_SECONDS:
            self._outcome = await self._probe()
            self._checked_at = time.monotonic()
        return self._outcome

    def reset(self) -> None:
        self._outcome = None


async def _probe_fhir() -> _Outcome:
    try:
        await fhir_client.check_capability_statement(timeout_seconds=_PROBE_TIMEOUT_SECONDS)
    except httpx.TransportError:
        return _Outcome("unhealthy", "OpenEMR FHIR API unreachable")
    except FhirClientError:
        return _Outcome("unhealthy", "OpenEMR FHIR API returned an unexpected response")
    except Exception:
        structlog.get_logger().error("readiness_probe_failed", check="fhir", exc_info=True)
        return _Outcome("unhealthy", "OpenEMR FHIR API check failed")
    return _HEALTHY


@cache
def _anthropic_client() -> anthropic.AsyncAnthropic:
    # Same credential resolution as app/reasoning/draft_claims.py.
    return anthropic.AsyncAnthropic()


async def _probe_llm_provider() -> _Outcome:
    client = _anthropic_client().with_options(timeout=_PROBE_TIMEOUT_SECONDS, max_retries=0)
    try:
        await client.models.retrieve(MODEL)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
        return _Outcome("unhealthy", "LLM provider rejected the configured API key")
    except anthropic.NotFoundError:
        return _Outcome("unhealthy", "Drafting model is not available to the configured API key")
    except anthropic.RateLimitError:
        return _Outcome("unhealthy", "LLM provider is rate limiting the configured API key")
    except anthropic.APIConnectionError:
        return _Outcome("unhealthy", "LLM provider unreachable")
    except anthropic.APIStatusError:
        return _Outcome("unhealthy", "LLM provider returned an error")
    except Exception:
        # e.g. no API key configured at all.
        structlog.get_logger().error("readiness_probe_failed", check="llm_provider", exc_info=True)
        return _Outcome("unhealthy", "LLM provider check failed")
    return _HEALTHY


#: Nothing is probed for stdout: the event log is written by this process
#: before any export is attempted, so if /ready can answer, it is working.
_STDOUT = _Outcome(
    "healthy",
    "Events are written as JSON lines to stdout; Langfuse export is not configured",
    backend="stdout",
)


def _observability_outcome() -> _Outcome:
    """Non-blocking: check_export_health reads a background-refreshed result."""
    if not langfuse_export.export_configured():
        return _STDOUT
    try:
        check_export_health()
    except ObservabilityExportUnavailable as exc:  # messages are F-37-safe
        return _Outcome("unhealthy", str(exc), backend="langfuse")
    return _Outcome("healthy", backend="langfuse")


_FHIR = _CachedProbe(_probe_fhir)
_LLM_PROVIDER = _CachedProbe(_probe_llm_provider)


@router.get("/health")
async def health() -> dict[str, str]:
    """Always 200. Process-liveness only — no dependency checks."""
    return {"status": "alive"}


@router.get(
    "/ready",
    response_model=ReadyResponse,
    responses={503: {"model": ReadyResponse, "description": "A required dependency is down"}},
)
async def ready() -> JSONResponse:
    fhir, llm_provider = await asyncio.gather(_FHIR.outcome(), _LLM_PROVIDER.outcome())
    # Read uncached, and allowed to override a healthy probe. The probe
    # proves the key is accepted and the model exists; it spends no tokens,
    # so it cannot see an exhausted credit balance, which leaves both of
    # those true while every completion fails (findings.md F-60). This
    # reads the service's own recent model calls instead. It is not part of
    # _LLM_PROVIDER because that result is cached for _CACHE_TTL_SECONDS,
    # and a readiness signal that lags an outage by up to 15 s would
    # reproduce the blind window F-60 describes.
    if provider_health.looks_down():
        llm_provider = _Outcome("unhealthy", "Recent model calls are failing")
    observability = _observability_outcome()
    assert observability.backend is not None  # every branch of _observability_outcome names one

    is_ready = fhir.status == "healthy" and llm_provider.status == "healthy"
    body = ReadyResponse(
        status="ready" if is_ready else "not_ready",
        checks={
            "fhir": DependencyCheck(status=fhir.status, required=True, detail=fhir.detail),
            "llm_provider": DependencyCheck(
                status=llm_provider.status, required=True, detail=llm_provider.detail
            ),
            "observability_backend": ObservabilityCheck(
                status=observability.status,
                required=False,
                detail=observability.detail,
                backend=observability.backend,
            ),
        },
    )
    return JSONResponse(status_code=200 if is_ready else 503, content=body.model_dump(mode="json"))
