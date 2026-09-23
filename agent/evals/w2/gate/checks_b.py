"""
Category B — extraction robustness, and category C's OCR-path cases.

These are the first cases in the suite that grade a **model**, not a
contract. Most run in `MODE_RECORDED`: the worker is driven by
`fixtures/transcriptions/`, real claude-opus-5 output recorded against these
exact PDFs and replayed with no network. A case that says "the model did not
invent a value" is worth nothing run against the ground-truth stub, which
hands the worker the right answer — so every case here names its mode, and
the ones that make a claim about extraction use the recording.

Scanned samples need tesseract. When it is missing these checks FAIL rather
than skip: a scanned-page case that quietly passes on a machine with no OCR
is exactly the silent pass the gate exists to prevent, and the CI image
installs it.
"""

from __future__ import annotations

from typing import Any

from app.models.w2 import (
    ExtractionStatus,
    FieldStatus,
    NumericLabValue,
    UnsupportedReason,
)
from evals.w2.gate import samples
from evals.w2.gate.models import Case, CheckOutcome
from evals.w2.gate.registry import check

SCANNED_HEAVY = "lab-scanned-heavy-abnormal-002"
SCANNED_LIGHT = "intake-scanned-light-med-005"
MULTIPAGE = "lab-multipage-003"
CLEAN_LAB = "lab-clean-001"
ILLEGIBLE = "intake-missing-allergy-006"


def _needs_tesseract() -> CheckOutcome | None:
    if samples.tesseract_is_available():
        return None
    return CheckOutcome.failed(
        "tesseract is not installed, so the OCR path cannot run and this scanned-page "
        "case cannot be scored. It fails rather than skips: a scanned case that goes "
        "green on a machine with no OCR is a silent pass. Install tesseract-ocr — the "
        "CI image does."
    )


def _lab(sample: str, mode: str = samples.MODE_RECORDED):
    report = samples.extraction(sample, mode)
    return report, report.output.payload


def _gt_row(sample: str, test_key: str, attr: str) -> dict:
    """
    The ground-truth record for one analyte attribute.

    Resolved by `test_key`, not by `panel.test_key`: the extraction carries
    the panel name as PRINTED (`comprehensive_metabolic_panel`) while the
    factory keys it (`cmp`). Matching on the printed name silently found
    nothing and made every row look missing.
    """
    suffix = f".{test_key}.{attr}"
    for record in samples.field_records(sample):
        path = record["field_path"]
        if path.startswith("results.") and path.endswith(suffix):
            return record
    return {}


def _same_reading(read: str, drawn: str) -> bool:
    """
    Does the model's reading denote what the page draws?

    Uses the locate step's own `normalize.same_number`, not a string
    comparison: `4` and `4.0` are one value, which is exactly what D24's
    `numeric` tier exists to say, and asserting on the spelling here would
    contradict the policy the rest of the suite enforces. Falls back to a
    normalised string compare for anything that is not a number.
    """
    from app.extraction.normalize import normalize, same_number

    if read == drawn:
        return True
    try:
        if same_number(read, drawn):
            return True
    except Exception:  # noqa: BLE001 - not a number; fall through to text
        pass
    return normalize(read) == normalize(drawn)


def _walk_located(obj: Any, sample: str, pages: dict[int, Any]) -> tuple[int, int, str | None]:
    """(located, fields, first problem) over every ExtractedField under `obj`."""
    from app.models.w2 import ExtractedField

    located = fields = 0
    for name in type(obj).model_fields:
        value = getattr(obj, name)
        if isinstance(value, ExtractedField):
            fields += 1
            if value.status is not FieldStatus.LOCATED:
                continue
            located += 1
            box = value.citation.bbox if value.citation else None
            if box is None:
                return located, fields, f"{sample}: {name} is located with no box"
            page = pages.get(box.page)
            if page is None:
                return (
                    located,
                    fields,
                    (f"{sample}: {name} is boxed on page {box.page}, which does not exist"),
                )
            if box.x1 > page["width"] + 1 or box.y1 > page["height"] + 1:
                return located, fields, f"{sample}: {name} box falls outside the page"
        elif isinstance(value, list):
            for item in value:
                if hasattr(type(item), "model_fields"):
                    a, b, problem = _walk_located(item, sample, pages)
                    located += a
                    fields += b
                    if problem:
                        return located, fields, problem
        elif hasattr(type(value), "model_fields"):
            a, b, problem = _walk_located(value, sample, pages)
            located += a
            fields += b
            if problem:
                return located, fields, problem
    return located, fields, None


