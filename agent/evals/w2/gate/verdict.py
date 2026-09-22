"""
The gate's decision. Five rules, applied in this order:

  (a) per-category minimum-n, over ACTIVE + PENDING specs
  (b) deterministic tier at 100% of active cases
  (c) critical-case override: a previously-passing critical case now failing
  (d) judge tier: category threshold plus a 95% Wilson lower-bound floor
  (e) the assignment's literal rule: >5% regression against the committed
      baseline, or below threshold, per category and on aggregate

The order is not cosmetic. (a) runs first because every rate below it is a
rate over a set somebody chose, and a branch that deletes the cases it fails
must be stopped before its scores are read. (c) runs before the aggregates
because on a 4-to-10-case category one case is 10 to 25 points, and a 5%
band is below the resolution of a single case — the severity rule is the
detector, and the percentage is the backstop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from evals.w2.gate.models import CaseResult, Tier
from evals.w2.gate.stats import all_pass_lower_bound, wilson_lower_bound


@dataclass
class Violation:
    rule: str
    summary: str
    detail: list[str] = field(default_factory=list)


@dataclass
class TierScore:
    scored: int = 0
    passed: int = 0
    skipped: int = 0

    @property
    def rate(self) -> float | None:
        return None if self.scored == 0 else self.passed / self.scored

    def as_dict(self) -> dict[str, Any]:
        return {
            "scored": self.scored,
            "passed": self.passed,
            "skipped": self.skipped,
            "rate": self.rate,
        }


@dataclass
class CategoryScore:
    key: str
    specs: int = 0
    active: int = 0
    pending: int = 0
    delegated: int = 0
    det: TierScore = field(default_factory=TierScore)
    judge: TierScore = field(default_factory=TierScore)

    @property
    def scored(self) -> int:
        return self.det.scored + self.judge.scored

    @property
    def passed(self) -> int:
        return self.det.passed + self.judge.passed

    @property
    def rate(self) -> float | None:
        return None if self.scored == 0 else self.passed / self.scored

    def as_dict(self) -> dict[str, Any]:
        return {
            "specs": self.specs,
            "active": self.active,
            "pending": self.pending,
            "delegated": self.delegated,
            "det": self.det.as_dict(),
            "judge": self.judge.as_dict(),
            "rate": self.rate,
        }


def score(results: list[CaseResult]) -> tuple[dict[str, CategoryScore], CategoryScore]:
    """Per-category and aggregate tallies. Pending and skipped never count as passes."""
    categories: dict[str, CategoryScore] = {}
    total = CategoryScore(key="ALL")
    for result in results:
        case = result.case
        score_for = categories.setdefault(case.category, CategoryScore(key=case.category))
        for bucket in (score_for, total):
            bucket.specs += 1
            if case.is_delegated:
                bucket.delegated += 1
            elif case.is_active:
                bucket.active += 1
            else:
                bucket.pending += 1
                continue
            tier = bucket.det if case.tier is Tier.DET else bucket.judge
            if result.scored:
                tier.scored += 1
                tier.passed += int(result.passed)
            else:
                tier.skipped += 1
    return categories, total


def _threshold(config: dict[str, Any], category: CategoryScore) -> float:
    det_t = float(config["det_threshold"])
    judge_t = float(config["judge_threshold"])
    if category.scored == 0:
        return min(det_t, judge_t)
    return (category.det.scored * det_t + category.judge.scored * judge_t) / category.scored


def evaluate(
    results: list[CaseResult],
    categories_config: dict[str, Any],
    baseline: dict[str, Any] | None,
) -> tuple[list[Violation], dict[str, CategoryScore], CategoryScore]:
    """The five rules, in order. Returns every violation found, not the first."""
    scores, aggregate = score(results)
    config = categories_config["categories"]
    tolerance = float(categories_config.get("regression_tolerance", 0.05))
    violations: list[Violation] = []

    # (a) per-category minimum-n, over ACTIVE + PENDING.
    short: list[str] = []
    for key, declared in sorted(config.items()):
        present = scores[key].specs if key in scores else 0
        n_min = int(declared["n_min"])
        if present < n_min:
            short.append(f"{key}: {present} specs, n_min {n_min} ({declared['name']})")
    unknown = sorted(set(scores) - set(config))
    for key in unknown:
        short.append(f"{key}: {scores[key].specs} specs in a category rubrics/ does not declare")
    if short:
        violations.append(
            Violation(
                rule="(a) per-category minimum-n",
                summary="a category has fewer specs than it is required to carry",
                detail=short,
            )
        )

    # (b) deterministic tier at 100%.
    det_failures = [
        r
        for r in results
        if r.case.is_active and r.case.tier is Tier.DET and r.scored and not r.passed
    ]
    if det_failures:
        violations.append(
            Violation(
                rule="(b) deterministic tier at 100%",
                summary=f"{len(det_failures)} deterministic case(s) failed",
                detail=[
                    f"{r.case.id} [{r.case.category}] {r.case.guards_against} :: "
                    + "; ".join(
                        f"{a.assertion.rubric}/{a.assertion.check}: {a.outcome.detail}"
                        for a in r.failures()
                    )
                    for r in det_failures
                ],
            )
        )

    # (c) critical-case override.
    if baseline is not None:
        baseline_cases: dict[str, Any] = baseline.get("cases", {})
        regressed: list[str] = []
        for result in results:
            case = result.case
            if not case.critical:
                continue
            was = baseline_cases.get(case.id, {}).get("verdict")
            if was != "pass":
                continue
            if not result.scored:
                # A case the run did not execute has not regressed — it has not
                # been measured. Blocking here would mean `--det-only` could
                # never pass, since it deliberately leaves the judge tier
                # unscored. Unscored critical cases are listed on their own in
                # the run output and are never counted as passes; the verdict
                # line says which tiers ran. That is the "visible notice, never
                # a silent pass" the work order asks for.
                continue
            if result.passed:
                continue
            why = "; ".join(f"{a.assertion.rubric}: {a.outcome.detail}" for a in result.failures())
            regressed.append(f"{case.id} [{case.category}] {case.guards_against} :: {why}")
        if regressed:
            violations.append(
                Violation(
                    rule="(c) critical-case override",
                    summary=(
                        f"{len(regressed)} critical case(s) that passed on the baseline no "
                        "longer pass"
                    ),
                    detail=regressed,
                )
            )

    # (d) judge tier: threshold plus the Wilson floor.
    judge_problems: list[str] = []
    for key, category in sorted(scores.items()):
        declared = config.get(key)
        if declared is None or category.judge.scored == 0:
            continue
        rate = category.judge.rate or 0.0
        threshold = float(declared["judge_threshold"])
        if rate < threshold:
            judge_problems.append(
                f"{key}: judge pass rate {rate:.3f} < threshold {threshold:.3f} "
                f"({category.judge.passed}/{category.judge.scored})"
            )
        slack = float(declared["judge_wilson_slack"])
        floor = all_pass_lower_bound(category.judge.scored) - slack
        bound = wilson_lower_bound(category.judge.passed, category.judge.scored)
        if bound < floor:
            judge_problems.append(
                f"{key}: 95% Wilson lower bound {bound:.3f} < floor {floor:.3f} "
                f"(all-pass bound at n={category.judge.scored} is "
                f"{all_pass_lower_bound(category.judge.scored):.3f}, slack {slack})"
            )
    if judge_problems:
        violations.append(
            Violation(
                rule="(d) judge tier threshold and Wilson floor",
                summary="a judge category fell below its threshold or its 95% lower bound",
                detail=judge_problems,
            )
        )

    # (e) the assignment's literal rule.
    literal: list[str] = []
    baseline_categories: dict[str, Any] = (baseline or {}).get("categories", {})
    baseline_aggregate: dict[str, Any] = (baseline or {}).get("aggregate", {})
    for key, category in sorted(scores.items()):
        declared = config.get(key)
        if declared is None or category.scored == 0:
            continue
        rate = category.rate or 0.0
        threshold = _threshold(declared, category)
        if rate < threshold - 1e-9:
            literal.append(
                f"{key}: pass rate {rate:.3f} below threshold {threshold:.3f} "
                f"({category.passed}/{category.scored})"
            )
        was = baseline_categories.get(key, {}).get("rate")
        if was is not None and rate < float(was) - tolerance - 1e-9:
            literal.append(
                f"{key}: pass rate {rate:.3f} is a {float(was) - rate:.3f} regression "
                f"against the baseline's {float(was):.3f} (tolerance {tolerance})"
            )
    if aggregate.scored:
        rate = aggregate.rate or 0.0
        threshold = _threshold(categories_config["aggregate"], aggregate)
        if rate < threshold - 1e-9:
            literal.append(f"ALL: aggregate pass rate {rate:.3f} below threshold {threshold:.3f}")
        was = baseline_aggregate.get("rate")
        if was is not None and rate < float(was) - tolerance - 1e-9:
            literal.append(
                f"ALL: aggregate pass rate {rate:.3f} is a {float(was) - rate:.3f} "
                f"regression against the baseline's {float(was):.3f}"
            )
    if literal:
        violations.append(
            Violation(
                rule="(e) >5% regression or below threshold",
                summary="a category or the aggregate regressed or fell below threshold",
                detail=literal,
            )
        )

    return violations, scores, aggregate
