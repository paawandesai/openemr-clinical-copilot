"""
Thresholds over the six-sample measurement (tests/intake_extractor_measurement.py).

No model call: the vision step is the ground-truth stub. What is pinned:

* no false discrepancy on a `derived` field, ever;
* discrepancy precision and recall of 1.0 against the factory's declared
  med/allergy perturbations (two, on these samples — the denominator is
  asserted too, so a sample change that removes them fails loudly);
* no fuzzy locate on a numeric / unit / date / flag field;
* text-layer pages locate nearly everything, on the right ink;
* the OCR path locates *something* on each scan (it is measured, not
  promised: the heavy scan is where D24 recorded the recall cost).
"""

from __future__ import annotations

import asyncio
from functools import cache

import pytest

from app.extraction import tesseract_available
from tests.intake_extractor_measurement import SampleMeasurement, format_table, measure_all

requires_tesseract = pytest.mark.skipif(
    not tesseract_available(), reason="tesseract is not installed; the OCR path cannot run"
)


@cache
def measured() -> tuple[SampleMeasurement, ...]:
    return tuple(asyncio.run(measure_all()))


@requires_tesseract
def test_every_sample_completes() -> None:
    assert [m.report.output.status.value for m in measured()] == ["complete"] * 6


@requires_tesseract
def test_no_false_discrepancy_on_derived_fields() -> None:
    rows = measured()
    assert sum(m.derived_fields for m in rows) >= 20  # there is something to get wrong
    assert sum(m.derived_false_discrepancies for m in rows) == 0


@requires_tesseract
def test_discrepancy_precision_and_recall() -> None:
    rows = measured()
    expected = sum(m.expected_discrepancies for m in rows)
    found = sum(m.found_discrepancies for m in rows)
    true_positives = sum(m.true_positives for m in rows)
    assert expected == 2
    assert true_positives == expected == found


@requires_tesseract
def test_no_fuzzy_tier_on_exact_only_fields() -> None:
    assert sum(m.fuzzy_on_exact_only for m in measured()) == 0


@requires_tesseract
def test_text_layer_pages_locate_on_the_right_ink() -> None:
    for m in measured():
        if not m.report.has_text_layer:
            continue
        assert m.located_rate is not None and m.located_rate >= 0.9, m.sample
        assert m.iou_checked and m.iou_ok == m.iou_checked, m.sample


@requires_tesseract
def test_scans_are_located_by_ocr() -> None:
    for m in measured():
        if m.report.has_text_layer:
            continue
        assert m.located > 0, m.sample


@requires_tesseract
def test_cross_check_agreement_is_reported_with_its_denominator() -> None:
    table = format_table(list(measured()))
    assert "cross-check agreement over comparable flags:" in table
    comparable = sum(
        m.report.flag_cross_check.comparable for m in measured() if m.report.flag_cross_check
    )
    disagree = sum(
        m.report.flag_cross_check.disagree for m in measured() if m.report.flag_cross_check
    )
    assert comparable >= 1
    assert disagree == 0
