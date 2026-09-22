#!/usr/bin/env python3
"""
Build the source-grounded answer view's local fixtures from the committed
doc-factory samples.

Work order 11 requirement 4: "For part 1 also support a local fixture mode
that loads a factory sample PDF + a mock FinalAnswer JSON built from its
ground truth." This is the builder for that JSON. It writes, per scenario:

    <out>/<scenario>/document.pdf      the sample PDF, byte-for-byte
    <out>/<scenario>/ground-truth.json the sample ground truth, byte-for-byte
    <out>/<scenario>/answer-view.json  an AnswerView built from them
    <out>/manifest.json                the scenario list, for the fixture page

Run it from `agent/`:

    python scripts/build_ui_fixtures.py

Nothing here invents a coordinate. Every `bbox` traces to a recorded
ground-truth box, and every value traces to `rendered_text` through
`factory_mapping.convert_ground_truth` (D18) — which means the fixture is
subject to the same rules the real pipeline is: a field the factory marks
`absent` or `illegible` arrives as an `UnsupportedField` and cannot be
rendered as fact (invariant 4), and a blank flag column arrives as
`ReportedFlag.not_reported` and never as `normal` (D20).

SCANNED SAMPLES ARE DRAWN IN SCAN SPACE
---------------------------------------
Ground truth records `bbox` on the *clean* layout — `coordinates.space` is
`clean_pdf` in all six samples — and for a scanned scenario the file that
gets rendered is the scan, which is a JPEG of that layout at some DPI, with
skew. The two spaces are related by the affine the factory records in
`transform.pages[n].pdf_points_to_image_px`.

The locate step (D3) matches values against word boxes taken from the
document it was handed, so in production a `bbox` is always already in the
space of the page the viewer will draw on. To keep the fixture the same
shape as production, every box is mapped into the scanned page's own point
space *here*, before any contract object is built — see
`ground_truth_in_document_space`. The viewer therefore has one job, page
space to CSS pixels, and never has to know which samples were scanned.

A skewed rectangle is not a rectangle, so the mapping takes the
axis-aligned hull of the four mapped corners. That is the honest answer for
an axis-aligned `BBox`: at 1.14 degrees of skew on a 270pt-wide header it
adds about 5pt of height, and the alternative would be a box that claims
the ink sits somewhere it does not.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

import pymupdf  # noqa: E402

from app.models.fhir_resources import FhirResourceType  # noqa: E402
from app.models.w2.answer import (  # noqa: E402
    FinalAnswer,
    GuidelineClaim,
    PatientFactClaim,
)
from app.models.w2.answer_view import (  # noqa: E402
    AnswerView,
    PageGeometry,
    ReportedFlagView,
    ViewerDocument,
)
from app.models.w2.citations import BBox, Citation  # noqa: E402
from app.models.w2.extraction import UnsupportedField, UnsupportedReason  # noqa: E402
from app.models.w2.factory_mapping import convert_ground_truth  # noqa: E402
from app.models.w2.intake_form import IntakeFormExtraction  # noqa: E402
from app.models.w2.lab_pdf import LabPdfExtraction  # noqa: E402
from app.models.w2.lab_values import COMPARABLE_REPORTED_FLAGS  # noqa: E402
from app.models.w2.source_types import SourceType  # noqa: E402
from app.models.w2.workers import (  # noqa: E402
    EvidenceChunk,
    GuidelineSource,
    RetrievalMethod,
)

REPO_ROOT = AGENT_ROOT.parent
DEFAULT_SAMPLES = REPO_ROOT / "agent" / "evals" / "w2" / "factory" / "output" / "samples"
DEFAULT_OUT = (
    REPO_ROOT
    / "interface"
    / "modules"
    / "custom_modules"
    / "oe-module-clinical-copilot"
    / "dev"
    / "fixtures"
)

#: A fixed namespace, so a rebuilt fixture keeps its correlation id and a
#: diff of the output shows only what actually changed.
CORRELATION_NAMESPACE = uuid.UUID("5f6d6f63-7501-4b32-9f00-77326669787a")

#: The guideline corpus does not exist yet — hybrid RAG is a later work
#: order — so the fixture's guideline evidence is placeholder text, and says
#: so in every field a chip can display. Writing plausible-looking clinical
#: guidance here would put sentences on a clinician's screen that no
#: guideline ever said, which is the exact failure invariant 3 exists to
#: prevent, only worse for being self-inflicted.
FIXTURE_GUIDELINE = GuidelineSource(
    guideline_id="fixture-guideline-0001",
    title="FIXTURE SOURCE - not clinical guidance",
    section="Placeholder section",
    publisher="Clinical Co-Pilot fixture builder",
    version="0",
    url=None,
)

FIXTURE_CHUNKS = (
    EvidenceChunk(
        chunk_id="fixture-chunk-0001",
        text=(
            "FIXTURE TEXT. This passage exists so the guideline citation path "
            "can be rendered and clicked before a guideline corpus exists. It "
            "is not clinical guidance and states nothing about any patient."
        ),
        source=FIXTURE_GUIDELINE,
        retrieval_score=0.0,
        rerank_score=None,
        method=RetrievalMethod.LEXICAL,
    ),
    EvidenceChunk(
        chunk_id="fixture-chunk-0002",
        text=(
            "FIXTURE TEXT, second chunk. Present so a guideline claim can "
            "carry more than one citation and the chip list can be seen to "
            "group them. Not clinical guidance."
        ),
        source=FIXTURE_GUIDELINE,
        retrieval_score=0.0,
        rerank_score=None,
        method=RetrievalMethod.LEXICAL,
    ),
)


# --- coordinates -------------------------------------------------------------


def scan_space_box(
    box: list[float], mapping: list[float], page_width_pt: float, image_width_px: float
) -> list[float]:
    """
    One clean-layout box -> the same ink on the scanned page, in PDF points.

    `mapping` is the factory's `pdf_points_to_image_px`, `[a, c, e, b, d, f]`,
    which it documents as `image_x = a*x + c*y + e ; image_y = b*x + d*y + f`.
    That lands in image pixels; the scanned page draws that image across its
    whole width, so points are pixels scaled by `page_width_pt /
    image_width_px`.

    All four corners, then the hull: with skew in the mapping, two corners
    would give a box that is neither the ink nor a rectangle.
    """
    a, c, e, b, d, f = mapping
    per_pixel = page_width_pt / image_width_px
    xs: list[float] = []
    ys: list[float] = []
    for x, y in ((box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])):
        xs.append((a * x + c * y + e) * per_pixel)
        ys.append((b * x + d * y + f) * per_pixel)
    return [min(xs), min(ys), max(xs), max(ys)]


def ground_truth_in_document_space(
    ground_truth: dict[str, Any], page_sizes: dict[int, tuple[float, float]]
) -> dict[str, Any]:
    """
    The same ground truth with every `bbox` expressed on the document that
    will actually be rendered.

    A no-op for the four clean scenarios, where the rendered file *is* the
    clean layout. For the two scanned ones it applies
    `transform.pages[n].pdf_points_to_image_px` and the pixel-to-point
    scale, and rewrites the `coordinates` block so the returned object does
    not claim a space it is no longer in.

    The returned dict is a deep copy: the committed file is never touched,
    and `document.sha256` is left alone, so
    `ground_truth_extraction_id()` still derives the same extraction id.
    """
    converted = json.loads(json.dumps(ground_truth))
    transform = converted.get("transform")
    if not transform:
        return converted

    by_page = {page["page"]: page for page in transform["pages"]}
    for field in converted["fields"]:
        box, page_number = field.get("bbox"), field.get("page")
        if box is None or page_number is None:
            continue
        page = by_page[page_number]
        width_pt = page_sizes[page_number][0]
        field["bbox"] = scan_space_box(
            box, page["pdf_points_to_image_px"], width_pt, page["image_size_px"][0]
        )

    converted["coordinates"] = {
        "space": "document_pdf",
        "units": "pt",
        "origin": "top-left",
        "y_axis": "down",
        "note": (
            "Rewritten by agent/scripts/build_ui_fixtures.py: boxes were mapped "
            "from the clean layout onto the rendered document through "
            "transform.pages[n].pdf_points_to_image_px, taking the axis-aligned "
            "hull of the four mapped corners. The committed sample file is "
            "unchanged and is still in clean_pdf space."
        ),
        "mapped_from": ground_truth["coordinates"]["space"],
    }
    return converted


def page_geometry(pdf_path: Path) -> list[PageGeometry]:
    """The rendered document's own page sizes, read from the file itself."""
    with pymupdf.open(pdf_path) as document:
        return [
            PageGeometry(
                page=index + 1,
                width_pt=page.rect.width,
                height_pt=page.rect.height,
                rotation=page.rotation,
            )
            for index, page in enumerate(document)
        ]


