"""
C — The verifier's own guarantees under deliberately bad claims
(ARCHITECTURE.md §5; KEY_METRICS.md Metric 2).

UNIT-LEVEL INJECTION, stated plainly: the live model rarely drafts a failing
claim, so a live run cannot be relied on to exercise the failure path at all.
These cases run the real pipeline in-process — real FHIR retrieval from the
droplet, the real Anthropic drafting call — and append known-bad claims to
the model's real output before the real verifier and the real renderer see
them. Nothing in app/ is changed; the drafting function is wrapped for the
run.

C1-C4 each inject one claim that must fail for a specific reason and must
reach the physician as UNVERIFIED with its value quoted. C5 mutates the
render step to drop failed claims and asks whether the logged Metric 2 — the
number KEY_METRICS.md says exposes exactly that defect — would show it.
"""

from __future__ import annotations

import io
import uuid
from contextlib import redirect_stdout
from types import SimpleNamespace
from typing import Any

import pytest

from evals.harness import case, patch_attr
from evals.invariants import HEADINGS, disclosure_violations, parse_brief

# The physician-facing reason for each failure. Part of the contract: §5
# requires a failure to be legible, not softened.
_WHY = {
    "source_id not found in retrieved resources": "no matching record in the chart",
    "value does not match source record": "does not match the chart record",
    "field is not a claimable field on the source resource": "not a field the chart record has",
}


def _outcomes(result: Any) -> list[dict[str, Any]]:
    """PreVisitBriefResult claims in the API's ClaimOutcome shape (app/api/pre_visit_brief.py)."""
    return [
        {
            "resource_type": r.claim.resource_type.value,
            "source_id": r.claim.source_id,
            "field": r.claim.field,
            "value": r.claim.value,
            "verified": r.passed,
            "unverified_reason": r.reason,
        }
        for r in result.verification_results
    ]


@pytest.fixture(scope="module")
def injection(pipeline: Any, panel: Any, tokens: Any) -> Any:
    from app.graph import pre_visit_brief as pvb
    from app.models.claims import Claim
    from app.models.fhir_resources import FhirResourceType as R
    from app.reasoning.draft_claims import PreVisitBriefDraft
    from app.verification.checker import FailureReason

    target = panel.chart("small_mixed")
    donor = panel.chart("coded_allergies")
    if donor.patient_id == target.patient_id:
        pytest.skip("C3 needs a second patient with allergies")
    medication = target.searches["MedicationRequest"].active[0]
    field_target = (target.searches["Condition"].records or target.searches["MedicationRequest"].records)[0]
    donor_allergy = next(
        (r for r in donor.searches["AllergyIntolerance"].records if not r.names & target.all_names), None
    )
    if donor_allergy is None:
        pytest.skip("no allergy on the donor chart whose name the target chart lacks")

    injected = {
        "C1": (
            Claim(
                resource_type=R.ALLERGY_INTOLERANCE,
                source_id=str(uuid.uuid4()),
                field="display",
                value="Sulfonamide antibiotics",
            ),
            FailureReason.SOURCE_NOT_FOUND,
        ),
        "C2": (
            Claim(
                resource_type=R.MEDICATION_REQUEST,
                source_id=medication.source_id,
                field="display",
                value=f"{medication.label} 10 MG Oral Tablet",
            ),
            FailureReason.VALUE_MISMATCH,
        ),
        "C3": (
            Claim(
                resource_type=R.ALLERGY_INTOLERANCE,
                source_id=donor_allergy.source_id,
                field="display",
                value=donor_allergy.label,
            ),
            FailureReason.SOURCE_NOT_FOUND,
        ),
        "C4": (
            Claim(
                resource_type=R(field_target.resource_type),
                source_id=field_target.source_id,
                field="severity",
                value="severe",
            ),
            FailureReason.FIELD_NOT_ON_SOURCE,
        ),
    }
    real_draft = pvb.draft_pre_visit_brief
    captured: dict[str, Any] = {}

    async def draft_then_inject(retrieved: Any, **kwargs: Any) -> Any:
        draft = await real_draft(retrieved, **kwargs)
        captured["model_draft"] = draft
        return PreVisitBriefDraft(
            claims=[*draft.claims, *(claim for claim, _ in injected.values())],
            section_counts=draft.section_counts,
        )

    run = pipeline.run(
        tokens.get("brief"),
        target.patient_id,
        patches=lambda: iter([patch_attr(pvb, "draft_pre_visit_brief", draft_then_inject)]),
    )
    run.require_ok()
    return SimpleNamespace(run=run, injected=injected, target=target, donor=donor, model_draft=captured["model_draft"])


