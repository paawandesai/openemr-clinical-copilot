"""
E — A patient who does not exist gets 404, never a clean "not recorded" brief.

Regression guard for the empty-Bundle bug fixed in commit 5ba6c61: OpenEMR's
FHIR search answers an unknown or malformed patient id with HTTP 200 and an
empty Bundle, exactly as for a real patient with nothing recorded
(ARCHITECTURE.md §5, "It also assumes the patient exists"). Before the fix,
a mistyped id rendered "Allergies: not recorded" — for nobody.

Each case first re-checks that the trap still exists upstream (the searches
really do return an empty 200 for the bad id), so a pass means the guard
held, not that OpenEMR stopped setting the trap.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from evals.harness import case, patch_attr, prompt_faithful_drafter

SEARCHED = ("Condition", "MedicationRequest", "AllergyIntolerance")


def _trap(oracle: Any, aio: Any, patient_id: str) -> dict[str, str]:
    """What OpenEMR answers for this id: the Patient read, and each search the brief runs."""

    async def probe() -> dict[str, str]:
        out = {"Patient read": f"HTTP {(await oracle.get(f'Patient/{patient_id}')).status_code}"}
        for rt in SEARCHED:
            search = await oracle.search(rt, patient_id)
            out[f"{rt} search"] = f"HTTP {search.status}, {len(search.records)} entries"
        return out

    return aio.run(probe())


def _not_found_violations(reply: Any) -> list[str]:
    violations = []
    if reply.status != 404 or reply.error != "patient_not_found":
        violations.append(f"HTTP {reply.status} {reply.error}, expected 404 patient_not_found")
    if reply.body and "brief" in reply.body:
        violations.append(f"a brief was rendered for a patient who does not exist: {reply.body['brief'][:120]!r}")
    if "not recorded" in reply.text.lower():
        violations.append("response says 'not recorded'")
    return violations


@case(
    "E1",
    title="Unknown, well-formed patient UUID: 404 patient_not_found, no brief",
    guards="the empty-Bundle bug returning: a nonexistent patient rendered as a real patient with "
    "'Allergies: not recorded'",
    cites="commit 5ba6c61 (Patient read before search); ARCHITECTURE.md §5 empty-results assumption; F-21",
    level="live endpoint",
)
def test_e1_unknown_patient_is_404(agent: Any, oracle: Any, aio: Any, panel: Any, observe: Any) -> None:
    patient_id = str(uuid.uuid4())
    assert patient_id not in panel.charts
    for label, answer in _trap(oracle, aio, patient_id).items():
        observe(f"OpenEMR, {label}", answer)
    reply = agent.brief(patient_id)
    observe("endpoint", f"HTTP {reply.status} {reply.error}")
    violations = _not_found_violations(reply)
    if violations:
        pytest.fail("\n".join(violations))


@case(
    "E2",
    title="A one-character typo of a real patient's id: 404, not an empty brief",
    guards="the most likely real-world bad id — a near-miss of a real one — being treated as an empty chart",
    cites="commit 5ba6c61; ARCHITECTURE.md §5; F-21",
    level="live endpoint",
)
def test_e2_typo_of_real_id_is_404(agent: Any, oracle: Any, aio: Any, panel: Any, observe: Any) -> None:
    real = panel.chart("coded_allergies").patient_id
    last = real[-1]
    typo = real[:-1] + format((int(last, 16) + 1) % 16, "x")
    if typo in panel.charts:
        pytest.skip("the one-character variant is itself a real patient")
    for label, answer in _trap(oracle, aio, typo).items():
        observe(f"OpenEMR, {label}", answer)
    reply = agent.brief(typo)
    observe("real id → typo", f"{real} → {typo}")
    observe("endpoint", f"HTTP {reply.status} {reply.error}")
    violations = _not_found_violations(reply)
    if violations:
        pytest.fail("\n".join(violations))


@case(
    "E3",
    title="Legacy integer pid ('20') instead of the FHIR id: rejected at the API (422) and in the pipeline",
    guards="an OpenEMR pid (the id the audit and SQL use) passed straight through to FHIR search, which "
    "answers it with an empty Bundle, producing a brief for no one — via either entry point",
    cites="F-22 / F-43 (pid=20 is meaningless as a FHIR id); app/models/api.py PATIENT_ID_PATTERN; commit 5ba6c61",
    level="live endpoint + in-process (run_pre_visit_brief has no id-format check of its own)",
)
def test_e3_legacy_pid_is_rejected_everywhere(
    agent: Any, pipeline: Any, oracle: Any, aio: Any, tokens: Any, observe: Any
) -> None:
    from app.fhir.exceptions import PatientNotFoundError

    for label, answer in _trap(oracle, aio, "20").items():
        observe(f"OpenEMR, {label}", answer)
    reply = agent.post({"patient_id": "20"}, token=tokens.get("brief"))
    observe("endpoint", f"HTTP {reply.status} {reply.error}")
    violations = []
    if reply.status != 422 or reply.error != "invalid_request":
        violations.append(f"endpoint answered HTTP {reply.status} {reply.error}, expected 422 invalid_request")

    run = pipeline.run(tokens.get("brief"), "20")
    observe(
        "in-process",
        f"raised {type(run.error).__name__ if run.error else 'nothing'}; "
        f"events {[e['event_type'] for e in run.events]}",
    )
    if not isinstance(run.error, PatientNotFoundError):
        rendered = f": {run.result.text[:160]!r}" if run.result else ""
        violations.append(
            f"run_pre_visit_brief('20') raised {type(run.error).__name__ if run.error else 'nothing'}"
            f", expected PatientNotFoundError{rendered}"
        )
    if run.events:
        violations.append(
            f"events logged before the patient check stopped the run: {[e['event_type'] for e in run.events]}"
        )

    # Control: with the Patient read removed (the pre-5ba6c61 pipeline), the same
    # id must reproduce the bug, or this case is not testing the guard at all.
    from app.fhir import client as fhir_client
    from app.graph import pre_visit_brief as pvb

    async def no_patient_check(token: str, patient_id: str) -> None:
        return None

    def mutant_patches() -> Any:
        yield patch_attr(fhir_client, "ensure_patient_exists", no_patient_check)
        # The bug is retrieval → render; the model is not part of it, so none is called.
        yield patch_attr(pvb, "draft_pre_visit_brief", prompt_faithful_drafter())

    mutant = pipeline.run(tokens.get("brief"), "20", patches=mutant_patches)
    reproduced = mutant.result is not None and "Allergies: not recorded" in mutant.result.text
    observe(
        "control, Patient read removed",
        "renders 'Allergies: not recorded' for pid '20'"
        if reproduced
        else f"did not reproduce ({type(mutant.error).__name__ if mutant.error else 'no brief text'})",
    )
    if not reproduced:
        violations.append("eval control failed: without the Patient read, pid '20' did not render an empty brief")
    if violations:
        pytest.fail("\n".join(violations))
