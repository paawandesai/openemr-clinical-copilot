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

What it does NOT prove: that the job passed. The gate runs in one job and
cannot read another's result. The pipeline is what joins them, which is why
(2) matters more than it looks.
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

from evals.w2.gate.models import Case, CheckOutcome, GateError
from evals.w2.gate.paths import REPO_ROOT
from evals.w2.gate.registry import check

CI_CONFIG = REPO_ROOT / ".gitlab-ci.yml"


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


@check("chk.x_delegation_is_real")
def x_delegation_is_real(case: Case) -> CheckOutcome:
    """The three conditions above, for one delegated case."""
    delegation = case.delegation()
    if not delegation:
        return CheckOutcome.failed(f"{case.id} is delegated and names no job")

    job_name = str(delegation.get("job", ""))
    suite = str(delegation.get("suite", ""))
    tests = [str(t) for t in (delegation.get("tests") or [])]

    jobs = _ci_jobs()
    if job_name not in jobs:
        return CheckOutcome.failed(
            f"{case.id} is delegated to CI job {job_name!r}, which is not in "
            f".gitlab-ci.yml (jobs: {sorted(jobs)}). The case is enforced by nothing."
        )
    job = jobs[job_name]

    if job.get("allow_failure"):
        return CheckOutcome.failed(
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
        return CheckOutcome.failed(
            f"the job {job_name!r} names nothing on the path to {suite}; its script is "
            f"{script!r}. Either it does not run the suite, or the suite moved."
        )

    path = REPO_ROOT / suite
    if not path.is_file():
        return CheckOutcome.failed(f"{case.id} names the suite {suite}, which does not exist")
    source = path.read_text(encoding="utf-8")
    missing = [
        name for name in tests if not re.search(rf"function\s+{re.escape(name)}\s*\(", source)
    ]
    if missing:
        return CheckOutcome.failed(
            f"{case.id} is delegated to {missing} in {suite}, which no longer "
            "contain them. Either the tests were renamed and this case was not, or "
            "the assertions were deleted and nothing noticed."
        )

    return CheckOutcome.ok(
        f"delegated to CI job {job_name!r} (no allow_failure), which runs {suite}; "
        f"all {len(tests)} named test(s) present: {', '.join(tests)}"
    )
