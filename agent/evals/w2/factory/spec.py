"""
Scenario specs: the YAML input to ``python -m evals.w2.factory generate``.

One file per scenario. Everything the factory does is a pure function of the
parsed spec plus the cached chart for ``patient_ref`` — there is no other
input, which is what makes a run reproducible.

    scenario_id: lab-clean-001
    seed: 20260921
    patient_ref: "18"
    doc_type: lab_pdf          # lab_pdf | intake_form
    quality: clean             # clean | scanned_light | scanned_heavy
    perturbations:
      - kind: abnormal_value
        test: glucose
        direction: high
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


class SpecError(ValueError):
    """Raised for a spec the factory cannot render."""


class DocType(str, Enum):
    LAB_PDF = "lab_pdf"
    INTAKE_FORM = "intake_form"


class Quality(str, Enum):
    CLEAN = "clean"
    SCANNED_LIGHT = "scanned_light"
    SCANNED_HEAVY = "scanned_heavy"

    @property
    def is_scanned(self) -> bool:
        return self is not Quality.CLEAN


class PerturbationKind(str, Enum):
    MISSING_FIELD = "missing_field"
    ILLEGIBLE_FIELD = "illegible_field"
    ABNORMAL_VALUE = "abnormal_value"
    MED_DISCREPANCY = "med_discrepancy"
    ALLERGY_DISCREPANCY = "allergy_discrepancy"
    WRONG_PATIENT_NAME = "wrong_patient_name"
    MULTI_PAGE = "multi_page"


#: Which document types each perturbation is meaningful for. ``med_discrepancy``
#: and ``allergy_discrepancy`` compare an intake form against the chart, so they
#: have no meaning on a lab report; ``multi_page`` only drives the lab renderer.
_APPLICABLE: dict[PerturbationKind, frozenset[DocType]] = {
    PerturbationKind.MISSING_FIELD: frozenset(DocType),
    PerturbationKind.ILLEGIBLE_FIELD: frozenset(DocType),
    PerturbationKind.WRONG_PATIENT_NAME: frozenset(DocType),
    PerturbationKind.ABNORMAL_VALUE: frozenset({DocType.LAB_PDF}),
    PerturbationKind.MULTI_PAGE: frozenset({DocType.LAB_PDF}),
    PerturbationKind.MED_DISCREPANCY: frozenset({DocType.INTAKE_FORM}),
    PerturbationKind.ALLERGY_DISCREPANCY: frozenset({DocType.INTAKE_FORM}),
}

_REQUIRED_PARAMS: dict[PerturbationKind, tuple[str, ...]] = {
    PerturbationKind.MISSING_FIELD: ("field",),
    PerturbationKind.ILLEGIBLE_FIELD: ("field",),
    PerturbationKind.ABNORMAL_VALUE: ("test", "direction"),
    PerturbationKind.MED_DISCREPANCY: ("mode",),
    PerturbationKind.ALLERGY_DISCREPANCY: ("mode",),
    PerturbationKind.WRONG_PATIENT_NAME: (),
    PerturbationKind.MULTI_PAGE: (),
}

_ENUM_PARAMS: dict[tuple[PerturbationKind, str], frozenset[str]] = {
    (PerturbationKind.ABNORMAL_VALUE, "direction"): frozenset({"high", "low"}),
    (PerturbationKind.MED_DISCREPANCY, "mode"): frozenset({"add", "omit", "dose_change"}),
    (PerturbationKind.ALLERGY_DISCREPANCY, "mode"): frozenset({"add", "omit"}),
}

_SCENARIO_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class Perturbation:
    """One intended difference between the rendered document and the chart."""

    kind: PerturbationKind
    params: dict[str, Any] = field(default_factory=dict)

    def get(self, name: str, default: Any = None) -> Any:
        return self.params.get(name, default)

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "params": dict(self.params)}


@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    seed: int
    patient_ref: str
    doc_type: DocType
    quality: Quality
    perturbations: tuple[Perturbation, ...] = ()
    #: Free-text note carried through into the ground truth, for humans only.
    note: str | None = None
    #: Where the spec was loaded from, for error messages. Not part of the hash.
    source_path: Path | None = None

    def perturbations_of(self, kind: PerturbationKind) -> tuple[Perturbation, ...]:
        return tuple(p for p in self.perturbations if p.kind is kind)

    def has(self, kind: PerturbationKind) -> bool:
        return any(p.kind is kind for p in self.perturbations)

    def as_dict(self) -> dict[str, Any]:
        """The spec as recorded in ground truth — no filesystem paths."""
        out: dict[str, Any] = {
            "scenario_id": self.scenario_id,
            "seed": self.seed,
            "patient_ref": self.patient_ref,
            "doc_type": self.doc_type.value,
            "quality": self.quality.value,
            "perturbations": [p.as_dict() for p in self.perturbations],
        }
        if self.note is not None:
            out["note"] = self.note
        return out


def _require(mapping: dict[str, Any], key: str, where: str) -> Any:
    if key not in mapping:
        raise SpecError(f"{where}: missing required key {key!r}")
    return mapping[key]


def _parse_perturbation(raw: Any, index: int, where: str, doc_type: DocType) -> Perturbation:
    at = f"{where}: perturbations[{index}]"
    if not isinstance(raw, dict):
        raise SpecError(f"{at}: expected a mapping, got {type(raw).__name__}")

    raw_kind = _require(raw, "kind", at)
    try:
        kind = PerturbationKind(raw_kind)
    except ValueError:
        known = ", ".join(sorted(k.value for k in PerturbationKind))
        raise SpecError(f"{at}: unknown kind {raw_kind!r}; known kinds: {known}") from None

    if doc_type not in _APPLICABLE[kind]:
        allowed = ", ".join(sorted(d.value for d in _APPLICABLE[kind]))
        raise SpecError(f"{at}: kind {kind.value!r} does not apply to doc_type {doc_type.value!r} (only {allowed})")

    params = {k: v for k, v in raw.items() if k != "kind"}
    for required in _REQUIRED_PARAMS[kind]:
        if required not in params:
            raise SpecError(f"{at}: kind {kind.value!r} requires key {required!r}")
    for (enum_kind, name), allowed_values in _ENUM_PARAMS.items():
        if enum_kind is kind and name in params and params[name] not in allowed_values:
            choices = ", ".join(sorted(allowed_values))
            raise SpecError(f"{at}: {name}={params[name]!r} is not one of: {choices}")

    return Perturbation(kind=kind, params=params)


def parse_spec(raw: Any, source_path: Path | None = None) -> ScenarioSpec:
    where = str(source_path) if source_path is not None else "<spec>"
    if not isinstance(raw, dict):
        raise SpecError(f"{where}: expected a top-level mapping, got {type(raw).__name__}")

    scenario_id = _require(raw, "scenario_id", where)
    if not isinstance(scenario_id, str) or not _SCENARIO_ID_RE.match(scenario_id):
        raise SpecError(
            f"{where}: scenario_id must be lowercase [a-z0-9._-], 1-64 chars; got {scenario_id!r}"
        )

    seed = _require(raw, "seed", where)
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise SpecError(f"{where}: seed must be an integer, got {seed!r}")

    patient_ref = _require(raw, "patient_ref", where)
    if not isinstance(patient_ref, (str, int)) or isinstance(patient_ref, bool):
        raise SpecError(f"{where}: patient_ref must be a string or integer, got {patient_ref!r}")

    try:
        doc_type = DocType(_require(raw, "doc_type", where))
    except ValueError:
        choices = ", ".join(d.value for d in DocType)
        raise SpecError(f"{where}: doc_type must be one of: {choices}") from None

    try:
        quality = Quality(_require(raw, "quality", where))
    except ValueError:
        choices = ", ".join(q.value for q in Quality)
        raise SpecError(f"{where}: quality must be one of: {choices}") from None

    raw_perturbations = raw.get("perturbations") or []
    if not isinstance(raw_perturbations, list):
        raise SpecError(f"{where}: perturbations must be a list, got {type(raw_perturbations).__name__}")
    perturbations = tuple(
        _parse_perturbation(p, i, where, doc_type) for i, p in enumerate(raw_perturbations)
    )

    note = raw.get("note")
    if note is not None and not isinstance(note, str):
        raise SpecError(f"{where}: note must be a string")

    unknown = set(raw) - {
        "scenario_id", "seed", "patient_ref", "doc_type", "quality", "perturbations", "note",
    }
    if unknown:
        raise SpecError(f"{where}: unknown top-level key(s): {', '.join(sorted(unknown))}")

    return ScenarioSpec(
        scenario_id=scenario_id,
        seed=seed,
        patient_ref=str(patient_ref),
        doc_type=doc_type,
        quality=quality,
        perturbations=perturbations,
        note=note,
        source_path=source_path,
    )


def load_spec(path: Path) -> ScenarioSpec:
    with path.open("r", encoding="utf-8") as handle:
        return parse_spec(yaml.safe_load(handle), source_path=path)


def load_specs(target: Path) -> list[ScenarioSpec]:
    """Load one spec file, or every ``*.yaml``/``*.yml`` under a directory."""
    if target.is_dir():
        paths = sorted(p for p in target.iterdir() if p.suffix in (".yaml", ".yml"))
        if not paths:
            raise SpecError(f"{target}: no *.yaml or *.yml spec files found")
    else:
        paths = [target]

    specs = [load_spec(p) for p in paths]
    seen: dict[str, Path] = {}
    for spec in specs:
        previous = seen.get(spec.scenario_id)
        if previous is not None:
            raise SpecError(f"duplicate scenario_id {spec.scenario_id!r} in {previous} and {spec.source_path}")
        seen[spec.scenario_id] = spec.source_path or target
    return specs
