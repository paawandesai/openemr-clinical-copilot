"""
Category E — medication and allergy discrepancies.

D2 and invariant 5: intake medications and allergies are never merged into
the chart, not even when they match. They are surfaced as discrepancies. The
mirror failure matters just as much — a `derived` rendering is how a real
form says a real drug name, and scoring it as a disagreement fills a
clinician's screen with noise until they stop reading it.
"""

from __future__ import annotations

import inspect
from datetime import date

from pydantic import ValidationError

from app.models.w2 import (
    AbnormalFlag,
    BBox,
    Citation,
    Discrepancy,
    DiscrepancyCategory,
    DiscrepancyKind,
    DocumentKind,
    FieldStatus,
    LabWriteBackRequest,
    LabWriteBackResult,
    NumericLabValue,
    SourceType,
    document_extraction_id,
    lab_result_extraction_id,
    lab_result_idempotency_key,
)
from app.models.w2 import intake_form as intake_module
from evals.w2.gate import samples
from evals.w2.gate.models import Case, CheckOutcome
from evals.w2.gate.registry import check

_SHA3 = "9c2a41be" + "0" * 120
_TEST_CODE = "2345-7"
_COLLECTED = date(2026, 3, 2)

_DOC_CITATION = Citation.located_document(
    source_id="intake-scanned-light-med-005",
    field_path="medications[6].dose",
    quote="amLODIPine 10 MG",
    bbox=BBox(page=1, x0=96.0, y0=402.0, x1=214.0, y1=413.0),
    extraction_id="w2x-1a55c7e0b93d4682f70c5ad18e2b4497",
)
_CHART_CITATION = Citation(
    source_type=SourceType.CHART,
    source_id="0b1f4c0e-2d3a-4a5b-8c7d-9e0f1a2b3c4d",
    page_or_section="MedicationRequest",
    field_or_chunk_id="medicationCodeableConcept.text",
    quote_or_value="amLODIPine 2.5 MG Oral Tablet",
)

#: Names a merge would have to wear. tests/test_w2_intake_form.py asserts the
#: same thing; repeated here because the gate must fail on its own evidence
#: rather than on another suite having been run.
_MERGE_SHAPED = ("merge", "apply_to_chart", "reconcile", "write_to_chart", "upsert", "sync")


@check("chk.e_added_is_surfaced_and_never_merged")
def e_added_is_surfaced_and_never_merged(case: Case) -> CheckOutcome:
    """
    CRITICAL. A medication the patient wrote down and the chart does not
    record is an `added` discrepancy with a document citation and no chart
    side — and there is no path that puts it in the chart.
    """
    del case
    discrepancy = Discrepancy(
        category=DiscrepancyCategory.MEDICATION,
        kind=DiscrepancyKind.ADDED,
        subject="amLODIPine",
        document_value="amLODIPine 10 MG",
        document_citation=_DOC_CITATION,
    )
    if discrepancy.chart_value is not None or discrepancy.chart_citation is not None:
        return CheckOutcome.failed("an `added` discrepancy carried a chart side")

    try:
        Discrepancy(
            category=DiscrepancyCategory.MEDICATION,
            kind=DiscrepancyKind.ADDED,
            subject="amLODIPine",
            document_value="amLODIPine 10 MG",
            document_citation=_DOC_CITATION,
            chart_value="amLODIPine 2.5 MG Oral Tablet",
            chart_citation=_CHART_CITATION,
        )
    except ValidationError:
        pass
    else:
        return CheckOutcome.failed(
            "`added` accepted a chart value; the kind no longer means what it says"
        )

    found = [
        name
        for name, _ in inspect.getmembers(intake_module)
        if any(shape in name.lower() for shape in _MERGE_SHAPED)
    ]
    if found:
        return CheckOutcome.failed(
            f"app/models/w2/intake_form.py exports merge-shaped symbols {found}; D2 says "
            "there is no such operation"
        )

    # The write-back arm needs a request that is valid in every other respect,
    # so that the ONLY thing that can reject it is the document kind. An
    # earlier version of this check sent `results=[]`, which `min_length=1`
    # rejects on its own — it passed while the D2 rule was removed, and the
    # planted-regression harness is what found that.
    def _intake_request(kind: DocumentKind) -> None:
        LabWriteBackRequest(
            patient_uuid="a2c373ec-57bf-41cb-8767-94410dcf663d",
            document_kind=kind,
            document={
                "filename": "intake.pdf",
                "mime_type": "application/pdf",
                "sha3_512": _SHA3,
                "byte_length": 1024,
                "content_base64": "eA==",
            },
            results=[
                LabWriteBackResult(
                    test_code=_TEST_CODE,
                    test_name="Glucose",
                    value=NumericLabValue(value=168.0),
                    unit="mg/dL",
                    collection_date=_COLLECTED,
                    abnormal=AbnormalFlag.HIGH,
                    page=1,
                    bbox=BBox(page=1, x0=230.0, y0=396.3, x1=275.6, y1=407.2),
                    idempotency_key=lab_result_idempotency_key(
                        document_sha3_512=_SHA3,
                        test_code=_TEST_CODE,
                        collection_date=_COLLECTED,
                    ),
                    extraction_id=lab_result_extraction_id(
                        document_sha3_512=_SHA3,
                        test_code=_TEST_CODE,
                        collection_date=_COLLECTED,
                    ),
                )
            ],
            extraction_id=document_extraction_id(
                document_sha3_512=_SHA3, extractor_version="w2-extractor/1.0.0"
            ),
            extractor_version="w2-extractor/1.0.0",
            correlation_id="4c547101-195d-47b2-bc4b-0db614573052",
        )

    try:
        _intake_request(DocumentKind.LAB_PDF)
    except ValidationError as exc:
        return CheckOutcome.failed(
            f"the control arm failed: an otherwise-valid lab write-back was rejected "
            f"({exc.error_count()} error(s)), so the intake arm below would prove "
            "nothing"
        )
    try:
        _intake_request(DocumentKind.INTAKE_FORM)
    except ValidationError:
        pass
    else:
        return CheckOutcome.failed(
            "the write-back request accepted an intake_form document; intake "
            "medications and allergies now have a path into the chart (D2, invariant 5)"
        )
    return CheckOutcome.ok(
        "added surfaces with a document side only; no merge symbol exists and the "
        "write-back request refuses an intake document"
    )


