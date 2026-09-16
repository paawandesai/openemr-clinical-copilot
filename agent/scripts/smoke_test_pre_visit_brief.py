#!/usr/bin/env python3
"""
End-to-end smoke test for app/graph/pre_visit_brief.py — real FHIR
retrieval from the deployed OpenEMR droplet, a real Anthropic call, real
verification, real rendering. No mocks. Not a test suite: a one-shot script
that prints the rendered brief for each run and checks what it logged.

Credentials exactly as scripts/smoke_test_claims.py:

    export FHIR_CLIENT_ID=...
    export FHIR_CLIENT_SECRET=...
    export ANTHROPIC_API_KEY=...
    python3 scripts/smoke_test_pre_visit_brief.py > logs/agent-stdout.jsonl
    python3 scripts/compute_metrics.py --log-file logs/agent-stdout.jsonl

Runs, each under its own patient_session_key:

  A  patient with an allergy on file (smoke_test_fhir.py's patient)
  B  patient with zero AllergyIntolerance records (smoke_test_claims.py's
     discovery; FHIR_EMPTY_ALLERGY_PATIENT_ID overrides)
  C  A's patient again, new session  } concurrently, so their log lines
  D  B's patient again, new session  } interleave in the same process

A and B run one after the other and give the clean per-node timings. C and
D exist for the attribution check: C shares A's patient but not its key,
and C/D share the event loop, so a key that followed the patient, or leaked
between concurrent runs, would show up.

Checks — exits 1 if any fails:
  - B's allergy section renders "not recorded"; A's does not.
  - For every run: every agent_event logged under its correlation_id
    carries that run's patient_session_key (never null, never another
    run's), the expected event types are all there, and no event anywhere
    else carries that key.
  - The per-node timings sum to no more than the logged end-to-end latency.

Every line this process writes to stdout is also kept in memory, so the
attribution check reads the same JSON lines compute_metrics.py later does.
Patient discovery (smoke_test_claims.py's helper) logs
retrieval_event_skipped with no key and no correlation_id; that is outside
every run and reported separately.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Any, TextIO

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.graph.pre_visit_brief import (  # noqa: E402
    PreVisitBriefResult,
    run_pre_visit_brief,
)
from app.middleware.logging import configure_logging  # noqa: E402
from scripts.smoke_test_claims import _find_empty_allergy_patient  # noqa: E402
from scripts.smoke_test_fhir import (  # noqa: E402
    _discover_a_patient_id,
    _get_password_grant_token,
    _require_env,
)

_NODES = ("retrieve", "draft", "verify", "render")
_NOT_RECORDED = "Allergies: not recorded"


class _StdoutRecorder(io.TextIOBase):
    """Passes every write through to the real stdout and keeps complete lines."""

    def __init__(self, target: TextIO) -> None:
        self._target = target
        self._pending = ""
        self.lines: list[str] = []

    def write(self, text: str) -> int:
        self._target.write(text)
        *complete, self._pending = (self._pending + text).split("\n")
        self.lines.extend(complete)
        return len(text)

    def flush(self) -> None:
        self._target.flush()


@dataclass(frozen=True)
class _Run:
    label: str
    patient_id: str
    key: str
    result: PreVisitBriefResult


def _session_key(username: str, patient_id: str) -> str:
    # (user, patient, session) — ARCHITECTURE.md §4. A fresh session id per
    # run, as a new chart opening would get.
    return f"{username}:{patient_id}:{uuid.uuid4()}"


async def _run(label: str, token: str, patient_id: str, key: str) -> _Run:
    result = await run_pre_visit_brief(token, patient_id, key)
    return _Run(label=label, patient_id=patient_id, key=key, result=result)


def _print_run(run: _Run) -> None:
    result = run.result
    passed = sum(1 for r in result.verification_results if r.passed)
    failed = len(result.verification_results) - passed
    print(f"\n{'=' * 72}\n{run.label}")
    print(f"patient:             {run.patient_id}")
    print(f"patient_session_key: {run.key}")
    print(f"correlation_id:      {result.correlation_id}")
    print(f"{'=' * 72}")
    print(result.text)
    print(f"\n  claims: {len(result.verification_results)} drafted, {passed} verified, {failed} unverified")
    print("  timing (seconds):")
    for node in _NODES:
        print(f"    {node:<9} {result.node_seconds[node]:8.3f}")
    print(f"    {'nodes':<9} {sum(result.node_seconds.values()):8.3f}")
    print(f"    {'total':<9} {result.total_seconds:8.3f}  (trigger → rendered, as logged in latency_event)")


def _report(passed: bool, message: str) -> bool:
    print(f"  {'PASS' if passed else 'FAIL'}: {message}")
    return passed


def _agent_events(lines: list[str]) -> list[dict[str, Any]]:
    events = []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("event") == "agent_event":
            events.append(record)
    return events


def _check_attribution(runs: list[_Run], events: list[dict[str, Any]]) -> bool:
    ok = True
    run_ids = {run.result.correlation_id for run in runs}
    ok &= _report(
        len(run_ids) == len(runs) and len({run.key for run in runs}) == len(runs),
        f"{len(runs)} runs, {len(run_ids)} distinct correlation_ids, {len({r.key for r in runs})} distinct keys",
    )

    for run in runs:
        cid = run.result.correlation_id
        mine = [e for e in events if e.get("correlation_id") == cid]
        keys = Counter(e.get("patient_session_key") for e in mine)
        types = Counter(e["event_type"] for e in mine)
        expected = Counter(
            retrieval_event=3,
            llm_usage_event=1,
            claims_drafted=1,
            claim_verified_event=len(run.result.verification_results),
            latency_event=1,
        )
        ok &= _report(
            set(keys) == {run.key},
            f"run {run.label[:1]}: all {len(mine)} events carry its own key "
            f"(keys seen: {dict(keys)})",
        )
        ok &= _report(
            types == expected,
            f"run {run.label[:1]}: event types {dict(sorted(types.items()))}",
        )
        body_mismatch = [
            e for e in mine
            if e["event_data"].get("correlation_id", cid) != cid
            or e["event_data"].get("patient_session_key", run.key) != run.key
        ]
        ok &= _report(
            not body_mismatch,
            f"run {run.label[:1]}: event_data correlation_id / patient_session_key agree with the envelope "
            f"({len(body_mismatch)} disagree)",
        )
        elsewhere = [e for e in events if e.get("patient_session_key") == run.key and e.get("correlation_id") != cid]
        ok &= _report(
            not elsewhere,
            f"run {run.label[:1]}: its key appears on no event outside the run ({len(elsewhere)} found)",
        )

    outside = [e for e in events if e.get("correlation_id") not in run_ids]
    outside_types = Counter(e["event_type"] for e in outside)
    outside_keys = Counter(e.get("patient_session_key") for e in outside)
    ok &= _report(
        set(outside_types) <= {"retrieval_event_skipped"} and set(outside_keys) <= {None},
        f"events outside every run (patient discovery): {dict(outside_types)}, keys {dict(outside_keys)}",
    )
    return ok


async def main() -> None:
    recorder = _StdoutRecorder(sys.stdout)
    sys.stdout = recorder
    # After the recorder is installed: configure_logging binds structlog to
    # whatever sys.stdout is at call time.
    configure_logging()

    client_id = _require_env("FHIR_CLIENT_ID")
    client_secret = _require_env("FHIR_CLIENT_SECRET")
    _require_env("ANTHROPIC_API_KEY")
    username = os.environ.get("FHIR_TEST_USERNAME", "physician")
    password = os.environ.get("FHIR_TEST_PASSWORD", "physician")

    print(f"Requesting a password-grant token for user '{username}'...")
    token = await _get_password_grant_token(client_id, client_secret, username, password)
    print("Token acquired.")

    allergy_patient = await _discover_a_patient_id(token)
    empty_allergy_patient = await _find_empty_allergy_patient(token, exclude=allergy_patient)

    run_a = await _run("A: allergy on file", token, allergy_patient, _session_key(username, allergy_patient))
    _print_run(run_a)
    run_b = await _run(
        "B: zero AllergyIntolerance records", token, empty_allergy_patient,
        _session_key(username, empty_allergy_patient),
    )
    _print_run(run_b)

    print(f"\n{'#' * 72}\nC and D: the same two patients, new sessions, run concurrently\n{'#' * 72}")
    run_c, run_d = await asyncio.gather(
        _run("C: A's patient, new session (concurrent with D)", token, allergy_patient,
             _session_key(username, allergy_patient)),
        _run("D: B's patient, new session (concurrent with C)", token, empty_allergy_patient,
             _session_key(username, empty_allergy_patient)),
    )
    _print_run(run_c)
    _print_run(run_d)
    runs = [run_a, run_b, run_c, run_d]

    print(f"\n{'=' * 72}\nChecks\n{'=' * 72}")
    ok = True
    for run, expect_not_recorded in ((run_a, False), (run_b, True), (run_c, False), (run_d, True)):
        rendered = _NOT_RECORDED in run.result.text
        ok &= _report(
            rendered == expect_not_recorded,
            f"run {run.label[:1]}: \"{_NOT_RECORDED}\" {'present' if rendered else 'absent'} "
            f"(expected {'present' if expect_not_recorded else 'absent'})",
        )
    for run in runs:
        nodes = sum(run.result.node_seconds.values())
        ok &= _report(
            set(run.result.node_seconds) == set(_NODES) and nodes <= run.result.total_seconds,
            f"run {run.label[:1]}: node timings {nodes:.3f}s ≤ end-to-end {run.result.total_seconds:.3f}s",
        )
    ok &= _check_attribution(runs, _agent_events(recorder.lines))

    sys.stdout.flush()
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
