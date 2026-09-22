"""
Category F — citation faithfulness and source-type separation.

Invariant 3 is the spine: a guideline citation never stands in for a chart
fact, and never the reverse. The biomedical-RAG artefact is why this cannot
be left to the judge alone — faithfulness scores around 0.98 with *no*
context, because there is nothing to contradict, so a faithfulness number
read without a citation check beside it means nothing.

F6 is the work order's addition: a reranker can return the right chunk while
the quote drifts off it, and `citation_present` cannot see that.
"""

from __future__ import annotations

from pydantic import ValidationError

from app.models.claims import Claim
from app.models.fhir_resources import FhirResourceType
from app.models.w2 import (
    BBox,
    Citation,
    EvidenceChunk,
    FinalAnswer,
    GuidelineClaim,
    GuidelineSource,
    PatientFactClaim,
    RetrievalMethod,
    SourceType,
    citation_from_claim,
    claim_from_citation,
)
from evals.w2.gate import samples
from evals.w2.gate.models import Case, CheckOutcome
from evals.w2.gate.registry import check

_CORRELATION_ID = "4c547101-195d-47b2-bc4b-0db614573052"

_CHUNK = EvidenceChunk(
    chunk_id="ada-2026-s6-0004",
    text=(
        "An A1C goal of less than 7% (53 mmol/mol) without significant hypoglycaemia "
        "is appropriate for many nonpregnant adults."
    ),
    source=GuidelineSource(
        guideline_id="ada-2026",
        title="Standards of Care in Diabetes",
        section="Section 6: Glycemic Goals and Hypoglycemia",
        publisher="American Diabetes Association",
    ),
    retrieval_score=0.81,
    rerank_score=0.94,
    method=RetrievalMethod.HYBRID,
)

_CHART_CITATION = Citation(
    source_type=SourceType.CHART,
    source_id="0b1f4c0e-2d3a-4a5b-8c7d-9e0f1a2b3c4d",
    page_or_section="Observation",
    field_or_chunk_id="valueQuantity.value",
    quote_or_value=7.4,
)

_DOCUMENT_CITATION = Citation.located_document(
    source_id="lab-clean-001",
    field_path="results.cmp.glucose.value",
    quote="89",
    bbox=BBox(page=1, x0=230.0, y0=396.3, x1=275.6, y1=407.2),
    extraction_id="w2x-8f4c1d9a3b6e05f27c0a19d4e8b73612",
)


@check("chk.f_guideline_evidence_cites_guidelines_only")
def f_guideline_evidence_cites_guidelines_only(case: Case) -> CheckOutcome:
    """A guideline claim takes guideline citations and refuses the other two."""
    del case
    citation = _CHUNK.to_citation()
    if citation.source_type is not SourceType.GUIDELINE:
        return CheckOutcome.failed(
            f"EvidenceChunk.to_citation() produced {citation.source_type.value}; the one "
            "method that mints guideline citations is now reusable for chart facts"
        )
    if citation.page_or_section != _CHUNK.source.section:
        return CheckOutcome.failed("a guideline citation lost its section")
    GuidelineClaim(
        statement="Many nonpregnant adults have an A1C goal below 7%.", citations=[citation]
    )

    for name, wrong in (("chart", _CHART_CITATION), ("document", _DOCUMENT_CITATION)):
        try:
            GuidelineClaim(statement="Many adults have an A1C goal below 7%.", citations=[wrong])
        except ValidationError:
            continue
        return CheckOutcome.failed(f"guideline evidence accepted a {name} citation")
    return CheckOutcome.ok("guideline evidence takes guideline citations and nothing else")


@check("chk.f_patient_facts_never_cite_a_guideline")
def f_patient_facts_never_cite_a_guideline(case: Case) -> CheckOutcome:
    """
    A statement about this patient is supported by the chart or by a document
    for this patient. A guideline has no opinion about them.
    """
    del case
    PatientFactClaim(statement="The last A1c was 7.4%.", citations=[_CHART_CITATION])
    PatientFactClaim(
        statement="Glucose on the uploaded report was 89 mg/dL.", citations=[_DOCUMENT_CITATION]
    )
    try:
        PatientFactClaim(
            statement="This patient's A1c goal is below 7%.",
            citations=[_CHUNK.to_citation()],
        )
    except ValidationError:
        pass
    else:
        return CheckOutcome.failed(
            "a patient fact was supported by a guideline citation; that is invariant 3's "
            "exact failure — general advice presented as a fact about this person"
        )
    try:
        PatientFactClaim(
            statement="The A1c was 7.4% and the report says 89 mg/dL.",
            citations=[_CHART_CITATION, _DOCUMENT_CITATION],
        )
    except ValidationError:
        return CheckOutcome.ok(
            "patient facts take chart or document citations, never a guideline, and "
            "never both kinds in one claim (D17)"
        )
    return CheckOutcome.failed(
        "one claim mixed chart and document provenance; D17 makes that two claims, or a "
        "Discrepancy when the point is that they differ"
    )


