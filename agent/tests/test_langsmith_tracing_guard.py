"""
Regression tests for the LangSmith tracing guard on the Pre-Visit Brief graph
(docs/audit/findings.md F-56). With LANGSMITH_TRACING=true, running the graph
through anything app.graph exports must send nothing, and the bearer token
must never be in what tracing would send.

No FHIR or Anthropic calls and no credentials: retrieval and claim drafting
are replaced with fakes returning marker records. The graph, its nodes,
verification, rendering and LangSmith itself are real. Outbound traffic is
contained two ways, so a broken guard fails a test instead of sending data:

- LANGSMITH_ENDPOINT points at a recorder on 127.0.0.1.
- Every non-loopback DNS lookup is recorded and refused, so a request to
  api.smith.langchain.com is caught even if the endpoint setting is ignored.

The control tests switch both guard layers off and assert the recorder does
receive the chart records. Without them, a recorder that silently stopped
receiving would make every "nothing was sent" assertion pass.

    cd agent && python -m unittest discover -s tests -v
"""

from __future__ import annotations

import asyncio
import contextlib
import http.server
import importlib
import os
import pkgutil
import socket
import threading
import time
import unittest
from datetime import datetime
from typing import Any
from unittest import mock

import langsmith.run_trees
import langsmith.utils
from langchain_core.tracers.langchain import LangChainTracer, wait_for_all_tracers
from langgraph.pregel import Pregel
from structlog.testing import capture_logs

import app.graph
from app.graph import pre_visit_brief as pvb
from app.models.claims import Claim
from app.models.fhir_resources import (
    AllergyIntolerance,
    Condition,
    FhirResourceType,
    MedicationRequest,
)
from app.reasoning.draft_claims import PreVisitBriefDraft, SectionCount

