"""
Category C — locate / bounding-box grounding.

The deterministic locate step and the "unlocatable means unsupported" rule
are project mechanisms, not framework behaviour, so they get their own
category. C3 is the critical one: it is the case that fails when an
unlocated value is let through as fact.
"""

from __future__ import annotations

from pydantic import ValidationError

from app.models.w2 import (
    BBox,
    Citation,
    ExtractedField,
    FieldStatus,
    PresentedFact,
    SourceType,
    UnsupportedFieldError,
    UnsupportedReason,
)
from evals.w2.gate import samples
from evals.w2.gate.models import Case, CheckOutcome
from evals.w2.gate.registry import check

_BBOX = BBox(page=1, x0=230.0, y0=396.3, x1=275.6, y1=407.2)
_EXTRACTION_ID = "w2x-8f4c1d9a3b6e05f27c0a19d4e8b73612"


def _located_citation() -> Citation:
    return Citation.located_document(
        source_id="lab-clean-001",
        field_path="results.cmp.glucose.value",
        quote="89",
        bbox=_BBOX,
        extraction_id=_EXTRACTION_ID,
    )


def _unlocated_citation() -> Citation:
    return Citation.unlocated_document(
        source_id="lab-clean-001",
        field_path="results.cmp.glucose.value",
        quote="89",
        page=1,
        extraction_id=_EXTRACTION_ID,
    )


@check("chk.c_located_requires_a_bbox")
def c_located_requires_a_bbox(case: Case) -> CheckOutcome:
    """
    `located` is not a label a caller can apply: it requires a document
    citation with a bbox, and D3 makes the locate step the only source of one.
    Also asserts the positive direction over the samples, so the check fails
    both when the rule is loosened and when locate stops producing boxes.
    """
    try:
        ExtractedField[str](status=FieldStatus.LOCATED, value="89", citation=_unlocated_citation())
    except ValidationError:
        pass
    else:
        return CheckOutcome.failed(
            "a field was marked located while citing a document with no bbox; "
            "invariant 4's requirement has been removed"
        )

    located = 0
    for scenario_id in case.scenario_ids():
        for record in samples.field_records(scenario_id):
            if record.get("status") != "present" or record.get("bbox") is None:
                continue
            located += 1
    if located == 0:
        return CheckOutcome.failed("no located ground-truth fields; the check proved nothing")
    return CheckOutcome.ok(f"located requires a bbox, and {located} ground-truth fields carry one")


@check("chk.c_unlocated_is_never_a_fact")
def c_unlocated_is_never_a_fact(case: Case) -> CheckOutcome:
    """
    CRITICAL. A value the model read and code could not place is not
    presentable, is not a `PresentedFact`, and is disclosed as
    `not_located_on_page` with what was read kept under a name that says it
    is unverified.
    """
    del case
    field = ExtractedField[str].unlocated("89", citation=_unlocated_citation())

    if field.is_presentable:
        return CheckOutcome.failed(
            "an unlocated field reports itself presentable; PRESENTABLE_STATUSES has "
            "been widened and an unlocated value can now be stated as fact (invariant 4)"
        )
    if field.as_presented_fact() is not None:
        return CheckOutcome.failed("as_presented_fact() returned a fact for an unlocated field")
    try:
        field.require_presented_fact()
    except UnsupportedFieldError:
        pass
    else:
        return CheckOutcome.failed("require_presented_fact() did not raise on an unlocated field")

    disclosure = field.as_unsupported("results.cmp.glucose.value")
    if disclosure is None:
        return CheckOutcome.failed(
            "an unlocated field produced no disclosure record; it would be neither "
            "shown nor disclosed"
        )
    if disclosure.reason is not UnsupportedReason.NOT_LOCATED_ON_PAGE:
        return CheckOutcome.failed(
            f"unlocated disclosed as {disclosure.reason.value}, not not_located_on_page"
        )
    if disclosure.unverified_value != "89":
        return CheckOutcome.failed(
            "the unlocated reading was dropped instead of disclosed as unverified"
        )
    if disclosure.citation is not None and disclosure.citation.bbox is not None:
        return CheckOutcome.failed("a not_located_on_page disclosure carried a bbox")

    try:
        PresentedFact[str](value="89", citation=_unlocated_citation())
    except ValidationError:
        return CheckOutcome.ok(
            "unlocated is not presentable, not a fact, and disclosed as "
            "not_located_on_page with the reading marked unverified"
        )
    return CheckOutcome.failed(
        "PresentedFact accepted a citation with no bbox; the type is forgeable and "
        "an unlocated value can be presented as fact"
    )


