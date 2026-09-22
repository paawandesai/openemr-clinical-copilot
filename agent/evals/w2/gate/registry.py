"""
check id -> the function that decides it.

A case names its checker by id. Nothing is looked up by convention or by
module scanning: an id with no registered function is a gate error, which is
what stops a case from silently scoring nothing.
"""

from __future__ import annotations

from collections.abc import Callable

from evals.w2.gate.models import Case, CheckOutcome, GateError

CheckFn = Callable[[Case], CheckOutcome]

REGISTRY: dict[str, CheckFn] = {}


def check(check_id: str) -> Callable[[CheckFn], CheckFn]:
    """Register a deterministic or judge checker under `check_id`."""

    def register(fn: CheckFn) -> CheckFn:
        if check_id in REGISTRY:
            raise GateError(f"check id {check_id} is registered twice")
        REGISTRY[check_id] = fn
        return fn

    return register


def resolve(check_id: str) -> CheckFn:
    try:
        return REGISTRY[check_id]
    except KeyError as exc:
        raise GateError(f"check id {check_id} has no registered implementation") from exc


def load_all_checks() -> None:
    """
    Import every check module for its side effect of registering.

    Explicit imports rather than a package walk: a checker that is not on this
    list does not exist, and that is easier to see in a diff than a file that
    was never picked up.
    """
    from evals.w2.gate import (  # noqa: F401  (imported for registration)
        checks_a,
        checks_c,
        checks_d,
        checks_delegation,
        checks_e,
        checks_f,
        checks_g,
        checks_j,
        checks_k,
        checks_m,
    )