@check("chk.e_omitted_has_a_chart_side_only")
def e_omitted_has_a_chart_side_only(case: Case) -> CheckOutcome:
    """
    The chart records it and the form does not. There is no page to cite, so
    a document citation on an `omitted` discrepancy points at something that
    is not there and is rejected.
    """
    Discrepancy(
        category=DiscrepancyCategory.ALLERGY,
        kind=DiscrepancyKind.OMITTED,
        subject="Fish (substance)",
        chart_value="Fish (substance)",
        chart_citation=_CHART_CITATION,
    )
    try:
        Discrepancy(
            category=DiscrepancyCategory.ALLERGY,
            kind=DiscrepancyKind.OMITTED,
            subject="Fish (substance)",
            chart_value="Fish (substance)",
            chart_citation=_CHART_CITATION,
            document_citation=_DOC_CITATION,
        )
    except ValidationError:
        pass
    else:
        return CheckOutcome.failed(
            "`omitted` accepted a document citation for a value that is not on the page"
        )

    expected = [
        text
        for scenario_id in case.scenario_ids()
        for text in samples.conversion(scenario_id).expected_discrepancies
        if "omits" in text
    ]
    if not expected:
        return CheckOutcome.failed(
            "the named scenarios record no omission; the sample that exercises this "
            "has changed or been dropped"
        )
    return CheckOutcome.ok(f"omitted keeps the chart side only; {len(expected)} in the samples")


@check("chk.e_changed_carries_both_sides")
def e_changed_carries_both_sides(case: Case) -> CheckOutcome:
    """A dose change names both readings, cites both, and is not equality."""
    Discrepancy(
        category=DiscrepancyCategory.MEDICATION,
        kind=DiscrepancyKind.CHANGED,
        subject="amLODIPine",
        document_value="amLODIPine 10 MG",
        chart_value="amLODIPine 2.5 MG Oral Tablet",
        document_citation=_DOC_CITATION,
        chart_citation=_CHART_CITATION,
    )
    for name, kwargs in (
        ("equal values", {"document_value": "same", "chart_value": "same"}),
        ("one side only", {"document_value": "amLODIPine 10 MG", "chart_value": None}),
    ):
        try:
            Discrepancy(
                category=DiscrepancyCategory.MEDICATION,
                kind=DiscrepancyKind.CHANGED,
                subject="amLODIPine",
                document_citation=_DOC_CITATION,
                chart_citation=_CHART_CITATION,
                **kwargs,
            )
        except ValidationError:
            continue
        return CheckOutcome.failed(f"`changed` accepted {name}")

    expected = [
        text
        for scenario_id in case.scenario_ids()
        for text in samples.conversion(scenario_id).expected_discrepancies
        if "the chart product is" in text
    ]
    if not expected:
        return CheckOutcome.failed("the named scenarios record no dose change")
    return CheckOutcome.ok(
        f"changed needs both sides and both citations; {len(expected)} in samples"
    )


