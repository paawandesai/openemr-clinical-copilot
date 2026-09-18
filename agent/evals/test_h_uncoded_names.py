"""
H — Records whose code carries no name (docs/audit/findings.md F-57).

OpenEMR sends an uncoded allergy with `code` set to the data-absent-reason
placeholder and the allergen named only in the narrative (`text.div`),
concatenated in unescaped (FhirAllergyIntoleranceService.php:217-221,
UtilsService::createNarrative). A5 covers the one such record in the live
population. These cases cover what that population cannot:

  H1  a Condition in the same shape: named only in its narrative
  H2  a MedicationRequest in the same shape
  H3  a record with the placeholder code and no narrative at all: nothing
      names it, so it must reach the physician as visibly UNVERIFIED, never
      as a verified value
  H4  narratives carrying markup: no markup reaches the brief, and active
      content is refused rather than partly used

MUTATION, stated plainly: each case runs the real pipeline in-process — real
FHIR retrieval from the droplet, the real Anthropic drafting call, the real
verifier and renderer — and changes named records in the retrieved Bundle
before the agent's FHIR client parses it: the code replaced with OpenEMR's
own data-absent concept, and the narrative set or removed. No write to the
chart; nothing in app/ is changed. Expectations for each mutated record come
from the oracle's own parse of the mutated resource (evals/oracle.py), not
from the agent's parser.
"""

from __future__ import annotations

import copy
import dataclasses
import html
import json
from collections.abc import Callable
from typing import Any

import pytest

from evals.harness import case, patch_attr
from evals.invariants import (
    HEADINGS,
    NAME_NOT_AVAILABLE,
    attribution_violations,
    completeness_violations,
    disclosure_violations,
    parse_brief,
)
from evals.oracle import PatientChart, normalize, parse_record, short

# Exactly what OpenEMR emits for a record with no code
# (UtilsService::createDataAbsentUnknownCodeableConcept).
DATA_ABSENT = {
    "coding": [
        {
            "system": "http://terminology.hl7.org/CodeSystem/data-absent-reason",
            "code": "unknown",
            "display": "Unknown",
        }
    ]
}
CONCEPT_FIELD = {"MedicationRequest": "medicationCodeableConcept"}
NO_NAME_WHY = "the chart record has no usable name; review it in the chart"
NO_NAME_REASON = "source record has no name to verify against"


def _narrative(inner: str) -> dict[str, str]:
    return {"status": "additional", "div": f"<div xmlns='http://www.w3.org/1999/xhtml'>{inner}</div>"}


@dataclasses.dataclass
class MutatedRun:
    chart: PatientChart  # the oracle's chart with each mutated record re-parsed from its mutated JSON
    response: dict[str, Any]  # the run in the API's response shape, for the invariants
    model_input: dict[str, Any]  # exactly what the model was given (draft_claims._serialize_retrieved)
    mutated: dict[str, dict[str, Any]]  # source_id -> the mutated raw resource

    def claims_for(self, source_id: str) -> list[dict[str, Any]]:
        return [c for c in self.response["claims"] if c["source_id"] == source_id]

    def model_saw(self, resource_type: str, source_id: str) -> dict[str, Any] | None:
        records = self.model_input[resource_type]["records"]
        return next((r for r in records if r["source_id"] == source_id), None)

    def section(self, resource_type: str) -> Any:
        return parse_brief(self.response["brief"]).section(resource_type)


