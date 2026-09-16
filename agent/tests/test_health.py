"""
/health and /ready (app/health.py). No network: each probe's I/O is
replaced — the FHIR capability check, the Anthropic models call, and
Langfuse's export health.

    cd agent && python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import unittest
from typing import Any
from unittest import mock

import anthropic
import httpx
import httpx2
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import health
from app.fhir.exceptions import FhirServerError
from app.middleware.langfuse_export import ObservabilityExportUnavailable

_ANTHROPIC_REQUEST = httpx2.Request("GET", "https://api.anthropic.test/v1/models/claude-opus-5")


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(health.router)
    return TestClient(app)


class _FakeModels:
    def __init__(self) -> None:
        self.error: BaseException | None = None
        self.calls = 0

    async def retrieve(self, model: str) -> object:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return object()


class _FakeAnthropic:
    def __init__(self) -> None:
        self.models = _FakeModels()
        self.options: dict[str, Any] = {}

    def with_options(self, **options: Any) -> _FakeAnthropic:
        self.options = options
        return self


class _ReadyBase(unittest.TestCase):
    def setUp(self) -> None:
        for probe in (health._FHIR, health._LLM_PROVIDER):
            probe.reset()
            self.addCleanup(probe.reset)

        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for name in ("LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
            os.environ.pop(name, None)

        self.fhir = mock.AsyncMock(return_value=None)
        patcher = mock.patch.object(health.fhir_client, "check_capability_statement", self.fhir)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.anthropic = _FakeAnthropic()
        patcher = mock.patch.object(health, "_anthropic_client", lambda: self.anthropic)
        patcher.start()
        self.addCleanup(patcher.stop)

    def checks(self) -> tuple[int, dict[str, Any]]:
        response = _client().get("/ready")
        return response.status_code, response.json()


class HealthTest(_ReadyBase):
    def test_health_is_200_regardless_of_dependencies(self) -> None:
        self.fhir.side_effect = httpx.ConnectError("down")
        self.anthropic.models.error = anthropic.APIConnectionError(request=_ANTHROPIC_REQUEST)

        response = _client().get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "alive"})
        self.fhir.assert_not_awaited()


class ReadyTest(_ReadyBase):
    def test_ready_when_fhir_and_llm_are_healthy(self) -> None:
        status, body = self.checks()

        self.assertEqual(status, 200)
        self.assertEqual(
            body,
            {
                "status": "ready",
                "checks": {
                    "fhir": {"status": "healthy", "required": True, "detail": None},
                    "llm_provider": {"status": "healthy", "required": True, "detail": None},
                    "observability_backend": {
                        "status": "disabled",
                        "required": False,
                        "detail": "Langfuse export not configured",
                    },
                },
            },
        )
        self.assertEqual(self.anthropic.options, {"timeout": health._PROBE_TIMEOUT_SECONDS, "max_retries": 0})

    def test_fhir_unreachable_is_503_and_named_without_exception_text(self) -> None:
        self.fhir.side_effect = httpx.ConnectError("connect to 10.0.0.9:9300 failed")

        status, body = self.checks()

        self.assertEqual(status, 503)
        self.assertEqual(body["status"], "not_ready")
        self.assertEqual(
            body["checks"]["fhir"],
            {"status": "unhealthy", "required": True, "detail": "OpenEMR FHIR API unreachable"},
        )
        self.assertEqual(body["checks"]["llm_provider"]["status"], "healthy")
        self.assertNotIn("10.0.0.9", str(body))

    def test_fhir_wrong_response_is_503(self) -> None:
        self.fhir.side_effect = FhirServerError("FHIR server returned 500: stack trace here")

        status, body = self.checks()

        self.assertEqual(status, 503)
        self.assertEqual(body["checks"]["fhir"]["detail"], "OpenEMR FHIR API returned an unexpected response")
        self.assertNotIn("stack trace", str(body))

    def test_llm_provider_failures_are_503_with_a_fixed_detail(self) -> None:
        cases = {
            "rejected key": (
                anthropic.AuthenticationError(
                    "invalid x-api-key", response=httpx2.Response(401, request=_ANTHROPIC_REQUEST), body=None
                ),
                "LLM provider rejected the configured API key",
            ),
            "model unavailable": (
                anthropic.NotFoundError(
                    "model not found", response=httpx2.Response(404, request=_ANTHROPIC_REQUEST), body=None
                ),
                "Drafting model is not available to the configured API key",
            ),
            "unreachable": (
                anthropic.APIConnectionError(request=_ANTHROPIC_REQUEST),
                "LLM provider unreachable",
            ),
            "no credentials": (
                TypeError("Could not resolve authentication method"),
                "LLM provider check failed",
            ),
        }
        for label, (error, detail) in cases.items():
            with self.subTest(label):
                health._LLM_PROVIDER.reset()
                self.anthropic.models.error = error

                status, body = self.checks()

                self.assertEqual(status, 503)
                self.assertEqual(
                    body["checks"]["llm_provider"],
                    {"status": "unhealthy", "required": True, "detail": detail},
                )

    def test_observability_failure_is_reported_but_does_not_gate_readiness(self) -> None:
        os.environ["LANGFUSE_BASE_URL"] = "http://langfuse.test"
        with mock.patch.object(
            health, "check_export_health", side_effect=ObservabilityExportUnavailable("Langfuse unreachable")
        ):
            status, body = self.checks()

        self.assertEqual(status, 200)
        self.assertEqual(
            body["checks"]["observability_backend"],
            {"status": "unhealthy", "required": False, "detail": "Langfuse unreachable"},
        )

    def test_probe_results_are_reused_within_the_ttl(self) -> None:
        self.checks()
        self.checks()

        self.assertEqual(self.fhir.await_count, 1)
        self.assertEqual(self.anthropic.models.calls, 1)

    def test_probes_run_again_once_the_ttl_has_passed(self) -> None:
        with mock.patch.object(health, "_CACHE_TTL_SECONDS", -1.0):
            self.checks()
            self.checks()

        self.assertEqual(self.fhir.await_count, 2)
        self.assertEqual(self.anthropic.models.calls, 2)


if __name__ == "__main__":
    unittest.main()
