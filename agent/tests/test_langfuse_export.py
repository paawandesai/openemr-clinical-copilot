"""
Tests for the Langfuse export path (app/middleware/langfuse_export.py) and
its hook in app/middleware/logging.py. No network: the Langfuse client is
replaced with a recording fake. stdlib unittest, so no test dependency is
added to requirements.txt.

    cd agent && python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import inspect
import io
import json
import os
import time
import unittest
from typing import Any
from unittest import mock

import httpx
from langfuse import Langfuse

from app.middleware import langfuse_export
from app.middleware import logging as agent_logging
from app.models.metrics_events import UseCase

_LANGFUSE_ENV = ("LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")


class _FakeObservation:
    def __init__(self, trace_id: str | None, n: int) -> None:
        self.trace_id = trace_id or f"{n:032x}"
        self.id = f"{n:016x}"
        self.ended = False

    def end(self) -> _FakeObservation:
        self.ended = True
        return self


class _FakeClient:
    create_trace_id = staticmethod(Langfuse.create_trace_id)

    def __init__(self, auth: bool | BaseException = True) -> None:
        self.events: list[dict[str, Any]] = []
        self.generations: list[dict[str, Any]] = []
        self.scores: list[dict[str, Any]] = []
        self.auth = auth
        self._n = 0

    def _observation(self, record: dict[str, Any]) -> _FakeObservation:
        # What the real client's private TracerProvider would do: use the
        # trace id the exporter pinned for this span, if any.
        pinned = langfuse_export._pinned_trace_id.get()
        record["pinned_trace_id"] = f"{pinned:032x}" if pinned is not None else None
        self._n += 1
        return _FakeObservation(record["pinned_trace_id"], self._n)

    def create_event(self, **kwargs: Any) -> _FakeObservation:
        self.events.append(kwargs)
        return self._observation(kwargs)

    def start_observation(self, **kwargs: Any) -> _FakeObservation:
        self.generations.append(kwargs)
        return self._observation(kwargs)

    def create_score(self, **kwargs: Any) -> None:
        self.scores.append(kwargs)

    def auth_check(self) -> bool:
        # The real SDK raises httpx.ConnectError when nothing is listening
        # and UnauthorizedError (status_code 401) on bad keys — verified
        # against server 4.37.0.
        if isinstance(self.auth, BaseException):
            raise self.auth
        return self.auth

    def flush(self) -> None:
        pass


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for name in _LANGFUSE_ENV:
            os.environ.pop(name, None)
        langfuse_export._exporter = None
        self.addCleanup(setattr, langfuse_export, "_exporter", None)

        self.propagated: list[dict[str, Any]] = []

        @contextlib.contextmanager
        def _record_propagation(**kwargs: Any):
            self.propagated.append(kwargs)
            yield

        patcher = mock.patch("langfuse.propagate_attributes", _record_propagation)
        patcher.start()
        self.addCleanup(patcher.stop)

    def install_fake(self, **kwargs: Any) -> _FakeClient:
        client = _FakeClient(**kwargs)
        langfuse_export._exporter = langfuse_export.LangfuseExporter(client)  # type: ignore[arg-type]
        return client

    def export(self, event_type: str, event_data: dict[str, Any], **kwargs: Any) -> None:
        langfuse_export._exporter.export(  # type: ignore[union-attr]
            event_type=event_type,
            event_data=event_data,
            use_case=kwargs.get("use_case"),
            patient_session_key=kwargs.get("patient_session_key"),
            correlation_id=kwargs.get("correlation_id", "corr-1"),
        )


class SignatureContractTest(unittest.TestCase):
    """The two parallel worktrees call these; their signatures must not move."""

    def test_configure_logging_signature_unchanged(self) -> None:
        self.assertEqual(
            str(inspect.signature(agent_logging.configure_logging)),
            "(level: 'int' = 20) -> 'None'",
        )

    def test_log_event_signature_unchanged(self) -> None:
        self.assertEqual(
            str(inspect.signature(agent_logging.log_event)),
            "(event_type: 'str', event_data: 'dict[str, Any] | None' = None, *, "
            "use_case: 'UseCase | None' = None, patient_session_key: 'str | None' = None, "
            "logger: 'structlog.typing.FilteringBoundLogger | None' = None) -> 'None'",
        )


class ConfigurationTest(_Base):
    def _configure_capturing_stdout(self) -> list[dict[str, Any]]:
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            agent_logging.configure_logging()
            agent_logging.log_event("retrieval_event_skipped", {"resource_type": "Condition"})
        return [json.loads(line) for line in buf.getvalue().splitlines()]

    def test_unconfigured_is_stdout_only_and_says_so(self) -> None:
        lines = self._configure_capturing_stdout()
        self.assertEqual(lines[0]["event"], "observability_export_disabled")
        self.assertEqual(lines[1]["event"], "agent_event")
        self.assertIsNone(langfuse_export._exporter)
        with self.assertRaisesRegex(
            langfuse_export.ObservabilityExportUnavailable, "not configured"
        ):
            langfuse_export.check_export_health()

    def test_partial_configuration_is_an_error_not_a_guess(self) -> None:
        os.environ["LANGFUSE_BASE_URL"] = "http://localhost:3000"
        lines = self._configure_capturing_stdout()
        self.assertEqual(lines[0]["level"], "error")
        self.assertEqual(
            lines[0]["missing"], ["LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"]
        )
        self.assertIsNone(langfuse_export._exporter)

    def test_stdout_line_is_still_written_when_export_enabled(self) -> None:
        client = self.install_fake()
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            agent_logging.configure_logging()
            agent_logging.log_event("retrieval_event_skipped", {"resource_type": "Condition"})
        self.assertIn('"event": "agent_event"', buf.getvalue())
        self.assertEqual(len(client.events), 1)

    def test_export_failure_never_reaches_the_caller(self) -> None:
        client = self.install_fake()
        client.create_event = mock.Mock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            agent_logging.configure_logging()
            agent_logging.log_event("retrieval_event_skipped", {})
        self.assertIn("observability_export_failed", buf.getvalue())


class MetricDerivationTest(_Base):
    def test_trace_is_derived_from_correlation_id(self) -> None:
        client = self.install_fake()
        self.export("retrieval_event_skipped", {}, correlation_id="abc")
        self.export("retrieval_event_skipped", {}, correlation_id="abc")
        ids = {e["pinned_trace_id"] for e in client.events}
        self.assertEqual(ids, {Langfuse.create_trace_id(seed="abc")})
        self.assertNotIn("trace_context", client.events[0])
        # The pin must not leak past the export call.
        self.assertIsNone(langfuse_export._pinned_trace_id.get())

    def test_no_correlation_id_means_no_forced_trace(self) -> None:
        client = self.install_fake()
        self.export("retrieval_event_skipped", {}, correlation_id=None)
        self.assertIsNone(client.events[0]["pinned_trace_id"])

    def test_session_trace_name_and_use_case_tag(self) -> None:
        self.install_fake()
        self.export(
            "retrieval_event_skipped",
            {},
            use_case=UseCase.CHANGE_DETECTION,
            patient_session_key="u|p|s",
        )
        self.assertEqual(
            self.propagated[0],
            {
                "session_id": "u|p|s",
                "tags": ["use_case:change_detection"],
                "trace_name": "change_detection",
            },
        )

    def test_metric_1_and_2_claim_scores(self) -> None:
        client = self.install_fake()
        base = {
            "source_id": "cond-1",
            "resource_type": "Condition",
            "correlation_id": "corr-1",
            "use_case": "pre_visit_brief",
            "patient_session_key": "u|p|s",
        }
        self.export("claim_verified_event", {**base, "passed": True, "disclosed_as_unverified": False})
        self.export("claim_verified_event", {**base, "passed": False, "disclosed_as_unverified": True})
        self.assertEqual(
            [(s["name"], s["value"], s["data_type"]) for s in client.scores],
            [
                ("verification_passed", 1.0, "BOOLEAN"),
                ("verification_passed", 0.0, "BOOLEAN"),
                ("unverified_claim_disclosed", 1.0, "BOOLEAN"),
            ],
        )
        self.assertTrue(all(s["observation_id"] for s in client.scores))

    def test_metric_3_scores_only_out_of_scope_requests(self) -> None:
        client = self.install_fake()
        held = ["user/Patient.read"]
        self.export("authorization_check_event", {"requested_scope": "user/Patient.read", "held_scopes": held, "outcome": "accepted", "correlation_id": "c"})
        self.export("authorization_check_event", {"requested_scope": "user/Observation.read", "held_scopes": held, "outcome": "rejected", "correlation_id": "c"})
        self.export("authorization_check_event", {"requested_scope": "user/Observation.read", "held_scopes": held, "outcome": "accepted", "correlation_id": "c"})
        self.export("authorization_check_event", {"requested_scope": "user/Observation.read", "held_scopes": held, "outcome": "timeout", "correlation_id": "c"})
        self.assertEqual(
            [(s["name"], s["value"]) for s in client.scores],
            [("out_of_scope_request_rejected", 1.0), ("out_of_scope_request_rejected", 0.0)],
        )
        self.assertEqual(
            [e["level"] for e in client.events], ["DEFAULT", "DEFAULT", "ERROR", "WARNING"]
        )

    def test_metric_4_latency_score(self) -> None:
        client = self.install_fake()
        self.export("latency_event", {"use_case": "pre_visit_brief", "trigger_ts": 100.0, "rendered_ts": 142.5, "correlation_id": "c"})
        self.export("latency_event", {"use_case": "pre_visit_brief", "trigger_ts": 100.0, "rendered_ts": 99.0, "correlation_id": "c"})
        self.assertEqual(
            [(s["name"], s["value"], s["data_type"]) for s in client.scores],
            [("response_latency_seconds", 42.5, "NUMERIC")],
        )
        self.assertEqual(client.events[1]["level"], "WARNING")

    def test_metric_5_retrieval_tags(self) -> None:
        self.install_fake()
        self.export(
            "retrieval_event",
            {"resource_type": "AllergyIntolerance", "correlation_id": "c", "use_case": "pre_visit_brief"},
            use_case=UseCase.PRE_VISIT_BRIEF,
        )
        self.assertEqual(
            self.propagated[0]["tags"],
            ["use_case:pre_visit_brief", "retrieved:AllergyIntolerance"],
        )

    def test_metric_6_llm_usage_becomes_generation(self) -> None:
        client = self.install_fake()
        self.export(
            "llm_usage_event",
            {"model": "claude-sonnet-5", "input_tokens": 1200, "output_tokens": 300, "cache_read_input_tokens": 800},
        )
        self.assertEqual(client.events, [])
        generation = client.generations[0]
        self.assertEqual(generation["as_type"], "generation")
        self.assertEqual(generation["model"], "claude-sonnet-5")
        self.assertEqual(
            generation["usage_details"],
            {"input": 1200, "output": 300, "input_cache_read": 800},
        )
        self.assertNotIn("input", generation)
        self.assertNotIn("output", generation)

    def test_payload_that_does_not_parse_is_visible_not_dropped(self) -> None:
        client = self.install_fake()
        self.export("claim_verified_event", {"passed": "maybe"})
        self.assertEqual(client.scores, [])
        self.assertEqual(client.events[0]["level"], "WARNING")
        self.assertIn("no metric derived", client.events[0]["status_message"])

    def test_never_sends_input_or_output(self) -> None:
        client = self.install_fake()
        self.export("retrieval_event_skipped", {"resource_type": "Condition"})
        self.assertNotIn("input", client.events[0])
        self.assertNotIn("output", client.events[0])


class _Unauthorized(Exception):
    status_code = 401


class HealthTest(_Base):
    def _wait_for_message(self, expected: str | None) -> str | None:
        """Polls until check_export_health() passes (expected=None) or raises containing `expected`."""
        deadline = time.monotonic() + 5
        last: str | None = "never checked"
        while time.monotonic() < deadline:
            try:
                langfuse_export.check_export_health()
                last = None
            except langfuse_export.ObservabilityExportUnavailable as exc:
                last = str(exc)
            if (expected is None and last is None) or (expected and last and expected in last):
                return last
            time.sleep(0.01)
        return last

    def test_health_is_non_blocking_then_reflects_background_check(self) -> None:
        self.install_fake(auth=True)
        with self.assertRaisesRegex(langfuse_export.ObservabilityExportUnavailable, "not yet confirmed"):
            langfuse_export.check_export_health()
        self.assertIsNone(self._wait_for_message(None))

    def test_connection_failure_reports_unreachable(self) -> None:
        self.install_fake(auth=httpx.ConnectError("connection refused"))
        self.assertEqual(self._wait_for_message("unreachable"), "Langfuse unreachable")

    def test_rejected_keys_report_rejected(self) -> None:
        self.install_fake(auth=_Unauthorized())
        self.assertEqual(
            self._wait_for_message("rejected"), "Langfuse rejected the configured API keys"
        )

    def test_messages_never_leak_exception_text(self) -> None:
        self.install_fake(auth=RuntimeError("secret internal detail http://10.0.0.5:3000"))
        message = self._wait_for_message("failed")
        self.assertEqual(message, "Langfuse reachability check failed")

    def test_result_older_than_max_staleness_is_not_reported_healthy(self) -> None:
        self.install_fake(auth=True)
        exporter = langfuse_export._exporter
        assert exporter is not None
        exporter._auth_result = "ok"
        exporter._auth_checked_at = time.monotonic() - langfuse_export._AUTH_CHECK_MAX_STALENESS_SECONDS - 1
        exporter._auth_check_running = True  # hold the refresh so the stale result is what's read
        with self.assertRaisesRegex(langfuse_export.ObservabilityExportUnavailable, "not yet confirmed"):
            langfuse_export.check_export_health()

    def test_result_within_ttl_is_reported_without_a_new_check(self) -> None:
        client = self.install_fake(auth=httpx.ConnectError("would fail if called"))
        exporter = langfuse_export._exporter
        assert exporter is not None
        exporter._auth_result = "ok"
        exporter._auth_checked_at = time.monotonic()
        langfuse_export.check_export_health()  # cached, fresh: passes, no network call
        self.assertFalse(exporter._auth_check_running)


if __name__ == "__main__":
    unittest.main()
