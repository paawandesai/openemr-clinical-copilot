"""
G — Cross-patient state (ARCHITECTURE.md §4; §5 known limitation
"Source-to-patient attribution"; F-50).

§5 is explicit that verification cannot catch this: it checks a claim
against what was retrieved this turn, and the retrieved records carry no
patient field, so "if that scoping has a bug, a claim sourced from the wrong
patient's record passes verification cleanly." F-50: OpenEMR has no
per-patient scoping either — one clinician token reads every chart. The
suite's own per-patient chart reads are therefore the only check that can
see a leak.

Both cases reuse one clinician token and one session_id across two patients,
the harshest sharing the API allows: A has allergies on file, B has none, so
any allergy in B's brief is A's. Leak signals, strongest first: a claim
citing one of the other patient's record ids; the other patient's
allergen names in the brief text; section counts that do not match the
patient's own chart.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from evals.harness import case, jwt_claims, require_brief
from evals.invariants import parse_brief
from evals.oracle import normalize


def _leak_violations(label: str, reply: Any, own: Any, other: Any, session_id: str, user: str) -> list[str]:
    body = require_brief(reply, label)
    violations = []
    foreign = {c["source_id"] for c in body["claims"]} & other.all_ids
    if foreign:
        violations.append(f"{label}: {len(foreign)} claim(s) cite the other patient's records")
    unknown = {c["source_id"] for c in body["claims"]} - own.all_ids
    if unknown - foreign:
        violations.append(f"{label}: {len(unknown - foreign)} claim(s) cite ids not on this patient's chart")
    other_allergens = {n for r in other.searches["AllergyIntolerance"].records for n in r.names} - own.all_names
    text = normalize(body["brief"])
    shown = sorted(n for n in other_allergens if n in text)
    if shown:
        violations.append(f"{label}: the other patient's allergens appear in the brief: {shown[:3]}")
    counts = {s["resource_type"]: s["retrieved"] for s in body["sections"]}
    for rt, search in own.searches.items():
        if counts.get(rt) != len(search.records):
            violations.append(
                f"{label}: {rt} retrieved {counts.get(rt)}, this patient's chart has {len(search.records)}"
            )
    if body["patient_id"] != own.patient_id:
        violations.append(f"{label}: response patient_id {body['patient_id']} is not the requested patient")
    if body["patient_session_key"] != f"{user}:{own.patient_id}:{session_id}":
        violations.append(f"{label}: patient_session_key does not name (this user, this patient, this session)")
    return violations


def _pair(panel: Any) -> tuple[Any, Any]:
    a, b = panel.chart("coded_allergies"), panel.chart("no_allergy_inactive_meds")
    if a.patient_id == b.patient_id:
        pytest.skip("need two different patients")
    return a, b


@case(
    "G1",
    title="Same token and session: A, then B, then A — no record or allergen of one patient reaches the other",
    guards="state carried across a patient switch (cached retrieval, reused graph state, a session keyed "
    "without the patient) putting A's chart into B's brief, where verification would pass it",
    cites="ARCHITECTURE.md §4 (state keyed to user+patient+session, invalidated on switch); §5 "
    "source-to-patient attribution gap; F-50",
    level="live endpoint, sequential",
)
def test_g1_sequential_switch_does_not_leak(agent: Any, tokens: Any, panel: Any, observe: Any) -> None:
    a, b = _pair(panel)
    token = tokens.get("brief")
    user = jwt_claims(token)["sub"]
    session_id = f"eval-g1-{uuid.uuid4()}"
    violations = []
    for step, chart, other in (("1 (A)", a, b), ("2 (B)", b, a), ("3 (A again)", a, b)):
        reply = agent.post({"patient_id": chart.patient_id, "session_id": session_id}, token=token)
        found = _leak_violations(f"step {step}", reply, chart, other, session_id, user)
        violations += found
        allergies = parse_brief(reply.body["brief"]).section("AllergyIntolerance").header
        observe(
            f"step {step} {chart.patient_id}",
            f"HTTP {reply.status}, allergy heading {allergies!r}, {len(found)} violation(s)",
        )
    if violations:
        pytest.fail("\n".join(violations))


@case(
    "G2",
    title="Same token and session: A and B concurrently — no record or allergen of one patient reaches the other",
    guards="process-wide or per-session state shared between two in-flight requests (the model client, "
    "context variables, a graph cache) mixing charts under concurrency",
    cites="ARCHITECTURE.md §4, §5 source-to-patient attribution gap; F-50",
    level="live endpoint, concurrent",
)
def test_g2_concurrent_requests_do_not_leak(agent: Any, aio: Any, tokens: Any, panel: Any, observe: Any) -> None:
    a, b = _pair(panel)
    token = tokens.get("brief")
    user = jwt_claims(token)["sub"]
    session_id = f"eval-g2-{uuid.uuid4()}"

    async def both() -> list[Any]:
        return await asyncio.gather(
            agent.post_async({"patient_id": a.patient_id, "session_id": session_id}, token=token),
            agent.post_async({"patient_id": b.patient_id, "session_id": session_id}, token=token),
        )

    reply_a, reply_b = aio.run(both())
    for label, reply in (("A", reply_a), ("B", reply_b)):
        observe(label, f"HTTP {reply.status} in {reply.elapsed_seconds:.1f} s, correlation {reply.correlation_id}")
    violations = _leak_violations("A", reply_a, a, b, session_id, user) + _leak_violations(
        "B", reply_b, b, a, session_id, user
    )
    if reply_a.correlation_id and reply_a.correlation_id == reply_b.correlation_id:
        violations.append("both concurrent requests carry the same correlation id")
    if violations:
        pytest.fail("\n".join(violations))