def _run_mutated(
    pipeline: Any,
    tokens: Any,
    chart: PatientChart,
    mutations: dict[tuple[str, str], Callable[[dict[str, Any]], None]],
) -> MutatedRun:
    """Run the pipeline with `mutations[(resource_type, source_id)]` applied to those raw resources."""
    from app.fhir import client as fhir_client
    from app.graph import pre_visit_brief as pvb
    from app.reasoning import draft_claims

    real_get_bundle = fhir_client._get_bundle
    real_draft = pvb.draft_pre_visit_brief
    model_inputs: list[dict[str, Any]] = []
    mutated: dict[str, dict[str, Any]] = {}

    async def get_bundle(token: str, resource_type: str, params: dict[str, Any]) -> dict[str, Any]:
        bundle = await real_get_bundle(token, resource_type, params)
        for entry in bundle.get("entry", []):
            resource = entry["resource"]
            mutate = mutations.get((resource_type, resource["id"]))
            if mutate is not None:
                mutate(resource)
                mutated[resource["id"]] = copy.deepcopy(resource)
        return bundle

    async def draft(retrieved: Any, **kwargs: Any) -> Any:
        model_inputs.append(json.loads(draft_claims._serialize_retrieved(retrieved)))
        return await real_draft(retrieved, **kwargs)

    def patches() -> Any:
        yield patch_attr(fhir_client, "_get_bundle", get_bundle)
        yield patch_attr(pvb, "draft_pre_visit_brief", draft)

    result = pipeline.run(tokens.get("brief"), chart.patient_id, patches=patches).require_ok()
    if set(mutated) != {source_id for _, source_id in mutations}:
        pytest.fail(f"mutation targets not retrieved: expected {sorted(mutations)}, mutated {sorted(mutated)}")

    searches = dict(chart.searches)
    for (resource_type, source_id), _ in mutations.items():
        search = searches[resource_type]
        records = tuple(
            parse_record(resource_type, mutated[source_id]) if r.source_id == source_id else r for r in search.records
        )
        searches[resource_type] = dataclasses.replace(search, records=records)

    response = {
        "brief": result.text,
        "claims": [
            {
                "resource_type": r.claim.resource_type.value,
                "source_id": r.claim.source_id,
                "field": r.claim.field,
                "value": r.claim.value,
                "verified": r.passed,
                "unverified_reason": r.reason,
            }
            for r in result.verification_results
        ],
        "sections": [
            {"resource_type": rt.value, "retrieved": c.retrieved, "in_brief": c.in_brief}
            for rt, c in result.section_counts.items()
        ],
    }
    return MutatedRun(dataclasses.replace(chart, searches=searches), response, model_inputs[0], mutated)


def _invariant_violations(run: MutatedRun) -> list[str]:
    return (
        disclosure_violations(run.response["claims"], run.response["brief"])
        + attribution_violations(run.response, run.chart)
        + completeness_violations(run.response, run.chart)
    )


def _named_from_narrative(run: MutatedRun, resource_type: str, source_id: str) -> list[str]:
    """The record is named in the brief, verified, by the name its narrative holds."""
    record = run.chart.record(resource_type, source_id)
    assert record is not None
    violations = []
    if not (record.uncoded and not record.coded_name and record.labels):
        violations.append(f"setup: the oracle does not see {short(source_id)} as named only by its narrative")
    seen = run.model_saw(resource_type, source_id)
    if seen is None or not isinstance(seen["display"], str) or normalize(seen["display"]) not in record.names:
        violations.append(
            f"the model was given {seen and seen['display']!r} for {short(source_id)}; its narrative names it "
            f"{list(record.labels)}"
        )
    shown = {normalize(v) for v in parse_brief(run.response["brief"]).section(resource_type).shown_as_verified()}
    named = [
        c
        for c in run.claims_for(source_id)
        if c["verified"] and normalize(str(c["value"])) in record.names and normalize(str(c["value"])) in shown
    ]
    if not named:
        violations.append(
            f"{HEADINGS[resource_type]}: {short(source_id)} is not shown, verified, as {list(record.labels)}; "
            f"its claims: {[(c['value'], c['verified']) for c in run.claims_for(source_id)]}"
        )
    return violations


# -- H1-H3: one run over one patient -------------------------------------------


@pytest.fixture(scope="module")
def uncoded_run(pipeline: Any, panel: Any, tokens: Any) -> tuple[MutatedRun, dict[str, str | None]]:
    """
    One active Condition and one active MedicationRequest moved to OpenEMR's
    uncoded-allergy shape (placeholder code, name in the narrative), and one
    allergy given the placeholder code and no narrative at all. One model call.
    """
    chart = panel.chart("small_mixed")

    def first(resource_type: str, active: bool) -> Any:
        search = chart.searches[resource_type]
        records = search.active if active else search.records
        return records[0] if records else None

    condition = first("Condition", active=True)
    medication = first("MedicationRequest", active=True)
    allergy = first("AllergyIntolerance", active=False)
    if medication is None or allergy is None:
        pytest.skip("small_mixed patient lacks an active medication or an allergy")

    def moved_to_narrative(resource_type: str, name: str) -> Callable[[dict[str, Any]], None]:
        def mutate(resource: dict[str, Any]) -> None:
            resource[CONCEPT_FIELD.get(resource_type, "code")] = copy.deepcopy(DATA_ABSENT)
            resource["text"] = _narrative(html.escape(name))

        return mutate

    def unnamed(resource: dict[str, Any]) -> None:
        resource["code"] = copy.deepcopy(DATA_ABSENT)
        resource.pop("text", None)

    mutations: dict[tuple[str, str], Callable[[dict[str, Any]], None]] = {
        ("MedicationRequest", medication.source_id): moved_to_narrative("MedicationRequest", medication.label),
        ("AllergyIntolerance", allergy.source_id): unnamed,
    }
    if condition is not None:
        mutations[("Condition", condition.source_id)] = moved_to_narrative("Condition", condition.label)
    run = _run_mutated(pipeline, tokens, chart, mutations)
    targets = {
        "Condition": condition.source_id if condition else None,
        "MedicationRequest": medication.source_id,
        "AllergyIntolerance": allergy.source_id,
    }
    return run, targets


