"""
The gate, as one command.

    cd agent && python -m evals.w2.gate
    cd agent && python -m evals.w2.gate --det-only      # deterministic tier only
    cd agent && python -m evals.w2.gate --seed-baseline # write baseline.json

Exit code is the verdict and nothing else, so CI needs no output parsing:

    0  PASS        every active case was scored and no rule fired
    1  BLOCKED     a gate rule fired
    2  ERROR       the gate could not run at all
    3  INCOMPLETE  no rule fired, but a critical case was never scored

3 exists because 0 was a lie. A run with the judge tier skipped scores no
category-M case, and every one of those is critical — so it exited 0 having
checked none of the judge meta-checks. A green pipeline that scored no
critical case is the exact failure this gate exists to prevent, so a missing
ANTHROPIC_API_KEY in CI now FAILS the job rather than printing a notice
nobody reads.

`--allow-incomplete` maps 3 to 0, for the one caller that means it: the local
pre-push hook, which deliberately runs `--det-only` and is not claiming to be
evidence. CI never passes it. It is a flag at the call site rather than an
exception buried in the gate, so "this run did not check the critical cases"
is visible in the command somebody reads.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from typing import Any

from evals.w2.gate import checks_delegation
from evals.w2.gate import judge as judge_module
from evals.w2.gate.loader import load_cases, load_categories
from evals.w2.gate.models import AssertionResult, Case, CaseResult, GateError, Status, Tier
from evals.w2.gate.paths import BASELINE, REPO_ROOT
from evals.w2.gate.registry import load_all_checks, resolve
from evals.w2.gate.verdict import CategoryScore, Violation, evaluate

PENDING_REASON = "pending_sut: specified, waiting on the supervisor graph"
MISSING_TOOL_REASON = "cannot run here: {why}"
JUDGE_SKIPPED_REASON = "judge tier skipped: no ANTHROPIC_API_KEY"
DET_ONLY_REASON = "judge tier not run: --det-only"
DELEGATED_NO_RESULT_REASON = "{why}"

RULE = "=" * 78


def _run_case(case: Case) -> CaseResult:
    results: list[AssertionResult] = []
    for assertion in case.assertions:
        fn = resolve(assertion.check)
        try:
            outcome = fn(case)
        except GateError:
            raise
        except Exception as exc:  # noqa: BLE001 - a check that blew up did not pass
            from evals.w2.gate.models import CheckOutcome

            outcome = CheckOutcome.failed(f"{assertion.check} raised {type(exc).__name__}: {exc}")
        results.append(AssertionResult(assertion=assertion, outcome=outcome))
    return CaseResult(case=case, results=tuple(results))


def _missing_tools(case: Case) -> list[str]:
    """
    External binaries a case declares and this machine does not have.

    Checked by the runner, not inside the checker, so the outcome is a SKIP
    with a notice rather than a failure that reads like broken code. The
    distinction matters because the two want opposite responses: a failure
    says fix the branch, a missing binary says install something.
    """
    missing: list[str] = []
    for tool in case.requires():
        if tool == "tesseract":
            # Ask the code that actually needs it, and quote its own reason.
            # "tesseract is not installed" was wrong on the first machine that
            # hit this: the binary was on PATH and `pytesseract` was not, and
            # a message that names the wrong missing thing sends people to
            # install something they already have.
            from app.extraction.words import OcrUnavailableError, _require_tesseract

            try:
                _require_tesseract()
            except OcrUnavailableError as exc:
                missing.append(str(exc))
    return missing


def execute(cases: list[Case], *, det_only: bool, judge_available: bool) -> list[CaseResult]:
    out: list[CaseResult] = []
    for case in cases:
        if not case.is_scored:
            out.append(CaseResult(case=case, results=(), skipped_reason=PENDING_REASON))
            continue
        if case.status is Status.DELEGATED:
            # A delegated case is scored against the artifact its job uploads.
            # Outside CI there is no artifact, and "the job is declared
            # correctly" is not the same claim as "the job passed" — the six
            # red pipelines that still reported a valid delegation are the
            # proof of that. Unscored, with a notice, never a pass.
            why = checks_delegation.missing_result_reason(case)
            if why:
                out.append(
                    CaseResult(
                        case=case,
                        results=(),
                        skipped_reason=DELEGATED_NO_RESULT_REASON.format(why=why),
                    )
                )
                continue
        absent = _missing_tools(case)
        if absent:
            out.append(
                CaseResult(
                    case=case,
                    results=(),
                    skipped_reason=MISSING_TOOL_REASON.format(why="; ".join(absent)),
                )
            )
            continue
        if case.tier is Tier.JUDGE:
            if det_only:
                out.append(CaseResult(case=case, results=(), skipped_reason=DET_ONLY_REASON))
                continue
            if not judge_available:
                out.append(CaseResult(case=case, results=(), skipped_reason=JUDGE_SKIPPED_REASON))
                continue
        out.append(_run_case(case))
    return out


def _commit() -> str:
    """
    The commit this run scored.

    Read from the environment first. `CI_COMMIT_SHA` is what GitLab sets, and
    `W2_GATE_COMMIT` is the manual override for a containerised run: this
    repository is developed in git worktrees, where `.git` is a *file*
    pointing at a path outside the container, so `git rev-parse` inside one
    cannot resolve it and silently produced a baseline naming "unknown" —
    which defeats the point of recording a commit at all.
    """
    for name in ("W2_GATE_COMMIT", "CI_COMMIT_SHA"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    try:
        return subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):  # pragma: no cover - environment
        return "unknown"


def build_baseline(
    results: list[CaseResult],
    scores: dict[str, CategoryScore],
    aggregate: CategoryScore,
) -> dict[str, Any]:
    config = judge_module.load_judge_config()
    return {
        "schema_version": "w2-gate/baseline/1.0.0",
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "commit": _commit(),
        "judge": {
            "model": config["model"],
            "model_version": config["model_version"],
            "prompt_version": config["prompt_version"],
            "calibration": "UNCALIBRATED",
        },
        "counts": {
            "specs": aggregate.specs,
            "active": aggregate.active,
            "pending": aggregate.pending,
        },
        "cases": {
            r.case.id: {
                "category": r.case.category,
                "status": r.case.status.value,
                "tier": r.case.tier.value,
                "critical": r.case.critical,
                "verdict": (
                    "pending"
                    if not r.case.is_scored
                    else ("skipped" if not r.scored else ("pass" if r.passed else "fail"))
                ),
            }
            for r in results
        },
        "categories": {key: value.as_dict() for key, value in sorted(scores.items())},
        "aggregate": aggregate.as_dict(),
    }


def _print_table(scores: dict[str, CategoryScore], config: dict[str, Any]) -> None:
    declared = config["categories"]
    print(
        f"{'cat':<4}{'specs':>6}{'nMin':>6}{'act':>5}{'pend':>6}{'dlg':>5}"
        f"{'det':>10}{'judge':>10}{'rate':>8}  name"
    )
    print("-" * 78)
    for key in sorted(set(declared) | set(scores)):
        category = scores.get(key, CategoryScore(key=key))
        spec = declared.get(key, {})
        det = f"{category.det.passed}/{category.det.scored}" if category.det.scored else "-"
        jud = f"{category.judge.passed}/{category.judge.scored}" if category.judge.scored else "-"
        if category.judge.skipped:
            jud += f"+{category.judge.skipped}sk"
        rate = "-" if category.rate is None else f"{category.rate:.3f}"
        print(
            f"{key:<4}{category.specs:>6}{spec.get('n_min', '?'):>6}{category.active:>5}"
            f"{category.pending:>6}{category.delegated:>5}{det:>10}{jud:>10}{rate:>8}"
            f"  {spec.get('name', '?')}"
        )


def _print_violations(violations: list[Violation]) -> None:
    for violation in violations:
        print()
        print(f"BLOCKED by rule {violation.rule}")
        print(f"  {violation.summary}")
        for line in violation.detail:
            print(f"    - {line}")


def configure_logging() -> None:
    """
    Render the run's structlog lines through the same stdout exit the service
    uses for a patient_session_key (D56, D61).

    The gate never called `app.middleware.logging.configure_logging`, so the
    lines the supervisor cases emit went out through structlog's default
    chain with the raw `<sub>:<patient uuid>:<session>` key in them, and
    `docs/w2/EVAL_GATE_EVIDENCE.md`, which records that output, held raw keys.
    This is structlog's default console chain with
    `pseudonymize_session_keys` added after `merge_contextvars`, exactly where
    the service has it, so every key and cited record id is HMAC'd before it
    is rendered. Without `AGENT_TELEMETRY_HMAC_SECRET` that is the dev
    default, and the values say so (`psk_dev_`, `rid_dev_`).

    Not the service's own `configure_logging`: that also starts the Langfuse
    exporter, and a gate run on a machine with `LANGFUSE_*` set must not
    export anything. No logger caching, so `capture_logs`, which the I and B
    checks read, still swaps the processors in and out.
    """
    import structlog

    from app.middleware.telemetry_key import pseudonymize_session_keys

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            pseudonymize_session_keys,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        cache_logger_on_first_use=False,
    )


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(prog="python -m evals.w2.gate", description=__doc__)
    parser.add_argument(
        "--det-only",
        action="store_true",
        help="deterministic tier only; no model calls, no credential",
    )
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help=(
            "exit 0 instead of 3 when the only problem is that a critical case was not "
            "scored. For the local pre-push hook, which does not run the judge tier and "
            "does not claim to be evidence. CI must not pass this."
        ),
    )
    parser.add_argument(
        "--seed-baseline", action="store_true", help="write this run's results to baseline.json"
    )
    parser.add_argument(
        "--json", dest="json_path", default=None, help="also write the full run to this path"
    )
    parser.add_argument(
        "--no-baseline",
        action="store_true",
        help="score without comparing to the committed baseline",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="print every assertion, not only failures"
    )
    args = parser.parse_args(argv)

    try:
        load_all_checks()
        cases = load_cases()
        categories_config = load_categories()
        # Unconditional, and before anything is scored: a judge model with no
        # price entry is a config error on --det-only too, where the judge is
        # never constructed and would otherwise never be checked.
        judge_module.check_pricing_config()
    except GateError as exc:
        print(f"gate error: {exc}", file=sys.stderr)
        return 2

    judge_available = judge_module.Judge.credential_present()
    judge_needed = any(case.is_active and case.tier is Tier.JUDGE for case in cases)

    print(RULE)
    print("Week 2 eval gate")
    print(RULE)

    if args.det_only:
        print("TIER: deterministic only (--det-only). Judge cases are not scored.")
    elif judge_needed and not judge_available:
        print()
        print("*" * 78)
        print("* ANTHROPIC_API_KEY is not set. The JUDGE TIER IS SKIPPED, so every")
        print("* category-M case goes unscored — and all of them are critical.")
        print("*")
        print("* THIS RUN WILL EXIT 3 AND FAIL THE JOB. That is deliberate: a green")
        print("* pipeline that scored no critical case is the failure this gate exists")
        print("* to prevent, and a notice in a log nobody reads is not a substitute.")
        print("*")
        print("* Fix: GitLab -> Settings -> CI/CD -> Variables -> ANTHROPIC_API_KEY,")
        print("* MASKED and NOT PROTECTED, so merge-request pipelines can see it.")
        print("*" * 78)
    elif judge_needed:
        config = judge_module.load_judge_config()
        print(
            f"TIER: deterministic + judge ({config['model_version']}, "
            f"prompt {config['prompt_version']}, best-of-{config['best_of']}) "
            "[judge results are UNCALIBRATED]"
        )

    try:
        results = execute(cases, det_only=args.det_only, judge_available=judge_available)
    except GateError as exc:
        print(f"gate error: {exc}", file=sys.stderr)
        return 2

    baseline: dict[str, Any] | None = None
    if not args.no_baseline and BASELINE.is_file():
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))

    violations, scores, aggregate = evaluate(results, categories_config, baseline)

    print()
    _print_table(scores, categories_config)
    print("-" * 78)
    all_det = f"{aggregate.det.passed}/{aggregate.det.scored}"
    all_judge = f"{aggregate.judge.passed}/{aggregate.judge.scored}"
    if aggregate.judge.skipped:
        all_judge += f"+{aggregate.judge.skipped}sk"
    all_rate = "-" if aggregate.rate is None else f"{aggregate.rate:.3f}"
    print(
        f"{'ALL':<4}{aggregate.specs:>6}{'':>6}{aggregate.active:>5}{aggregate.pending:>6}"
        f"{aggregate.delegated:>5}{all_det:>10}{all_judge:>10}{all_rate:>8}"
    )

    if args.verbose:
        print()
        print("Assertions")
        for result in results:
            if not result.scored:
                continue
            for entry in result.results:
                mark = "PASS" if entry.outcome.passed else "FAIL"
                print(
                    f"  [{mark}] {result.case.id} {entry.assertion.rubric}"
                    f"/{entry.assertion.check}: {entry.outcome.detail}"
                )

    pending = [r for r in results if not r.case.is_scored]
    delegated = [r for r in results if r.case.is_delegated]
    skipped = [r for r in results if r.case.is_scored and not r.scored]
    print()
    print(f"Pending (specified, not executable yet): {len(pending)}")
    if pending:
        by_category: dict[str, list[str]] = {}
        for result in pending:
            by_category.setdefault(result.case.category, []).append(result.case.id)
        for key, ids in sorted(by_category.items()):
            print(f"  {key}: {' '.join(ids)}")
    if delegated:
        print()
        print(
            f"Delegated to another job in this pipeline: {len(delegated)} — the "
            "BEHAVIOUR is not scored here"
        )
        for result in delegated:
            job = result.case.delegation().get("job", "?")
            if not result.scored:
                mark = "  ? "
            elif result.passed:
                mark = " ok "
            else:
                mark = "FAIL"
            print(f"  [{mark}] {result.case.id} [{result.case.category}] -> {job}")
        print("  Scored for them: the job exists, it is not allow_failure, the named tests are")
        print("  still in the suite, AND that job's own result artifact says it ran and passed")
        print("  those tests for the commit under test. [  ? ] means there is no artifact here,")
        print("  so the case is UNSCORED — outside CI the gate cannot know, and does not guess.")

    if skipped:
        print(f"Active but NOT SCORED this run: {len(skipped)} — not passes")
        for result in skipped:
            print(f"  {result.case.id} [{result.case.category}] {result.skipped_reason}")
        critical_skipped = [r for r in skipped if r.case.critical]
        if critical_skipped:
            print()
            print("!" * 78)
            print(f"! {len(critical_skipped)} CRITICAL case(s) were not scored in this run.")
            print("! They are not passes. This run is not evidence that they hold, and the")
            print("! critical-case override could not be evaluated for them.")
            for result in critical_skipped:
                print(f"!   {result.case.id} [{result.case.category}] {result.case.guards_against}")
            print("!" * 78)

    if baseline is None:
        print()
        print(
            "No baseline to compare against: rules (c) and the regression half of (e) "
            "are not evaluated."
        )
    else:
        print()
        print(
            f"Baseline: {baseline.get('commit', '?')[:8]} "
            f"generated {baseline.get('generated_at', '?')}"
        )

    if judge_needed and not args.det_only and judge_available:
        engine = judge_module.judge()
        print(
            f"Judge: {engine.calls_made} model call(s), {engine.cache_hits} cache hit(s), "
            f"{engine.input_tokens} in / {engine.output_tokens} out tokens, "
            f"~${engine.estimated_cost_usd():.4f}"
        )

    _print_violations(violations)

    blocked = bool(violations)
    unscored = [r for r in results if r.case.is_scored and not r.scored]
    unscored_critical = [r for r in unscored if r.case.critical]
    # Any active case the run did not execute makes the run incomplete, not
    # only a critical one. A skipped case is not a passing case, and the
    # difference between "4 scanned cases did not run" and "everything is
    # green" has to survive into the exit code.
    incomplete = bool(unscored)

    print()
    print(RULE)
    judge_state = (
        "SKIPPED"
        if (judge_needed and not args.det_only and not judge_available)
        else ("NOT RUN" if args.det_only else "RUN [UNCALIBRATED]")
    )
    if blocked:
        outcome = "BLOCKED"
    elif not incomplete:
        outcome = "PASS"
    elif args.allow_incomplete:
        outcome = "PASS (INCOMPLETE, --allow-incomplete)"
    else:
        outcome = "INCOMPLETE"
    skipped_note = f" | SKIPPED {len(unscored)}" if unscored else ""
    print(
        f"VERDICT: {outcome} | "
        f"specs {aggregate.specs} | active {aggregate.active} | pending {aggregate.pending} | "
        f"det {aggregate.det.passed}/{aggregate.det.scored} | "
        f"judge {aggregate.judge.passed}/{aggregate.judge.scored} ({judge_state})"
        f"{skipped_note}"
    )
    if incomplete:
        reasons: dict[str, list[str]] = {}
        for result in unscored:
            reasons.setdefault(result.skipped_reason or "?", []).append(result.case.id)
        detail = "; ".join(f"{len(ids)} {why} [{', '.join(ids)}]" for why, ids in reasons.items())
        critical_note = f" {len(unscored_critical)} of them CRITICAL." if unscored_critical else ""
        print(
            f"NOT EVIDENCE: {len(unscored)} active case(s) were never scored.{critical_note} "
            f"{detail}"
        )
    print(RULE)

    payload = build_baseline(results, scores, aggregate)
    payload["violations"] = [
        {"rule": v.rule, "summary": v.summary, "detail": v.detail} for v in violations
    ]
    payload["blocked"] = blocked
    payload["incomplete"] = incomplete
    payload["unscored"] = [r.case.id for r in unscored]
    payload["unscored_critical"] = [r.case.id for r in unscored_critical]
    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")

    if args.seed_baseline:
        if blocked:
            print("refusing to seed a baseline from a blocked run", file=sys.stderr)
            return 2
        if incomplete:
            # Seeding here would record every unscored case as
            # "skipped", and rule (c) fires only on a case the baseline says
            # passed — so each of them would be exempt from the critical-case
            # override for as long as that baseline stood.
            print(
                "refusing to seed a baseline from a run that did not score "
                f"{len(unscored)} active case(s): "
                f"{', '.join(r.case.id for r in unscored)}",
                file=sys.stderr,
            )
            return 2
        if payload.get("commit") in (None, "", "unknown"):
            print(
                "refusing to seed a baseline that cannot name the commit it scored. "
                "Set W2_GATE_COMMIT (or run where `git rev-parse` works): a baseline "
                "naming 'unknown' cannot be compared against anything later.",
                file=sys.stderr,
            )
            return 2
        if os.environ.get("W2_GATE_SESSION") != "1":
            print(
                "refusing to write baseline.json: it is a gate file and this is not a "
                "W2_GATE_SESSION=1 session",
                file=sys.stderr,
            )
            return 2
        seed = {
            key: value
            for key, value in payload.items()
            if key not in ("violations", "blocked", "incomplete", "unscored", "unscored_critical")
        }
        BASELINE.write_text(json.dumps(seed, indent=2) + "\n", encoding="utf-8")
        print(f"baseline written to {BASELINE}")

    if blocked:
        return 1
    if incomplete and not args.allow_incomplete:
        return 3
    return 0
