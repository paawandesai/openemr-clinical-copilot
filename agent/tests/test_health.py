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
from structlog.testing import capture_logs

from app import health
from app.fhir.exceptions import FhirServerError
from app.middleware.langfuse_export import ObservabilityExportUnavailable
from app.reasoning import provider_health

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


class _FakeUsage:
    input_tokens = 10
    output_tokens = 5
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0
    iterations: list[Any] = []


class _FakeTextBlock:
    type = "text"
    text = '{"claims": []}'


class _FakeMessage:
    model = "claude-opus-5"
    stop_reason = "end_turn"
    stop_details = None
    usage = _FakeUsage()
    content = [_FakeTextBlock()]


class _FakeStream:
    request_id = "req_fake"

    async def get_final_message(self) -> _FakeMessage:
        return _FakeMessage()


class _StreamingAnthropic:
    """
    Enough of AsyncAnthropic for draft_claims' one call:
    `async with _client().beta.messages.stream(...) as stream`. Raises
    `error` from the context manager when one is given, which is where a
    credit-balance failure surfaces.
    """

    def __init__(self, error: BaseException | None) -> None:
        self._error = error
        self.beta = self

    @property
    def messages(self) -> _StreamingAnthropic:
        return self

    def stream(self, **_: Any) -> _StreamingAnthropic:
        return self

    async def __aenter__(self) -> _FakeStream:
        if self._error is not None:
            raise self._error
        return _FakeStream()

    async def __aexit__(self, *_: Any) -> bool:
        return False


class _ReadyBase(unittest.TestCase):
    def setUp(self) -> None:
        for probe in (health._FHIR, health._LLM_PROVIDER):
            probe.reset()
            self.addCleanup(probe.reset)
        provider_health.reset()
        self.addCleanup(provider_health.reset)

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
        self.assertEqual(
            self.anthropic.options, {"timeout": health._PROBE_TIMEOUT_SECONDS, "max_retries": 0}
        )

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
        self.assertEqual(
            body["checks"]["fhir"]["detail"], "OpenEMR FHIR API returned an unexpected response"
        )
        self.assertNotIn("stack trace", str(body))

    def test_llm_provider_failures_are_503_with_a_fixed_detail(self) -> None:
        cases = {
            "rejected key": (
                anthropic.AuthenticationError(
                    "invalid x-api-key",
                    response=httpx2.Response(401, request=_ANTHROPIC_REQUEST),
                    body=None,
                ),
                "LLM provider rejected the configured API key",
            ),
            "model unavailable": (
                anthropic.NotFoundError(
                    "model not found",
                    response=httpx2.Response(404, request=_ANTHROPIC_REQUEST),
                    body=None,
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
            health,
            "check_export_health",
            side_effect=ObservabilityExportUnavailable("Langfuse unreachable"),
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


class ProviderOutageTest(_ReadyBase):
    """
    F-60: the credit balance ran out, every brief returned 502, and /ready
    kept reporting llm_provider healthy — because models.retrieve spends no
    tokens, so it cannot see a billing failure. In every test here the
    tokenless probe SUCCEEDS, which is what made the outage invisible.
    """

    def _fail(self, times: int) -> None:
        for _ in range(times):
            provider_health.record_failure()

    def test_ready_is_503_when_recent_model_calls_are_failing(self) -> None:
        self._fail(provider_health._FAILURE_THRESHOLD)

        status, body = self.checks()

        self.assertEqual(status, 503)
        self.assertEqual(body["status"], "not_ready")
        self.assertEqual(
            body["checks"]["llm_provider"],
            {"status": "unhealthy", "required": True, "detail": "Recent model calls are failing"},
        )
        # The probe itself still passed: this is the F-60 blind spot.
        self.assertEqual(self.anthropic.models.error, None)

    def test_a_single_failed_call_does_not_make_the_service_unready(self) -> None:
        """One bad request is that request's problem, not the provider's."""
        self._fail(1)

        status, body = self.checks()

        self.assertEqual(status, 200)
        self.assertEqual(body["checks"]["llm_provider"]["status"], "healthy")

    def test_a_success_clears_the_run_of_failures(self) -> None:
        self._fail(provider_health._FAILURE_THRESHOLD)
        provider_health.record_success()

        status, body = self.checks()

        self.assertEqual(status, 200)
        self.assertEqual(body["checks"]["llm_provider"]["status"], "healthy")

    def test_the_run_goes_stale_so_readiness_can_recover_without_traffic(self) -> None:
        """
        Without this, readiness that gates traffic deadlocks: no traffic
        means no model calls, so no success can ever clear the count.
        """
        self._fail(provider_health._FAILURE_THRESHOLD)
        self.assertEqual(self.checks()[0], 503)

        with mock.patch.object(provider_health, "_RECOVERY_SECONDS", -1.0):
            status, body = self.checks()

        self.assertEqual(status, 200)
        self.assertEqual(body["checks"]["llm_provider"]["status"], "healthy")

    def test_the_outage_is_reported_without_waiting_for_the_probe_cache(self) -> None:
        """
        The signal is read uncached. A readiness check that lagged an
        outage by the cache TTL would reproduce F-60's blind window.
        """
        self.assertEqual(self.checks()[0], 200)  # caches a healthy probe result

        self._fail(provider_health._FAILURE_THRESHOLD)
        status, _ = self.checks()

        self.assertEqual(status, 503)
        self.assertEqual(self.anthropic.models.calls, 1)  # cache was not re-run


class ProviderHealthWiringTest(unittest.TestCase):
    """
    The counter is only useful if the real drafting path feeds it. These
    drive app/reasoning/draft_claims.py itself, not the module in isolation.
    """

    def setUp(self) -> None:
        provider_health.reset()
        self.addCleanup(provider_health.reset)

    def _draft(self, error: BaseException | None) -> None:
        """Run the real drafting call with the Anthropic client replaced."""
        import asyncio

        from app.models.fhir_resources import FhirResourceType as R
        from app.reasoning import draft_claims

        retrieved: dict[Any, list[Any]] = {
            R.CONDITION: [],
            R.MEDICATION_REQUEST: [],
            R.ALLERGY_INTOLERANCE: [],
        }
        with (
            mock.patch.object(draft_claims, "_client", lambda: _StreamingAnthropic(error)),
            mock.patch.object(draft_claims, "_STUB_LLM_DRAFTING", False),
            capture_logs(),
        ):
            asyncio.run(draft_claims.draft_pre_visit_brief(retrieved, patient_session_key="u:p:s"))

    def test_a_credit_balance_error_is_recorded_against_readiness(self) -> None:
        """The exact shape F-60 saw: a 400 invalid_request_error on billing."""
        credit_error = anthropic.BadRequestError(
            "Your credit balance is too low to access the Anthropic API.",
            response=httpx2.Response(400, request=_ANTHROPIC_REQUEST),
            body=None,
        )

        with self.assertRaises(anthropic.BadRequestError):
            self._draft(credit_error)

        self.assertEqual(provider_health.consecutive_failures(), 1)

        for _ in range(provider_health._FAILURE_THRESHOLD - 1):
            with self.assertRaises(anthropic.BadRequestError):
                self._draft(credit_error)

        self.assertTrue(provider_health.looks_down())

    def test_a_successful_draft_clears_the_run(self) -> None:
        provider_health.record_failure()
        provider_health.record_failure()

        self._draft(None)

        self.assertEqual(provider_health.consecutive_failures(), 0)
        self.assertFalse(provider_health.looks_down())


if __name__ == "__main__":
    unittest.main()
