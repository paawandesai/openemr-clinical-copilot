"""
Every schema field is located under a declared value class, and no number,
unit, date or flag is ever located by the fuzzy tier (D24).

D24 measured the blind spot these tests pin: shape inference sees only what
the model emitted, so OCR reading `ratio` as `rtaio` (or a colon-written
ratio) infers `free_text` and would switch the fuzzy tier back on. The
worker never infers — `fields.FIELD_DECLARATIONS` is the declaration — and
the tests below show both that inference *would* have near-matched and that
the worker does not.
"""

from __future__ import annotations

import typing

import pytest

from app.extraction import (
    EXACT_ONLY_VALUE_CLASSES,
    LocateStatus,
    MatchKind,
    TextSource,
    ValueClass,
    build_page_words,
    locate,
)
from app.extraction.geometry import BBox as GeometryBox
from app.extraction.words import DocumentWords
from app.models.w2.documents import DocumentPatientHeader
from app.models.w2.extraction import ExtractedField
from app.models.w2.intake_form import IntakeFormExtraction
from app.models.w2.lab_pdf import LabPdfExtraction
from app.workers.intake_extractor.assemble import assemble_lab
from app.workers.intake_extractor.fields import (
    FIELD_DECLARATIONS,
    NOT_EXTRACTED_FIELDS,
    declared_class,
    pattern_for,
)
from app.workers.intake_extractor.vision_schema import RawField
from tests.intake_extractor_measurement import measure_all
from tests.test_intake_extractor_assemble import (
    lab_document,
    raw_lab,
    raw_row,
    source_document,
)


def _leaf_paths(model: type, prefix: str) -> set[str]:
    """Every ExtractedField-typed attribute reachable from `model`, as a pattern."""
    found: set[str] = set()
    hints = typing.get_type_hints(model)
    for name in model.model_fields:
        annotation = hints[name]
        path = f"{prefix}.{name}" if prefix else name
        origin = typing.get_origin(annotation)
        args = typing.get_args(annotation)
        candidates = [annotation, *args]
        if origin is list:
            path += "[]"
            candidates = list(args)
        for candidate in candidates:
            if isinstance(candidate, type) and issubclass(candidate, ExtractedField):
                found.add(path)
            elif (
                isinstance(candidate, type)
                and hasattr(candidate, "model_fields")
                and candidate.__module__.startswith("app.models.w2")
                and not issubclass(candidate, ExtractedField)
                and candidate.__name__ not in {"SourceDocument", "DocumentChecksums"}
            ):
                found |= _leaf_paths(candidate, path)
    return found


def _normalised(paths: set[str]) -> set[str]:
    return {pattern_for(path) for path in paths}


def test_every_lab_schema_field_has_a_declared_class() -> None:
    leaves = _normalised(_leaf_paths(LabPdfExtraction, ""))
    assert leaves, "the walk found no ExtractedField — the test would pass vacuously"
    undeclared = sorted(leaves - set(FIELD_DECLARATIONS) - NOT_EXTRACTED_FIELDS)
    assert undeclared == []


def test_every_intake_schema_field_has_a_declared_class() -> None:
    leaves = _normalised(_leaf_paths(IntakeFormExtraction, ""))
    undeclared = sorted(leaves - set(FIELD_DECLARATIONS) - NOT_EXTRACTED_FIELDS)
    assert undeclared == []


def test_the_walk_sees_the_nested_fields() -> None:
    # A control on the walk itself: if it stopped descending, the two tests
    # above would pass with nothing checked.
    leaves = _normalised(_leaf_paths(LabPdfExtraction, ""))
    assert {"results[].value", "results[].unit", "patient.name", "order.collected_at"} <= leaves
    assert _normalised(_leaf_paths(DocumentPatientHeader, "patient")) >= {"patient.date_of_birth"}


def test_no_declaration_is_orphaned() -> None:
    leaves = _normalised(_leaf_paths(LabPdfExtraction, "")) | _normalised(
        _leaf_paths(IntakeFormExtraction, "")
    )
    assert sorted(set(FIELD_DECLARATIONS) - leaves) == []


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("results[3].value", ValueClass.NUMERIC),
        ("results[0].unit", ValueClass.UNIT),
        ("results[0].reported_flag", ValueClass.FLAG),
        ("order.collected_at", ValueClass.DATE),
        ("patient_header.date_of_birth", ValueClass.DATE),
        ("demographics.mrn", ValueClass.NUMERIC),
        ("medications[2].dose", ValueClass.NUMERIC),
        ("medications[2].name", ValueClass.FREE_TEXT),
    ],
)
def test_declared_classes(path: str, expected: ValueClass) -> None:
    assert declared_class(path) is expected


