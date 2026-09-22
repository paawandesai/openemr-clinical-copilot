"""
Thresholds on the locate measurement over the doc-factory's six samples.

`tests/locate_measurement.py` produces the numbers; this file decides which
of them are allowed to move. Run the tables yourself with:

    cd agent && python -m tests.locate_measurement

**What is gated and what is only reported.** Every threshold here is on
**clean pages**, because a clean page is read from the PDF's own text
layer: the characters are exact, the run is deterministic, and a
regression can only come from this branch's code. The scanned samples go
through Tesseract, whose output depends on the installed binary's version
and build, so gating their located rate would make an unrelated `brew
upgrade` fail the suite. They are measured and printed, and the two
assertions made about them are properties of the deterministic code rather
than of OCR accuracy: that the number-and-text tiers invent nothing when
the fuzzy tier is off, and that the de-skew estimator recovers the skew
the factory recorded.

Each threshold below states the value measured when it was set, so the
headroom is visible rather than implied.
"""

from __future__ import annotations

import math

import pytest

from app.extraction import (
    EXACT_ONLY_VALUE_CLASSES,
    LocateStatus,
    MatchKind,
    TextSource,
    tesseract_available,
)
from tests.locate_measurement import (
    IOU_FLOOR,
    discover_samples,
    measure_all,
    rate,
    render_tables,
)

# --- clean-page thresholds ------------------------------------------------

#: Row-anchored search must find nearly everything a clean page draws.
#: Measured 99.6% (235/236); the one miss is a genuine ambiguity — the
#: lab-clean-001 sample names "Glucose" on two rows, and both carry mg/dL.
CLEAN_ROW_ANCHORED_LOCATED = 0.98

#: A page-wide search may legitimately return `ambiguous` — a lab report
#: draws `mg/dL` twenty times — but it must never fail to find a value the
#: page actually draws. Measured 0.0%.
CLEAN_NOT_LOCATED_MAX = 0.01

#: Where a clean page's box lands. Measured: median 1.000, min 0.989 over
#: 296 located text fields. Checkboxes are excluded — see `locate_measurement`.
CLEAN_IOU_MEDIAN = 0.99
CLEAN_IOU_MIN = 0.90

#: A value the document does not carry must not get a box. Measured 0/4 on
#: the fields the factory marks absent or illegible, and 0/323 on the
#: one-character decoys.
CLEAN_FALSE_LOCATE_MAX = 0.01

#: The numeric tier, probed with the ground truth's `89.0` against a page
#: that draws `89`. Measured 93.9%; the remainder are values that genuinely
#: appear on two rows and are reported ambiguous.
CLEAN_NUMERIC_LOCATED = 0.90

# --- scanned-page assertions ----------------------------------------------

#: With the fuzzy tier off, every remaining tier is exact or numeric, so a
#: one-character decoy should be found only if OCR happened to produce
#: exactly the decoy's characters. Measured 0/46 and 0/90.
SCANNED_STRICT_FALSE_LOCATE_MAX = 0.02

#: The fuzzy-tier policy, stated as a number: a one-character decoy of a
#: numeric, unit, date or flag field is never located, under **default**
#: settings — not merely with the fuzzy tier switched off. This is exact,
#: not a budget, because the classes in `EXACT_ONLY_VALUE_CLASSES` reach
#: only the exact and numeric tiers, and neither can match a string that
#: differs by a character. Measured 0/242 across all six samples.
EXACT_ONLY_DECOY_FALSE_LOCATE_MAX = 0.0

#: How close the de-skew estimate has to be to the skew the factory
#: recorded. Measured: 1.05 vs 1.14, -0.12 vs -0.13, -0.23 vs -0.24 degrees.
SKEW_TOLERANCE_DEGREES = 0.5


@pytest.fixture(scope="module")
def measurement():
    """One pass over all six samples; OCR is slow, so share it."""
    return measure_all(allow_ocr=tesseract_available())


@pytest.fixture(scope="module")
def clean(measurement):
    def select(probe: str):
        return [o for o in measurement.by_probe(probe) if o.quality == "clean"]

    return select


