"""Types the gate is written in. No I/O here."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Status(StrEnum):
    ACTIVE = "active"
    PENDING_SUT = "pending_sut"
    #: Enforced by a named job in the same pipeline that this gate cannot run
    #: itself. The gate scores the integrity of the delegation — the job
    #: exists, it is not allowed to fail, the named tests are still there —
    #: and the job scores the behaviour.
    DELEGATED = "delegated"


class Tier(StrEnum):
    DET = "det"
    JUDGE = "judge"


@dataclass(frozen=True)
class Assertion:
    rubric: str
    tier: Tier
    check: str


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    critical: bool
    status: Status
    inputs: dict[str, object]
    expected: str
    assertions: tuple[Assertion, ...]
    guards_against: str
    source_path: str
    #: Present only for status=delegated: the job, suite and test names the
    #: gate checks against the repository.
    delegated_to: dict[str, object] | None = None

    @property
    def is_active(self) -> bool:
        return self.status is Status.ACTIVE

    @property
    def is_delegated(self) -> bool:
        return self.status is Status.DELEGATED

    @property
    def is_scored(self) -> bool:
        """Whether this run executes anything for the case at all."""
        return self.status in (Status.ACTIVE, Status.DELEGATED)

    def delegation(self) -> dict[str, object]:
        return dict(self.delegated_to or {})

    @property
    def tier(self) -> Tier:
        """
        A case is a judge-tier case if any of its assertions needs the judge.

        Deliberately not per-assertion: a case's verdict is the AND of its
        assertions, so a case with one judge assertion inherits the judge's
        variance and belongs in the tier that is scored with a majority and a
        Wilson bound rather than at 100%.
        """
        return Tier.JUDGE if any(a.tier is Tier.JUDGE for a in self.assertions) else Tier.DET

    def scenario_ids(self) -> list[str]:
        value = self.inputs.get("scenario_ids", [])
        return list(value) if isinstance(value, list) else []

    def requires(self) -> list[str]:
        value = self.inputs.get("requires", [])
        return list(value) if isinstance(value, list) else []

    def fixtures(self) -> list[str]:
        value = self.inputs.get("fixtures", [])
        return list(value) if isinstance(value, list) else []


@dataclass(frozen=True)
class CheckOutcome:
    """One assertion's result. `detail` is shown on failure and on -v."""

    passed: bool
    detail: str
    #: Anything the judge tier needs to reproduce a verdict — model version,
    #: majority, cache hit. Empty for deterministic checks.
    evidence: dict[str, object] = field(default_factory=dict)

    @classmethod
    def ok(cls, detail: str, **evidence: object) -> CheckOutcome:
        return cls(passed=True, detail=detail, evidence=dict(evidence))

    @classmethod
    def failed(cls, detail: str, **evidence: object) -> CheckOutcome:
        return cls(passed=False, detail=detail, evidence=dict(evidence))


@dataclass(frozen=True)
class AssertionResult:
    assertion: Assertion
    outcome: CheckOutcome


@dataclass(frozen=True)
class CaseResult:
    case: Case
    results: tuple[AssertionResult, ...]
    #: Set when the case could not be scored at all — a pending case, or a
    #: judge case with the judge tier skipped. Never counted as a pass.
    skipped_reason: str | None = None

    @property
    def passed(self) -> bool:
        if self.skipped_reason is not None:
            return False
        return bool(self.results) and all(r.outcome.passed for r in self.results)

    @property
    def scored(self) -> bool:
        return self.skipped_reason is None

    def failures(self) -> list[AssertionResult]:
        return [r for r in self.results if not r.outcome.passed]


class GateError(RuntimeError):
    """A gate that cannot run. Exit 2 — never a verdict."""
