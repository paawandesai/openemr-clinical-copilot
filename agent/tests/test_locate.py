"""
Unit tests for `app.extraction` — normalisation, rows, locate, row anchoring.

Offline: no network, no credentials, no model. The few tests that read a
PDF read the doc-factory's committed clean samples, which are in the
repository. Nothing here needs Tesseract; the OCR path is measured in
`test_locate_measurement.py`, which skips when it is absent.

Most of these build pages by hand through `build_page_words` rather than
rendering a PDF, because the point under test is what `locate` does with a
given arrangement of boxes, and stating the arrangement outright is
clearer than inferring it from a rendering.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.extraction import (
    EXACT_ONLY_VALUE_CLASSES,
    BBox,
    FuzzyMode,
    LocateStatus,
    MatchKind,
    OcrPolicy,
    PageIndex,
    TextSource,
    ValueClass,
    build_page_words,
    classify_value,
    extract_words,
    is_unit,
    locate,
    locate_in_row,
    normalize,
    parse_number,
    same_number,
    split_number_and_unit,
    squash,
)
from app.extraction.words import estimate_baseline_slope

SAMPLES = Path(__file__).resolve().parents[1] / "evals/w2/factory/output/samples"

LINE_HEIGHT = 12.0
ROW_PITCH = 14.0


def page_of(rows: list[list[tuple[str, float, float]]], *, source=TextSource.TEXT_LAYER, page=1):
    """
    Build a page from rows of `(text, x0, width)`, one row per line, laid
    out top to bottom at a fixed pitch.
    """
    items: list[tuple[str, BBox, float]] = []
    for row_index, row in enumerate(rows):
        top = 50.0 + row_index * ROW_PITCH
        for text, x0, width in row:
            items.append(
                (
                    text,
                    BBox(page=page, x0=x0, y0=top, x1=x0 + width, y1=top + LINE_HEIGHT),
                    1.0,
                )
            )
    return build_page_words(page=page, width=612.0, height=792.0, items=items, source=source)


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------


class TestNormalisation:
    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("Glucose", "glucose"),
            ("mg/dL", "MG/DL"),
            ("70 - 99", "70-99"),
            ("a  b\tc\nd", "a b c d"),
            ("Okonjo–Reyes", "Okonjo-Reyes"),
            ("µg/dL", "ug/dL"),
            ("mcg/dL", "ug/dL"),
            ("37°C", "37C"),
            ("mg per dL", "mg/dL"),
            ("mg / dL", "mg/dL"),
            ("50 percent", "50 %"),
            ("Cow’s milk", "Cow's milk"),
            ("soft­hyphen", "softhyphen"),
        ],
    )
    def test_these_are_the_same_text(self, left: str, right: str) -> None:
        assert squash(left) == squash(right)

    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("Sodium", "Na"),
            ("5", "50"),
            ("mg/dL", "mg/mL"),
            ("Glucose", "Glucoses"),
        ],
    )
    def test_these_are_not(self, left: str, right: str) -> None:
        assert squash(left) != squash(right)

    def test_per_is_only_folded_between_units(self) -> None:
        # "per" is an ordinary English word; folding it everywhere would
        # rewrite prose, so it collapses to "/" only between unit atoms.
        assert normalize("one tablet per day") == "one tablet per day"
        assert normalize("12 mg per dL") == "12 mg/dl"

    def test_whitespace_is_not_evidence(self) -> None:
        # PyMuPDF splits handwriting into glyph runs and OCR invents
        # spaces, so the squashed form has to ignore them entirely.
        assert squash("F e m a l e") == squash("Female")

    @pytest.mark.parametrize(
        ("text", "expected"),
        [("5", "5"), ("5.0", "5"), ("5.00", "5"), ("1,234", "1234"), ("-2.5", "-2.5")],
    )
    def test_parse_number(self, text: str, expected: str) -> None:
        parsed = parse_number(text)
        assert parsed is not None
        assert parsed == parse_number(expected)

    @pytest.mark.parametrize("text", ["", "abc", "5,0", "5 mg", "1.2.3", "."])
    def test_parse_number_rejects(self, text: str) -> None:
        # "5,0" is rejected on purpose: telling a decimal comma from a
        # thousands separator needs a locale this code does not have, and
        # guessing wrong turns 1,500 into one and a half.
        assert parse_number(text) is None

    def test_five_and_five_point_zero_zero_are_one_number(self) -> None:
        assert same_number("5", "5.00")
        assert same_number("5.0", "5")
        assert not same_number("5", "50")

    @pytest.mark.parametrize(
        ("text", "number", "unit"),
        [
            ("89 mg/dL", "89", "mg/dl"),
            ("89mg/dL", "89", "mg/dl"),
            ("0.84", "0.84", ""),
            ("90 mL/min/1.73", "90", "ml/min/1.73"),
        ],
    )
    def test_split_number_and_unit(self, text: str, number: str, unit: str) -> None:
        split = split_number_and_unit(text)
        assert split is not None
        assert split[0] == parse_number(number)
        assert split[1] == unit

    def test_split_number_and_unit_on_non_numbers(self) -> None:
        assert split_number_and_unit("Serum") is None

    @pytest.mark.parametrize(
        "unit",
        ["mg/dl", "mmol/l", "ml/min/1.73", "%", "k/ul", "uiu/ml", "ratio", "fl", "g/dl", "u/l"],
    )
    def test_is_unit_accepts_real_units(self, unit: str) -> None:
        assert is_unit(unit)

    @pytest.mark.parametrize(
        "text", ["", "-99", "d2118745", "labadie station suite 85", "-08-30 07:12", "glucose"]
    )
    def test_is_unit_rejects_everything_else(self, text: str) -> None:
        # This is the guard on the number-only fallback: if a CLIA number's
        # tail counted as a unit, "22D2118740" would locate on a stray 22.
        assert not is_unit(text)


# --------------------------------------------------------------------------
# Rows and skew
# --------------------------------------------------------------------------


class TestRows:
    def test_a_flat_page_has_no_slope(self) -> None:
        page = page_of([[("Glucose", 57.0, 32.0), ("89", 270.0, 10.0)]] * 4)
        assert page.baseline_slope == pytest.approx(0.0, abs=1e-9)
        assert len(page.rows) == 4

    def test_a_skewed_page_is_still_four_rows(self) -> None:
        # Across 400 points, a 0.02 slope drifts 8pt — most of a row's
        # height. Assigning rows on raw y would split every printed row and
        # put a test name on a different row from its result.
        slope = 0.02
        items: list[tuple[str, BBox, float]] = []
        for row_index in range(4):
            top = 50.0 + row_index * ROW_PITCH
            for text, x0, width in (
                ("Glucose", 57.0, 32.0),
                ("89", 270.0, 10.0),
                ("mg/dL", 326.0, 24.0),
                ("70 - 99", 414.0, 27.0),
            ):
                drift = slope * (x0 + width / 2.0)
                items.append(
                    (
                        text,
                        BBox(
                            page=1,
                            x0=x0,
                            y0=top + drift,
                            x1=x0 + width,
                            y1=top + drift + LINE_HEIGHT,
                        ),
                        1.0,
                    )
                )
        page = build_page_words(
            page=1, width=612.0, height=792.0, items=items, source=TextSource.OCR
        )
        assert page.baseline_slope == pytest.approx(slope, abs=0.004)
        assert len(page.rows) == 4
        assert [w.text for w in page.row_words(0)] == ["Glucose", "89", "mg/dL", "70 - 99"]

    def test_slope_estimate_needs_two_boxes(self) -> None:
        assert estimate_baseline_slope([]) == 0.0
        assert estimate_baseline_slope([BBox(page=1, x0=0, y0=0, x1=1, y1=1)]) == 0.0


# --------------------------------------------------------------------------
# locate()
# --------------------------------------------------------------------------


class TestLocate:
    def test_one_match_is_located_with_its_box(self) -> None:
        page = page_of([[("Creatinine", 57.0, 39.0), ("0.84", 263.0, 17.0)]])
        result = locate("0.84", page)
        assert result.status is LocateStatus.LOCATED
        assert result.kind is MatchKind.EXACT
        assert result.bbox is not None
        assert result.bbox.as_tuple() == (263.0, 50.0, 280.0, 62.0)
        assert result.best.matched_text == "0.84"

    def test_two_matches_are_ambiguous_and_yield_no_box(self) -> None:
        # The whole point of D3's locate step: it must be able to decline
        # to produce a bbox rather than pick one of two equal answers.
        page = page_of(
            [
                [("Glucose", 57.0, 32.0), ("89", 270.0, 10.0)],
                [("Chloride", 57.0, 32.0), ("89", 270.0, 10.0)],
            ]
        )
        result = locate("89", page)
        assert result.status is LocateStatus.AMBIGUOUS
        assert result.bbox is None
        assert result.best is None
        assert len(result.candidates) == 2
        assert {c.bbox.y0 for c in result.candidates} == {50.0, 64.0}

    def test_absent_value_is_not_located(self) -> None:
        page = page_of([[("Glucose", 57.0, 32.0), ("89", 270.0, 10.0)]])
        result = locate("142", page)
        assert result.status is LocateStatus.NOT_LOCATED
        assert result.candidates == ()
        assert result.kind is None

    def test_empty_value_is_not_located(self) -> None:
        page = page_of([[("Glucose", 57.0, 32.0)]])
        assert locate("   ", page).status is LocateStatus.NOT_LOCATED

    def test_reason_never_repeats_the_value(self) -> None:
        # Invariant 1: no chart content in messages that may be logged.
        page = page_of([[("Glucose", 57.0, 32.0)]])
        assert "Nadia" not in locate("Nadia Okonjo-Reyes", page).reason

    @pytest.mark.parametrize("probe", ["5", "5.0", "5.00", "5.000"])
    def test_numeric_forms_are_one_value(self, probe: str) -> None:
        page = page_of([[("Potassium", 57.0, 40.0), ("5", 270.0, 6.0)]])
        result = locate(probe, page)
        assert result.status is LocateStatus.LOCATED
        assert result.bbox.as_tuple() == (270.0, 50.0, 276.0, 62.0)

    def test_a_value_is_not_found_inside_a_longer_number(self) -> None:
        # "5" must not be located on the 5 of "15": a match has to start
        # where a word starts and end where it ends.
        page = page_of([[("BUN", 57.0, 20.0), ("15", 270.0, 10.0)]])
        assert locate("5", page).status is LocateStatus.NOT_LOCATED

    def test_number_and_unit_across_an_empty_flag_column(self) -> None:
        # A lab row puts a flag column between the result and its units, so
        # "111 mg/dL" is never adjacent on the page. The number-only tier
        # is what finds it, and the box it returns is the number alone.
        page = page_of(
            [
                [
                    ("Glucose", 57.0, 32.0),
                    ("111", 265.0, 15.0),
                    ("H", 306.0, 7.0),
                    ("mg/dL", 326.0, 24.0),
                ]
            ]
        )
        result = locate("111 mg/dL", page)
        assert result.status is LocateStatus.LOCATED
        assert result.kind is MatchKind.NUMBER_ONLY
        assert result.bbox.as_tuple() == (265.0, 50.0, 280.0, 62.0)

    def test_number_and_unit_when_they_are_adjacent(self) -> None:
        # Adjacent on the page means the squashed stream already contains
        # "111mg/dl", so this is an exact match over two words and the
        # weaker numeric tiers are never reached. The box covers both.
        page = page_of([[("Glucose", 57.0, 32.0), ("111", 265.0, 15.0), ("mg/dL", 290.0, 24.0)]])
        result = locate("111 mg/dL", page)
        assert result.kind is MatchKind.EXACT
        assert result.bbox.as_tuple() == (265.0, 50.0, 314.0, 62.0)

    def test_the_numeric_tier_matches_a_differently_written_number(self) -> None:
        # Same unit, number written another way: only the numeric tier can
        # see that "5.00 mmol/L" and a drawn "5 mmol/L" are one value.
        page = page_of([[("Potassium", 57.0, 40.0), ("5", 265.0, 6.0), ("mmol/L", 275.0, 28.0)]])
        result = locate("5.00 mmol/L", page)
        assert result.kind is MatchKind.NUMERIC
        assert result.bbox.as_tuple() == (265.0, 50.0, 303.0, 62.0)

    @pytest.mark.parametrize("probe", ["70 - 94", "2026-08-31 07:12", "22D2118745"])
    def test_no_fallback_to_a_leading_number(self, probe: str) -> None:
        # A range, a date and an identifier all start with a number. Falling
        # back to it would box an unrelated 70, 2026 or 22 and call the
        # whole value located.
        page = page_of(
            [
                [("Glucose", 57.0, 32.0), ("70", 265.0, 10.0), ("mg/dL", 290.0, 24.0)],
                [("Collected", 57.0, 40.0), ("2026", 265.0, 20.0)],
                [("CLIA", 57.0, 20.0), ("22", 265.0, 10.0)],
            ]
        )
        assert locate(probe, page).status is LocateStatus.NOT_LOCATED

    def test_multi_word_span_across_columns(self) -> None:
        page = page_of([[("70", 414.0, 10.0), ("-", 426.0, 3.0), ("99", 431.0, 10.0)]])
        result = locate("70 - 99", page)
        assert result.status is LocateStatus.LOCATED
        assert result.bbox.as_tuple() == (414.0, 50.0, 441.0, 62.0)
        assert len(result.best.line_bboxes) == 1
        assert result.best.matched_text == "70 - 99"

    def test_trailing_punctuation_does_not_hide_a_value(self) -> None:
        page = page_of([[("Everett,", 130.0, 34.0), ("Massachusetts", 168.0, 60.0)]])
        result = locate("Everett", page)
        assert result.status is LocateStatus.LOCATED
        # The box is the whole drawn word, comma included: the locate step
        # reports where the ink is, and does not split a glyph run.
        assert result.bbox.as_tuple() == (130.0, 50.0, 164.0, 62.0)

    def test_a_dash_is_not_edge_punctuation(self) -> None:
        # If "-" were skippable at an edge, "70" would match inside "70-99".
        page = page_of([[("70-99", 414.0, 27.0)]])
        assert locate("70", page).status is LocateStatus.NOT_LOCATED


class TestMultiLineSpans:
    def test_union_box_and_per_line_boxes(self) -> None:
        page = page_of(
            [
                [("Ongoing", 60.0, 34.0), ("primary", 97.0, 32.0)],
                [("fibromyalgia", 60.0, 55.0), ("review", 118.0, 28.0)],
            ]
        )
        result = locate("Ongoing primary fibromyalgia review", page)
        assert result.status is LocateStatus.LOCATED
        # bbox is the union of both lines; line_bboxes has them separately,
        # which is the doc-factory's `line_bboxes` shape.
        assert result.bbox.as_tuple() == (60.0, 50.0, 146.0, 76.0)
        assert len(result.best.line_bboxes) == 2
        assert result.best.line_bboxes[0].as_tuple() == (60.0, 50.0, 129.0, 62.0)
        assert result.best.line_bboxes[1].as_tuple() == (60.0, 64.0, 146.0, 76.0)

    def test_hyphenation_across_a_line_break(self) -> None:
        page = page_of([[("hydrochlo-", 60.0, 48.0)], [("ride", 60.0, 18.0)]])
        result = locate("hydrochloride", page)
        assert result.status is LocateStatus.LOCATED
        assert result.bbox.as_tuple() == (60.0, 50.0, 108.0, 76.0)
        assert len(result.best.line_bboxes) == 2

    def test_a_hyphen_inside_a_line_is_kept(self) -> None:
        # "Okonjo-Reyes" is a hyphenated name, not a broken word: the
        # hyphen only disappears when the next word is on the next row.
        page = page_of([[("Okonjo-Reyes,", 467.0, 60.0), ("MD", 530.0, 18.0)]])
        assert locate("OkonjoReyes", page).status is LocateStatus.NOT_LOCATED
        assert locate("Okonjo-Reyes", page).status is LocateStatus.LOCATED


class TestFuzzyTier:
    def test_a_text_layer_gets_no_near_matches(self) -> None:
        # A text layer reports characters exactly, so a value that is not
        # there character-for-character is not there.
        page = page_of([[("Petrosian", 60.0, 40.0)]], source=TextSource.TEXT_LAYER)
        assert locate("Petroslan", page).status is LocateStatus.NOT_LOCATED

    def test_an_ocr_page_does(self) -> None:
        page = page_of([[("Petroslan", 60.0, 40.0)]], source=TextSource.OCR)
        result = locate("Petrosian", page)
        assert result.status is LocateStatus.LOCATED
        assert result.kind is MatchKind.FUZZY
        assert 0.84 <= result.best.score < 1.0

    def test_fuzzy_can_be_forced_off_for_ocr(self) -> None:
        page = page_of([[("Petroslan", 60.0, 40.0)]], source=TextSource.OCR)
        assert locate("Petrosian", page, fuzzy=FuzzyMode.NEVER).status is (LocateStatus.NOT_LOCATED)

    def test_fuzzy_can_be_forced_on_for_a_text_layer(self) -> None:
        page = page_of([[("Petroslan", 60.0, 40.0)]], source=TextSource.TEXT_LAYER)
        assert locate("Petrosian", page, fuzzy=FuzzyMode.ALWAYS).located

    def test_two_equally_near_matches_are_ambiguous(self) -> None:
        page = page_of(
            [[("Petroslan", 60.0, 40.0)], [("Petrosien", 60.0, 40.0)]], source=TextSource.OCR
        )
        result = locate("Petrosian", page)
        assert result.status is LocateStatus.AMBIGUOUS
        assert len(result.candidates) == 2

    def test_a_clear_winner_among_near_matches_is_located(self) -> None:
        # Unlike the exact tiers, fuzzy scores can separate candidates; a
        # match that is clearly better than the runner-up is not a tie.
        page = page_of(
            [[("Petrosian", 60.0, 40.0)], [("Petro5i4n", 60.0, 40.0)]], source=TextSource.OCR
        )
        result = locate("Petrosian", page)
        assert result.status is LocateStatus.LOCATED
        assert result.bbox.y0 == 50.0


class TestValueClass:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("89", ValueClass.NUMERIC),
            ("0.84", ValueClass.NUMERIC),
            ("5.00", ValueClass.NUMERIC),
            ("89 mg/dL", ValueClass.NUMERIC),
            ("70 - 99", ValueClass.NUMERIC),
            ("0.50 - 1.10", ValueClass.NUMERIC),
            ("2026-08-30 07:12", ValueClass.DATE),
            ("09/14/2026", ValueClass.DATE),
            ("1959-12-28", ValueClass.DATE),
            ("mg/dL", ValueClass.UNIT),
            ("mL/min/1.73", ValueClass.UNIT),
            ("%", ValueClass.UNIT),
            ("H", ValueClass.FLAG),
            ("L", ValueClass.FLAG),
            ("X", ValueClass.FLAG),
            ("Jae222 Dach178", ValueClass.FREE_TEXT),
            ("890 Mills Well", ValueClass.FREE_TEXT),
            ("22D2118740", ValueClass.FREE_TEXT),
            ("Fexofenadine hydrochloride", ValueClass.FREE_TEXT),
            ("Amoxicillin/Clavulanate", ValueClass.FREE_TEXT),
            ("", ValueClass.FREE_TEXT),
        ],
    )
    def test_classification(self, value: str, expected: ValueClass) -> None:
        assert classify_value(value) is expected

    def test_a_garbled_unit_is_still_unit_shaped(self) -> None:
        # OCR reads mg/dL as md/gL. Inference has to keep calling that a
        # unit, or the fuzzy tier would near-match it back onto a units
        # column and pick a row at random.
        assert classify_value("md/gL") is ValueClass.UNIT
        assert classify_value("moml/L") is ValueClass.UNIT

    def test_the_four_narrow_classes_are_the_exact_only_set(self) -> None:
        assert EXACT_ONLY_VALUE_CLASSES == {
            ValueClass.NUMERIC,
            ValueClass.UNIT,
            ValueClass.DATE,
            ValueClass.FLAG,
        }
        assert ValueClass.FREE_TEXT not in EXACT_ONLY_VALUE_CLASSES


class TestFuzzyPolicy:
    """
    A near match is a claim that two strings are the same despite a
    difference. For a number, a unit, a date or a flag, the difference is
    the value — so those classes never reach the fuzzy tier.
    """

    @pytest.mark.parametrize(
        ("drawn", "probe"),
        [
            ("3.1", "8.1"),  # a normal and a critical potassium
            ("mmol/L", "mmol/I"),
            ("2026-09-09", "2026-09-08"),
            ("111", "141"),
        ],
    )
    def test_a_near_miss_on_a_narrow_class_is_not_located(self, drawn: str, probe: str) -> None:
        page = page_of([[("Potassium", 57.0, 40.0), (drawn, 265.0, 30.0)]], source=TextSource.OCR)
        result = locate(probe, page)
        assert result.status is LocateStatus.NOT_LOCATED
        assert result.bbox is None
        assert "fuzzy tier is not used" in result.reason

    def test_the_policy_is_not_a_default_that_fuzzy_mode_can_lift(self) -> None:
        page = page_of([[("3.1", 265.0, 15.0)]], source=TextSource.OCR)
        assert locate("8.1", page, fuzzy=FuzzyMode.ALWAYS).status is LocateStatus.NOT_LOCATED

    def test_free_text_still_reaches_the_fuzzy_tier(self) -> None:
        page = page_of([[("Petroslan", 60.0, 40.0)]], source=TextSource.OCR)
        result = locate("Petrosian", page)
        assert result.located
        assert result.value_class is ValueClass.FREE_TEXT
        assert result.tier == "fuzzy"
        assert 0.84 <= result.score < 1.0

    def test_an_exact_match_on_a_narrow_class_is_unaffected(self) -> None:
        # The policy withholds the fuzzy tier, not the value: a number that
        # really is on the page is still located, by the exact tier.
        page = page_of([[("3.1", 265.0, 15.0)]], source=TextSource.OCR)
        result = locate("3.1", page)
        assert result.located
        assert result.tier == "exact"
        assert result.score == 1.0

    def test_the_numeric_tier_still_works_under_the_policy(self) -> None:
        page = page_of([[("5", 265.0, 6.0)]], source=TextSource.OCR)
        result = locate("5.00", page)
        assert result.located
        assert result.tier == "numeric"
        assert result.value_class is ValueClass.NUMERIC

    def test_a_declared_class_overrides_what_the_string_looks_like(self) -> None:
        # `rtaio` is OCR's reading of the unit `ratio`, and does not look
        # like a unit any more. Inference calls it free text; a caller that
        # knows it asked for a unit says so, and the tier is withheld.
        page = page_of([[("ratio", 60.0, 22.0)]], source=TextSource.OCR)
        assert classify_value("rtaio") is ValueClass.FREE_TEXT
        assert locate("rtaio", page).value_class is ValueClass.FREE_TEXT
        declared = locate("rtaio", page, value_class=ValueClass.UNIT)
        assert declared.status is LocateStatus.NOT_LOCATED
        assert declared.value_class is ValueClass.UNIT

    def test_the_row_label_may_still_be_near_matched(self) -> None:
        # The policy applies to the value, not to the label that anchors
        # it. On a degraded scan a fuzzy label match is often the only way
        # the row is found at all.
        page = page_of([[("Potasslum", 57.0, 40.0), ("3.1", 265.0, 15.0)]], source=TextSource.OCR)
        result = locate_in_row("Potassium", "3.1", page)
        assert result.located
        assert result.label_candidates[0].kind is MatchKind.FUZZY
        assert result.tier == "exact"
        assert result.value.value_class is ValueClass.NUMERIC

    def test_a_row_anchored_near_miss_on_a_number_is_still_refused(self) -> None:
        page = page_of([[("Potassium", 57.0, 40.0), ("3.1", 265.0, 15.0)]], source=TextSource.OCR)
        assert locate_in_row("Potassium", "8.1", page).status is LocateStatus.NOT_LOCATED


# --------------------------------------------------------------------------
# locate_in_row()
# --------------------------------------------------------------------------


def lab_page(source=TextSource.TEXT_LAYER):
    """Three lab rows that all carry the same units and a repeated result."""
    return page_of(
        [
            [
                ("Glucose", 57.0, 32.0),
                ("111", 265.0, 15.0),
                ("H", 306.0, 7.0),
                ("mg/dL", 326.0, 24.0),
                ("70 - 99", 414.0, 27.0),
            ],
            [
                ("BUN", 57.0, 20.0),
                ("15", 270.0, 10.0),
                ("mg/dL", 326.0, 24.0),
                ("7 - 20", 414.0, 22.0),
            ],
            [
                ("Creatinine", 57.0, 39.0),
                ("15", 270.0, 10.0),
                ("mg/dL", 326.0, 24.0),
                ("0.50 - 1.10", 414.0, 41.0),
            ],
        ],
        source=source,
    )


class TestRowAnchoring:
    def test_a_repeated_value_is_settled_by_its_label(self) -> None:
        page = lab_page()
        assert locate("15", page).status is LocateStatus.AMBIGUOUS
        result = locate_in_row("BUN", "15", page)
        assert result.status is LocateStatus.LOCATED
        assert result.bbox.as_tuple() == (270.0, 64.0, 280.0, 76.0)
        assert result.rows_searched == (1,)

    def test_a_repeated_unit_is_settled_by_its_label(self) -> None:
        page = lab_page()
        assert locate("mg/dL", page).status is LocateStatus.AMBIGUOUS
        result = locate_in_row("Creatinine", "mg/dL", page)
        assert result.located
        assert result.bbox.y0 == 78.0

    def test_a_value_on_another_row_is_not_found(self) -> None:
        # The work-order requirement in one line: a 5 is never matched from
        # elsewhere on the page.
        page = lab_page()
        result = locate_in_row("Glucose", "15", page)
        assert result.status is LocateStatus.NOT_LOCATED
        assert result.label_candidates  # the label itself was found
        assert result.rows_searched == (0,)

    def test_a_missing_label_is_reported_as_such(self) -> None:
        result = locate_in_row("Ferritin", "15", lab_page())
        assert result.status is LocateStatus.NOT_LOCATED
        assert result.label_candidates == ()
        assert "label" in result.value.reason

    def test_a_label_on_two_rows_keeps_the_answer_ambiguous(self) -> None:
        page = page_of(
            [
                [("Glucose", 57.0, 32.0), ("89", 270.0, 10.0)],
                [("Glucose", 57.0, 32.0), ("89", 270.0, 10.0)],
            ]
        )
        result = locate_in_row("Glucose", "89", page)
        assert result.status is LocateStatus.AMBIGUOUS
        assert len(result.value.candidates) == 2
        assert result.rows_searched == (0, 1)

    def test_the_label_itself_is_never_the_match(self) -> None:
        page = page_of([[("Sodium", 57.0, 30.0), ("Sodium", 270.0, 30.0)]])
        result = locate_in_row("Sodium", "Sodium", page)
        assert result.located
        assert result.bbox.x0 == 270.0

    def test_after_label_false_searches_the_whole_row(self) -> None:
        page = page_of([[("137", 57.0, 15.0), ("Sodium", 270.0, 30.0)]])
        assert locate_in_row("Sodium", "137", page).status is LocateStatus.NOT_LOCATED
        assert locate_in_row("Sodium", "137", page, after_label=False).located

    def test_an_empty_page_is_handled(self) -> None:
        page = page_of([])
        assert locate_in_row("Glucose", "89", page).status is LocateStatus.NOT_LOCATED
        assert locate("89", page).status is LocateStatus.NOT_LOCATED


# --------------------------------------------------------------------------
# Against a real committed sample
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def lab_clean():
    directory = SAMPLES / "lab-clean-001"
    document = extract_words(directory / "lab-clean-001.pdf", policy=OcrPolicy.NEVER)
    truth = json.loads((directory / "lab-clean-001.ground_truth.json").read_text())
    return document, {entry["field_path"]: entry for entry in truth["fields"]}


class TestAgainstTheFactorySample:
    def test_the_clean_sample_is_read_from_its_text_layer(self, lab_clean) -> None:
        document, _ = lab_clean
        page = document.page(1)
        assert page.source is TextSource.TEXT_LAYER
        assert page.baseline_slope == pytest.approx(0.0, abs=1e-9)
        assert all(word.confidence == 1.0 for word in page.words)

    def test_row_anchored_boxes_match_ground_truth_exactly(self, lab_clean) -> None:
        document, truth = lab_clean
        index = PageIndex.for_page(document.page(1))
        checked = 0
        for path, entry in truth.items():
            if not path.startswith("results.") or not path.endswith(".value"):
                continue
            label = truth[path[: -len(".value")] + ".name"]
            result = locate_in_row(label["rendered_text"], entry["rendered_text"], index)
            assert result.located, path
            assert result.bbox.as_tuple() == pytest.approx(tuple(entry["bbox"]), abs=0.01), path
            checked += 1
        assert checked >= 20

    def test_a_value_the_document_does_not_carry_is_not_found(self, lab_clean) -> None:
        document, _ = lab_clean
        # Invariant 4: what cannot be located is not located, and gets no box.
        result = locate("Ferritin 12 ng/mL", document.page(1))
        assert result.status is LocateStatus.NOT_LOCATED
        assert result.bbox is None
