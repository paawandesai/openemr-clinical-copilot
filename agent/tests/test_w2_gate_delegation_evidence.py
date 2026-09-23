"""
The delegated cases are scored against an artifact the delegated job writes.

These tests exist because the thing being checked is itself a check: for six
pipelines W2-K4 and W2-K6 reported a valid delegation to a job that was failing
every time, and nothing noticed. The classifier that closed that hole needs to
fail for the right reasons, so each reason gets a test.

The planted-regression harness covers the structural half (job deleted,
allow_failure switched on, test renamed away) by patching a scratch tree and
re-running the whole gate. It cannot cover this half: the artifact is a CI
artifact and is deliberately absent from a local tree, which is the very
condition these tests have to construct.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.w2.gate import checks_delegation as cd
from evals.w2.gate.loader import load_cases

COMMIT = "a" * 40
TESTS = ["testOne", "testTwo"]


def _junit(path: Path, names: list[str], *, failing: list[str] | None = None) -> None:
    failing = failing or []
    cases = "".join(
        f'<testcase name="{n}" class="T">'
        + ("<failure>boom</failure>" if n in failing else "")
        + "</testcase>"
        for n in names
    )
    path.write_text(
        f'<?xml version="1.0"?><testsuites><testsuite name="T">{cases}</testsuite></testsuites>',
        encoding="utf-8",
    )


def _result(path: Path, **over: object) -> None:
    payload = {
        "schema": cd.RESULT_SCHEMA,
        "job": "w2-writeback-php",
        "commit": COMMIT,
        "pipeline_id": "1",
        "job_id": "2",
        "job_url": "https://example/2",
        "exit_code": 0,
        "status": "success",
        "junit": "w2-writeback-php.xml",
    }
    payload.update(over)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Point the checker's repo root at a scratch tree and fix the commit."""
    monkeypatch.setattr(cd, "REPO_ROOT", tmp_path)
    # The structural half reads .gitlab-ci.yml and the PHP suite from the real
    # repo; pointing REPO_ROOT at a scratch tree would make it fail for reasons
    # these tests are not about. Stub it sound and let each test say otherwise.
    monkeypatch.setattr(cd, "_structural_problem", lambda case: None)
    monkeypatch.setenv("W2_GATE_COMMIT", COMMIT)
    monkeypatch.delenv("CI_COMMIT_SHA", raising=False)
    _junit(tmp_path / "w2-writeback-php.xml", TESTS)
    return tmp_path


def _case():
    return next(c for c in load_cases() if c.id == "W2-K6")


def test_a_green_result_for_this_commit_is_accepted(wired):
    _result(wired / "w2-writeback-php.result.json")
    assert cd._check_run_evidence(_case(), "w2-writeback-php", TESTS) is None


def test_a_result_from_another_commit_is_refused(wired):
    _result(wired / "w2-writeback-php.result.json", commit="b" * 40)
    problem = cd._check_run_evidence(_case(), "w2-writeback-php", TESTS)
    assert problem and "another commit" in problem


def test_a_failed_job_fails_the_case(wired):
    _result(wired / "w2-writeback-php.result.json", status="failed", exit_code=2)
    problem = cd._check_run_evidence(_case(), "w2-writeback-php", TESTS)
    assert problem and "did not pass" in problem


def test_success_is_not_taken_on_trust_when_a_named_test_never_ran(wired):
    _junit(wired / "w2-writeback-php.xml", ["testOne"])
    _result(wired / "w2-writeback-php.result.json")
    problem = cd._check_run_evidence(_case(), "w2-writeback-php", TESTS)
    assert problem and "testTwo" in problem


def test_success_is_not_taken_on_trust_when_a_named_test_failed(wired):
    _junit(wired / "w2-writeback-php.xml", TESTS, failing=["testTwo"])
    _result(wired / "w2-writeback-php.result.json")
    problem = cd._check_run_evidence(_case(), "w2-writeback-php", TESTS)
    assert problem and "did not pass" in problem


def test_a_result_written_by_a_different_job_is_not_evidence(wired):
    _result(wired / "w2-writeback-php.result.json", job="some-other-job")
    problem = cd._check_run_evidence(_case(), "w2-writeback-php", TESTS)
    assert problem and "not evidence about this job" in problem


def test_an_absent_artifact_makes_the_case_unscored_not_passed(wired):
    # No result file: the runner must SKIP the case. A skip is not a pass, and
    # this is the difference between "the job passed" and "we cannot see".
    assert cd.missing_result_reason(_case()) is not None


def test_a_broken_delegation_is_still_scored_without_an_artifact(wired, monkeypatch):
    # Structural breakage outranks the missing artifact: otherwise a branch
    # could disable a delegated case just by not being in CI.
    monkeypatch.setattr(cd, "_structural_problem", lambda case: "job deleted")
    assert cd.missing_result_reason(_case()) is None