def test_the_six_samples_are_all_there() -> None:
    samples = discover_samples()
    assert len(samples) == 6
    assert {s.quality for s in samples} == {"clean", "scanned_light", "scanned_heavy"}


def test_clean_pages_are_read_from_the_text_layer(measurement) -> None:
    # If a clean page ever fell through to OCR, every threshold below would
    # be measuring Tesseract instead of this branch.
    clean_ids = {s.scenario_id for s in discover_samples() if s.quality == "clean"}
    readers = {t.source for t in measurement.timings if t.scenario_id in clean_ids}
    assert readers == {TextSource.TEXT_LAYER}


def test_clean_pages_locate_what_they_draw(clean) -> None:
    outcomes = clean("rendered")
    assert len(outcomes) > 300
    assert rate(outcomes, LocateStatus.NOT_LOCATED) <= CLEAN_NOT_LOCATED_MAX


def test_clean_row_anchored_search_resolves_the_ambiguity(clean) -> None:
    anchored = clean("row_anchored")
    page_wide_keys = {(o.scenario_id, o.field_path) for o in anchored}
    page_wide = [o for o in clean("rendered") if (o.scenario_id, o.field_path) in page_wide_keys]
    assert len(anchored) > 200

    # The point of the row anchor: the same fields, far fewer ambiguities.
    assert rate(anchored) >= CLEAN_ROW_ANCHORED_LOCATED
    assert rate(anchored) > rate(page_wide)
    assert rate(anchored, LocateStatus.AMBIGUOUS) < rate(page_wide, LocateStatus.AMBIGUOUS)


