"""
scripts/compute_metrics.py against small hand-written logs — the paths a
single synthetic run doesn't exercise: no data, broken absolutes, the
llm_call alias, and priced cost.

    cd agent && python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "compute_metrics.py"
_spec = importlib.util.spec_from_file_location("compute_metrics", _SCRIPT)
assert _spec and _spec.loader
compute_metrics = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = compute_metrics  # dataclasses resolve annotations via sys.modules
_spec.loader.exec_module(compute_metrics)

SESSION = "user:u|patient:p|session:s"


def agent_event(event_type: str, event_data: dict[str, Any], use_case: str | None = None) -> str:
    return json.dumps(
        {
            "event_type": event_type,
            "event_data": event_data,
            "use_case": use_case,
            "patient_session_key": SESSION if use_case else None,
            "event": "agent_event",
            "correlation_id": "c1",
            "level": "info",
        }
    )


def claim(passed: bool, disclosed: bool) -> str:
    return agent_event(
        "claim_verified_event",
        {"source_id": "x", "resource_type": "Condition", "passed": passed, "disclosed_as_unverified": disclosed,
         "correlation_id": "c1", "use_case": "pre_visit_brief", "patient_session_key": SESSION},
        "pre_visit_brief",
    )


def auth(requested: str, outcome: str) -> str:
    return agent_event(
        "authorization_check_event",
        {"requested_scope": requested, "held_scopes": ["user/Patient.read"], "outcome": outcome, "correlation_id": "c1"},
    )


class ComputeMetricsTest(unittest.TestCase):
    def run_script(self, lines: list[str], *extra: str) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "log.jsonl"
            log.write_text("\n".join(lines) + "\n")
            out = io.StringIO()
            with mock.patch("sys.argv", ["compute_metrics.py", "--log-file", str(log), *extra]), contextlib.redirect_stdout(out):
                code = compute_metrics.main()
        return code, out.getvalue()

    def test_no_events_is_insufficient_data_never_a_fake_percentage(self) -> None:
        code, out = self.run_script(["INFO:     127.0.0.1 - GET /health 200", '{"event": "observability_export_enabled"}', "0.5"])
        self.assertEqual(code, 0)
        self.assertIn("lines: 3 | agent_event lines: 0 | other structured log lines: 1 | non-log lines skipped: 2", out)
        for event_type in ("claim_verified_event", "authorization_check_event", "latency_event", "retrieval_event", "llm_usage_event"):
            self.assertIn(f"insufficient data — 0 events of type {event_type}", out)
        self.assertNotIn("0.0%", out)
        self.assertNotIn("100.0%", out)

    def test_all_claims_pass_leaves_disclosure_rate_without_data(self) -> None:
        _, out = self.run_script([claim(True, False)])
        self.assertIn("100.0%  (1 passed of 1 claims)", out)
        self.assertIn("insufficient data — 0 events of type claim_verified_event with passed == false found", out)

    def test_undisclosed_failure_breaks_the_absolute(self) -> None:
        code, out = self.run_script([claim(False, True), claim(False, False)])
        self.assertEqual(code, 1)
        self.assertIn("50.0%  (1 disclosed of 2 failed claims) — BELOW the 100% absolute", out)
        self.assertIn("UNDISCLOSED: line 2", out)

    def test_accepted_out_of_scope_request_violates_invariant(self) -> None:
        code, out = self.run_script([auth("user/Patient.read", "accepted"), auth("user/Observation.read", "rejected"), auth("user/Observation.read", "accepted")])
        self.assertEqual(code, 1)
        self.assertIn("INVARIANT VIOLATED", out)
        self.assertIn("1 of 2 out-of-scope requests rejected (50.0%)", out)
        self.assertIn("in-scope requests (not counted): 1", out)
        self.assertIn("ACCEPTED OUT-OF-SCOPE: line 3", out)

    def test_only_in_scope_requests_is_insufficient_not_holding(self) -> None:
        code, out = self.run_script([auth("user/Patient.read", "accepted")])
        self.assertEqual(code, 0)
        self.assertIn("insufficient data — 0 events of type authorization_check_event with requested_scope not in held_scopes found", out)
        self.assertNotIn("INVARIANT HOLDS", out)

    def test_latency_is_per_use_case_with_nearest_rank(self) -> None:
        lines = [
            agent_event("latency_event", {"use_case": "change_detection", "trigger_ts": 0, "rendered_ts": s, "correlation_id": "c1"}, "change_detection")
            for s in (10, 20, 30, 40, 100)
        ]
        _, out = self.run_script(lines)
        self.assertIn("change_detection: n=5  p50=30.0s  p95=100.0s  over budget: 1", out)
        self.assertIn("pre_visit_brief: insufficient data — 0 events of type latency_event with use_case=pre_visit_brief found", out)

    def test_retrieval_coverage_names_the_missing_type(self) -> None:
        lines = [
            agent_event("retrieval_event", {"resource_type": t, "correlation_id": "c1", "use_case": "change_detection"}, "change_detection")
            for t in ("Encounter", "Condition", "MedicationRequest")
        ]
        _, out = self.run_script(lines)
        self.assertIn("3/4 required types queried", out)
        self.assertIn("missing: Observation", out)
        self.assertIn("mean coverage 75.0%, fully covered 0 of 1", out)

    def test_llm_call_alias_and_explicit_prices(self) -> None:
        line = agent_event(
            "llm_call",
            {"served_model": "m", "input_tokens": 1_000_000, "output_tokens": 100_000,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 500_000},
            "pre_visit_brief",
        )
        _, unpriced = self.run_script([line])
        self.assertIn("pricing: none — no pricing constant exists in this codebase", unpriced)
        self.assertIn("→ USD not computed", unpriced)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as prices:
            json.dump({"m": {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5}}, prices)
        _, priced = self.run_script([line], "--prices", prices.name)
        Path(prices.name).unlink()
        # 1.0M × $2 + 0.1M × $10 + 0.5M × $0.2 = $3.10
        self.assertIn("→ $3.100000", priced)


if __name__ == "__main__":
    unittest.main()
