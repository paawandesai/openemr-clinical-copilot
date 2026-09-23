"""
Delegated cases: the gate checks that the delegation is real.

Some invariants cannot be asserted from here. W2-K4's — a failure mid-write
leaves no ledger row and no orphaned ciphertext file — lives in the fork's
PHP module, its proof is PHPUnit against faked collaborators, and the gate's
image has no PHP. Marking such a case `pending_sut` said "nobody is checking
this", which was true and is now avoidable; marking it `active` and asserting
some weaker Python-side property would have been a lie.

`delegated` is the third answer: the behaviour is enforced by a named job in
the same pipeline, and what the gate scores is whether that delegation still
holds up. Three things have to be true, and all three are checked here
against the repository rather than taken on trust:

1. the named job exists in `.gitlab-ci.yml`;
2. it is not `allow_failure: true`, so its failure fails the pipeline the
   same way the eval gate's does;
3. every named test method still exists in the named suite file.

That is what stops `delegated` becoming a way to make a case disappear. A
case cannot be quietly parked by pointing it at a job somebody deleted, at a
job that is allowed to fail, or at a test that has been renamed away — each
of those fails this check, deterministically, in the gate that the branch is
scored by.

4. the job actually RAN and PASSED for the commit under test, read from the
   result file the job itself writes and uploads as an artifact.

(4) was missing until D5B409B's follow-up, and its absence was not theoretical:
the write-back job failed six pipelines in a row on a missing `unzip`, and
W2-K4 and W2-K6 reported a valid delegation through every one of them. The
delegation was structurally perfect and enforced by nothing.

The result file is evidence, so it is treated as evidence: it must name this
job, it must name the commit being scored, and the JUnit XML beside it must
show each named test actually passing. A file naming another commit is stale
and is refused — otherwise one green run would vouch for every later one.

It is NOT signed, deliberately. Anyone who can write `.gitlab-ci.yml` can make
the job print whatever they like, so a signature would authenticate the same
party it is meant to constrain. The gate is a guard against drift and mistakes,
not a boundary against someone editing the pipeline on purpose (CLAUDE.md says
this of the hooks too). What the file does buy is freshness and provenance,
which is what actually failed here.

Outside CI there is no artifact and no way to know. The case is then UNSCORED —
skipped with a notice, never passed — so a local run reports INCOMPLETE rather
than a green that has not been earned.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import xml.etree.ElementTree as ET
from typing import Any

from evals.w2.gate.models import Case, CheckOutcome, GateError
from evals.w2.gate.paths import REPO_ROOT
from evals.w2.gate.registry import check

CI_CONFIG = REPO_ROOT / ".gitlab-ci.yml"

RESULT_SCHEMA = "w2-gate/delegated-result/1.0.0"


def result_path(job_name: str) -> pathlib.Path:
    """Where the delegated job's result artifact lands, relative to the repo root."""
    return REPO_ROOT / f"{job_name}.result.json"


def commit_under_test() -> str | None:
    """
    The commit the gate is scoring, as the runner resolves it (`run._commit`):
    the environment first, then `git rev-parse HEAD`. None only where neither
    works — a scratch copy with no git, as the planted-regression harness
    makes.

    The git fallback is what makes a stale LOCAL artifact visible. Without it
    this returned None outside CI, the commit comparison below never ran, and
    a result produced three commits ago vouched for the tree in front of you.
    """
    for var in ("W2_GATE_COMMIT", "CI_COMMIT_SHA"):
        value = os.environ.get(var)
        if value:
            return value.strip()
    try:
        head = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return None
    return head or None


