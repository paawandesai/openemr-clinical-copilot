"""
Category A — clean extraction and schema baseline.

The happy-path floor. Everything here is deterministic: a judge asked
whether a payload parses is a slower, less reliable `json.loads` (D6).
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from app.models.w2 import (
    BBox,
    Citation,
    IntakeFormExtraction,
    LabPdfExtraction,
    SourceType,
)
from app.models.w2.factory_mapping import KNOWN_RENDERINGS
from evals.w2.gate import samples
from evals.w2.gate.models import Case, CheckOutcome
from evals.w2.gate.paths import PUBLISHED_SCHEMAS_DIR
from evals.w2.gate.registry import check


@check("chk.a_lab_clean_parses")
def a_lab_clean_parses(case: Case) -> CheckOutcome:
    """Every clean lab scenario converts, with nothing dropped on the way."""
    notes: list[str] = []
    for scenario_id in case.scenario_ids():
        conversion = samples.conversion(scenario_id)
        extraction = conversion.extraction
        if not isinstance(extraction, LabPdfExtraction):
            return CheckOutcome.failed(f"{scenario_id} did not convert to a LabPdfExtraction")
        if conversion.unmapped_field_paths:
            return CheckOutcome.failed(
                f"{scenario_id}: {len(conversion.unmapped_field_paths)} ground-truth field "
                f"paths have no home in the schema: {conversion.unmapped_field_paths[:5]}"
            )
        if not extraction.results:
            return CheckOutcome.failed(f"{scenario_id}: a clean lab report extracted no rows")
        notes.append(f"{scenario_id}: {len(extraction.results)} rows, 0 unmapped")
    return CheckOutcome.ok("; ".join(notes))


@check("chk.a_every_presented_value_has_a_bbox")
def a_every_presented_value_has_a_bbox(case: Case) -> CheckOutcome:
    """
    Invariant 4 at the top of the pipeline: anything presentable as a fact
    carries a located document citation, and a located citation has a bbox.
    """
    checked = 0
    for scenario_id in case.scenario_ids():
        extraction = samples.lab(scenario_id)
        for result in extraction.results:
            fact = result.presented_value()
            if fact is None:
                continue
            checked += 1
            if fact.citation.bbox is None:
                return CheckOutcome.failed(
                    f"{scenario_id}: {result.test_key} is presented as fact with no bbox"
                )
            if fact.citation.source_type is not SourceType.DOCUMENT:
                return CheckOutcome.failed(
                    f"{scenario_id}: {result.test_key} is presented from a "
                    f"{fact.citation.source_type.value} citation, not a document one"
                )
    if checked == 0:
        return CheckOutcome.failed("no presentable values found; the check proved nothing")
    return CheckOutcome.ok(f"{checked} presented values, each with a document bbox")


@check("chk.a_intake_clean_parses")
def a_intake_clean_parses(case: Case) -> CheckOutcome:
    """The intake form converts, and every drawn field declares how it was drawn."""
    notes: list[str] = []
    for scenario_id in case.scenario_ids():
        conversion = samples.conversion(scenario_id)
        if not isinstance(conversion.extraction, IntakeFormExtraction):
            return CheckOutcome.failed(f"{scenario_id} did not convert to an IntakeFormExtraction")
        if conversion.unmapped_field_paths:
            return CheckOutcome.failed(
                f"{scenario_id}: unmapped ground-truth paths {conversion.unmapped_field_paths[:5]}"
            )
        undeclared = [
            record["field_path"]
            for record in samples.field_records(scenario_id)
            if record.get("status") == "present"
            and record.get("kind") == "text"
            and record.get("rendering") not in KNOWN_RENDERINGS
        ]
        if undeclared:
            return CheckOutcome.failed(
                f"{scenario_id}: {len(undeclared)} drawn text fields declare no known "
                f"rendering: {undeclared[:5]}"
            )
        form = conversion.extraction
        notes.append(
            f"{scenario_id}: {len(form.medications)} meds, {len(form.allergies)} allergies"
        )
    return CheckOutcome.ok("; ".join(notes))


def _document_citation_kwargs() -> dict[str, Any]:
    return {
        "source_type": SourceType.DOCUMENT,
        "source_id": "lab-clean-001",
        "page_or_section": "1",
        "field_or_chunk_id": "results.cmp.glucose.value",
        "quote_or_value": "89",
        "bbox": BBox(page=1, x0=230.0, y0=396.3, x1=275.6, y1=407.2),
        "extraction_id": "w2x-8f4c1d9a3b6e05f27c0a19d4e8b73612",
    }


@check("chk.a_citation_carries_all_five_fields")
def a_citation_carries_all_five_fields(case: Case) -> CheckOutcome:
    """
    Invariant 2's five fields plus bbox, each one load-bearing.

    Built by removing one field at a time from a citation that is otherwise
    valid, so the check fails the moment any of them becomes optional,
    defaulted or blankable — which is what "stripping a citation" looks like
    in a diff.
    """
    del case
    base = _document_citation_kwargs()
    Citation(**base)  # the control: the complete citation is accepted

    survived: list[str] = []
    removals: dict[str, Any] = {
        "source_id": "",
        "page_or_section": None,
        "field_or_chunk_id": "",
        "quote_or_value": None,
        "bbox": None,
    }
    for field_name, blank in removals.items():
        broken = dict(base)
        broken[field_name] = blank
        try:
            citation = Citation(**broken)
        except ValidationError:
            continue
        # bbox is the one whose absence is legal on the Citation itself: an
        # unlocated document citation is a real thing. What must not survive
        # is calling it located.
        if field_name == "bbox" and not citation.is_located_document:
            continue
        survived.append(field_name)

    if survived:
        return CheckOutcome.failed(
            "a document citation was accepted without "
            f"{survived} — invariant 2's contract is no longer enforced"
        )
    return CheckOutcome.ok("all five citation fields plus bbox are load-bearing")


@check("chk.a_each_value_cites_its_own_field_id")
def a_each_value_cites_its_own_field_id(case: Case) -> CheckOutcome:
    """
    No two rows share a `field_or_chunk_id`, and no `test_key` repeats.

    This is the deterministic half of the repeated-entity collapse a VLM
    produces on a dense table: 44 rows that all cite row 1 look complete.
    """
    for scenario_id in case.scenario_ids():
        extraction = samples.lab(scenario_id)
        seen: dict[str, str] = {}
        for result in extraction.results:
            citation = result.citation
            if citation is None:
                continue
            owner = seen.get(citation.field_or_chunk_id)
            if owner is not None:
                return CheckOutcome.failed(
                    f"{scenario_id}: {result.test_key} and {owner} both cite "
                    f"{citation.field_or_chunk_id}"
                )
            seen[citation.field_or_chunk_id] = result.test_key
        if len(seen) < len(extraction.results):
            return CheckOutcome.failed(
                f"{scenario_id}: {len(extraction.results)} rows but only {len(seen)} "
                "distinct value citations"
            )
    return CheckOutcome.ok("every row cites its own field path")


@check("chk.a_schema_version_is_pinned")
def a_schema_version_is_pinned(case: Case) -> CheckOutcome:
    """A payload announcing a different schema version is refused, not read."""
    del case
    payload = json.loads(samples.lab("lab-clean-001").model_dump_json())
    payload["schema_version"] = "w2-contracts/lab_pdf/2.0.0"
    try:
        LabPdfExtraction.model_validate_json(json.dumps(payload))
    except ValidationError:
        pass
    else:
        return CheckOutcome.failed(
            "a lab_pdf payload claiming schema_version 2.0.0 was accepted; a version "
            "pin that reads anything is not a pin"
        )

    intake_payload = json.loads(samples.intake("intake-clean-004").model_dump_json())
    intake_payload["schema_version"] = "w2-contracts/intake_form/0.9.0"
    try:
        IntakeFormExtraction.model_validate_json(json.dumps(intake_payload))
    except ValidationError:
        return CheckOutcome.ok("both extraction schemas refuse a foreign schema_version")
    return CheckOutcome.failed("an intake_form payload claiming schema_version 0.9.0 was accepted")


@check("chk.a_published_schemas_accept_and_reject")
def a_published_schemas_accept_and_reject(case: Case) -> CheckOutcome:
    """
    D22: `docs/w2/schemas/*.schema.json` is the wire contract. It must accept
    every converted sample and reject an instance with a key it does not
    declare — a validator that accepts everything is not validating.
    """
    del case
    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import ValidationError as SchemaError
    except ImportError as exc:  # pragma: no cover - environment, not logic
        return CheckOutcome.failed(f"jsonschema is not installed: {exc}")

    pairs = (("lab_pdf", samples.LAB_SCENARIOS), ("intake_form", samples.INTAKE_SCENARIOS))
    accepted = 0
    for name, scenario_ids in pairs:
        path = PUBLISHED_SCHEMAS_DIR / f"{name}.schema.json"
        if not path.is_file():
            return CheckOutcome.failed(f"published schema {path} is missing")
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        for scenario_id in scenario_ids:
            instance = json.loads(samples.conversion(scenario_id).extraction.model_dump_json())
            try:
                validator.validate(instance)
            except SchemaError as exc:
                return CheckOutcome.failed(
                    f"{scenario_id} does not validate against {name}.schema.json: {exc.message}"
                )
            accepted += 1
        # the negative arm
        instance = json.loads(samples.conversion(scenario_ids[0]).extraction.model_dump_json())
        instance["a_key_the_schema_does_not_declare"] = 1
        try:
            validator.validate(instance)
        except SchemaError:
            continue
        return CheckOutcome.failed(
            f"{name}.schema.json accepted an undeclared property; additionalProperties "
            "is no longer false and the wire contract is open"
        )
    return CheckOutcome.ok(f"{accepted} samples validate; both schemas reject an undeclared key")
