"""
scripts/w2_dashboard.py — the Week 2 dashboard and alert evaluator — against
small hand-written logs, so each number can be checked by hand.

    cd agent && python -m pytest tests/test_w2_dashboard.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app.middleware import langfuse_export
from app.models.telemetry_events import StepOutcome, Tool, ToolCallEvent

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "w2_dashboard.py"
_spec = importlib.util.spec_from_file_location("w2_dashboard", _SCRIPT)
assert _spec is not None and _spec.loader is not None
dashboard = importlib.util.module_from_spec(_spec)
# Registered before it runs: @dataclass resolves annotations through
# sys.modules[cls.__module__].
sys.modules["w2_dashboard"] = dashboard
_spec.loader.exec_module(dashboard)

KEY = "user-1:patient-1:session-1"


def line(event_type: str, data: dict, *, correlation_id: str = "c-1", **extra) -> str:
    return json.dumps(
        {
            "event": "agent_event",
            "event_type": event_type,
            "event_data": data,
            "correlation_id": correlation_id,
            "timestamp": "2026-09-22T12:00:00Z",
            "patient_session_key": KEY,
            **extra,
        }
    )


def request(route: str, status: int, ms: float, method: str = "POST") -> str:
    return line(
        "http_request_event",
        {"method": method, "route": route, "status_code": status, "latency_ms": ms},
    )


def tool(name: str, outcome: str, status: int | None, retries: int = 0) -> str:
    return line(
        "tool_call_event",
        {
            "tool": name,
            "outcome": outcome,
            "status_code": status,
            "latency_ms": 5.0,
            "attempts": retries + 1,
            "retries": retries,
        },
    )


def build(lines: list[str], prices: dict | None = None) -> dict:
    return dashboard.build(list(dashboard.read_events(lines)), prices)


def alert(d: dict, name: str) -> dict:
    return next(a for a in dashboard.evaluate_alerts(d) if a["name"] == name)


class TestRequests:
    def test_probes_are_counted_but_never_dilute_the_error_rate(self) -> None:
        d = build(
            [request("/ready", 200, 1.0, "GET")] * 50
            + [request("/pre-visit-brief", 200, 1000.0)] * 3
            + [request("/pre-visit-brief", 502, 30.0)]
        )
        r = d["requests"]
        assert (r["count"], r["errors"], r["probe_requests"]) == (4, 1, 50)
        assert r["error_rate"] == 0.25
        assert r["status_classes"] == {"2xx": 3, "5xx": 1}

    def test_a_4xx_is_not_an_error(self) -> None:
        d = build([request("/pre-visit-brief", 404, 5.0)] * 20)
        assert d["requests"]["error_rate"] == 0.0
        assert alert(d, "request_error_rate")["state"] == "ok"

    def test_percentiles_interpolate(self) -> None:
        d = build([request("/pre-visit-brief", 200, float(ms)) for ms in range(1, 101)])
        assert d["requests"]["p50_ms"] == pytest.approx(50.5)
        assert d["requests"]["p95_ms"] == pytest.approx(95.05)

    def test_lines_that_are_not_agent_events_are_skipped(self) -> None:
        d = build(["INFO: uvicorn started", "{not json", json.dumps({"event": "other"})])
        assert d["window"]["events"] == 0


class TestSteps:
    def test_steps_are_keyed_pipeline_dot_step(self) -> None:
        d = build(
            [
                line(
                    "step_event",
                    {
                        "pipeline": "conversation",
                        "step": "routing",
                        "outcome": "refused",
                        "latency_ms": 0.4,
                    },
                ),
                line(
                    "step_event",
                    {
                        "pipeline": "pre_visit_brief",
                        "step": "retrieve",
                        "outcome": "error",
                        "latency_ms": 900.0,
                    },
                ),
            ]
        )
        assert d["steps"]["conversation.routing"]["outcomes"] == {"refused": 1}
        assert d["steps"]["pre_visit_brief.retrieve"]["errors"] == 1

    def test_the_extractors_own_step_lines_are_in_the_by_step_view(self) -> None:
        d = build([line("intake_extraction_step", {"step": "vision", "latency_ms": 29466})])
        assert d["steps"]["intake_extractor.vision"]["p95_ms"] == 29466


class TestTools:
    def test_retries_and_dependency_failures(self) -> None:
        d = build(
            [tool("llm", "ok", None, retries=2)]
            + [tool("fhir_read", "ok", 200)] * 17
            + [tool("fhir_read", "error", 500), tool("fhir_read", "error", None)]
            + [tool("fhir_read", "error", 404)]
        )
        fhir = d["tools"]["fhir_read"]
        assert (fhir["calls"], fhir["dependency_failures"], fhir["other_errors"]) == (20, 2, 1)
        assert fhir["failure_rate"] == 0.1
        assert (d["tools"]["llm"]["retries"], d["tools"]["llm"]["calls_with_retries"]) == (2, 1)
        assert alert(d, "tool_failure_rate")["state"] == "firing"
        assert alert(d, "tool_failure_rate")["value"] == {"tool": "fhir_read", "rate": 0.1}

    @pytest.mark.parametrize(
        ("outcome", "status"),
        [
            ("ok", 200),
            ("error", 500),
            ("error", 503),
            ("error", 429),
            ("error", None),
            ("error", 404),
            ("error", 401),
            ("error", 409),
        ],
    )
    def test_the_failure_rule_is_the_exporters_rule(self, outcome: str, status: int | None) -> None:
        data = {"outcome": outcome, "status_code": status}
        event = ToolCallEvent(
            tool=Tool.FHIR_READ,
            outcome=StepOutcome(outcome),
            latency_ms=1.0,
            attempts=1,
            retries=0,
            status_code=status,
            correlation_id="c",
        )
        assert dashboard.is_dependency_failure(data) == langfuse_export.is_dependency_failure(event)


class TestAlerts:
    def test_too_few_samples_is_not_a_verdict(self) -> None:
        d = build([request("/pre-visit-brief", 502, 5.0)] * 3)
        assert alert(d, "request_error_rate")["state"] == "insufficient_data"

    def test_answer_latency_fires_over_its_threshold(self) -> None:
        d = build([request("/pre-visit-brief/conversation", 200, 50_000.0)] * 10)
        assert alert(d, "answer_p95_latency")["state"] == "firing"

    def test_located_rate_collapse_reads_text_layer_documents_only(self) -> None:
        scanned = line(
            "intake_extraction_completed",
            {"has_text_layer": False, "field_outcomes": {"located": 36, "unlocated": 71}},
        )
        digital = line(
            "intake_extraction_completed",
            {
                "has_text_layer": True,
                "field_outcomes": {"located": 40, "unlocated": 20},
                "mean_confidence": 0.6,
            },
        )
        d = build([scanned])
        # A heavy scan's low rate is expected (D24) and is not what this alert is for.
        assert alert(d, "text_layer_located_rate_collapse")["state"] == "insufficient_data"
        d = build([scanned, digital])
        assert d["extraction"]["text_layer_located_rate"] == pytest.approx(40 / 60)
        assert alert(d, "text_layer_located_rate_collapse")["state"] == "firing"

    def test_verification_failure_spike(self) -> None:
        passed = line("claim_verified_event", {"passed": True})
        failed = line("claim_verified_event", {"passed": False})
        assert (
            alert(build([passed] * 18 + [failed] * 2), "verification_failure_spike")["state"]
            == "ok"
        )
        assert (
            alert(build([passed] * 17 + [failed] * 3), "verification_failure_spike")["state"]
            == "firing"
        )


def test_cost_is_per_encounter_and_only_with_a_price_file() -> None:
    usage = {"model": "m", "input_tokens": 1_000_000, "output_tokens": 0}
    lines = [
        line("llm_usage_event", usage, correlation_id="enc-a"),
        line("llm_usage_event", usage, correlation_id="enc-a"),
        line("llm_usage_event", usage, correlation_id="enc-b"),
    ]
    assert build(lines)["cost"]["cost_per_encounter_mean_usd"] is None
    cost = build(lines, {"m": {"input": 2.0, "output": 10.0}})["cost"]
    assert cost["encounters_with_llm_calls"] == 2
    assert cost["cost_per_encounter_mean_usd"] == pytest.approx(3.0)


def test_rendered_output_never_carries_the_session_key() -> None:
    d = build(
        [request("/pre-visit-brief", 200, 10.0), line("claim_verified_event", {"passed": True})]
    )
    alerts = dashboard.evaluate_alerts(d)
    assert KEY not in dashboard.render_text(d, alerts)
    assert KEY not in dashboard.render_html(d, alerts)
    assert KEY not in json.dumps(d)