TOKEN = "SENTINEL-BEARER-TOKEN-3c9e1f"
PATIENT_ID = "SENTINEL-PATIENT-ID-8a24"
SESSION_KEY = "SENTINEL-SESSION-KEY-d71b"
CHART_MARKERS = (
    "SENTINEL-CHART-CONDITION",
    "SENTINEL-CHART-MEDICATION",
    "SENTINEL-CHART-ALLERGY",
)

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class _Recorder:
    """Records every HTTP request sent to it and answers 200 {}."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, str], bytes]] = []
        requests = self.requests

        class Handler(http.server.BaseHTTPRequestHandler):
            def _record(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                requests.append((self.command, self.path, dict(self.headers), body))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

            do_GET = do_POST = do_PATCH = _record

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def summary(self) -> list[str]:
        return [f"{method} {path} ({len(body)} bytes)" for method, path, _, body in self.requests]

    def payload(self) -> bytes:
        """Every header and body received, as one searchable blob."""
        return b"\n".join(
            repr(headers).encode() + b"\n" + body for _, _, headers, body in self.requests
        )


def _reset_langsmith() -> None:
    """
    Sends anything queued, then drops LangSmith's process-wide caches: it
    caches env lookups and its client (with the endpoint it was built with),
    so without this one test's settings would silently apply to the next.
    """
    wait_for_all_tracers()
    langsmith.run_trees._CLIENT = None
    langsmith.utils.get_env_var.cache_clear()
    langsmith.utils.get_tracer_project.cache_clear()


def _records() -> dict[FhirResourceType, list[Any]]:
    return {
        FhirResourceType.CONDITION: [
            Condition(source_id="cond-1", display=CHART_MARKERS[0], clinical_status="active")
        ],
        FhirResourceType.MEDICATION_REQUEST: [
            MedicationRequest(
                source_id="med-1",
                display=CHART_MARKERS[1],
                status="active",
                authored_date=datetime(2014, 2, 1),
            )
        ],
        FhirResourceType.ALLERGY_INTOLERANCE: [
            AllergyIntolerance(
                source_id="allergy-1", display=CHART_MARKERS[2], clinical_status="active"
            )
        ],
    }


class _TracingEnabled(unittest.TestCase):
    """LANGSMITH_TRACING=true, traffic contained, FHIR and drafting faked."""

    def setUp(self) -> None:
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)

        self.recorder = _Recorder()
        self.addCleanup(self.recorder.stop)
        for name in ("LANGCHAIN_TRACING_V2", "LANGCHAIN_API_KEY", "LANGSMITH_PROJECT"):
            os.environ.pop(name, None)
        os.environ.update(
            {
                "LANGSMITH_TRACING": "true",
                "LANGSMITH_ENDPOINT": self.recorder.url,
                "LANGCHAIN_ENDPOINT": self.recorder.url,
                "LANGSMITH_API_KEY": "test-not-a-real-key",
            }
        )

        self.dns_lookups: list[str] = []
        real_getaddrinfo = socket.getaddrinfo

        def contained_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
            if host in _LOOPBACK:
                return real_getaddrinfo(host, *args, **kwargs)
            self.dns_lookups.append(str(host))
            raise socket.gaierror(f"non-loopback lookup refused in test: {host}")

        dns = mock.patch("socket.getaddrinfo", contained_getaddrinfo)
        dns.start()
        self.addCleanup(dns.stop)

        # Runs first on cleanup, while the recorder and DNS containment are
        # still up, so anything queued lands in the recorder.
        _reset_langsmith()
        self.addCleanup(_reset_langsmith)

        self.fhir_calls = 0
        records = _records()

        def fake_fetch(resource_type: FhirResourceType) -> Any:
            async def fetch(token: str, patient_id: str, **_: Any) -> list[Any]:
                self.fhir_calls += 1
                return records[resource_type]

            return fetch

        async def fake_draft(
            retrieved: dict[FhirResourceType, list[Any]], **_: Any
        ) -> PreVisitBriefDraft:
            claims = [
                Claim(source_id=r.source_id, field="display", value=r.display, resource_type=rt)
                for rt, rs in retrieved.items()
                for r in rs
            ]
            counts = {
                rt: SectionCount(retrieved=len(rs), in_brief=len(rs))
                for rt, rs in retrieved.items()
            }
            return PreVisitBriefDraft(claims=claims, section_counts=counts)

        async def fake_patient_read(token: str, patient_id: str) -> None:
            self.fhir_calls += 1

        patcher = mock.patch.object(pvb.fhir_client, "ensure_patient_exists", fake_patient_read)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name, resource_type in (
            ("get_conditions", FhirResourceType.CONDITION),
            ("get_medication_requests", FhirResourceType.MEDICATION_REQUEST),
            ("get_allergy_intolerances", FhirResourceType.ALLERGY_INTOLERANCE),
        ):
            patcher = mock.patch.object(pvb.fhir_client, name, fake_fetch(resource_type))
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(pvb, "draft_pre_visit_brief", fake_draft)
        patcher.start()
        self.addCleanup(patcher.stop)

    def context(self) -> pvb.PreVisitBriefContext:
        return pvb.PreVisitBriefContext(
            token=TOKEN,
            patient_id=PATIENT_ID,
            patient_session_key=SESSION_KEY,
            trigger_ts=time.time(),
        )

    def run_brief(self) -> pvb.PreVisitBriefResult:
        with capture_logs():
            return asyncio.run(pvb.run_pre_visit_brief(TOKEN, PATIENT_ID, SESSION_KEY))

    def disable_both_guards(self) -> None:
        """What removing the guard would do. The control for 'nothing sent'."""
        for name, replacement in (
            ("tracing_context", lambda **_: contextlib.nullcontext()),
            ("_refuse_if_traced", lambda config: None),
        ):
            patcher = mock.patch.object(pvb, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def smith_lookups(self) -> list[str]:
        return [host for host in self.dns_lookups if "smith.langchain.com" in host]

    def assert_nothing_sent(self) -> None:
        wait_for_all_tracers()
        self.assertEqual(self.smith_lookups(), [], "request to smith.langchain.com attempted")
        self.assertEqual(self.dns_lookups, [], "non-loopback lookup attempted")
        self.assertEqual(self.recorder.summary(), [], "tracing data sent to the LangSmith endpoint")


class TracingGuardTest(_TracingEnabled):
    def test_run_pre_visit_brief_sends_nothing(self) -> None:
        result = self.run_brief()

        self.assertIn(CHART_MARKERS[0], result.text)  # the graph really ran
        self.assert_nothing_sent()

    def test_exported_graph_object_sends_nothing(self) -> None:
        with capture_logs():
            final = asyncio.run(
                pvb.PRE_VISIT_BRIEF_GRAPH.ainvoke({"node_seconds": {}}, context=self.context())
            )

        self.assertIn(CHART_MARKERS[0], final["text"])
        self.assert_nothing_sent()

    def test_compiled_graph_reached_directly_refuses_before_retrieval(self) -> None:
        compiled = pvb.PRE_VISIT_BRIEF_GRAPH._compiled

        with capture_logs(), self.assertRaises(pvb.TracingEnabledError):
            asyncio.run(compiled.ainvoke({"node_seconds": {}}, context=self.context()))

        self.assertEqual(self.fhir_calls, 0)
        wait_for_all_tracers()
        self.assertNotEqual(
            self.recorder.requests, [], "expected the refused run itself to be traced"
        )
        payload = self.recorder.payload()
        for secret in (TOKEN, *CHART_MARKERS):
            with self.subTest(absent=secret):
                self.assertNotIn(secret.encode(), payload)
        self.assertEqual(self.smith_lookups(), [])

    def test_tracer_passed_as_a_callback_is_refused_too(self) -> None:
        # tracing_context(enabled=False) does not detach an explicit tracer,
        # so this is the node check's job alone.
        os.environ.pop("LANGSMITH_TRACING")
        _reset_langsmith()
        compiled = pvb.PRE_VISIT_BRIEF_GRAPH._compiled

        with capture_logs(), self.assertRaises(pvb.TracingEnabledError):
            asyncio.run(
                compiled.ainvoke(
                    {"node_seconds": {}},
                    {"callbacks": [LangChainTracer()]},
                    context=self.context(),
                )
            )

        self.assertEqual(self.fhir_calls, 0)
        wait_for_all_tracers()
        payload = self.recorder.payload()
        for secret in (TOKEN, *CHART_MARKERS):
            with self.subTest(absent=secret):
                self.assertNotIn(secret.encode(), payload)
        self.assertEqual(self.smith_lookups(), [])


class GuardControlTest(_TracingEnabled):
    """Both guard layers off: proves the harness sees what a broken guard sends."""

    def test_chart_records_reach_the_recorder_when_the_guard_is_gone(self) -> None:
        self.disable_both_guards()

        self.run_brief()

        wait_for_all_tracers()
        self.assertIn("POST /runs/multipart", " ".join(self.recorder.summary()))
        payload = self.recorder.payload()
        for marker in CHART_MARKERS:
            with self.subTest(present=marker):
                self.assertIn(marker.encode(), payload)
        self.assertEqual(self.smith_lookups(), [], "the endpoint redirect must hold even here")

    def test_bearer_token_is_never_in_the_trace_payload(self) -> None:
        # The token is runtime context, not graph state (F-56). Checked
        # against a fully traced run, so a change that moves it into state,
        # metadata or a node's inputs fails here.
        self.disable_both_guards()

        self.run_brief()

        wait_for_all_tracers()
        payload = self.recorder.payload()
        self.assertIn(CHART_MARKERS[0].encode(), payload)  # a real payload, not an empty one
        for secret in (TOKEN, "Bearer", PATIENT_ID, SESSION_KEY):
            with self.subTest(absent=secret):
                self.assertNotIn(secret.encode(), payload)


class ExportedSurfaceTest(unittest.TestCase):
    def test_exported_graph_object_has_no_other_way_to_run(self) -> None:
        for name in (
            "invoke",
            "stream",
            "astream",
            "stream_events",
            "astream_events",
            "astream_log",
            "batch",
            "abatch",
            "transform",
            "atransform",
            "with_config",
        ):
            with self.subTest(method=name):
                self.assertFalse(hasattr(pvb.PRE_VISIT_BRIEF_GRAPH, name))

    def test_no_module_in_app_graph_holds_a_raw_compiled_graph(self) -> None:
        for module_info in pkgutil.iter_modules(app.graph.__path__):
            module = importlib.import_module(f"app.graph.{module_info.name}")
            raw = [name for name, value in vars(module).items() if isinstance(value, Pregel)]
            with self.subTest(module=module.__name__):
                self.assertEqual(raw, [])
