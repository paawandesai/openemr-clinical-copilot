"""
/health and /ready (app/health.py). No network: the observability check's
exporter is replaced; FHIR and LLM provider stubs are exercised as-is.

    cd agent && python -m unittest discover -s tests -v
"""

from __future__ import annotations

import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import health
from app.middleware import langfuse_export
from app.middleware.langfuse_export import ObservabilityExportUnavailable


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(health.router)
    return TestClient(app)


class HealthEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        langfuse_export._exporter = None
        self.addCleanup(setattr, langfuse_export, "_exporter", None)
        self.addCleanup(setattr, langfuse_export, "_disabled_reason", langfuse_export._disabled_reason)

    def test_health_is_200_regardless_of_dependencies(self) -> None:
        with mock.patch.object(
            health, "check_export_health", side_effect=ObservabilityExportUnavailable("Langfuse unreachable")
        ):
            response = _client().get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "alive"})

    def test_fhir_and_llm_stubs_are_unchanged(self) -> None:
        with mock.patch.object(health, "check_export_health"):
            checks = _client().get("/ready").json()["checks"]
        self.assertEqual(
            checks["fhir"],
            {"status": "not_implemented", "detail": "FHIR dependency check — ARCHITECTURE.md §2"},
        )
        self.assertEqual(
            checks["llm_provider"],
            {"status": "not_implemented", "detail": "LLM provider dependency check — ARCHITECTURE.md §5"},
        )

    def test_observability_healthy_still_503_while_stubs_remain(self) -> None:
        with mock.patch.object(health, "check_export_health"):
            response = _client().get("/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "not_ready")
        self.assertEqual(response.json()["checks"]["observability_backend"], {"status": "healthy"})

    def test_observability_unreachable_is_reported_not_raised(self) -> None:
        with mock.patch.object(
            health, "check_export_health", side_effect=ObservabilityExportUnavailable("Langfuse unreachable")
        ):
            response = _client().get("/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["checks"]["observability_backend"],
            {"status": "unhealthy", "detail": "Langfuse unreachable"},
        )

    def test_unconfigured_export_is_unhealthy_through_the_real_function(self) -> None:
        langfuse_export._disabled_reason = "Langfuse export not configured"
        response = _client().get("/ready")
        self.assertEqual(
            response.json()["checks"]["observability_backend"],
            {"status": "unhealthy", "detail": "Langfuse export not configured"},
        )


if __name__ == "__main__":
    unittest.main()