@check("chk.f_mixed_answer_keeps_the_lists_separate")
def f_mixed_answer_keeps_the_lists_separate(case: Case) -> CheckOutcome:
    """
    An answer that reports a value and then a recommendation carries two
    claims in two lists, each correctly typed. The separation is structural:
    there is no flag on a shared type that an `if` could ignore.
    """
    del case
    answer = FinalAnswer(
        answer="The last A1c was 7.4%. Guidance puts many adults' goal below 7%.",
        patient_facts=[
            PatientFactClaim(statement="The last A1c was 7.4%.", citations=[_CHART_CITATION])
        ],
        guideline_evidence=[
            GuidelineClaim(
                statement="Many nonpregnant adults have an A1C goal below 7%.",
                citations=[_CHUNK.to_citation()],
            )
        ],
        correlation_id=_CORRELATION_ID,
    )
    kinds = {citation.source_type for citation in answer.all_citations()}
    if kinds != {SourceType.CHART, SourceType.GUIDELINE}:
        return CheckOutcome.failed(f"the answer's citations collapsed to {sorted(kinds)}")
    if len(answer.chart_facts) != 1 or answer.document_facts:
        return CheckOutcome.failed("chart and document facts are no longer separable")
    for claim in answer.guideline_evidence:
        if any(c.source_type is not SourceType.GUIDELINE for c in claim.citations):
            return CheckOutcome.failed("a non-guideline citation reached guideline_evidence")
    return CheckOutcome.ok("patient facts and guideline evidence stay in their own lists")


@check("chk.f_every_claim_carries_a_citation")
def f_every_claim_carries_a_citation(case: Case) -> CheckOutcome:
    """
    An uncited clinical claim is not expressible. `min_length=1` on both claim
    types means the render step has nothing to render one from — which is what
    makes "strip the citations" a construction error rather than a quiet
    downgrade in output quality.
    """
    del case
    for name, claim_type in (("patient fact", PatientFactClaim), ("guideline", GuidelineClaim)):
        try:
            claim_type(statement="Glucose was 89 mg/dL.", citations=[])
        except ValidationError:
            continue
        return CheckOutcome.failed(f"an uncited {name} claim was accepted")

    answer = FinalAnswer(
        answer="The last A1c was 7.4%.",
        patient_facts=[
            PatientFactClaim(statement="The last A1c was 7.4%.", citations=[_CHART_CITATION])
        ],
        correlation_id=_CORRELATION_ID,
    )
    if len(answer.all_citations()) != 1:
        return CheckOutcome.failed("all_citations() does not cover every claim")
    return CheckOutcome.ok("no claim type can be constructed without a citation")


@check("chk.f_quote_is_a_substring_of_its_source")
def f_quote_is_a_substring_of_its_source(case: Case) -> CheckOutcome:
    """
    The work order's F6. A citation's `quote_or_value` occurs verbatim in the
    text of the source it names — the retrieved chunk for a guideline, the
    drawn page text for a document field.

    This is the check that survives a reranker returning the right chunk while
    the quote drifts away from it: `citation_present` sees a well-formed
    citation and a chunk id that exists, and cannot tell that the quote is no
    longer in the chunk.
    """
    guideline_citation = _CHUNK.to_citation()
    quote = str(guideline_citation.quote_or_value)
    if quote not in _CHUNK.text:
        return CheckOutcome.failed("a guideline citation's quote is not in the chunk it cites")
    drifted = Citation(
        source_type=SourceType.GUIDELINE,
        source_id=_CHUNK.source.guideline_id,
        page_or_section=_CHUNK.source.section,
        field_or_chunk_id=_CHUNK.chunk_id,
        quote_or_value="An A1C goal of less than 6.5% is appropriate for all adults.",
    )
    if str(drifted.quote_or_value) in _CHUNK.text:
        return CheckOutcome.failed("the negative arm is not negative; the fixture has changed")

    checked = 0
    for scenario_id in case.scenario_ids():
        page = samples.page_text(scenario_id)
        extraction = samples.conversion(scenario_id).extraction
        results = getattr(extraction, "results", [])
        for result in results:
            citation = result.citation
            if citation is None:
                continue
            checked += 1
            drawn = str(citation.quote_or_value)
            if drawn not in page:
                return CheckOutcome.failed(
                    f"{scenario_id}: {result.test_key} quotes {drawn!r}, which is not on "
                    "the page it cites"
                )
    if checked == 0:
        return CheckOutcome.failed(
            "no document citations checked; the substring rule proved nothing"
        )
    return CheckOutcome.ok(
        f"{checked} document quotes are on their page; a guideline quote is in its chunk, "
        "and a drifted quote is detectable"
    )


