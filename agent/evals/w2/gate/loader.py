"""
Loading and validating the gate's inputs.

Every load fails loudly. A gate that starts with half its cases because one
file did not parse reports a pass rate over a set nobody chose.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.w2.gate.models import Assertion, Case, GateError, Status, Tier
from evals.w2.gate.paths import (
    CASE_SCHEMA,
    CASES_DIR,
    CATEGORIES_FILE,
    JUDGE_CONFIG,
    RUBRICS_FILE,
)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GateError(f"{path} is missing") from exc
    except json.JSONDecodeError as exc:
        raise GateError(f"{path} is not valid JSON: {exc}") from exc


def load_rubrics() -> dict[str, Any]:
    return _read_json(RUBRICS_FILE)["rubrics"]


def load_categories() -> dict[str, Any]:
    return _read_json(CATEGORIES_FILE)


def load_judge_config() -> dict[str, Any]:
    return _read_json(JUDGE_CONFIG)


def load_case_schema() -> dict[str, Any]:
    return _read_json(CASE_SCHEMA)


def _validate_against_schema(instances: list[tuple[Path, Any]], schema: dict[str, Any]) -> None:
    """
    Validate every case file against case_schema.json.

    jsonschema is a dev dependency and the CI job installs it. It is imported
    here rather than at module scope so that a missing dev dependency is a
    named gate error instead of an ImportError from an unrelated import.
    """
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover - environment, not logic
        raise GateError(
            "jsonschema is not installed; the gate validates every case file "
            "against case_schema.json and will not run without it "
            "(pip install -r requirements-dev.txt)"
        ) from exc

    validator = Draft202012Validator(schema)
    problems: list[str] = []
    for path, instance in instances:
        for error in sorted(validator.iter_errors(instance), key=lambda e: list(e.path)):
            location = "/".join(str(p) for p in error.path) or "(root)"
            problems.append(f"{path.name}: {location}: {error.message}")
    if problems:
        raise GateError("case files do not match case_schema.json:\n  " + "\n  ".join(problems))


def load_cases() -> list[Case]:
    """Every case in cases/, schema-validated, sorted by id."""
    if not CASES_DIR.is_dir():
        raise GateError(f"{CASES_DIR} is missing")

    paths = sorted(p for p in CASES_DIR.glob("*.json"))
    if not paths:
        raise GateError(f"no case files in {CASES_DIR}")

    instances = [(path, _read_json(path)) for path in paths]
    _validate_against_schema(instances, load_case_schema())

    rubrics = load_rubrics()
    cases: list[Case] = []
    seen: dict[str, Path] = {}
    for path, raw in instances:
        case_id = raw["id"]
        if case_id in seen:
            raise GateError(f"case id {case_id} appears in {seen[case_id].name} and {path.name}")
        seen[case_id] = path

        if not case_id.startswith(f"W2-{raw['category']}"):
            raise GateError(f"{path.name}: id {case_id} does not sit in category {raw['category']}")

        assertions: list[Assertion] = []
        for entry in raw["assertions"]:
            rubric = entry["rubric"]
            tier = Tier(entry["tier"])
            declared = rubrics.get(rubric, {}).get("tier")
            if declared is None:
                raise GateError(f"{path.name}: rubric {rubric} is not in rubrics.json")
            if declared != tier.value:
                raise GateError(
                    f"{path.name}: rubric {rubric} is declared {declared} in rubrics.json "
                    f"but the case asserts it at tier {tier.value}"
                )
            assertions.append(Assertion(rubric=rubric, tier=tier, check=entry["check"]))

        status = Status(raw["status"])
        pending_checks = [a.check for a in assertions if a.check.startswith("pending.")]
        if status is not Status.PENDING_SUT and pending_checks:
            raise GateError(
                f"{path.name}: a {status.value} case cannot carry pending checks {pending_checks}"
            )
        if status is Status.PENDING_SUT and not pending_checks:
            raise GateError(
                f"{path.name}: a pending_sut case names no pending check, so nothing records "
                "that it is waiting on the graph"
            )

        delegated_to = raw.get("delegated_to")
        if status is Status.DELEGATED and delegated_to is None:
            raise GateError(
                f"{path.name}: a delegated case must name the job that enforces it; "
                "without delegated_to the status is a claim with nothing behind it"
            )
        if status is not Status.DELEGATED and delegated_to is not None:
            raise GateError(f"{path.name}: delegated_to is only meaningful on a delegated case")

        cases.append(
            Case(
                id=case_id,
                category=raw["category"],
                critical=bool(raw["critical"]),
                status=status,
                inputs=raw["inputs"],
                expected=raw["expected"],
                assertions=tuple(assertions),
                guards_against=raw["guards_against"],
                source_path=str(path.relative_to(CASES_DIR.parent.parent.parent.parent)),
                delegated_to=delegated_to,
            )
        )

    return sorted(cases, key=lambda c: (c.category, int(c.id.split(c.category)[-1])))