@check("chk.b_unit_is_transcribed_not_converted")
def b_unit_is_transcribed_not_converted(case: Case) -> CheckOutcome:
    """
    B1. The unit the model read is the unit the page prints — no conversion
    the document did not perform.

    Asserted on what the model READ, not on what the locate step placed. On a
    heavy scan the OCR text is garbled enough that exact-only matching (D24)
    places no numeric field at all, so every value and unit comes back
    `unlocated`. That is the right outcome and it is W2-B6's subject; the
    question here is a different one — whether the model converted mg/dL to
    mmol/L, which it can do whether or not code could then find the number.
    """
    del case
    blocked = _needs_tesseract()
    if blocked:
        return blocked
    _, extraction = _lab(SCANNED_HEAVY)
    checked = 0
    for result in extraction.results:
        drawn = str(
            _gt_row(SCANNED_HEAVY, result.test_key, "unit").get("rendered_text", "")
        ).strip()
        read = result.unit.value
        if not drawn or read is None:
            continue
        checked += 1
        if not _same_reading(str(read).strip(), drawn):
            return CheckOutcome.failed(
                f"{result.test_key}: the page prints unit {drawn!r} and the model read "
                f"{str(read)!r}. A converted unit is a value the document never stated, "
                "and the chart has no reference range to contradict it."
            )
    if checked == 0:
        return CheckOutcome.failed("no units read from the page; the check proved nothing")
    return CheckOutcome.ok(
        f"{checked} units transcribed verbatim from a scanned_heavy page, none converted "
        "(mode: real recorded model output)"
    )


@check("chk.b_value_is_not_the_reference_range_bound")
def b_value_is_not_the_reference_range_bound(case: Case) -> CheckOutcome:
    """
    B2. The result column sits beside the reference-range column. The value
    the model read must be the result, not a bound of the range next to it.
    Asserted on the reading rather than on a located fact, for the reason
    given in B1.
    """
    del case
    blocked = _needs_tesseract()
    if blocked:
        return blocked
    _, extraction = _lab(SCANNED_HEAVY)
    checked = 0
    for result in extraction.results:
        value = result.value.value
        if not isinstance(value, NumericLabValue):
            continue
        drawn = str(
            _gt_row(SCANNED_HEAVY, result.test_key, "value").get("rendered_text", "")
        ).strip()
        if not drawn:
            continue
        checked += 1
        if not _same_reading(value.as_text(), drawn):
            range_read = result.reference_range.value
            bounds = (
                [b for b in (range_read.low, range_read.high) if b is not None]
                if range_read is not None
                else []
            )
            hint = (
                f" — and it is a bound of its own reference range {bounds}"
                if value.value in bounds
                else ""
            )
            return CheckOutcome.failed(
                f"{result.test_key}: the page prints {drawn!r} and the model read "
                f"{value.as_text()!r}{hint}"
            )
    if checked == 0:
        return CheckOutcome.failed("no numeric readings to compare")
    return CheckOutcome.ok(
        f"{checked} values on a scanned page are the result, not a range bound "
        "(mode: real recorded model output)"
    )


