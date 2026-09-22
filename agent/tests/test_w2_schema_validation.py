"""
The published JSON Schemas, checked with a real JSON Schema validator.

`tests/test_w2_json_schema_export.py` asserts the committed files match
what the models produce. That is a drift check, and it would pass just as
happily if Pydantic emitted something no validator accepts. These schemas
are a graded deliverable that other tools read, so this module checks the
other half:

1. each schema is itself a valid draft 2020-12 schema
   (`Draft202012Validator.check_schema`);
2. every doc-factory sample, converted through the Week 2 contracts and
   serialised, validates against the schema for its kind;
3. a deliberately malformed instance per schema is *rejected*, so a run
   where validation silently does nothing cannot pass.

**What a published schema does not carry**, stated here because a consumer
will otherwise assume it does: JSON Schema describes shape — types,
enums, required keys, bounds, `additionalProperties: false`. It cannot
express the cross-field rules that make these contracts safe. "A `located`
field has a citation with a bbox" (invariant 4), "an idempotency key is
the composition of its own components" (D1) and "a claim's citations share
one source type" (D17) all live in Pydantic model validators, and an
instance can satisfy every schema in `docs/w2/schemas/` while breaking all
three. `test_the_schema_cannot_express_the_invariants` pins that boundary
so nobody discovers it by shipping against it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaValidationError
from pydantic import ValidationError

from app.models.w2.extraction import ExtractedField, FieldStatus
from app.models.w2.intake_form import IntakeFormExtraction
from app.models.w2.lab_pdf import LabPdfExtraction
from tests.test_w2_factory_ground_truth import (
    ALL_SAMPLES,
    INTAKE_SAMPLES,
    LAB_SAMPLES,
    convert,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "docs" / "w2" / "schemas"

SCHEMA_FOR_KIND = {"lab_pdf": LAB_SAMPLES, "intake_form": INTAKE_SAMPLES}


def load_schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(load_schema(name))


def instance(scenario_id: str) -> dict[str, Any]:
    """A converted sample, serialised the way a consumer would receive it."""
    return convert(scenario_id).extraction.model_dump(mode="json")


def all_messages(errors: list[SchemaValidationError]) -> list[str]:
    """
    Every message in an error tree, including the nested ones.

    An optional field is `anyOf: [{$ref}, {type: null}]`, so a failure
    inside it surfaces as "not valid under any of the given schemas" with
    the real reason in `.context`. Asserting on the top-level message alone
    would pass for any failure whatsoever, which is not what these tests
    mean to check.
    """
    found: list[str] = []
    for error in errors:
        found.append(error.message)
        found.extend(all_messages(list(error.context or [])))
    return found


# -- the schemas are schemas ------------------------------------------------


@pytest.mark.parametrize("name", sorted(SCHEMA_FOR_KIND))
def test_the_schema_is_a_valid_draft_2020_12_schema(name: str) -> None:
    Draft202012Validator.check_schema(load_schema(name))


@pytest.mark.parametrize("name", sorted(SCHEMA_FOR_KIND))
def test_every_ref_in_the_schema_resolves(name: str) -> None:
    """A `$ref` to a `$defs` key that is not there would make the schema
    vacuous for that subtree — it would fail at validation time, not at
    check_schema time."""
    schema = load_schema(name)
    defined = set(schema.get("$defs", {}))

    def refs(node: Any) -> list[str]:
        found: list[str] = []
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$ref" and isinstance(value, str):
                    found.append(value)
                else:
                    found.extend(refs(value))
        elif isinstance(node, list):
            for item in node:
                found.extend(refs(item))
        return found

    for ref in refs(schema):
        assert ref.startswith("#/$defs/"), f"unexpected $ref target {ref!r}"
        assert ref.removeprefix("#/$defs/") in defined, f"{ref} is not defined"


# -- real instances validate ------------------------------------------------


@pytest.mark.parametrize(
    ("name", "scenario_id"),
    [(name, scenario_id) for name, ids in SCHEMA_FOR_KIND.items() for scenario_id in ids],
)
def test_a_converted_factory_sample_validates(name: str, scenario_id: str) -> None:
    errors = sorted(validator(name).iter_errors(instance(scenario_id)), key=str)
    assert errors == [], "\n".join(
        f"{list(error.absolute_path)}: {error.message}" for error in errors[:10]
    )


def test_all_six_samples_are_covered() -> None:
    covered = [scenario_id for ids in SCHEMA_FOR_KIND.values() for scenario_id in ids]
    assert sorted(covered) == sorted(ALL_SAMPLES)


@pytest.mark.parametrize("scenario_id", LAB_SAMPLES)
def test_a_lab_sample_is_rejected_by_the_intake_schema(scenario_id: str) -> None:
    """The two schemas are not interchangeable. If a lab report validated
    against the intake schema, neither would be saying much."""
    assert not validator("intake_form").is_valid(instance(scenario_id))


@pytest.mark.parametrize("scenario_id", INTAKE_SAMPLES)
def test_an_intake_sample_is_rejected_by_the_lab_schema(scenario_id: str) -> None:
    assert not validator("lab_pdf").is_valid(instance(scenario_id))


# -- deliberately invalid instances are rejected ---------------------------


def test_a_lab_instance_with_a_zero_page_bbox_is_rejected() -> None:
    """
    Pages are 1-based. A consumer indexing from zero produces exactly this,
    and `minimum: 1` on `BBox.page` is the published schema's statement of
    the convention.
    """
    broken = instance("lab-clean-001")
    citation = broken["results"][0]["value"]["citation"]
    assert citation["bbox"]["page"] == 1
    citation["bbox"]["page"] = 0

    errors = list(validator("lab_pdf").iter_errors(broken))
    assert errors, "a zero page number must not validate"
    assert any("0 is less than the minimum of 1" in message for message in all_messages(errors))


def test_an_intake_instance_with_an_invented_status_is_rejected() -> None:
    """
    `FieldStatus` is closed. A producer that invents `verified` — the exact
    word invariant 4 exists to keep away from unlocated values — must fail
    at the boundary rather than be read as something this contract knows.
    """
    broken = instance("intake-clean-004")
    assert broken["demographics"]["name"]["status"] == FieldStatus.LOCATED.value
    broken["demographics"]["name"]["status"] = "verified"

    errors = list(validator("intake_form").iter_errors(broken))
    assert errors, "an unknown FieldStatus must not validate"
    assert any("'verified' is not one of" in message for message in all_messages(errors))


@pytest.mark.parametrize("name", sorted(SCHEMA_FOR_KIND))
def test_an_unknown_property_is_rejected(name: str) -> None:
    """`extra="forbid"` survives into the published schema as
    `additionalProperties: false`. A patient identifier smuggled into an
    extraction payload is refused by a third-party validator too, not only
    by Pydantic."""
    scenario_id = SCHEMA_FOR_KIND[name][0]
    broken = instance(scenario_id)
    broken["patient_id"] = "a2c373ec-57bf-41cb-8767-94410dcf663d"

    with pytest.raises(SchemaValidationError, match="patient_id"):
        validator(name).validate(broken)


@pytest.mark.parametrize("name", sorted(SCHEMA_FOR_KIND))
def test_a_missing_required_field_is_rejected(name: str) -> None:
    scenario_id = SCHEMA_FOR_KIND[name][0]
    broken = instance(scenario_id)
    del broken["document"]

    with pytest.raises(SchemaValidationError, match="document"):
        validator(name).validate(broken)


def test_the_discriminated_lab_value_union_is_enforced() -> None:
    """A value tagged `numeric` that carries qualitative text is the shape
    D10 exists to make impossible, and the published schema has to say so
    too — the discriminator is the whole reason for the tag."""
    broken = instance("lab-clean-001")
    value = broken["results"][0]["value"]["value"]
    assert value["kind"] == "numeric"
    value.pop("value")
    value["text"] = "positive"

    assert not validator("lab_pdf").is_valid(broken)


# -- the boundary, pinned --------------------------------------------------


def test_the_schema_cannot_express_the_invariants() -> None:
    """
    A `located` field with no citation satisfies the published schema and
    is refused by the contract. That is not a defect in the export — JSON
    Schema has no way to say it — but a consumer who validates against
    `docs/w2/schemas/` and stops there has checked shape, not safety.

    Pinned as a test so the limitation is discovered by reading the suite
    rather than by shipping against it. If a future export ever does
    encode this, this test fails and the docstring above gets rewritten.
    """
    payload = instance("lab-clean-001")
    row = payload["results"][0]["value"]
    assert row["status"] == FieldStatus.LOCATED.value
    row["citation"] = None

    # JSON Schema: fine.
    assert validator("lab_pdf").is_valid(payload)

    # The contract: not fine.
    with pytest.raises(ValidationError, match="located field requires a citation"):
        LabPdfExtraction.model_validate_json(json.dumps(payload))


def test_the_contract_accepts_what_the_schema_accepts_for_real_samples() -> None:
    """The other direction: for every unmodified sample, schema validity and
    contract validity agree. They only diverge on instances hand-built to
    break an invariant."""
    for name, ids in SCHEMA_FOR_KIND.items():
        model = LabPdfExtraction if name == "lab_pdf" else IntakeFormExtraction
        for scenario_id in ids:
            payload = instance(scenario_id)
            assert validator(name).is_valid(payload)
            parsed = model.model_validate_json(json.dumps(payload))
            assert parsed == convert(scenario_id).extraction


def test_the_published_schema_is_a_wire_contract_read_through_json() -> None:
    """
    How a consumer must feed these models, which the schema alone does not
    say and which `strict=True` makes non-obvious.

    In strict mode Pydantic refuses a Python `str` where an enum member is
    declared — that is the whole point, and it is what stops a stringly
    typed value drifting in from Python code. JSON has no enum type, so
    `model_validate_json` still reads `"located"` as `FieldStatus.LOCATED`;
    `model_validate` on an already-parsed `dict` does not.

    So `docs/w2/schemas/` describes the JSON on the wire, and the door into
    the contract is `model_validate_json`. Handing a parsed dict to
    `model_validate` fails on every enum in the payload, which looks like a
    schema mismatch and is not one.
    """
    payload = instance("lab-clean-001")
    assert validator("lab_pdf").is_valid(payload)

    # The wire: accepted, and equal to what was serialised.
    assert (
        LabPdfExtraction.model_validate_json(json.dumps(payload))
        == convert("lab-clean-001").extraction
    )

    # The parsed dict: refused, on the enums.
    with pytest.raises(ValidationError) as raised:
        LabPdfExtraction.model_validate(payload)
    assert any(error["type"] == "is_instance_of" for error in raised.value.errors()), (
        "expected strict-mode enum refusals, not a shape mismatch"
    )


def test_extracted_field_round_trips_through_json() -> None:
    """The unit the schemas are mostly made of, checked on its own: a
    serialised ExtractedField is re-readable as the same thing — through
    JSON, per the test above."""
    field = ExtractedField[str].missing()
    restored = ExtractedField[str].model_validate_json(json.dumps(field.model_dump(mode="json")))
    assert restored == field