def _ocr_row(texts: list[str], y: float = 100.0) -> DocumentWords:
    items = []
    x = 50.0
    for text in texts:
        width = 8.0 * len(text)
        items.append((text, GeometryBox(1, x, y, x + width, y + 10.0), 0.9))
        x += width + 12.0
    return DocumentWords(
        pages=(build_page_words(page=1, width=612, height=792, items=items, source=TextSource.OCR),)
    )


@pytest.mark.parametrize(
    ("printed", "transcribed"),
    [
        # The page prints the unit; the transcription garbled it. Bare
        # `rtaio` vs `ratio` falls under the fuzzy threshold, so the D24
        # example is carried by the longer form a report actually prints.
        ("ratio (calc)", "rtaio (calc)"),
        # A transposed digit inside a unit — eGFR per 1.73 m2 body surface.
        ("mL/min/1.73m2", "mL/min/1.37m2"),
        # A colon-written ratio, one digit off.
        ("7.6:1.98", "7.6:1.99"),
    ],
)
def test_inference_would_near_match_but_the_worker_does_not(printed: str, transcribed: str) -> None:
    words = _ocr_row(["Albumin/Globulin", "1.3", printed])
    page = words.page(1)
    assert page is not None

    # The blind spot, demonstrated: with the class inferred from the value's
    # shape, the fuzzy tier runs and places the value on the wrong text.
    inferred = locate(transcribed, page)
    assert inferred.value_class is ValueClass.FREE_TEXT
    assert inferred.status is LocateStatus.LOCATED
    assert inferred.kind is MatchKind.FUZZY

    # The worker declares `unit` for a result's unit column, so it does not.
    raw = raw_lab(
        [
            raw_row(
                "ag_ratio",
                name="Albumin/Globulin",
                value="1.3",
                unit=transcribed,
                page=1,
            )
        ]
    )
    assembled = assemble_lab(raw, words, source_document(page_count=1), lab_document.extraction_id)
    unit_record = next(r for r in assembled.records if r.schema_path == "results[].unit")
    assert unit_record.value_class is ValueClass.UNIT
    assert unit_record.outcome == "unlocated"
    assert unit_record.match_kind is not MatchKind.FUZZY
    assert not assembled.extraction.results[0].unit.is_presentable


def test_bare_rtaio_is_the_d24_misclassification() -> None:
    # D24's single inference/declaration disagreement: the garbled unit no
    # longer looks like a unit. The declaration does not care what it looks like.
    from app.extraction import classify_value

    assert classify_value("rtaio") is ValueClass.FREE_TEXT
    assert declared_class("results[0].unit") is ValueClass.UNIT


def test_free_text_still_gets_the_fuzzy_tier_on_ocr() -> None:
    # The policy is narrow on purpose: a drug name OCR slightly misread is
    # still located (at lower confidence), so the test above is not passing
    # merely because fuzzy matching is off everywhere.
    words = _ocr_row(["Glucoes", "89"])
    raw = raw_lab([raw_row("glucose", name="Glucose", value="89", page=1)])
    assembled = assemble_lab(raw, words, source_document(page_count=1), lab_document.extraction_id)
    name = next(r for r in assembled.records if r.schema_path == "results[].test_name")
    assert name.outcome == "located"
    assert name.match_kind is MatchKind.FUZZY
    assert name.confidence is not None and name.confidence < 0.9 * 0.8 + 1e-9


def test_no_exact_only_field_is_fuzzy_located_on_any_factory_sample() -> None:
    import asyncio

    offenders = []
    checked = 0
    for measured in asyncio.run(measure_all()):
        for entry in measured.report.fields:
            if ValueClass(entry.value_class) not in EXACT_ONLY_VALUE_CLASSES:
                continue
            checked += 1
            if entry.outcome == "located" and entry.match_tier == "fuzzy":
                offenders.append((measured.sample, entry.field_path))
    assert checked > 200
    assert offenders == []


def test_raw_field_is_the_only_input_shape() -> None:
    # A transcription carries text and a page, never coordinates (D3).
    assert set(RawField.model_fields) == {"state", "text", "page"}