# --- citations ---------------------------------------------------------------


def _document_citation(
    field: dict[str, Any], *, source_id: str, extraction_id: str
) -> Citation | None:
    """A located document citation for one ground-truth field record."""
    box, page = field.get("bbox"), field.get("page")
    if box is None or page is None:
        return None
    return Citation.located_document(
        source_id=source_id,
        field_path=field["field_path"],
        quote=field.get("rendered_text", ""),
        bbox=BBox(page=page, x0=box[0], y0=box[1], x1=box[2], y1=box[3]),
        extraction_id=extraction_id,
    )


def _fixture_chart_source_id(scenario_id: str, resource_type: str, key: str) -> str:
    """
    A source id for a chart record the doc-factory describes but does not
    identify.

    Ground truth records what the chart *says* — `origin: "chart"` fields
    and each perturbation's `chart_value` — and no FHIR resource id, because
    the factory generates documents rather than reading the API. A chart
    citation needs one.

    Two ways to get one would both be lies. Reusing the patient UUID would
    put a Patient id where an AllergyIntolerance id belongs: well-formed,
    resolvable, and pointing at the wrong record. Inventing a UUID-shaped
    string would be worse, because nothing on screen would mark it as
    invented. So the id says out loud what it is, and stays stable across
    rebuilds so the fixture diffs cleanly.
    """
    return f"fixture-chart:{scenario_id}:{resource_type}:{key}"


