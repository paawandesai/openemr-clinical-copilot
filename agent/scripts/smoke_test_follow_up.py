#!/usr/bin/env python3
"""
End-to-end smoke test for POST /pre-visit-brief/follow-up
(app/api/follow_up.py, app/graph/follow_up.py) — the real FastAPI app
in-process, real FHIR retrieval from the live OpenEMR, real Anthropic
calls, the real verifier. No mocks, except in run C, which wraps the real
drafting step to append claims that must not verify. Not deployed and not
a test suite: a one-shot script that prints every answer and checks it.

Credentials as scripts/smoke_test_pre_visit_brief.py:

    export FHIR_CLIENT_ID=...
    export FHIR_CLIENT_SECRET=...
    export ANTHROPIC_API_KEY=...
    export FHIR_BASE_URL=https://64.177.42.210:9300/apis/default/fhir FHIR_VERIFY_TLS=false
    python3 scripts/smoke_test_follow_up.py > logs/follow-up-smoke.jsonl
    python3 scripts/compute_metrics.py --log-file logs/follow-up-smoke.jsonl

FHIR_FOLLOW_UP_PATIENT_ID picks the patient; otherwise the first patient
with an allergy record and a medication that is no longer active is used.

Runs, all under the session_id of one real Pre-Visit Brief for the patient:

  A  answerable: is <a no-longer-active medication> still active, and when
     was it prescribed?
  B  not answerable from the retrieved set: the latest HbA1c (a lab), and
     blood pressure at the last visit (vitals / visit history).
  C  A's question again, with four claims appended after the real draft:
     a real medication with the wrong status, a medication record that does
     not exist, a real allergy with a field it does not have, and a real
     allergy id cited as a Condition.

Checks — exits 1 if any fails:
  - A answers with at least one verified claim naming the medication, and
    a verified status and prescribed date for it.
  - B's answers open with "That information isn't in the retrieved record
    for this visit.", have no verified claim, and name the expected gap.
  - C: every injected claim fails with its expected reason, is rendered as
    an UNVERIFIED line, never as a plain line, and the answer is not
    reported as fully answered.
  - Every run: every verified claim's value matches an independent re-read
    of the same record from FHIR; every failed claim is rendered UNVERIFIED;
    every event under the run's correlation_id carries use_case
    follow_up_drilling and the brief's patient_session_key, with the
    expected event types; no log line contains the question text.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Any
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx  # noqa: E402

from app.fhir import client as fhir_client  # noqa: E402
from app.graph import follow_up as follow_up_graph  # noqa: E402
from app.main import app  # noqa: E402
from app.models.api import FollowUpResponse, PreVisitBriefResponse  # noqa: E402
from app.models.claims import Claim  # noqa: E402
from app.models.fhir_resources import FhirResourceType as R  # noqa: E402
from app.reasoning.draft_follow_up import FollowUpDraft  # noqa: E402
from app.verification.checker import FailureReason, _values_match  # noqa: E402
from scripts.smoke_test_fhir import _get_password_grant_token, _require_env  # noqa: E402
from scripts.smoke_test_pre_visit_brief import (  # noqa: E402
    _agent_events,
    _report,
    _StdoutRecorder,
)

_NOT_IN_RECORD = follow_up_graph.NOT_IN_RECORD


@dataclass(frozen=True)
class _Injected:
    claim: Claim
    reason: FailureReason


@dataclass(frozen=True)
class _Run:
    label: str
    question: str
    status: int
    body: FollowUpResponse


async def _find_patient(token: str) -> str:
    override = os.environ.get("FHIR_FOLLOW_UP_PATIENT_ID")
    if override:
        return override
    async with httpx.AsyncClient(verify=fhir_client._VERIFY_TLS, timeout=30.0) as http:
        response = await http.get(
            f"{fhir_client.FHIR_BASE_URL}/Patient",
            headers={"Authorization": f"Bearer {token}"},
            params={"_count": 100},
        )
    response.raise_for_status()
    for entry in response.json().get("entry", []):
        patient_id = entry["resource"]["id"]
        allergies = await fhir_client.get_allergy_intolerances(token, patient_id)
        medications = await fhir_client.get_medication_requests(token, patient_id)
        if allergies and any(m.status != "active" and m.rxnorm_code for m in medications):
            return patient_id
    print("ERROR: no suitable patient; set FHIR_FOLLOW_UP_PATIENT_ID.", file=sys.stderr)
    sys.exit(1)


async def _oracle(token: str, patient_id: str) -> dict[R, list[Any]]:
    """An independent re-read of the bounded set, outside the pipeline."""
    conditions, medications, allergies = await asyncio.gather(
        fhir_client.get_conditions(token, patient_id),
        fhir_client.get_medication_requests(token, patient_id),
        fhir_client.get_allergy_intolerances(token, patient_id),
    )
    return {R.CONDITION: conditions, R.MEDICATION_REQUEST: medications, R.ALLERGY_INTOLERANCE: allergies}


async def _ask(
    http: httpx.AsyncClient, label: str, token: str, patient_id: str, session_id: str, question: str
) -> _Run:
    response = await http.post(
        "/pre-visit-brief/follow-up",
        json={"patient_id": patient_id, "session_id": session_id, "question": question},
        headers={"Authorization": f"Bearer {token}"},
    )
    if response.status_code != 200:
        print(f"ERROR: {label} returned {response.status_code}: {response.text}", file=sys.stderr)
        sys.exit(1)
    return _Run(label, question, response.status_code, FollowUpResponse.model_validate_json(response.text))


def _print_run(run: _Run) -> None:
    body = run.body
    print(f"\n{'=' * 72}\n{run.label}\nQ: {run.question}")
    print(f"answer_status: {body.answer_status.value}   gaps: {[g.value for g in body.gaps]}")
    print(f"correlation_id: {body.correlation_id}")
    print(f"{'=' * 72}")
    print(body.answer)
    print(f"\n  verification: {body.verification.claims} claims, {body.verification.verified} verified, "
          f"{body.verification.unverified} unverified")
    for claim in body.claims:
        mark = "VERIFIED  " if claim.verified else "UNVERIFIED"
        print(f"    {mark} {claim.resource_type.value}/{claim.source_id} {claim.field}={claim.value!r}"
              + (f"  [{claim.unverified_reason}]" if claim.unverified_reason else ""))
    t = body.timing
    print(f"  timing: retrieve {t.retrieve_seconds:.2f}s, draft {t.draft_seconds:.2f}s, "
          f"verify {t.verify_seconds:.4f}s, render {t.render_seconds:.4f}s, total {t.total_seconds:.2f}s")


def _answer_lines(body: FollowUpResponse) -> list[str]:
    return [line for line in body.answer.splitlines() if line.startswith("  - ")]


def _check_common(run: _Run, oracle: dict[R, list[Any]], key: str, events: list[dict[str, Any]]) -> bool:
    ok = True
    body = run.body
    tag = run.label.split(":")[0]
    ok &= _report(body.patient_session_key == key, f"run {tag}: patient_session_key is the brief's ({key})")

    mismatched = []
    for claim in body.claims:
        if not claim.verified:
            continue
        copies = [r for r in oracle[claim.resource_type] if r.source_id == claim.source_id]
        if not copies or not all(_values_match(claim.value, getattr(r, claim.field)) for r in copies):
            mismatched.append(claim)
    ok &= _report(
        not mismatched,
        f"run {tag}: all {body.verification.verified} verified claim(s) match an independent FHIR re-read "
        f"({len(mismatched)} do not)",
    )
    failed = [c for c in body.claims if not c.verified]
    unverified_lines = [line for line in _answer_lines(body) if line.startswith("  - UNVERIFIED: ")]
    ok &= _report(
        len(unverified_lines) == len(failed),
        f"run {tag}: {len(failed)} failed claim(s), {len(unverified_lines)} UNVERIFIED line(s) in the answer",
    )

    mine = [e for e in events if e.get("correlation_id") == body.correlation_id]
    types = Counter(e["event_type"] for e in mine)
    expected = Counter(
        retrieval_event=3,
        llm_usage_event=1,
        claims_drafted=1,
        claim_verified_event=body.verification.claims,
        latency_event=1,
    )
    ok &= _report(types == expected, f"run {tag}: event types {dict(sorted(types.items()))}")
    ok &= _report(
        {e.get("use_case") for e in mine} == {"follow_up_drilling"}
        and {e.get("patient_session_key") for e in mine} == {key},
        f"run {tag}: all {len(mine)} events tagged follow_up_drilling and the brief's key",
    )
    claim_events = [e["event_data"] for e in mine if e["event_type"] == "claim_verified_event"]
    ok &= _report(
        all(e["disclosed_as_unverified"] for e in claim_events if not e["passed"]),
        f"run {tag}: every failed claim_verified_event has disclosed_as_unverified=true",
    )
    return ok


async def main() -> None:
    recorder = _StdoutRecorder(sys.stdout)
    sys.stdout = recorder
    from app.middleware.logging import configure_logging

    configure_logging()

    client_id = _require_env("FHIR_CLIENT_ID")
    client_secret = _require_env("FHIR_CLIENT_SECRET")
    _require_env("ANTHROPIC_API_KEY")
    username = os.environ.get("FHIR_TEST_USERNAME", "physician")
    password = os.environ.get("FHIR_TEST_PASSWORD", "physician")
    token = await _get_password_grant_token(client_id, client_secret, username, password)
    print("Token acquired.")

    patient_id = await _find_patient(token)
    oracle = await _oracle(token, patient_id)
    print(f"patient: {patient_id}  records: " + ", ".join(f"{t.value}={len(v)}" for t, v in oracle.items()))

    inactive_med = next(m for m in oracle[R.MEDICATION_REQUEST] if m.status != "active" and m.rxnorm_code)
    allergy = oracle[R.ALLERGY_INTOLERANCE][0]
    question_a = f"Is the {inactive_med.display} still active, and when was it prescribed?"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://agent.local", timeout=300.0) as http:
        brief_response = await http.post(
            "/pre-visit-brief", json={"patient_id": patient_id}, headers={"Authorization": f"Bearer {token}"}
        )
        if brief_response.status_code != 200:
            print(f"ERROR: brief returned {brief_response.status_code}: {brief_response.text}", file=sys.stderr)
            sys.exit(1)
        brief = PreVisitBriefResponse.model_validate_json(brief_response.text)
        print(f"\nBrief: session_id {brief.session_id}, {brief.verification.verified}/{brief.verification.claims} "
              f"claims verified, key {brief.patient_session_key}")
        session_id, key = brief.session_id, brief.patient_session_key

        run_a = await _ask(http, "A: answerable from the retrieved set", token, patient_id, session_id, question_a)
        _print_run(run_a)
        run_b1 = await _ask(
            http, "B1: not in the retrieved set (lab)", token, patient_id, session_id,
            "What was the patient's most recent hemoglobin A1c result?",
        )
        _print_run(run_b1)
        run_b2 = await _ask(
            http, "B2: not in the retrieved set (vitals / visit)", token, patient_id, session_id,
            "What was her blood pressure at the last visit?",
        )
        _print_run(run_b2)

        injected = [
            _Injected(Claim(source_id=inactive_med.source_id, field="status", value="active",
                            resource_type=R.MEDICATION_REQUEST), FailureReason.VALUE_MISMATCH),
            _Injected(Claim(source_id=str(uuid.uuid4()), field="display", value="Warfarin 5 MG Oral Tablet",
                            resource_type=R.MEDICATION_REQUEST), FailureReason.SOURCE_NOT_FOUND),
            _Injected(Claim(source_id=allergy.source_id, field="severity", value="severe anaphylaxis",
                            resource_type=R.ALLERGY_INTOLERANCE), FailureReason.FIELD_NOT_ON_SOURCE),
            _Injected(Claim(source_id=allergy.source_id, field="display", value=allergy.display,
                            resource_type=R.CONDITION), FailureReason.SOURCE_NOT_FOUND),
        ]
        real_draft = follow_up_graph.draft_follow_up

        async def draft_with_injected_claims(*args: Any, **kwargs: Any) -> FollowUpDraft:
            draft = await real_draft(*args, **kwargs)
            return FollowUpDraft(
                claims=[*draft.claims, *(i.claim for i in injected)], coverage=draft.coverage, gaps=draft.gaps
            )

        with mock.patch.object(follow_up_graph, "draft_follow_up", draft_with_injected_claims):
            run_c = await _ask(
                http, "C: A's question with four unverifiable claims injected", token, patient_id,
                session_id, question_a,
            )
        _print_run(run_c)

    runs = [run_a, run_b1, run_b2, run_c]
    events = _agent_events(recorder.lines)
    print(f"\n{'=' * 72}\nChecks\n{'=' * 72}")
    ok = True

    a = run_a.body
    med_ids = {m.source_id for m in oracle[R.MEDICATION_REQUEST] if m.display == inactive_med.display}
    verified_a = [c for c in a.claims if c.verified]
    ok &= _report(
        a.answer_status.value in ("answered", "partially_answered") and not a.answer.splitlines()[2] == _NOT_IN_RECORD,
        f"run A: answer_status {a.answer_status.value}",
    )
    for field in ("display", "status", "authored_date"):
        cited = [c for c in verified_a if c.field == field and c.source_id in med_ids]
        ok &= _report(bool(cited), f"run A: verified {field} claim on a {inactive_med.display} record "
                                   f"({', '.join(f'{c.value}' for c in cited) or 'none'})")

    for run, gap_options in ((run_b1, {"lab_results"}), (run_b2, {"other_chart_data", "visit_history"})):
        b = run.body
        tag = run.label[:2]
        ok &= _report(b.answer_status.value == "not_in_record", f"run {tag}: answer_status {b.answer_status.value}")
        ok &= _report(b.answer.splitlines()[2] == _NOT_IN_RECORD, f"run {tag}: answer opens with \"{_NOT_IN_RECORD}\"")
        ok &= _report(b.verification.verified == 0, f"run {tag}: {b.verification.verified} verified claims")
        ok &= _report(bool(gap_options & {g.value for g in b.gaps}),
                      f"run {tag}: gaps {[g.value for g in b.gaps]} include one of {sorted(gap_options)}")

    c = run_c.body
    plain_lines = [line for line in _answer_lines(c) if not line.startswith("  - UNVERIFIED: ")]
    for item in injected:
        outcome = [
            o for o in c.claims
            if (o.resource_type, o.source_id, o.field, o.value)
            == (item.claim.resource_type, item.claim.source_id, item.claim.field, item.claim.value)
        ]
        ok &= _report(
            len(outcome) == 1 and not outcome[0].verified and outcome[0].unverified_reason == item.reason.value,
            f"run C: injected {item.claim.resource_type.value} {item.claim.field}={item.claim.value!r} → "
            f"{'unverified: ' + outcome[0].unverified_reason if outcome and not outcome[0].verified else outcome}",
        )
        ok &= _report(
            not any(f'"{item.claim.value}"' in line or f": {item.claim.value}" in line for line in plain_lines),
            f"run C: {item.claim.value!r} appears in no plain (verified-looking) answer line",
        )
    ok &= _report(c.answer_status.value != "answered", f"run C: answer_status {c.answer_status.value} (not answered)")
    ok &= _report(c.verification.unverified >= len(injected),
                  f"run C: {c.verification.unverified} unverified of {c.verification.claims} claims")

    for run in runs:
        ok &= _check_common(run, oracle, key, events)

    leaked = [run.label[:2] for run in runs if any(run.question in line for line in recorder.lines if line.startswith("{"))]
    ok &= _report(not leaked, f"no JSON log line contains a question's text (leaked: {leaked or 'none'})")

    sys.stdout.flush()
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