@check("chk.b_every_row_extracted_once")
def b_every_row_extracted_once(case: Case) -> CheckOutcome:
    """
    B3. A multi-page panel: every row present, none duplicated, and rows on
    the continuation page found. Dropped rows and repeated-entity collapse
    both look like a complete report until you count.
    """
    del case
    _, extraction = _lab(MULTIPAGE)

    # Compared on the PRINTED analyte name, normalised. Neither of the other
    # two identifiers works: the extraction carries the panel name as printed
    # while the factory keys it (`comprehensive_metabolic_panel` vs `cmp`),
    # and the model slugs `test_key` off the printed name (`alkaline_
    # phosphatase`) where the factory abbreviates it (`alp`) — 14 of 44 differ
    # that way on this sample alone. The printed name is the one thing both
    # sides read off the same page, which is what makes it the right join.
    def _norm(text: str) -> str:
        return "".join(ch for ch in text.lower() if ch.isalnum())

    expected = {
        _norm(str(r.get("rendered_text") or r.get("value") or ""))
        for r in samples.field_records(MULTIPAGE)
        if r["field_path"].startswith("results.") and r["field_path"].endswith(".name")
    }
    expected.discard("")
    got_names = [
        _norm(str(r.test_name.value)) for r in extraction.results if r.test_name.value is not None
    ]
    got_keys = [r.test_key for r in extraction.results]

    if len(set(got_keys)) != len(got_keys):
        dupes = sorted({g for g in got_keys if got_keys.count(g) > 1})
        return CheckOutcome.failed(f"rows extracted more than once: {dupes}")
    if len(set(got_names)) != len(got_names):
        dupes = sorted({g for g in got_names if got_names.count(g) > 1})
        return CheckOutcome.failed(f"the same analyte name appears on two rows: {dupes}")
    missing = sorted(expected - set(got_names))
    if missing:
        return CheckOutcome.failed(
            f"{len(missing)} analyte(s) printed on the page are absent from the "
            f"extraction: {missing[:6]}"
        )
    extra = sorted(set(got_names) - expected)
    if extra:
        return CheckOutcome.failed(
            f"{len(extra)} extracted analyte(s) are not on the page: {extra[:6]}"
        )
    pages = {
        r.citation.bbox.page
        for r in extraction.results
        if r.citation is not None and r.citation.bbox is not None
    }
    if len(pages) < 2:
        return CheckOutcome.failed(
            f"every located row is on page {pages}; the continuation page was not read"
        )
    return CheckOutcome.ok(
        f"{len(got_names)} analytes, each exactly once and each printed on the page, "
        f"spanning pages {sorted(pages)} (mode: real recorded model output)"
    )


@check("chk.b_illegible_is_not_guessed")
def b_illegible_is_not_guessed(case: Case) -> CheckOutcome:
    """
    B4. A field scribbled out on the page comes back with no value at all —
    not the chart's, not a plausible one — and is disclosed. Hallucination on
    an illegible field is rare and, when it happens, unrecoverable.
    """
    del case
    report = samples.extraction(ILLEGIBLE)
    form = report.output.payload
    field = form.demographics.city_state_zip
    truth = samples.record_for(ILLEGIBLE, "patient.city_state_zip")
    chart_value = str(truth.get("value", ""))

    if field.status is FieldStatus.LOCATED:
        return CheckOutcome.failed(
            f"the scribbled-out field came back located with {field.value!r}; the page "
            "carries ink and no readable text"
        )
    if field.value is not None:
        return CheckOutcome.failed(f"an unreadable field carried a value: {field.value!r}")
    disclosed = [u for u in report.unsupported if u.field_path.endswith("city_state_zip")]
    if not disclosed:
        return CheckOutcome.failed(
            "the unreadable field was dropped rather than disclosed as unsupported"
        )
    for entry in disclosed:
        if entry.unverified_value and chart_value and chart_value in entry.unverified_value:
            return CheckOutcome.failed(
                "the disclosure carries the CHART's value for a field the document does "
                "not show — that is chart truth presented as document content"
            )
    return CheckOutcome.ok(
        f"illegible field disclosed as {disclosed[0].reason.value} with no value guessed "
        f"(mode: real recorded model output)"
    )


@check("chk.b_dates_are_iso_or_unsupported")
def b_dates_are_iso_or_unsupported(case: Case) -> CheckOutcome:
    """
    B5. Every extracted date is either the date the page shows, parsed, or
    unsupported. Never a guess at the day/month order.
    """
    del case
    checked = 0
    for sample in (CLEAN_LAB, MULTIPAGE):
        _, extraction = _lab(sample)
        for result in extraction.results:
            field = result.collection_date
            if field.status is FieldStatus.MISSING:
                continue
            checked += 1
            if field.status is FieldStatus.LOCATED and field.value is None:
                return CheckOutcome.failed(f"{sample}/{result.test_key}: located with no date")
            if field.value is not None and not hasattr(field.value, "isoformat"):
                return CheckOutcome.failed(
                    f"{sample}/{result.test_key}: collection_date is "
                    f"{type(field.value).__name__}, not a date"
                )
    if checked == 0:
        return CheckOutcome.failed("no collection dates to check")
    return CheckOutcome.ok(f"{checked} collection dates: parsed to a date or disclosed")