def _chart_citation(
    *, scenario_id: str, resource_type: FhirResourceType, field: str, key: str, value: str
) -> Citation:
    """
    One chart citation. D14: `page_or_section` is the FHIR resource type,
    from the five in tool scope.

    `Patient` is not one of them, and that is deliberate rather than an
    oversight to work around — `FhirResourceType` excludes it because
    ARCHITECTURE.md §3 treats resolving which chart is open as a
    precondition of the workflow, "not a resource a claim is ever attributed
    to". So the demographics the factory took from the chart are not cited
    here; the allergies and medications are, which is also where D2 lives.
    """
    return Citation(
        source_type=SourceType.CHART,
        source_id=_fixture_chart_source_id(scenario_id, resource_type.value, key),
        page_or_section=resource_type.value,
        field_or_chunk_id=field,
        quote_or_value=value,
    )


def _unsupported(field: dict[str, Any], *, source_id: str, extraction_id: str) -> UnsupportedField:
    """
    One `absent` or `illegible` ground-truth record -> its disclosure.

    `unverified_value` stays None for both: neither status means anything
    was read, and the factory's own expectation for its illegible case is
    that "an extractor must report it as illegible, not guess".
    """
    if field["status"] == "absent":
        return UnsupportedField(
            field_path=field["field_path"],
            reason=UnsupportedReason.NOT_PRESENT_IN_DOCUMENT,
        )
    return UnsupportedField(
        field_path=field["field_path"],
        reason=UnsupportedReason.ILLEGIBLE_ON_PAGE,
        citation=_document_citation(field, source_id=source_id, extraction_id=extraction_id),
    )


# --- claims ------------------------------------------------------------------