def missing_result_reason(case: Case) -> str | None:
    """
    Why this delegated case cannot be scored here, or None if it can.

    Called by the runner, not from inside the check, for the same reason
    `requires: [tesseract]` is: "no artifact because we are not in CI" is a
    skip with a notice, not a failing assertion about the branch.
    """
    delegation = case.delegation() or {}
    job_name = str(delegation.get("job", ""))
    if not job_name:
        return None
    if _structural_problem(case) is not None:
        # The delegation is broken on its face — job deleted, allow_failure
        # switched on, test renamed away. That is a finding about the branch
        # and does not depend on any artifact, so the case is SCORED and the
        # check reports it. Skipping here would let a branch disable a
        # delegated case simply by not being in CI.
        return None
    path = result_path(job_name)
    if not path.is_file():
        return (
            f"no result artifact for the delegated job {job_name!r}: "
            f"{path.name} is absent, so whether it passed is unknown here"
        )
    # An artifact for another commit says nothing about this one, exactly as a
    # missing one says nothing: UNSCORED, not failed. The branch is not at
    # fault; the artifact is out of date. Re-run scripts/w2-writeback-result.sh.
    try:
        recorded = str(json.loads(path.read_text(encoding="utf-8")).get("commit") or "")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None  # unreadable: the check itself reports it, as a failure
    expected = commit_under_test()
    if expected and recorded and recorded != expected:
        return (
            f"{path.name} is stale: it records commit {recorded[:12]}, and the gate is "
            f"scoring {expected[:12]}. Re-run scripts/w2-writeback-result.sh after every "
            "commit, rebase or branch switch"
        )
    return None


def _named_test_outcomes(junit: pathlib.Path, tests: list[str]) -> tuple[list[str], list[str]]:
    """(absent, failed) among `tests`, read from a JUnit XML report."""
    root = ET.parse(junit).getroot()
    seen: dict[str, bool] = {}
    for case_el in root.iter("testcase"):
        name = case_el.get("name") or ""
        bad = any(case_el.find(tag) is not None for tag in ("failure", "error", "skipped"))
        seen[name] = seen.get(name, True) and not bad
    absent = [t for t in tests if t not in seen]
    failed = [t for t in tests if seen.get(t) is False]
    return absent, failed


def _check_run_evidence(case: Case, job_name: str, tests: list[str]) -> str | None:
    """Condition (4). Returns a failure detail, or None when the evidence holds."""
    path = result_path(job_name)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"the result artifact {path.name} could not be read: {type(exc).__name__}"

    if payload.get("schema") != RESULT_SCHEMA:
        return f"{path.name} declares schema {payload.get('schema')!r}, expected {RESULT_SCHEMA!r}"
    if payload.get("job") != job_name:
        return (
            f"{path.name} was written by job {payload.get('job')!r}, but {case.id} is "
            f"delegated to {job_name!r}. It is not evidence about this job."
        )

    expected_commit = commit_under_test()
    recorded = str(payload.get("commit") or "")
    if expected_commit and recorded and recorded != expected_commit:
        return (
            f"{path.name} records commit {recorded[:12]}, but the gate is scoring "
            f"{expected_commit[:12]}. A result from another commit does not vouch for "
            "this one; one green run would otherwise cover every later one."
        )

    if payload.get("status") != "success":
        return (
            f"the delegated job {job_name!r} reported status "
            f"{payload.get('status')!r} (exit {payload.get('exit_code')!r}) for this "
            f"commit. See {payload.get('job_url') or 'the job log'}. The case is "
            "delegated to a job that did not pass."
        )

    junit_name = str(payload.get("junit") or "")
    junit = REPO_ROOT / junit_name if junit_name else None
    if junit is None or not junit.is_file():
        return (
            f"{path.name} claims success but its JUnit report {junit_name!r} is not "
            "here, so which tests ran cannot be checked."
        )
    try:
        absent, failed = _named_test_outcomes(junit, tests)
    except ET.ParseError as exc:
        return f"the JUnit report {junit_name} is not parseable XML: {exc}"
    if absent:
        return (
            f"the job reported success but its JUnit report does not mention {absent}. "
            "The tests this case names were not run."
        )
    if failed:
        return f"the job reported success but {failed} did not pass in its JUnit report"
    return None