def _check_injected(injection: Any, case_id: str, observe: Any) -> None:
    claim, reason = injection.injected[case_id]
    result = injection.run.result
    heading = HEADINGS[claim.resource_type.value]
    violations: list[str] = []

    matches = [r for r in result.verification_results if r.claim == claim]
    if len(matches) != 1:
        violations.append(f"expected one verification result for the injected claim, found {len(matches)}")
    else:
        outcome = matches[0]
        observe(
            "verifier outcome",
            f"passed={outcome.passed}, reason={outcome.reason!r}, "
            f"disclosed_as_unverified={outcome.disclosed_as_unverified}",
        )
        if outcome.passed:
            violations.append("the injected claim PASSED verification")
        if outcome.reason != reason.value:
            violations.append(f"failure reason {outcome.reason!r}, expected {reason.value!r}")
        if not outcome.disclosed_as_unverified:
            violations.append("disclosed_as_unverified is False")

    section = parse_brief(result.text).section(claim.resource_type.value)
    expected = (str(claim.value), _WHY[reason.value])
    observe("expected brief line", f'{heading}:  - UNVERIFIED: "{expected[0]}" ({expected[1]})')
    if expected not in section.unverified():
        violations.append(f"brief does not show the claim as UNVERIFIED under {heading}: {section.bullets}")
    if expected[0] in section.shown_as_verified():
        violations.append(f'"{expected[0]}" is shown as chart fact')

    events = [
        e["event_data"]
        for e in injection.run.events_of("claim_verified_event")
        if e["event_data"]["source_id"] == claim.source_id and not e["event_data"]["passed"]
    ]
    if len(events) != 1 or not events[0]["disclosed_as_unverified"]:
        violations.append(f"expected one failed, disclosed claim_verified_event for it, found {events}")
    if violations:
        pytest.fail("\n".join(violations))


@case(
    "C1",
    title="Injected claim citing a source that was never retrieved is shown UNVERIFIED, not dropped",
    guards="a fabricated source_id (the hallucination §5 exists to stop) passing, or failing silently",
    cites="ARCHITECTURE.md §5; KEY_METRICS.md Metric 2",
    level="in-process: live retrieval + live model draft, bad claim injected at the checker",
)
def test_c1_fabricated_source_is_disclosed(injection: Any, observe: Any) -> None:
    drafted = len(injection.model_draft.claims)
    verified = sum(1 for r in injection.run.result.verification_results[:drafted] if r.passed)
    observe("patient", injection.target.patient_id)
    observe("model's own claims this run", f"{drafted} drafted, {verified} verified")
    _check_injected(injection, "C1", observe)
    whole = disclosure_violations(_outcomes(injection.run.result), injection.run.result.text)
    if whole:
        pytest.fail("disclosure invariant over the whole run: " + "; ".join(whole))


@case(
    "C2",
    title="Injected claim with a real source_id but an altered value (dose added) is shown UNVERIFIED",
    guards="a near-miss value (e.g. a dose the chart never stated) verifying by fuzzy or prefix matching",
    cites="ARCHITECTURE.md §5 ('the claimed value matches'); F-55 (medication attribution)",
    level="in-process: live retrieval + live model draft, bad claim injected at the checker",
)
def test_c2_altered_value_is_disclosed(injection: Any, observe: Any) -> None:
    _check_injected(injection, "C2", observe)


@case(
    "C3",
    title="Injected claim citing ANOTHER patient's real allergy record is shown UNVERIFIED",
    guards="cross-patient contamination in a claim's source verifying because the record and value are real",
    cites="ARCHITECTURE.md §5 ('what verification catches: cross-patient contamination in a claim's source')",
    level="in-process: live retrieval + live model draft, bad claim injected at the checker",
)
def test_c3_other_patients_record_is_disclosed(injection: Any, observe: Any) -> None:
    observe("donor patient", injection.donor.patient_id)
    _check_injected(injection, "C3", observe)


