"""
B — Source attribution and completeness, over every brief the panel produces
(ARCHITECTURE.md §5; §6 open risk "retrieval incompleteness"; KEY_METRICS.md
Metrics 1, 2 and 5).

The agent's verifier checks claims against the records the agent itself
parsed. These cases check the same responses against the suite's own,
independent read of the chart (evals/oracle.py), which is the only way to see
the §5 limitations — retrieval completeness, source-data accuracy — that the
verifier cannot see by construction.

Each case also runs its checker on a deliberately corrupted copy of a real
response and requires a violation, so a checker that cannot fail cannot pass.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

import pytest

from evals.harness import case, require_brief
from evals.invariants import attribution_violations, completeness_violations, disclosure_violations

PANEL = ("empty_chart", "no_allergy_inactive_meds", "coded_allergies", "uncoded_allergy", "outlier")


def _panel_briefs(agent: Any, panel: Any) -> list[tuple[Any, dict[str, Any]]]:
    out = []
    for stratum in PANEL:
        patient_id = panel.picks[stratum]
        if patient_id is None:
            continue
        body = require_brief(agent.brief(patient_id), f"{stratum} {patient_id}")
        out.append((panel.charts[patient_id], body))
    return out


@case(
    "B1",
    title="Every verified claim cites a record of this patient and states a value that record holds",
    guards="fabricated or altered source_ids or values rendered as chart fact; a claim sourced from another "
    "resource type or another patient passing as verified",
    cites="ARCHITECTURE.md §5 (claims bound to source_id + field + value); §6 'fabricated claims' risk; "
    "KEY_METRICS.md Metric 1",
    level="live endpoint, panel of 5 patients",
)
def test_b1_claims_resolve_to_real_chart_records(agent: Any, panel: Any, observe: Any) -> None:
    briefs = _panel_briefs(agent, panel)
    violations: list[str] = []
    for chart, body in briefs:
        found = attribution_violations(body, chart)
        violations += [f"{chart.patient_id}: {v}" for v in found]
        observe(
            f"{chart.patient_id}",
            f"{body['verification']['claims']} claims, {body['verification']['verified']} verified, "
            f"{len(found)} attribution violation(s)",
        )

    # Control: a verified claim moved to a random id must be caught.
    chart, body = next(((c, b) for c, b in briefs if any(x["verified"] for x in b["claims"])), (None, None))
    if chart is not None:
        doctored = copy.deepcopy(body)
        next(x for x in doctored["claims"] if x["verified"])["source_id"] = str(uuid.uuid4())
        if not attribution_violations(doctored, chart):
            pytest.fail("eval control failed: a verified claim citing a random source_id was not flagged")

    # Control (F-57): a record whose code names it, shown as having no name.
    named = next(
        (
            (c, b, i)
            for c, b in briefs
            for i, x in enumerate(b["claims"])
            if x["verified"] and (rec := c.record(x["resource_type"], x["source_id"])) and rec.coded_name
        ),
        None,
    )
    if named is not None:
        chart, body, index = named
        doctored = copy.deepcopy(body)
        doctored["claims"][index].update(value=None, verified=False)
        if not attribution_violations(doctored, chart):
            pytest.fail("eval control failed: a coded record shown as having no name was not flagged")

    if violations:
        pytest.fail("\n".join(violations))


@case(
    "B2",
    title="Every failed claim is shown as UNVERIFIED and every brief line is backed by a claim",
    guards="a failed claim silently dropped or rendered as if verified; free text in the brief that no claim "
    "backs (the reasoning model's prose reaching the physician)",
    cites="ARCHITECTURE.md §5 ('never silently dropped, never softened'); KEY_METRICS.md Metric 2",
    level="live endpoint, panel of 5 patients",
)
def test_b2_brief_text_discloses_every_claim(agent: Any, panel: Any, observe: Any) -> None:
    briefs = _panel_briefs(agent, panel)
    violations: list[str] = []
    unverified_total = 0
    for chart, body in briefs:
        unverified_total += body["verification"]["unverified"]
        violations += [f"{chart.patient_id}: {v}" for v in disclosure_violations(body["claims"], body["brief"])]
        if body["verification"]["claims"] != len(body["claims"]):
            violations.append(f"{chart.patient_id}: verification summary counts disagree with the claim list")
    observe("unverified claims across the panel (live model)", unverified_total)

    chart, body = next(((c, b) for c, b in briefs if b["claims"]), (None, None))
    if chart is not None:
        doctored = copy.deepcopy(body)
        doctored["brief"] = doctored["brief"] + "\n  - Warfarin 5 MG Oral Tablet"
        if not disclosure_violations(doctored["claims"], doctored["brief"]):
            pytest.fail("eval control failed: an unbacked line appended to the brief was not flagged")

    if violations:
        pytest.fail("\n".join(violations))


@case(
    "B3",
    title="Nothing on the chart is silently missing: counts match the chart, every record is cited or counted",
    guards="a partial FHIR read or a model that skips records producing a clean-looking brief that verifies "
    "100% while leaving records out — the blind spot verification cannot see",
    cites="ARCHITECTURE.md §5 'Retrieval completeness' and §6 (open risk, 'requires separate "
    "retrieval-completeness evaluation'); KEY_METRICS.md Metric 5; F-22 (tell the user what was omitted)",
    level="live endpoint, panel of 5 patients",
)
def test_b3_no_record_silently_missing(agent: Any, panel: Any, observe: Any) -> None:
    briefs = _panel_briefs(agent, panel)
    violations: list[str] = []
    for chart, body in briefs:
        found = completeness_violations(body, chart)
        violations += [f"{chart.patient_id}: {v}" for v in found]
        observe(
            chart.patient_id,
            ", ".join(f"{s['resource_type']} {s['retrieved']}/{s['in_brief']}" for s in body["sections"])
            + " (retrieved/in brief)",
        )

    chart, body = next(((c, b) for c, b in briefs if b["claims"]), (None, None))
    if chart is not None:
        doctored = copy.deepcopy(body)
        doctored["claims"] = doctored["claims"][1:]
        if not completeness_violations(doctored, chart):
            pytest.fail("eval control failed: a record whose claim was removed was not flagged")

    if violations:
        pytest.fail("\n".join(violations))