@check("chk.c_illegible_is_disclosed_without_a_guess")
def c_illegible_is_disclosed_without_a_guess(case: Case) -> CheckOutcome:
    """
    An illegible field carries no value at all — not the chart's, not a
    plausible one. The bbox may be present: it is the ink, and evidence that
    something is there.
    """
    ink = Citation(
        source_type=SourceType.DOCUMENT,
        source_id="intake-missing-allergy-006",
        page_or_section="1",
        field_or_chunk_id="patient.city_state_zip",
        quote_or_value="",
        bbox=_BBOX,
        extraction_id=_EXTRACTION_ID,
    )
    field = ExtractedField[str].illegible(citation=ink)
    if field.value is not None:
        return CheckOutcome.failed("an illegible field carried a value")
    disclosure = field.as_unsupported("patient.city_state_zip")
    if disclosure is None or disclosure.reason is not UnsupportedReason.ILLEGIBLE_ON_PAGE:
        return CheckOutcome.failed("an illegible field was not disclosed as illegible_on_page")
    if disclosure.unverified_value is not None:
        return CheckOutcome.failed(
            f"an illegible field disclosed a guessed value {disclosure.unverified_value!r}; "
            "nothing was read, so there is nothing to disclose"
        )

    # And the same, end to end, on the sample that actually scribbles a field out.
    found = False
    for scenario_id in case.scenario_ids():
        form = samples.intake(scenario_id)
        if form.demographics.city_state_zip.status is FieldStatus.ILLEGIBLE:
            found = True
            if form.demographics.city_state_zip.value is not None:
                return CheckOutcome.failed(
                    f"{scenario_id}: the scribbled-out field came back with a value"
                )
    if not found:
        return CheckOutcome.failed(
            "no illegible field in the named scenarios; the end-to-end arm proved nothing"
        )
    return CheckOutcome.ok("illegible carries no value and discloses no guess")


@check("chk.c_bbox_and_page_agree")
def c_bbox_and_page_agree(case: Case) -> CheckOutcome:
    """
    A citation cannot name one page and point at another, and cannot point
    past the end of the document it cites.
    """
    try:
        Citation(
            source_type=SourceType.DOCUMENT,
            source_id="lab-multipage-003",
            page_or_section="1",
            field_or_chunk_id="results.iron_vitamins.ferritin.value",
            quote_or_value="6",
            bbox=BBox(page=2, x0=1.0, y0=1.0, x1=2.0, y1=2.0),
            extraction_id=_EXTRACTION_ID,
        )
    except ValidationError:
        pass
    else:
        return CheckOutcome.failed("a citation naming page 1 with a bbox on page 2 was accepted")

    checked = 0
    for scenario_id in case.scenario_ids():
        extraction = samples.lab(scenario_id)
        pages = extraction.document.page_count
        for result in extraction.results:
            citation = result.citation
            if citation is None or citation.bbox is None:
                continue
            checked += 1
            if citation.bbox.page > pages:
                return CheckOutcome.failed(
                    f"{scenario_id}: {result.test_key} cites page {citation.bbox.page} of a "
                    f"{pages}-page document"
                )
            declared = (citation.page_or_section or "").strip()
            if declared.isdigit() and int(declared) != citation.bbox.page:
                return CheckOutcome.failed(
                    f"{scenario_id}: {result.test_key} says page {declared}, bbox is on "
                    f"page {citation.bbox.page}"
                )
    if checked == 0:
        return CheckOutcome.failed("no located citations to check")
    return CheckOutcome.ok(f"{checked} citations agree with their bbox and stay in bounds")