@check("chk.b_ocr_values_match_the_clean_layout")
def b_ocr_values_match_the_clean_layout(case: Case) -> CheckOutcome:
    """
    B6. On an image-only PDF every value is EITHER presented as fact and equal
    to what the clean layout draws, OR disclosed as unsupported. What must
    never happen is the third thing: a confident number that differs.

    On this sample the honest outcome is the second one throughout. The OCR
    text is garbled, exact-only matching (D24) places no numeric, and the
    worker presents nothing and discloses everything. That is the behaviour
    worth pinning: the chart carries no reference ranges, so a wrong number
    with a box behind it would be contradicted by nothing downstream.
    """
    del case
    blocked = _needs_tesseract()
    if blocked:
        return blocked
    report, extraction = _lab(SCANNED_HEAVY)
    if extraction.document.has_text_layer:
        return CheckOutcome.failed(f"{SCANNED_HEAVY} is supposed to have no text layer")

    presented = disclosed = wrong = 0
    for result in extraction.results:
        drawn = str(
            _gt_row(SCANNED_HEAVY, result.test_key, "value").get("rendered_text", "")
        ).strip()
        fact = result.presented_value()
        if fact is not None:
            presented += 1
            if drawn and not _same_reading(fact.value.as_text(), drawn):
                wrong += 1
        elif result.value.value is not None:
            disclosed += 1
    if wrong:
        return CheckOutcome.failed(
            f"{wrong} of {presented} values presented as fact differ from what the clean "
            "layout draws. A wrong number with a box is worse than no number: nothing "
            "downstream can contradict it."
        )
    if presented == 0 and disclosed == 0:
        return CheckOutcome.failed("the scanned report produced neither a fact nor a disclosure")
    not_located = [
        u for u in report.unsupported if u.reason is UnsupportedReason.NOT_LOCATED_ON_PAGE
    ]
    if disclosed and not not_located:
        return CheckOutcome.failed(
            f"{disclosed} values the locate step could not place were dropped rather than disclosed"
        )
    return CheckOutcome.ok(
        f"image-only PDF: {presented} values presented (all matching the clean layout), "
        f"{disclosed} read-but-unplaced and disclosed, {len(not_located)} "
        "not_located_on_page entries — nothing guessed (mode: real recorded model output)"
    )


@check("chk.b_reference_range_text_is_kept")
def b_reference_range_text_is_kept(case: Case) -> CheckOutcome:
    """
    B7. The printed range survives in `ReferenceRange.text` even where the
    bounds parse, because the text is what a citation quotes.
    """
    del case
    _, extraction = _lab(CLEAN_LAB)
    checked = 0
    for result in extraction.results:
        fact = result.reference_range.as_presented_fact()
        if fact is None:
            continue
        checked += 1
        if not fact.value.text or not fact.value.text.strip():
            return CheckOutcome.failed(
                f"{result.test_key}: the range parsed to bounds and lost its printed "
                "text; a citation would quote something the page does not say"
            )
        if str(fact.citation.quote_or_value) != fact.value.text:
            return CheckOutcome.failed(
                f"{result.test_key}: the citation quotes "
                f"{fact.citation.quote_or_value!r} but the range text is "
                f"{fact.value.text!r}"
            )
    if checked == 0:
        return CheckOutcome.failed("no located reference ranges to check")
    return CheckOutcome.ok(f"{checked} reference ranges keep their printed text and cite it")