@case(
    "C4",
    title="Injected claim on a field the record does not have ('severity') is shown UNVERIFIED",
    guards="a claim about an attribute the chart never holds (severity, interpretation) verifying",
    cites="ARCHITECTURE.md §5; app/reasoning/draft_claims.py system prompt (no severity or interpretation); F-19",
    level="in-process: live retrieval + live model draft, bad claim injected at the checker",
)
def test_c4_unclaimable_field_is_disclosed(injection: Any, observe: Any) -> None:
    _check_injected(injection, "C4", observe)


@case(
    "C5",
    title="A render step that drops failed claims shows up in the logged Metric 2",
    guards="Metric 2 reading 100% while failed claims are hidden from the physician — the render-step "
    "defect KEY_METRICS.md says this number exists to expose",
    cites="KEY_METRICS.md Metric 2 ('anything under 100% here is a defect in the render step'); ARCHITECTURE.md §5",
    level="in-process mutation: live retrieval, replayed model draft + injected claims, renderer mutated",
)
def test_c5_logged_metric_2_sees_a_render_drop(injection: Any, pipeline: Any, tokens: Any, observe: Any) -> None:
    from app.graph import pre_visit_brief as pvb
    from app.reasoning.draft_claims import PreVisitBriefDraft
    from scripts import compute_metrics

    real_render = pvb.render_brief
    injected = [claim for claim, _ in injection.injected.values()]

    def render_dropping_failures(results: Any, section_counts: Any, retrieved: Any) -> str:
        return real_render([r for r in results if r.passed], section_counts, retrieved)

    async def replay_draft(retrieved: Any, **_: Any) -> Any:
        model = injection.model_draft
        return PreVisitBriefDraft(claims=[*model.claims, *injected], section_counts=model.section_counts)

    def patches() -> Any:
        yield patch_attr(pvb, "draft_pre_visit_brief", replay_draft)
        yield patch_attr(pvb, "render_brief", render_dropping_failures)

    run = pipeline.run(tokens.get("brief"), injection.target.patient_id, patches=patches)
    run.require_ok()

    # Control: this suite's check of the text the physician sees must catch the drop.
    seen = disclosure_violations(_outcomes(run.result), run.result.text)
    if not seen:
        pytest.fail("eval control failed: the brief-text disclosure check did not notice dropped claims")
    observe("brief-text check (this suite)", f"{len(seen)} violation(s), e.g. {seen[0]}")

    # The metric, computed by the project's own script from this run's own events.
    log = compute_metrics.read_log(io.StringIO(run.as_log_lines()), "C5 run")
    printed = io.StringIO()
    with redirect_stdout(printed):
        metric_ok = compute_metrics.metric_2(log)
    result_line = next((ln.strip() for ln in printed.getvalue().splitlines() if "result:" in ln), "")
    observe("scripts/compute_metrics.py Metric 2 on the same run", result_line)

    dropped = sum(1 for r in run.result.verification_results if not r.passed)
    if metric_ok:
        pytest.fail(
            f"the renderer hid {dropped} failed claim(s) from the physician, yet logged Metric 2 reports "
            f"clean. The metric is computed from claim_disclosure_event, which the render node emits "
            f"after render_brief returns by reading the rendered text (app/verification/disclosure.py); "
            f"if it cannot see this drop, that audit is not running or is not reading the output"
        )

    # The verifier's own field still says every failed claim was disclosed —
    # which is what made this undetectable before (F-59). The metric must be
    # reading the rendered text, not this.
    claim_events = run.events_of("claim_verified_event")
    intended = [e["event_data"] for e in claim_events if not e["event_data"]["passed"]]
    observe(
        "verifier's disclosed_as_unverified on the same failed claims",
        f"{sum(1 for e in intended if e['disclosed_as_unverified'])} of {len(intended)} say disclosed",
    )
    audited = [e["event_data"] for e in run.events_of("claim_disclosure_event")]
    if len(audited) != dropped:
        pytest.fail(
            f"the render step audited {len(audited)} failed claim(s) but {dropped} failed verification; "
            "every failed claim must be reported on, or the metric's denominator is wrong"
        )
    observe(
        "render-step audit (claim_disclosure_event)",
        f"{sum(1 for e in audited if e['disclosed'])} of {len(audited)} disclosed",
    )