@case(
    "H1",
    title="Uncoded condition named only in its narrative: named in the brief and verified against that name",
    guards="a problem whose code is the data-absent placeholder reaching the physician as a stand-in "
    "('(unspecified problem)') that verifies, instead of by the name the chart records",
    cites="F-57 (same fallback in all three parsers); ARCHITECTURE.md §5 (source data accuracy); "
    "FhirAllergyIntoleranceService.php:217-221 (the shape reproduced)",
    level="in-process mutation: live retrieval, one active Condition's code set to data-absent and its name "
    "moved to the narrative, live model draft",
)
def test_h1_uncoded_condition_named_from_narrative(uncoded_run: Any, observe: Any) -> None:
    run, targets = uncoded_run
    source_id = targets["Condition"]
    if source_id is None:
        pytest.skip("small_mixed patient has no active condition")
    observe("patient", run.chart.patient_id)
    observe("mutated record", f"Condition/{short(source_id)}: narrative {run.mutated[source_id]['text']['div']!r}")
    observe("model was given", run.model_saw("Condition", source_id))
    observe("claims on it", [(c["value"], c["verified"]) for c in run.claims_for(source_id)])

    violations = _named_from_narrative(run, "Condition", source_id) + _invariant_violations(run)
    if violations:
        pytest.fail("\n".join(violations))


@case(
    "H2",
    title="Uncoded medication named only in its narrative: named in the brief and verified against that name",
    guards="a current medication whose code is the data-absent placeholder reaching the physician as "
    "'(unspecified medication)', verified",
    cites="F-57; ARCHITECTURE.md §5; F-55 (medications are expected to carry RxNorm, so an uncoded one is "
    "the exception the parser must still name)",
    level="in-process mutation: live retrieval, one active MedicationRequest's code set to data-absent and "
    "its name moved to the narrative, live model draft",
)
def test_h2_uncoded_medication_named_from_narrative(uncoded_run: Any, observe: Any) -> None:
    run, targets = uncoded_run
    source_id = targets["MedicationRequest"]
    observe("patient", run.chart.patient_id)
    observe(
        "mutated record",
        f"MedicationRequest/{short(source_id)}: narrative {run.mutated[source_id]['text']['div']!r}",
    )
    observe("model was given", run.model_saw("MedicationRequest", source_id))
    observe("claims on it", [(c["value"], c["verified"]) for c in run.claims_for(source_id)])

    violations = _named_from_narrative(run, "MedicationRequest", source_id) + _invariant_violations(run)
    if violations:
        pytest.fail("\n".join(violations))


@case(
    "H3",
    title="Placeholder code and no narrative: shown as UNVERIFIED '(name not available)', never verified",
    guards="a record nothing names rendered under a stand-in string the model copies and the verifier "
    "confirms (the F-57 failure shape), or dropped, or counted as verified",
    cites="F-57 remediation (an uncertain parse renders as visibly uncertain); ARCHITECTURE.md §5 "
    "('never silently dropped, never softened'); KEY_METRICS.md Metric 2",
    level="in-process mutation: live retrieval, one allergy's code set to data-absent and its narrative "
    "removed, live model draft",
)
def test_h3_unnamed_record_is_visibly_unverified(uncoded_run: Any, observe: Any) -> None:
    run, targets = uncoded_run
    source_id = targets["AllergyIntolerance"]
    record = run.chart.record("AllergyIntolerance", source_id)
    claims = run.claims_for(source_id)
    section = run.section("AllergyIntolerance")
    observe("patient", run.chart.patient_id)
    observe("mutated record", f"AllergyIntolerance/{short(source_id)}: code data-absent, no narrative")
    observe("allergy section", json.dumps("\n".join([section.header, *(f"  - {b}" for b in section.bullets)])))
    observe("claims on it", claims)
    observe(
        "verification",
        f"{sum(c['verified'] for c in run.response['claims'])} verified, "
        f"{sum(not c['verified'] for c in run.response['claims'])} unverified",
    )

    violations = []
    if record is None or record.labels:
        violations.append(f"setup: the oracle still finds a name for {short(source_id)}")
    if run.model_saw("AllergyIntolerance", source_id) is not None:
        violations.append("the record with no name was given to the model to draft from")
    expected = [("display", None, False, NO_NAME_REASON)]
    got = [(c["field"], c["value"], c["verified"], c["unverified_reason"]) for c in claims]
    if got != expected:
        violations.append(f"claims on the record: {got}; expected exactly {expected}")
    if (NAME_NOT_AVAILABLE, NO_NAME_WHY) not in section.unverified():
        violations.append(f"allergy section does not show it as UNVERIFIED {NAME_NOT_AVAILABLE!r}: {section.bullets}")
    if any("unspecified" in b.lower() or b == "Unknown" for b in section.bullets):
        violations.append(f"a placeholder reached the allergy section: {section.bullets}")
    violations += _invariant_violations(run)
    if violations:
        pytest.fail("\n".join(violations))