@check("chk.b_unparseable_output_fails_with_no_payload")
def b_unparseable_output_fails_with_no_payload(case: Case) -> CheckOutcome:
    """
    B10. When the model returns something the schema cannot read, the
    extraction is `failed` with `model_output_invalid` and NO payload. D7
    forbids returning a partial extraction beside a non-complete status, and
    unparseable structured output is a measured failure mode for this task.
    """
    del case
    import asyncio

    from structlog.testing import capture_logs

    from app.models.w2 import ExtractionFailureReason
    from app.workers.intake_extractor import attach_and_extract
    from app.workers.intake_extractor.vision import VisionCallError

    async def broken(_kind, _pdf, _pages):  # noqa: ANN001, ANN202
        raise VisionCallError(
            ExtractionFailureReason.MODEL_OUTPUT_INVALID,
            "the model returned prose where the schema wanted an object",
        )

    with capture_logs():
        report = asyncio.run(
            attach_and_extract(
                samples.ground_truth(CLEAN_LAB)["chart_source"]["patient_uuid"],
                samples.sample_pdf(CLEAN_LAB),
                samples.sample_kind(CLEAN_LAB),
                vision=broken,
            )
        )
    output = report.output
    if output.status is not ExtractionStatus.FAILED:
        return CheckOutcome.failed(f"unparseable output gave status {output.status.value}")
    if output.payload is not None:
        return CheckOutcome.failed(
            "a failed extraction carried a payload; D7 forbids a partial extraction "
            "beside a non-complete status"
        )
    if output.failure_reason is not ExtractionFailureReason.MODEL_OUTPUT_INVALID:
        return CheckOutcome.failed(f"failure_reason was {output.failure_reason}")
    return CheckOutcome.ok(
        "unparseable model output -> status failed, model_output_invalid, no payload"
    )


@check("chk.c_ocr_path_locates_on_a_scanned_page")
def c_ocr_path_locates_on_a_scanned_page(case: Case) -> CheckOutcome:
    """
    C2. On a page that is only pixels, the OCR locate path runs and places
    fields, with boxes inside the page.

    Deliberately not "every value is located". Exact-only matching (D24)
    means a heavy scan's garbled numerics are placed by nothing, which is the
    policy working; what would be a regression is the path placing NOTHING at
    all, because then every scanned document degrades to unsupported and the
    failure looks like caution. Both scanned samples are checked, because the
    light one is where most fields should land.
    """
    del case
    blocked = _needs_tesseract()
    if blocked:
        return blocked

    totals: dict[str, tuple[int, int]] = {}
    for sample in (SCANNED_HEAVY, SCANNED_LIGHT):
        report = samples.extraction(sample)
        pages = {p["number"]: p for p in samples.ground_truth(sample)["pages"]}
        located, fields, problem = _walk_located(report.output.payload, sample, pages)
        if problem:
            return CheckOutcome.failed(problem)
        if located == 0:
            return CheckOutcome.failed(
                f"{sample}: the OCR locate path placed nothing at all. Every scanned "
                "document would degrade to unsupported, and the failure would look "
                "like caution."
            )
        totals[sample] = (located, fields)
    summary = ", ".join(f"{s.split('-')[1]}: {a}/{b} located" for s, (a, b) in totals.items())
    return CheckOutcome.ok(f"OCR locate runs on both scanned samples ({summary}), all in bounds")


@check("chk.c_unlocatable_value_is_disclosed")
def c_unlocatable_value_is_disclosed(case: Case) -> CheckOutcome:
    """
    C6. Whatever the model claims, a value code could not place on the page is
    not presented. Exercised end to end here rather than at the contract: the
    worker's own output must contain no presentable field without a box, and
    every field it could not place must appear in the disclosure list.
    """
    del case
    checked = disclosed = 0
    for sample in (CLEAN_LAB, ILLEGIBLE, MULTIPAGE):
        report = samples.extraction(sample)
        payload = report.output.payload
        for result in getattr(payload, "results", []):
            for name in ("value", "unit", "reference_range", "reported_flag"):
                field = getattr(result, name)
                checked += 1
                if field.is_presentable and (field.citation is None or field.citation.bbox is None):
                    return CheckOutcome.failed(
                        f"{sample}/{result.test_key}.{name} is presentable with no box; "
                        "the model's word became a fact without the locate step agreeing"
                    )
        for entry in report.unsupported:
            disclosed += 1
            if entry.reason is UnsupportedReason.NOT_LOCATED_ON_PAGE:
                if entry.citation is not None and entry.citation.bbox is not None:
                    return CheckOutcome.failed(
                        f"{sample}: {entry.field_path} is disclosed as not-located and "
                        "carries a box"
                    )
    if checked == 0:
        return CheckOutcome.failed("no fields inspected")
    return CheckOutcome.ok(
        f"{checked} extracted fields: nothing presentable without a box; {disclosed} "
        "unsupported fields disclosed"
    )