@check("chk.e_derived_rendering_is_not_a_discrepancy")
def e_derived_rendering_is_not_a_discrepancy(case: Case) -> CheckOutcome:
    """
    The chart holds `Fexofenadine hydrochloride 30 MG Oral Tablet`; the form
    says `Fexofenadine hydrochloride`, because that is what a patient writes.
    The factory declares that `derived` (D21) and it produces a chart-truth
    note — never a discrepancy.

    Three things, because any one alone could pass while the rule was broken:
    every derived field produces a note; no derived field is named by a
    perturbation (a perturbation is the only thing that may disagree with the
    chart); and the conversion invents no discrepancy beyond the ones the
    perturbations declare.
    """
    derived_total = 0
    for scenario_id in case.scenario_ids():
        conversion = samples.conversion(scenario_id)
        ground_truth = samples.ground_truth(scenario_id)

        derived_paths = {
            record["field_path"]
            for record in ground_truth["fields"]
            if record.get("rendering") == "derived"
        }
        noted_paths = {
            note.field_path for note in conversion.chart_truth if note.rendering == "derived"
        }
        if derived_paths - noted_paths:
            return CheckOutcome.failed(
                f"{scenario_id}: derived fields {sorted(derived_paths - noted_paths)[:5]} "
                "produced no chart-truth note; the divergence is neither surfaced nor "
                "recorded"
            )
        derived_total += len(derived_paths)

        perturbed_paths: set[str] = set()
        declared = 0
        for perturbation in ground_truth.get("perturbations", []):
            perturbed_paths.update(perturbation.get("field_paths") or ())
            if perturbation.get("expected_discrepancy"):
                declared += 1

        collision = derived_paths & perturbed_paths
        if collision:
            return CheckOutcome.failed(
                f"{scenario_id}: {sorted(collision)} are `derived` renderings and are "
                "also named by a perturbation; a re-expression is being treated as a "
                "deliberate disagreement (D21)"
            )
        if len(conversion.expected_discrepancies) != declared:
            return CheckOutcome.failed(
                f"{scenario_id}: {len(conversion.expected_discrepancies)} expected "
                f"discrepancies against {declared} perturbations that declare one; the "
                "conversion is inventing discrepancies the factory did not"
            )
    if derived_total == 0:
        return CheckOutcome.failed(
            "no `derived` rendering in the named scenarios; the false-positive arm proved nothing"
        )
    return CheckOutcome.ok(
        f"{derived_total} derived renderings, each recorded as chart truth and none of "
        "them scored as a discrepancy"
    )


@check("chk.e_allergen_text_survives_verification")
def e_allergen_text_survives_verification(case: Case) -> CheckOutcome:
    """
    CRITICAL, and Week 1's defect re-covered on the new path: an allergen is
    surfaced with the text the page carries, cited to that text. A placeholder
    standing in for an uncoded allergen — Week 1's "(unspecified allergen)" —
    must never be the value of a located field, because a located field is
    exactly what renders as verified.
    """
    placeholders = ("(unspecified allergen)", "unspecified allergen", "unknown allergen", "n/a")
    checked = 0
    for scenario_id in case.scenario_ids():
        form = samples.intake(scenario_id)
        if not form.allergies:
            return CheckOutcome.failed(f"{scenario_id}: no allergy rows to check")
        for allergy in form.allergies:
            substance = allergy.substance
            if substance.status is not FieldStatus.LOCATED:
                continue
            checked += 1
            value = str(substance.value or "")
            if not value.strip():
                return CheckOutcome.failed(
                    f"{scenario_id}: allergy row {allergy.row_index} is located with no text"
                )
            if value.strip().lower() in placeholders:
                return CheckOutcome.failed(
                    f"{scenario_id}: allergy row {allergy.row_index} renders as {value!r} "
                    "and would pass verification; that is findings.md's uncoded-allergy "
                    "defect back on the Week 2 path"
                )
            citation = substance.citation
            if citation is None or citation.quote_or_value != value:
                return CheckOutcome.failed(
                    f"{scenario_id}: allergy row {allergy.row_index} cites "
                    f"{citation.quote_or_value if citation else None!r} but presents "
                    f"{value!r}"
                )
    if checked == 0:
        return CheckOutcome.failed("no located allergen rows; the check proved nothing")
    return CheckOutcome.ok(f"{checked} allergens carry their own text and cite it")