# -- H4: markup in the narrative -----------------------------------------------


@case(
    "H4",
    title="Markup in an uncoded record's narrative never reaches the brief; active content is refused",
    guards="the narrative — untrusted HTML that OpenEMR builds by concatenation — turning a parsing fix "
    "into an HTML/script injection path to the physician's screen, or a hostile narrative being partly "
    "trusted as a name",
    cites="F-57 (narrative fallback); UtilsService::createNarrative (title concatenated unescaped); "
    "FHIR R4 Narrative txt-1 (formatting elements only)",
    level="in-process mutation: live retrieval, three allergies' codes set to data-absent and their "
    "narratives set to markup-bearing HTML, live model draft",
)
def test_h4_markup_in_narrative_never_reaches_the_brief(
    pipeline: Any, panel: Any, tokens: Any, observe: Any
) -> None:
    chart = panel.chart("coded_allergies")
    allergies = chart.searches["AllergyIntolerance"].records
    if len(allergies) < 3:
        pytest.skip("H4 needs a patient with three allergy records")
    benign, script, escaped = allergies[:3]
    narratives = {
        # Formatting and attributes only: the text is the allergen.
        benign.source_id: f"<b>{html.escape(benign.label)}</b><img src='x' onerror='alert(1)'/>"
        "<a href='javascript:alert(2)'></a>",
        # Active content: refused whole.
        script.source_id: f"{html.escape(script.label)}<script>alert(document.cookie)</script>",
        # Markup escaped into text: refused, never passed on as text that looks like a tag.
        escaped.source_id: f"&lt;img src=x onerror=alert(1)&gt;{html.escape(escaped.label)}",
    }

    def with_narrative(source_id: str) -> Callable[[dict[str, Any]], None]:
        def mutate(resource: dict[str, Any]) -> None:
            resource["code"] = copy.deepcopy(DATA_ABSENT)
            resource["text"] = _narrative(narratives[source_id])

        return mutate

    run = _run_mutated(
        pipeline, tokens, chart, {("AllergyIntolerance", sid): with_narrative(sid) for sid in narratives}
    )
    section = run.section("AllergyIntolerance")
    observe("patient", chart.patient_id)
    for source_id in narratives:
        observe(
            f"AllergyIntolerance/{short(source_id)}",
            f"narrative {run.mutated[source_id]['text']['div']!r} -> claims "
            f"{[(c['value'], c['verified']) for c in run.claims_for(source_id)]}",
        )

    violations = []
    for source_id in narratives:
        seen = run.model_saw("AllergyIntolerance", source_id)
        for text in [
            *(str(c["value"]) for c in run.claims_for(source_id)),
            *([str(seen["display"])] if seen else []),
        ]:
            if "<" in text or ">" in text:
                violations.append(f"markup reached the brief pipeline for {short(source_id)}: {text!r}")
    for bullet in section.bullets:
        if "<" in bullet or ">" in bullet:
            violations.append(f"markup in the rendered allergy section: {bullet!r}")
    violations += _named_from_narrative(run, "AllergyIntolerance", benign.source_id)
    for refused in (script, escaped):
        got = [(c["value"], c["verified"]) for c in run.claims_for(refused.source_id)]
        if got != [(None, False)] or run.model_saw("AllergyIntolerance", refused.source_id) is not None:
            violations.append(
                f"{short(refused.source_id)}: a narrative with active or escaped markup was not refused "
                f"(claims {got})"
            )
    if section.unverified().count((NAME_NOT_AVAILABLE, NO_NAME_WHY)) != 2:
        violations.append(f"expected two UNVERIFIED {NAME_NOT_AVAILABLE!r} lines: {section.bullets}")
    violations += _invariant_violations(run)
    if violations:
        pytest.fail("\n".join(violations))