def test_clean_boxes_land_on_the_value(clean) -> None:
    ious = sorted(o.iou for o in clean("rendered") if o.iou is not None and o.field_kind == "text")
    assert len(ious) > 250
    assert ious[len(ious) // 2] >= CLEAN_IOU_MEDIAN
    assert ious[0] >= CLEAN_IOU_MIN
    assert all(value >= IOU_FLOOR for value in ious)


def test_clean_row_anchored_boxes_land_on_the_value(clean) -> None:
    located = [
        o
        for o in clean("row_anchored")
        if o.status is LocateStatus.LOCATED and o.iou is not None and o.field_kind == "text"
    ]
    assert located
    assert all(o.iou >= IOU_FLOOR for o in located)


def test_a_value_the_clean_document_does_not_carry_gets_no_box(clean) -> None:
    # Invariant 4, measured two ways: the fields the factory says are
    # absent or illegible, and one-character decoys of fields that are not.
    missing = clean("missing_field")
    assert missing, "the factory samples should contain absent/illegible fields"
    assert rate(missing) == 0.0

    decoys = clean("decoy")
    assert len(decoys) > 100
    assert rate(decoys) <= CLEAN_FALSE_LOCATE_MAX


def test_clean_numeric_normalisation(clean) -> None:
    numeric = clean("numeric_value")
    assert numeric
    assert rate(numeric) >= CLEAN_NUMERIC_LOCATED


def test_a_decoy_of_a_number_unit_date_or_flag_is_never_located(measurement) -> None:
    # The fuzzy-tier policy, measured end to end and with default settings:
    # `3.1` and `8.1` are one character apart and are a normal and a
    # critical potassium, so no near match may ever place one on the other.
    narrow = [
        outcome
        for outcome in measurement.by_probe("decoy")
        if outcome.field_class in EXACT_ONLY_VALUE_CLASSES
    ]
    assert len(narrow) > 100, "the decoy set should cover the narrow classes broadly"
    offenders = [o for o in narrow if o.status is LocateStatus.LOCATED]
    assert rate(narrow) <= EXACT_ONLY_DECOY_FALSE_LOCATE_MAX, (
        f"{len(offenders)} of {len(narrow)} decoys located; first: "
        f"{offenders[0].field_path if offenders else None}"
    )


def test_the_narrow_classes_never_reach_the_fuzzy_tier(measurement) -> None:
    # The same policy from the other side: whatever was located on a
    # numeric, unit, date or flag field came from an exact or numeric
    # match, never a near one.
    for probe in ("rendered", "row_anchored", "numeric_value"):
        for outcome in measurement.by_probe(probe):
            if outcome.value_class in EXACT_ONLY_VALUE_CLASSES and outcome.kind is not None:
                assert outcome.kind is not MatchKind.FUZZY, f"{probe}: {outcome.field_path}"


# --- scanned ---------------------------------------------------------------


requires_tesseract = pytest.mark.skipif(
    not tesseract_available(), reason="tesseract is not installed; the OCR path cannot run"
)


@requires_tesseract
def test_scanned_pages_are_read_by_ocr(measurement) -> None:
    scanned_ids = {s.scenario_id for s in discover_samples() if s.quality != "clean"}
    timings = [t for t in measurement.timings if t.scenario_id in scanned_ids]
    assert timings
    assert all(t.source is TextSource.OCR for t in timings)
    assert all(t.words > 0 for t in timings)


@requires_tesseract
def test_ocr_is_slower_than_a_text_layer_but_not_unboundedly(measurement) -> None:
    # D7 puts extraction at upload time precisely because of this gap;
    # the assertion is a smoke check that OCR has not become pathological.
    ocr = [t.seconds for t in measurement.timings if t.source is TextSource.OCR]
    text = [t.seconds for t in measurement.timings if t.source is TextSource.TEXT_LAYER]
    assert max(text) < min(ocr)
    assert max(ocr) < 30.0


@requires_tesseract
def test_the_deskew_estimate_matches_the_recorded_scan_skew(measurement) -> None:
    recorded: dict[tuple[str, int], float] = {}
    for sample in discover_samples():
        transform = sample.ground_truth.get("transform")
        if not transform:
            continue
        for page in transform["pages"]:
            recorded[(sample.scenario_id, page["page"])] = page["skew_degrees"]
    assert recorded

    for timing in measurement.timings:
        key = (timing.scenario_id, timing.page)
        if key not in recorded:
            continue
        # The factory skews the page by +angle; the estimator recovers the
        # same sign, because both are measured in page space with y down.
        assert math.isclose(timing.skew_degrees, recorded[key], abs_tol=SKEW_TOLERANCE_DEGREES), (
            f"{key}: estimated {timing.skew_degrees:.2f}, recorded {recorded[key]:.2f}"
        )


@requires_tesseract
def test_the_policy_holds_on_scanned_pages_with_declared_classes(measurement) -> None:
    # Same assertion again on the scanned samples alone, and with the field
    # class declared rather than inferred — which is how a schema-driven
    # extractor calls this. Inference and declaration disagreed on exactly
    # one probe across the six samples (`ratio` misread as `rtaio`), so
    # both are measured rather than one standing in for the other.
    for probe in ("decoy", "decoy_declared"):
        narrow = [
            outcome
            for outcome in measurement.by_probe(probe)
            if outcome.quality != "clean" and outcome.field_class in EXACT_ONLY_VALUE_CLASSES
        ]
        assert narrow, f"{probe} should cover the narrow classes on scanned pages"
        assert rate(narrow) <= EXACT_ONLY_DECOY_FALSE_LOCATE_MAX, probe


@requires_tesseract
def test_without_the_fuzzy_tier_ocr_invents_nothing(measurement) -> None:
    # Every false locate on a scanned page comes from the fuzzy tier. That
    # is the trade the tier makes, and a caller that cannot accept it has a
    # switch; what must stay true is that the exact and numeric tiers do
    # not invent boxes just because the reader was OCR.
    strict = measurement.by_probe("decoy_no_fuzzy")
    assert len(strict) > 50
    assert rate(strict) <= SCANNED_STRICT_FALSE_LOCATE_MAX


def test_the_tables_render(measurement) -> None:
    # Cheap guard on the reporting path: the work order's deliverable is
    # this table, and a harness that measures but cannot print is no use.
    tables = render_tables(measurement)
    for heading in ("A. Pages read", "C. Located rate", "F. False locates", "G. Row anchoring"):
        assert heading in tables