@check("chk.e_discrepancy_sides_are_type_checked")
def e_discrepancy_sides_are_type_checked(case: Case) -> CheckOutcome:
    """
    Invariant 3 at the one place two sources are held side by side: the
    document side is a document citation, the chart side a chart citation,
    and a guideline has no opinion about what this patient's chart records.
    """
    del case
    guideline = Citation(
        source_type=SourceType.GUIDELINE,
        source_id="ada-2026",
        page_or_section="Section 6: Glycemic Targets",
        field_or_chunk_id="ada-2026-s6-0004",
        quote_or_value="An A1C goal of <7% is appropriate for many nonpregnant adults.",
    )
    for side, kwargs in (
        ("document_citation", {"document_citation": guideline, "chart_citation": _CHART_CITATION}),
        ("chart_citation", {"document_citation": _DOC_CITATION, "chart_citation": guideline}),
        (
            "chart_citation<-document",
            {"document_citation": _DOC_CITATION, "chart_citation": _DOC_CITATION},
        ),
    ):
        try:
            Discrepancy(
                category=DiscrepancyCategory.MEDICATION,
                kind=DiscrepancyKind.CHANGED,
                subject="amLODIPine",
                document_value="amLODIPine 10 MG",
                chart_value="amLODIPine 2.5 MG Oral Tablet",
                **kwargs,
            )
        except ValidationError:
            continue
        return CheckOutcome.failed(f"a discrepancy accepted the wrong source type on {side}")
    return CheckOutcome.ok("both sides of a discrepancy are type-checked against their role")


@check("chk.e_nkda_is_distinct_from_an_empty_list")
def e_nkda_is_distinct_from_an_empty_list(case: Case) -> CheckOutcome:
    """
    A ticked "no known drug allergies" box is an assertion; an empty allergy
    table is an absence. They are different fields because a clinician needs
    to tell them apart, and collapsing them turns "the patient did not fill
    this in" into "the patient has no allergies".
    """
    for scenario_id in case.scenario_ids():
        form = samples.intake(scenario_id)
        box = form.no_known_drug_allergies
        if box.status is FieldStatus.LOCATED and box.value is True and form.allergies:
            return CheckOutcome.failed(
                f"{scenario_id}: the no-known-drug-allergies box is ticked and "
                f"{len(form.allergies)} allergies are listed"
            )
        if box.status is FieldStatus.MISSING and box.value is not None:
            return CheckOutcome.failed(f"{scenario_id}: a missing checkbox carried a value")
    names = set(type(samples.intake(samples.INTAKE_SCENARIOS[0])).model_fields)
    if "no_known_drug_allergies" not in names:
        return CheckOutcome.failed(
            "the no-known-drug-allergies box is no longer its own field; an empty list "
            "now has to stand for an assertion"
        )
    return CheckOutcome.ok("the assertion and the absence remain separate fields")


@check("chk.e_wrapped_or_truncated_is_located_not_a_discrepancy")
def e_wrapped_or_truncated_is_located_not_a_discrepancy(case: Case) -> CheckOutcome:
    """
    D21: all four renderings map to `located` — each is genuinely on the page
    — and everything but `verbatim` also produces a chart-truth note. A
    truncated field is located: the ellipsised string is what the page says,
    and a citation quoting it is honest. What is lost goes to chart truth, not
    into the value and not into a discrepancy.
    """
    seen: dict[str, int] = {}
    for scenario_id in case.scenario_ids():
        conversion = samples.conversion(scenario_id)
        ground_truth = samples.ground_truth(scenario_id)
        perturbed_paths: set[str] = set()
        for perturbation in ground_truth.get("perturbations", []):
            perturbed_paths.update(perturbation.get("field_paths") or ())

        for note in conversion.chart_truth:
            if note.rendering is None:
                continue
            seen[note.rendering] = seen.get(note.rendering, 0) + 1
            if note.rendering == "verbatim":
                return CheckOutcome.failed(
                    f"{scenario_id}: {note.field_path} is verbatim and still produced a "
                    "chart-truth note; the page says exactly what the chart holds"
                )
            if note.field_path in perturbed_paths:
                return CheckOutcome.failed(
                    f"{scenario_id}: {note.field_path} ({note.rendering}) is also named "
                    "by a perturbation; a rendering artefact is being reported as a "
                    "clinical disagreement"
                )
        for record in ground_truth["fields"]:
            if record.get("rendering") in ("wrapped", "truncated") and record.get("bbox") is None:
                return CheckOutcome.failed(
                    f"{scenario_id}: {record['field_path']} is {record['rendering']} with no "
                    "bbox; D21 makes all four renderings located"
                )
    if not seen:
        return CheckOutcome.failed("no non-verbatim renderings in the named scenarios")
    return CheckOutcome.ok(
        f"non-verbatim renderings produce chart-truth notes and no discrepancies ({seen})"
    )