@check("chk.f_bbox_is_document_only")
def f_bbox_is_document_only(case: Case) -> CheckOutcome:
    """
    A chart has no pages and a guideline chunk has no pixels. A bbox on
    either would mean a coordinate arrived from somewhere that cannot produce
    one, so it is refused rather than ignored (D3).
    """
    del case
    box = BBox(page=1, x0=0.0, y0=0.0, x1=10.0, y1=10.0)
    for source_type, section in (
        (SourceType.CHART, "Observation"),
        (SourceType.GUIDELINE, "Section 6: Glycemic Goals and Hypoglycemia"),
    ):
        try:
            Citation(
                source_type=source_type,
                source_id="x",
                page_or_section=section,
                field_or_chunk_id="y",
                quote_or_value="z",
                bbox=box,
            )
        except ValidationError:
            continue
        return CheckOutcome.failed(f"a {source_type.value} citation accepted a bbox")
    return CheckOutcome.ok("bbox is rejected on chart and guideline citations")


@check("chk.f_extraction_id_is_document_only")
def f_extraction_id_is_document_only(case: Case) -> CheckOutcome:
    """
    A document citation names the run that produced it — D1 puts that id,
    alone, into `procedure_result.comments` — and the other two source types
    are not produced by an extraction, so the field is refused there.
    """
    del case
    extraction_id = "w2x-8f4c1d9a3b6e05f27c0a19d4e8b73612"
    try:
        Citation(
            source_type=SourceType.DOCUMENT,
            source_id="lab-clean-001",
            page_or_section="1",
            field_or_chunk_id="results.cmp.glucose.value",
            quote_or_value="89",
        )
    except ValidationError:
        pass
    else:
        return CheckOutcome.failed(
            "a document citation was accepted with no extraction_id; the value could not "
            "be traced back to the run that produced it"
        )
    for source_type, section in (
        (SourceType.CHART, "Observation"),
        (SourceType.GUIDELINE, "Section 6: Glycemic Goals and Hypoglycemia"),
    ):
        try:
            Citation(
                source_type=source_type,
                source_id="x",
                page_or_section=section,
                field_or_chunk_id="y",
                quote_or_value="z",
                extraction_id=extraction_id,
            )
        except ValidationError:
            continue
        return CheckOutcome.failed(f"a {source_type.value} citation accepted an extraction_id")
    return CheckOutcome.ok("extraction_id is required for documents and refused elsewhere")


@check("chk.f_week1_claims_round_trip")
def f_week1_claims_round_trip(case: Case) -> CheckOutcome:
    """
    Week 1's `Claim` is invariant 2's five fields under other names, and the
    bridge is lossless both ways — including `value=None`, which findings.md
    F-57 requires: a chart record with no usable name is cited as nameless
    and renders UNVERIFIED, never under a placeholder.
    """
    del case
    values: list[str | float | int | None] = ["Fexofenadine hydrochloride", 7.4, 42, None]
    for value in values:
        claim = Claim(
            source_id="0b1f4c0e-2d3a-4a5b-8c7d-9e0f1a2b3c4d",
            field="medicationCodeableConcept.text",
            value=value,
            resource_type=FhirResourceType.MEDICATION_REQUEST,
        )
        citation = citation_from_claim(claim)
        if citation.source_type is not SourceType.CHART:
            return CheckOutcome.failed("a Week 1 claim became a non-chart citation")
        back = claim_from_citation(citation)
        if (back.source_id, back.field, back.value, back.resource_type) != (
            claim.source_id,
            claim.field,
            claim.value,
            claim.resource_type,
        ):
            return CheckOutcome.failed(f"round trip lost information for value={value!r}")
    try:
        claim_from_citation(_DOCUMENT_CITATION)
    except ValueError:
        return CheckOutcome.ok(
            f"{len(values)} claim shapes round-trip, including value=None; a document "
            "citation has no Week 1 equivalent and refuses to invent one"
        )
    return CheckOutcome.failed(
        "a document citation was converted into a Week 1 chart Claim; that invents a "
        "resource_type and is exactly the invariant-3 confusion the bridge forbids"
    )