def _fields_by_path(ground_truth: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {field["field_path"]: field for field in ground_truth["fields"]}


def _lab_claims(
    ground_truth: dict[str, Any], extraction: LabPdfExtraction
) -> list[PatientFactClaim]:
    """
    One claim per row the laboratory actually flagged.

    Rows with a blank flag column are not claimed as normal — that is D20 —
    they appear in `lab_flags`, where the reported and computed flags are
    shown side by side and the outcome says `not_reported`.
    """
    fields = _fields_by_path(ground_truth)
    source_id = extraction.document.document_id
    claims: list[PatientFactClaim] = []

    for result in extraction.results:
        cross_check = result.cross_check_flag()
        if cross_check is None or cross_check.reported not in COMPARABLE_REPORTED_FLAGS:
            continue
        prefix = f"results.{result.panel}.{result.test_key}" if result.panel else result.test_key
        citations = [
            citation
            for suffix in ("value", "unit", "reference_range", "flag", "name")
            if (field := fields.get(f"{prefix}.{suffix}")) is not None
            and (
                citation := _document_citation(
                    field, source_id=source_id, extraction_id=extraction.extraction_id
                )
            )
            is not None
        ]
        if not citations:
            continue
        name = result.test_name.as_presented_fact()
        value = result.value.as_presented_fact()
        unit = result.unit.as_presented_fact()
        if name is None or value is None:
            continue
        printed = getattr(value.value, "value", None)
        shown = printed if printed is not None else getattr(value.value, "text", "")
        claims.append(
            PatientFactClaim(
                statement=(
                    f"The report gives {name.value} as {shown}"
                    f"{' ' + unit.value if unit else ''}, flagged "
                    f"{cross_check.reported.value}."
                ),
                citations=citations,
            )
        )
    return claims


def _lab_header_claims(
    ground_truth: dict[str, Any], extraction: LabPdfExtraction
) -> list[PatientFactClaim]:
    """
    The header the report prints about itself and the patient.

    Present on every lab sample, flagged rows or not — `lab-clean-001` has
    no flagged row at all (D20: 22 rows, every flag column blank, every
    outcome `not_reported`), and without these there would be nothing on it
    to click. These are document facts: what this page says, cited to where
    it says it, with no claim that the chart agrees.
    """
    fields = _fields_by_path(ground_truth)
    source_id = extraction.document.document_id
    extraction_id = extraction.extraction_id

    wanted = (
        ("lab.name", "The report was issued by {}."),
        ("order.accession", "The report carries accession number {}."),
        ("order.collected", "The specimen was collected at {}."),
        ("order.reported", "The report was issued at {}."),
        ("patient.name", "The report names the patient as {}."),
        ("patient.dob", "The report gives the date of birth as {}."),
    )
    claims: list[PatientFactClaim] = []
    for path, template in wanted:
        field = fields.get(path)
        if field is None or field["status"] != "present":
            continue
        citation = _document_citation(field, source_id=source_id, extraction_id=extraction_id)
        if citation is None:
            continue
        claims.append(
            PatientFactClaim(
                statement=template.format(field.get("rendered_text", "")),
                citations=[citation],
            )
        )
    return claims


def _intake_claims(
    ground_truth: dict[str, Any], extraction: IntakeFormExtraction
) -> list[PatientFactClaim]:
    """
    What the form says, stated as what the form says.

    Invariant 5 and D2: intake medications and allergies are
    document-derived facts and are never merged into the chart, not even
    when they match. The wording carries that — "the form lists", never
    "the patient takes" — and the view puts them under a heading that says
    it again.
    """
    fields = _fields_by_path(ground_truth)
    source_id = extraction.document.document_id
    extraction_id = extraction.extraction_id
    claims: list[PatientFactClaim] = []

    def cite(*paths: str) -> list[Citation]:
        found = []
        for path in paths:
            field = fields.get(path)
            if field is None:
                continue
            citation = _document_citation(field, source_id=source_id, extraction_id=extraction_id)
            if citation is not None:
                found.append(citation)
        return found

    concern = extraction.chief_concern.as_presented_fact()
    if concern is not None:
        citations = cite("visit.chief_concern")
        if citations:
            claims.append(
                PatientFactClaim(
                    statement=f"The form gives the reason for the visit as {concern.value}.",
                    citations=citations,
                )
            )

    for medication in extraction.medications:
        name = medication.name.as_presented_fact()
        if name is None:
            continue
        index = medication.row_index
        citations = cite(
            f"medications[{index}].name",
            f"medications[{index}].dose",
            f"medications[{index}].frequency",
            f"medications[{index}].route",
        )
        if not citations:
            continue
        parts = [
            part.value
            for part in (
                medication.dose.as_presented_fact(),
                medication.frequency.as_presented_fact(),
                medication.route.as_presented_fact(),
            )
            if part is not None
        ]
        detail = ", ".join(parts)
        claims.append(
            PatientFactClaim(
                statement=(f"The form lists {name.value}" + (f" - {detail}." if detail else ".")),
                citations=citations,
            )
        )

    for allergy in extraction.allergies:
        substance = allergy.substance.as_presented_fact()
        if substance is None:
            continue
        index = allergy.row_index
        citations = cite(f"allergies[{index}].substance", f"allergies[{index}].reaction")
        if not citations:
            continue
        reaction = allergy.reaction.as_presented_fact()
        claims.append(
            PatientFactClaim(
                statement=(
                    f"The form lists an allergy to {substance.value}"
                    + (f", reaction {reaction.value}." if reaction else ".")
                ),
                citations=citations,
            )
        )
    return claims


def _chart_claims(ground_truth: dict[str, Any]) -> list[PatientFactClaim]:
    """
    The chart side of the split.

    Two sources, both recorded by the factory: allergy rows it printed by
    reading the record (`origin: "chart"`), and the `chart_value` of each
    perturbation, which is the factory saying in as many words "this is what
    the chart holds and the document does not".

    That second one is the whole of D2 and invariant 5 on one screen:
    `intake-missing-allergy-006` has an allergy the chart records and the
    form omits, and `intake-scanned-light-med-005` has a dose the form gives
    differently from the chart's product. Neither is merged. Both appear as
    a chart fact beside a document fact, for a human to resolve.
    """
    scenario_id = ground_truth["scenario"]["scenario_id"]
    claims: list[PatientFactClaim] = []

    for field in ground_truth["fields"]:
        path, value = field["field_path"], field.get("value")
        if field.get("origin") != "chart" or value is None:
            continue
        if not path.startswith("allergies[") or not path.endswith(".allergen"):
            continue
        row = path[len("allergies[") : path.index("]")]
        claims.append(
            PatientFactClaim(
                statement=f"The chart records an allergy to {value}.",
                citations=[
                    _chart_citation(
                        scenario_id=scenario_id,
                        resource_type=FhirResourceType.ALLERGY_INTOLERANCE,
                        field="code",
                        key=row,
                        value=str(value),
                    )
                ],
            )
        )

    for index, perturbation in enumerate(ground_truth.get("perturbations", [])):
        chart_value = perturbation.get("chart_value")
        if chart_value is None:
            continue
        kind = perturbation["kind"]
        if kind == "allergy_discrepancy":
            resource_type, field, statement = (
                FhirResourceType.ALLERGY_INTOLERANCE,
                "code",
                f"The chart records an allergy to {chart_value}, which this form does not list.",
            )
        elif kind == "med_discrepancy":
            resource_type, field, statement = (
                FhirResourceType.MEDICATION_REQUEST,
                "medicationCodeableConcept",
                f"The chart records the medication product as {chart_value}.",
            )
        else:
            # missing_field and illegible_field: the chart value is a
            # demographic, and demographics are not a citable chart section.
            # The document side is already disclosed as unsupported.
            continue
        claims.append(
            PatientFactClaim(
                statement=statement,
                citations=[
                    _chart_citation(
                        scenario_id=scenario_id,
                        resource_type=resource_type,
                        field=field,
                        key=f"perturbation{index}",
                        value=str(chart_value),
                    )
                ],
            )
        )
    return claims


def _guideline_claims() -> list[GuidelineClaim]:
    return [
        GuidelineClaim(
            statement=(
                "FIXTURE EVIDENCE. Placeholder guideline text, shown so the "
                "guideline section and its chips can be verified before a "
                "corpus exists. Not clinical guidance."
            ),
            citations=[
                Citation(
                    source_type=SourceType.GUIDELINE,
                    source_id=chunk.source.guideline_id,
                    page_or_section=chunk.source.section,
                    field_or_chunk_id=chunk.chunk_id,
                    quote_or_value=chunk.text,
                )
                for chunk in FIXTURE_CHUNKS
            ],
        )
    ]


def _lab_flags(extraction: LabPdfExtraction) -> list[ReportedFlagView]:
    """
    Every row that has a value to compute from, flagged or not.

    D20's consequence, made visible: across the six samples this reports
    `not_reported` far more often than it reports agreement, and the view
    shows the denominator rather than a percentage over the rows that
    happened to be comparable.
    """
    views: list[ReportedFlagView] = []
    for result in extraction.results:
        cross_check = result.cross_check_flag()
        name = result.test_name.as_presented_fact()
        if cross_check is None or name is None:
            continue
        views.append(
            ReportedFlagView(
                test_name=name.value,
                panel=result.panel,
                cross_check=cross_check,
                citation=result.reported_flag.citation,
            )
        )
    return views


# --- assembly ----------------------------------------------------------------


def build_answer_view(ground_truth: dict[str, Any], pdf_path: Path) -> AnswerView:
    """One committed sample -> one `AnswerView` the fixture page can render."""
    geometry = page_geometry(pdf_path)
    sizes = {page.page: (page.width_pt, page.height_pt) for page in geometry}
    located = ground_truth_in_document_space(ground_truth, sizes)

    conversion = convert_ground_truth(located)
    extraction = conversion.extraction
    scenario_id = conversion.scenario_id

    if isinstance(extraction, LabPdfExtraction):
        document_claims = [
            *_lab_header_claims(located, extraction),
            *_lab_claims(located, extraction),
        ]
        flags = _lab_flags(extraction)
        headline = "Abnormal results the laboratory flagged on this report"
    else:
        document_claims = _intake_claims(located, extraction)
        flags = []
        headline = "What the uploaded intake form says"

    unsupported = [
        _unsupported(field, source_id=scenario_id, extraction_id=extraction.extraction_id)
        for field in located["fields"]
        if field["status"] in ("absent", "illegible")
    ]

    answer = FinalAnswer(
        answer=(
            f"{headline}. Patient-record facts and guideline evidence are "
            "listed separately below; every statement carries the citation it "
            "rests on, and values that could not be located on their page are "
            "disclosed as unsupported rather than stated."
        ),
        patient_facts=[*_chart_claims(located), *document_claims],
        guideline_evidence=_guideline_claims(),
        unsupported=unsupported,
        correlation_id=str(uuid.uuid5(CORRELATION_NAMESPACE, scenario_id)),
    )

    return AnswerView(
        answer=answer,
        documents=[
            ViewerDocument(document=extraction.document, pages=geometry),
        ],
        evidence_chunks=list(FIXTURE_CHUNKS),
        lab_flags=flags,
    )


def build(samples: Path, out: Path, only: list[str] | None = None) -> list[str]:
    """Build every scenario under `samples` into `out`. Returns what it wrote."""
    scenarios = sorted(path.name for path in samples.iterdir() if path.is_dir())
    if only:
        missing = sorted(set(only) - set(scenarios))
        if missing:
            raise SystemExit(f"no such scenario(s) under {samples}: {', '.join(missing)}")
        scenarios = [scenario for scenario in scenarios if scenario in only]

    out.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    entries: list[dict[str, Any]] = []

    for scenario in scenarios:
        source = samples / scenario
        truth_path = source / f"{scenario}.ground_truth.json"
        ground_truth = json.loads(truth_path.read_text(encoding="utf-8"))
        pdf_path = source / ground_truth["document"]["path"]

        target = out / scenario
        target.mkdir(parents=True, exist_ok=True)
        view = build_answer_view(ground_truth, pdf_path)

        shutil.copyfile(pdf_path, target / "document.pdf")
        shutil.copyfile(truth_path, target / "ground-truth.json")
        (target / "answer-view.json").write_text(
            json.dumps(view.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8"
        )
        written.append(scenario)
        entries.append(
            {
                "scenario_id": scenario,
                "doc_type": ground_truth["scenario"]["doc_type"],
                "quality": ground_truth["scenario"]["quality"],
                "page_count": ground_truth["document"]["page_count"],
                "scanned": ground_truth.get("transform") is not None,
                "claims": len(view.answer.patient_facts) + len(view.answer.guideline_evidence),
                "unsupported": len(view.answer.unsupported),
                "lab_flags": len(view.lab_flags),
            }
        )

    (out / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": view.schema_version,
                "note": (
                    "Generated by agent/scripts/build_ui_fixtures.py from the "
                    "committed doc-factory samples. Not committed; rebuild it "
                    "rather than editing it."
                ),
                "scenarios": entries,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--samples", type=Path, default=DEFAULT_SAMPLES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--scenario", action="append", dest="scenarios")
    args = parser.parse_args(argv)

    if not args.samples.is_dir():
        print(f"no doc-factory samples at {args.samples}", file=sys.stderr)
        return 1
    written = build(args.samples, args.out, args.scenarios)
    for scenario in written:
        print(f"built {args.out / scenario}")
    print(f"{len(written)} scenario(s) -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