def _ci_jobs() -> dict[str, Any]:
    if not CI_CONFIG.is_file():
        raise GateError(f"{CI_CONFIG} is missing; a delegated case has nothing to delegate to")
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment, not logic
        raise GateError(f"pyyaml is needed to read {CI_CONFIG.name}: {exc}") from exc
    loaded = yaml.safe_load(CI_CONFIG.read_text(encoding="utf-8")) or {}
    reserved = {"stages", "workflow", "variables", "default", "include", "image", "cache"}
    return {
        name: body
        for name, body in loaded.items()
        if isinstance(body, dict) and name not in reserved and not name.startswith(".")
    }


def _structural_problem(case: Case) -> str | None:
    """Conditions (1)-(3): the delegation itself, read off the repository."""
    delegation = case.delegation()
    if not delegation:
        return f"{case.id} is delegated and names no job"

    job_name = str(delegation.get("job", ""))
    suite = str(delegation.get("suite", ""))
    tests = [str(t) for t in (delegation.get("tests") or [])]

    jobs = _ci_jobs()
    if job_name not in jobs:
        return (
            f"{case.id} is delegated to CI job {job_name!r}, which is not in "
            f".gitlab-ci.yml (jobs: {sorted(jobs)}). The case is enforced by nothing."
        )
    job = jobs[job_name]

    if job.get("allow_failure"):
        return (
            f"the job {job_name!r} is allow_failure: {job['allow_failure']!r}. A "
            "delegated case has to fail the pipeline when its job fails, or "
            "delegating it is how it stops mattering."
        )

    # The job has to plausibly run the suite. PHPUnit is pointed at a config
    # file, not at a test file, so an exact-path match would never hold —
    # walk the suite's parents and require the script to name one of them.
    # Loose enough to survive the config file being renamed, tight enough to
    # catch a job pointed somewhere else entirely.
    script = " ".join(str(line) for line in (job.get("script") or []))
    ancestors: list[str] = []
    walked = pathlib.PurePosixPath(suite)
    while walked.parent != walked:
        walked = walked.parent
        if str(walked) != ".":
            ancestors.append(str(walked))
    if suite not in script and not any(a in script for a in ancestors):
        return (
            f"the job {job_name!r} names nothing on the path to {suite}; its script is "
            f"{script!r}. Either it does not run the suite, or the suite moved."
        )

    path = REPO_ROOT / suite
    if not path.is_file():
        return f"{case.id} names the suite {suite}, which does not exist"
    source = path.read_text(encoding="utf-8")
    missing = [
        name for name in tests if not re.search(rf"function\s+{re.escape(name)}\s*\(", source)
    ]
    if missing:
        return (
            f"{case.id} is delegated to {missing} in {suite}, which no longer "
            "contain them. Either the tests were renamed and this case was not, or "
            "the assertions were deleted and nothing noticed."
        )

    return None


@check("chk.x_delegation_is_real")
def x_delegation_is_real(case: Case) -> CheckOutcome:
    """All four conditions, for one delegated case."""
    problem = _structural_problem(case)
    if problem:
        return CheckOutcome.failed(problem)

    delegation = case.delegation() or {}
    job_name = str(delegation.get("job", ""))
    suite = str(delegation.get("suite", ""))
    tests = [str(t) for t in (delegation.get("tests") or [])]

    problem = _check_run_evidence(case, job_name, tests)
    if problem:
        return CheckOutcome.failed(problem)

    payload = json.loads(result_path(job_name).read_text(encoding="utf-8"))
    return CheckOutcome.ok(
        f"delegated to CI job {job_name!r} (no allow_failure), which runs {suite}; "
        f"all {len(tests)} named test(s) present and PASSING in its JUnit report for "
        f"commit {str(payload.get('commit') or '?')[:12]} "
        f"(pipeline {payload.get('pipeline_id')}, job {payload.get('job_id')}): "
        f"{', '.join(tests)}"
    )
