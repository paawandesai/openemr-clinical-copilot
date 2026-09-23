"""
Category M — the judge meta-check.

The work order's third addition, and the answer to "who validates the
validator" for the one thing this gate can validate without a labelled set:
whether the judge still grades a frozen fixture the way it is labelled.

Four fixtures, two per judge rubric, one of each polarity. They are frozen:
their text is the cache key, so editing one is a visible change to the
committed fixture and not a quiet re-baselining.

What this catches, precisely:

* a judge prompt edited to be permissive — `fc_fail` and `sr_fail` start
  passing, and both are here;
* a judge prompt edited to be paranoid — `fc_pass` and `sr_pass` start
  failing, which would otherwise show up as a mystery regression spread
  across every judge case in the suite;
* a silent model rotation — `judge.py` compares the served model against the
  pinned version and fails the tier.

What it does NOT catch: a judge that was wrong from the start. That needs
100-200 human-labelled examples per rubric and is out of scope; every judge
result the gate prints says UNCALIBRATED for that reason.

Both rubrics are covered, and the fixtures were chosen by measurement rather
than by argument — see D48. Every fixture is stable: five independent
best-of-3 gradings under the committed prompt all agreeing with its label.
Each rubric additionally has at least one *discriminating* fixture, one a
generic one-line rubric grades the other way on all five rounds. Stability is
what stops a critical fixture becoming the flake that gets the category
deleted; discrimination is the only thing that makes the set a drift detector
rather than an alive-check.

    discriminating (fc)  fc_fail_guideline_as_patient_fact
                         fc_fail_added_clinical_judgement
    discriminating (sr)  sr_fail, sr_fail_over_refusal
    alive-checks         fc_pass, sr_pass, fc_fail,
                         fc_fail_invented_unit_conversion

Both properties have to be measured over five rounds, and neither can be
assumed from the other. Three votes wrongly rejected two of the fixtures above
on a first pass, and wrongly admitted fc_fail_invented_unit_conversion as a
detector on a second — a generic rubric grades that one fail on two rounds in
five, which the planted-regression harness found by failing to flip it.

Two candidates were rejected outright. A claim citing a chunk that does not
contain it is stable (15/15) but a generic rubric fails it too, so it would
have added a third alive-check and no coverage. And "the laboratory reported
the potassium as normal" over a row with a blank flag column was graded PASS
by the committed prompt, 12/15 — the fc rubric does not enforce D20 at the
prose level. That is a finding, not a fixture: blank-is-not-normal is held
deterministically by W2-D2, which is where a safety rule a type can hold
belongs.
"""

from __future__ import annotations

import json
from typing import Any

from evals.w2.gate.judge import judge
from evals.w2.gate.models import Case, CheckOutcome, GateError
from evals.w2.gate.paths import FIXTURES_DIR
from evals.w2.gate.registry import check


def _fixture(relative: str) -> dict[str, Any]:
    path = FIXTURES_DIR / relative
    if not path.is_file():
        raise GateError(f"judge meta-check fixture {path} is missing")
    return json.loads(path.read_text(encoding="utf-8"))


def _grade_named(case: Case) -> CheckOutcome:
    relatives = case.fixtures()
    if len(relatives) != 1:
        raise GateError(f"{case.id} names {len(relatives)} fixtures; a meta-check names one")
    fixture = _fixture(relatives[0])
    expected = fixture["expected_verdict"]
    rubric = fixture["rubric"]

    verdict = judge().grade(case.id, rubric, fixture["payload"])
    evidence: dict[str, object] = {
        "fixture": fixture["fixture_id"],
        "expected": expected,
        "verdict": verdict.verdict,
        "votes": list(verdict.votes),
        "majority": verdict.majority,
        "model_version": verdict.model_version,
        "prompt_version": verdict.prompt_version,
        "cached": verdict.cached,
        "calibration": "UNCALIBRATED",
    }
    if verdict.verdict != expected:
        return CheckOutcome.failed(
            f"{fixture['fixture_id']}: the judge said {verdict.verdict} where the frozen "
            f"label is {expected} (votes {list(verdict.votes)}). "
            f"{fixture['why_this_label']} "
            f"First reason given: {verdict.reasons[0] if verdict.reasons else '(none)'}",
            **evidence,
        )
    detail = (
        f"{fixture['fixture_id']}: {verdict.verdict} as labelled "
        f"({verdict.majority}/{len(verdict.votes)}"
        f"{', cached' if verdict.cached else ''}) [UNCALIBRATED]"
    )
    return CheckOutcome.ok(detail, **evidence)


@check("judge.m_factually_consistent_known_pass")
def m_factually_consistent_known_pass(case: Case) -> CheckOutcome:
    return _grade_named(case)


@check("judge.m_factually_consistent_known_fail")
def m_factually_consistent_known_fail(case: Case) -> CheckOutcome:
    return _grade_named(case)


@check("judge.m_safe_refusal_known_pass")
def m_safe_refusal_known_pass(case: Case) -> CheckOutcome:
    return _grade_named(case)


@check("judge.m_safe_refusal_known_fail")
def m_safe_refusal_known_fail(case: Case) -> CheckOutcome:
    return _grade_named(case)


@check("judge.m_safe_refusal_known_fail_over_refusal")
def m_safe_refusal_known_fail_over_refusal(case: Case) -> CheckOutcome:
    return _grade_named(case)


@check("judge.m_factually_consistent_known_fail_guideline_as_patient_fact")
def m_factually_consistent_known_fail_guideline_as_patient_fact(case: Case) -> CheckOutcome:
    return _grade_named(case)


@check("judge.m_factually_consistent_known_fail_invented_unit_conversion")
def m_factually_consistent_known_fail_invented_unit_conversion(case: Case) -> CheckOutcome:
    return _grade_named(case)


@check("judge.m_factually_consistent_known_fail_added_clinical_judgement")
def m_factually_consistent_known_fail_added_clinical_judgement(case: Case) -> CheckOutcome:
    return _grade_named(case)
