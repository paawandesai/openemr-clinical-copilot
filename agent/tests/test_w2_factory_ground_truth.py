"""
Factory compatibility: all six committed ground truths convert to valid
schema instances.

The samples are read from `feat/doc-factory` with `git show`, not vendored
here. That branch is where they live (D8 records the relocation to
`agent/evals/w2/factory/`), copying 250 KB of JSON into `tests/` would put
two sources of truth in the repository, and `git show` is a local read with
no network and no credentials — the same constraints the rest of
`agent/tests/` runs under.

If the branch is not present the module skips, loudly, naming what it
wanted. A skip is not a pass and the session report says which it was.
"""

from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

from app.models.w2.documents import DocumentKind
from app.models.w2.extraction import ExtractedField, FieldStatus
from app.models.w2.factory_mapping import (
    KNOWN_RENDERINGS,
    SCHEMA_FIELDS_WITHOUT_FACTORY_PATH,
    SUPPORTED_GROUND_TRUTH_SCHEMA_VERSION,
    UNMAPPED_FACTORY_PATHS,
    GroundTruthConversion,
    convert_ground_truth,
    map_field_path,
    parse_lab_value,
    same_meaning,
)
from app.models.w2.intake_form import IntakeFormExtraction
from app.models.w2.lab_pdf import LabPdfExtraction
from app.models.w2.lab_values import (
    COMPARABLE_REPORTED_FLAGS,
    AbnormalFlag,
    CrossCheckOutcome,
    NumericLabValue,
    ReportedFlag,
)

FACTORY_BRANCH = "feat/doc-factory"
SAMPLE_DIR = "agent/evals/w2/factory/output/samples"

LAB_SAMPLES = ("lab-clean-001", "lab-scanned-heavy-abnormal-002", "lab-multipage-003")
INTAKE_SAMPLES = (
    "intake-clean-004",
    "intake-scanned-light-med-005",
    "intake-missing-allergy-006",
)
ALL_SAMPLES = LAB_SAMPLES + INTAKE_SAMPLES

REPO_ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def _branch_available() -> bool:
    probe = subprocess.run(
        ["git", "rev-parse", "--verify", f"{FACTORY_BRANCH}^{{commit}}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return probe.returncode == 0


@lru_cache(maxsize=len(ALL_SAMPLES))
def load_ground_truth(scenario_id: str) -> dict[str, Any]:
    if not _branch_available():
        pytest.skip(
            f"branch {FACTORY_BRANCH} is not present in this checkout; "
            f"cannot read {SAMPLE_DIR}/{scenario_id}"
        )
    path = f"{FACTORY_BRANCH}:{SAMPLE_DIR}/{scenario_id}/{scenario_id}.ground_truth.json"
    shown = subprocess.run(
        ["git", "show", path],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if shown.returncode != 0:
        pytest.skip(f"{path} is not readable: {shown.stderr.strip()}")
    return json.loads(shown.stdout)


@lru_cache(maxsize=len(ALL_SAMPLES))
def convert(scenario_id: str) -> GroundTruthConversion:
    return convert_ground_truth(load_ground_truth(scenario_id))


# -- the conversion ---------------------------------------------------------


@pytest.mark.parametrize("scenario_id", ALL_SAMPLES)
def test_every_sample_converts_to_a_valid_schema_instance(scenario_id: str) -> None:
    conversion = convert(scenario_id)
    assert conversion.scenario_id == scenario_id
    expected = LabPdfExtraction if scenario_id in LAB_SAMPLES else IntakeFormExtraction
    assert isinstance(conversion.extraction, expected)
    # Re-validating the dumped instance proves it is not merely constructed
    # but serialisable and re-readable under the same contract.
    assert expected.model_validate(conversion.extraction.model_dump()) == conversion.extraction


@pytest.mark.parametrize("scenario_id", ALL_SAMPLES)
def test_every_factory_field_path_maps(scenario_id: str) -> None:
    """Requirement 8: every ground-truth field_path lands on a schema field.
    Anything the adapter cannot place comes back here rather than being
    dropped, so a new factory path is a test failure."""
    conversion = convert(scenario_id)
    assert conversion.unmapped_field_paths == []


@pytest.mark.parametrize("scenario_id", ALL_SAMPLES)
def test_the_mapping_table_agrees_with_the_conversion(scenario_id: str) -> None:
    """`map_field_path` is what the generated mapping document is built
    from; it must cover exactly what the converter covers."""
    ground_truth = load_ground_truth(scenario_id)
    kind = DocumentKind(ground_truth["scenario"]["doc_type"])
    for record in ground_truth["fields"]:
        path = record["field_path"]
        assert map_field_path(path, kind) is not None, f"{path} has no entry in the mapping table"


def test_the_declared_unmapped_set_is_empty_and_true() -> None:
    assert UNMAPPED_FACTORY_PATHS == frozenset()


def test_the_reverse_direction_is_documented() -> None:
    """A mapping table is only honest if it lists both directions."""
    assert "intake_form.medications[].route" in SCHEMA_FIELDS_WITHOUT_FACTORY_PATH
    assert "lab_pdf.results[].loinc_code" in SCHEMA_FIELDS_WITHOUT_FACTORY_PATH
    assert all(reason for reason in SCHEMA_FIELDS_WITHOUT_FACTORY_PATH.values())


def test_the_adapter_pins_the_ground_truth_version() -> None:
    assert load_ground_truth("lab-clean-001")["schema_version"] == (
        SUPPORTED_GROUND_TRUTH_SCHEMA_VERSION
    )
    with pytest.raises(ValueError, match="this adapter reads"):
        convert_ground_truth({"schema_version": "doc-factory-groundtruth/9.9.9"})


# -- invariant 4 through the adapter ---------------------------------------


def test_an_absent_field_does_not_become_the_chart_value() -> None:
    """
    `intake-missing-allergy-006` omits `patient.dob` and its ground-truth
    `value` is the chart's `2014-01-24`. Its own expected discrepancy: "An
    extractor must report it as absent, not infer it." Copying `value` into
    the schema would break invariant 4 silently, on exactly the case the
    factory built to catch it.
    """
    conversion = convert("intake-missing-allergy-006")
    assert isinstance(conversion.extraction, IntakeFormExtraction)
    dob = conversion.extraction.demographics.date_of_birth
    assert dob.status is FieldStatus.MISSING
    assert dob.value is None
    assert dob.as_presented_fact() is None

    chart_truth = {note.field_path: note for note in conversion.chart_truth}
    assert chart_truth["patient.dob"].chart_value == "2014-01-24"


def test_an_illegible_field_keeps_its_ink_and_drops_its_value() -> None:
    conversion = convert("intake-missing-allergy-006")
    assert isinstance(conversion.extraction, IntakeFormExtraction)
    city = conversion.extraction.demographics.city_state_zip
    assert city.status is FieldStatus.ILLEGIBLE
    assert city.value is None
    assert city.citation is not None and city.citation.bbox is not None

    chart_truth = {note.field_path: note for note in conversion.chart_truth}
    assert chart_truth["patient.city_state_zip"].chart_value == "Everett, Massachusetts 02149"


@pytest.mark.parametrize("scenario_id", ALL_SAMPLES)
def test_no_converted_field_presents_a_value_the_page_does_not_carry(
    scenario_id: str,
) -> None:
    """Across all six: anything presentable quotes what was drawn."""
    ground_truth = load_ground_truth(scenario_id)
    drawn = {record["field_path"]: record for record in ground_truth["fields"]}
    for field, path in _walk_extracted_fields(convert(scenario_id).extraction):
        fact = field.as_presented_fact()
        if fact is None:
            continue
        record = drawn.get(path)
        if record is None:
            continue
        assert record["status"] == "present", f"{path} is {record['status']} yet presentable"
        assert fact.citation.quote_or_value == record["rendered_text"]


def _walk_extracted_fields(model: Any, prefix: str = "") -> list[tuple[ExtractedField[Any], str]]:
    """Every ExtractedField in an extraction, keyed by the factory path its
    citation names."""
    found: list[tuple[ExtractedField[Any], str]] = []
    for value in getattr(model, "__dict__", {}).values():
        if isinstance(value, ExtractedField):
            citation = value.citation
            if citation is not None:
                found.append((value, citation.field_or_chunk_id))
        elif isinstance(value, list):
            for item in value:
                found.extend(_walk_extracted_fields(item, prefix))
        elif hasattr(value, "__dict__") and not isinstance(value, (str, int, float)):
            found.extend(_walk_extracted_fields(value, prefix))
    return found


# -- the numbers are read off the page, not copied from the answer ---------


@pytest.mark.parametrize("scenario_id", LAB_SAMPLES)
def test_every_lab_value_parses_to_the_ground_truths_number(scenario_id: str) -> None:
    """
    The adapter derives its value from `rendered_text`, never from the
    ground truth's `value`. This checks it arrives at the same number
    anyway — which is the real test that it read the page rather than the
    answer key.
    """
    ground_truth = load_ground_truth(scenario_id)
    extraction = convert(scenario_id).extraction
    assert isinstance(extraction, LabPdfExtraction)
    by_key = {row.test_key: row for row in extraction.results}

    checked = 0
    for record in ground_truth["fields"]:
        parts = record["field_path"].split(".")
        if len(parts) != 4 or parts[0] != "results" or parts[3] != "value":
            continue
        row = by_key[parts[2]]
        fact = row.value.as_presented_fact()
        assert fact is not None, f"{record['field_path']} should be located"
        assert isinstance(fact.value, NumericLabValue)
        assert fact.value.value == pytest.approx(float(record["value"]))
        assert parse_lab_value(record["rendered_text"]) == fact.value
        checked += 1
    assert checked > 0


@pytest.mark.parametrize("scenario_id", LAB_SAMPLES)
def test_no_generated_report_contradicts_the_computed_flag(scenario_id: str) -> None:
    """
    The independent half of the contract, run over real generated reports:
    the deterministic rule recomputes each row's flag from its value and
    range, and nothing the factory printed contradicts it. A failure here
    would mean either the rule or the factory misreads a lab report.
    """
    extraction = convert(scenario_id).extraction
    assert isinstance(extraction, LabPdfExtraction)
    assert extraction.flag_verification_failures() == []


@pytest.mark.parametrize("scenario_id", LAB_SAMPLES)
def test_a_generated_report_prints_a_flag_only_where_it_means_to(
    scenario_id: str,
) -> None:
    """
    D20 in the data. These reports print a flag on abnormal rows and leave
    the column blank everywhere else, which is how lab reports are printed —
    so most rows cross-check to `not_reported`, and that is the correct
    outcome, not a gap.

    Under D12 every one of those blanks would have been read as a printed
    `normal` and scored as agreement. This asserts the opposite: a blank row
    is never `agree`, and where a flag *is* printed it must match.
    """
    extraction = convert(scenario_id).extraction
    assert isinstance(extraction, LabPdfExtraction)

    printed, blank = 0, 0
    for row in extraction.results:
        check = row.cross_check_flag()
        assert check is not None, f"{row.test_key}: every sample row has a value"
        if check.reported in COMPARABLE_REPORTED_FLAGS:
            assert check.outcome is CrossCheckOutcome.AGREE, f"{row.test_key}: {check}"
            printed += 1
        else:
            assert check.reported is ReportedFlag.NOT_REPORTED
            assert check.outcome is not CrossCheckOutcome.AGREE
            assert check.is_verification_failure is False
            blank += 1
    assert blank > 10, "most rows of a generated report have a blank flag column"
    assert printed + blank == len(extraction.results)


def test_the_abnormal_sample_prints_its_three_flags_and_they_all_check_out() -> None:
    """
    lab-scanned-heavy-abnormal-002 perturbs glucose high, potassium low and
    ALT high, and prints H/L/H for exactly those three. This is the only
    sample where the cross-check has anything to compare, so it is where
    `agree` has to be earned.
    """
    extraction = convert("lab-scanned-heavy-abnormal-002").extraction
    assert isinstance(extraction, LabPdfExtraction)

    compared = {}
    for row in extraction.results:
        check = row.cross_check_flag()
        assert check is not None
        if check.reported in COMPARABLE_REPORTED_FLAGS:
            assert check.outcome is CrossCheckOutcome.AGREE
            compared[row.test_key] = check.computed

    assert compared == {
        "glucose": AbnormalFlag.HIGH,
        "potassium": AbnormalFlag.LOW,
        "alt": AbnormalFlag.HIGH,
    }


def test_the_clean_sample_prints_no_flags_at_all() -> None:
    """The complement: a report with nothing abnormal prints nothing in the
    flag column, so there is nothing to compare anywhere in it."""
    extraction = convert("lab-clean-001").extraction
    assert isinstance(extraction, LabPdfExtraction)
    outcomes = {row.cross_check_flag().outcome for row in extraction.results}  # type: ignore[union-attr]
    assert outcomes == {CrossCheckOutcome.NOT_REPORTED}
    assert extraction.flag_verification_failures() == []


# -- shape of the converted documents ---------------------------------------


@pytest.mark.parametrize(
    ("scenario_id", "results", "pages"),
    [("lab-clean-001", 22, 1), ("lab-multipage-003", 44, 2)],
)
def test_lab_documents_convert_with_every_row(scenario_id: str, results: int, pages: int) -> None:
    extraction = convert(scenario_id).extraction
    assert isinstance(extraction, LabPdfExtraction)
    assert len(extraction.results) == results
    assert extraction.document.page_count == pages
    assert extraction.presentable_results() == extraction.results


@pytest.mark.parametrize(
    ("scenario_id", "medications", "allergies"),
    [("intake-clean-004", 4, 9), ("intake-scanned-light-med-005", 11, 3)],
)
def test_intake_documents_convert_with_every_row(
    scenario_id: str, medications: int, allergies: int
) -> None:
    extraction = convert(scenario_id).extraction
    assert isinstance(extraction, IntakeFormExtraction)
    assert len(extraction.medications) == medications
    assert len(extraction.allergies) == allergies


def test_the_collection_date_reaches_every_row() -> None:
    """It is the third component of D1's idempotency key, so a row without
    one could not be written back."""
    extraction = convert("lab-clean-001").extraction
    assert isinstance(extraction, LabPdfExtraction)
    for row in extraction.results:
        fact = row.collection_date.as_presented_fact()
        assert fact is not None
        assert fact.value.isoformat() == "2026-08-30"


def test_checkboxes_convert_to_booleans_that_match_the_ground_truth() -> None:
    ground_truth = load_ground_truth("intake-clean-004")
    extraction = convert("intake-clean-004").extraction
    assert isinstance(extraction, IntakeFormExtraction)
    by_condition = {entry.condition: entry for entry in extraction.family_history}

    for record in ground_truth["fields"]:
        if record["kind"] != "checkbox" or not record["field_path"].startswith("family_history."):
            continue
        condition = record["field_path"].split(".")[1]
        fact = by_condition[condition].present.as_presented_fact()
        assert fact is not None
        assert fact.value is record["value"]


def test_expected_discrepancies_survive_the_conversion() -> None:
    """They are the factory's own one-sentence statements of what an
    extractor should notice — the raw material for a `Discrepancy`."""
    conversion = convert("intake-scanned-light-med-005")
    assert any("amLODIPine" in sentence for sentence in conversion.expected_discrepancies)
    assert convert("lab-clean-001").expected_discrepancies == []


def test_the_synthetic_extraction_id_is_stable() -> None:
    """Ground truth has no OpenEMR digest and no real run, so the adapter
    derives one. It has to be the same on every run or citations would
    churn."""
    first = convert_ground_truth(load_ground_truth("lab-clean-001"))
    second = convert_ground_truth(load_ground_truth("lab-clean-001"))
    assert first.extraction.extraction_id == second.extraction.extraction_id


# -- ground truth 0.2.0: the declared rendering -----------------------------


@pytest.mark.parametrize("scenario_id", ALL_SAMPLES)
def test_every_declared_rendering_is_one_the_adapter_knows(scenario_id: str) -> None:
    """A rendering the adapter has never heard of would be silently treated
    as divergent. Fail instead, naming it."""
    for record in load_ground_truth(scenario_id)["fields"]:
        rendering = record.get("rendering")
        assert rendering in KNOWN_RENDERINGS, (
            f"{record['field_path']}: unknown rendering {rendering!r}; "
            f"the adapter knows {sorted(KNOWN_RENDERINGS)}"
        )


@pytest.mark.parametrize("scenario_id", ALL_SAMPLES)
def test_every_verbatim_field_parses_back_to_the_ground_truths_value(
    scenario_id: str,
) -> None:
    """
    The factory *declares* which fields are drawn verbatim. This checks the
    declaration rather than trusting it: for every field the factory calls
    verbatim, the value the adapter parsed out of `rendered_text` must mean
    the same thing as the ground truth's own `value`.

    It catches two different defects — a field mislabelled `verbatim` when
    the page really does say something else, and a parser in this contract
    that disagrees with the renderer (a date format, a censored number, a
    checkbox glyph). Without it, the conversion proof would be a restatement
    of the factory's opinion of itself.
    """
    ground_truth = load_ground_truth(scenario_id)

    # Grouped, not a 1:1 map: more than one schema field may legitimately
    # cite the same place on the page. `LabResult.collection_date` reuses
    # `order.collected`'s citation and holds the date component that D1's
    # idempotency key is over, while `LabOrderHeader.collected_at` holds the
    # full timestamp. The assertion is that *some* field citing a verbatim
    # record reads it as the ground truth does — a deliberate narrowing
    # alongside it is not a misread.
    extracted: dict[str, list[Any]] = {}
    for field, path in _walk_extracted_fields(convert(scenario_id).extraction):
        extracted.setdefault(path, []).append(field)

    checked = 0
    for record in ground_truth["fields"]:
        if record["status"] != "present" or record.get("rendering") != "verbatim":
            continue
        fields = extracted.get(record["field_path"])
        if not fields:
            continue
        assert any(same_meaning(field.value, record["value"]) for field in fields), (
            f"{record['field_path']}: factory declares verbatim, but no field "
            f"citing it reads {record['rendered_text']!r} as the ground-truth "
            f"value {record['value']!r} — parsed "
            f"{[field.value for field in fields]!r}"
        )
        checked += 1
    assert checked > 20, "the samples should exercise plenty of verbatim fields"


@pytest.mark.parametrize("scenario_id", ALL_SAMPLES)
def test_chart_truth_is_exactly_what_the_factory_declares_as_divergent(
    scenario_id: str,
) -> None:
    """
    `chart_truth` is the raw material for a `Discrepancy`, so it has to be
    neither more nor less than the set of fields the document does not convey
    as the chart holds it: everything not `present`, plus every `present`
    field whose declared rendering is not `verbatim`.
    """
    ground_truth = load_ground_truth(scenario_id)
    expected = {
        record["field_path"]
        for record in ground_truth["fields"]
        if record.get("value") is not None
        and (record["status"] != "present" or record.get("rendering") != "verbatim")
    }
    actual = {note.field_path for note in convert(scenario_id).chart_truth}
    assert actual == expected


def test_a_derived_field_keeps_the_drawn_form_and_records_the_chart_wording() -> None:
    """
    The medication case, which is what `derived` exists to express: the form
    carries the common name and the chart holds the full RxNorm product
    string. Neither is wrong, and the schema must not silently prefer one.
    """
    conversion = convert("intake-clean-004")
    assert isinstance(conversion.extraction, IntakeFormExtraction)
    first = conversion.extraction.medications[0]
    fact = first.name.as_presented_fact()
    assert fact is not None
    assert fact.value == "diphenhydrAMINE Hydrochloride"

    note = next(n for n in conversion.chart_truth if n.field_path == "medications[0].name")
    assert note.rendering == "derived"
    assert note.chart_value == "diphenhydrAMINE Hydrochloride 25 MG Oral Tablet"
    assert note.note is not None


def test_no_sample_field_is_wrapped_or_truncated_today() -> None:
    """
    The factory's own commit says no committed sample wraps or truncates. If
    one starts to, the adapter's handling of those two renderings stops being
    theory and this test is where that is noticed.
    """
    seen = {
        record.get("rendering")
        for scenario_id in ALL_SAMPLES
        for record in load_ground_truth(scenario_id)["fields"]
    }
    assert seen <= {"verbatim", "derived"}, (
        f"a sample now renders {sorted(seen - {'verbatim', 'derived'})}; "
        "check _extracted() and the chart_truth note for that path"
    )


def test_the_lab_director_caption_is_no_longer_part_of_the_value() -> None:
    """
    Ground truth 0.1.0 drew `lab.director` with its "Laboratory Director: "
    caption inside the field, making the field `derived` and its bbox cover
    text nobody extracts. 0.2.0 makes the caption chrome. Pinned because it
    is the one place a reader of the earlier session report might expect a
    truncated string.
    """
    conversion = convert("lab-clean-001")
    assert isinstance(conversion.extraction, LabPdfExtraction)
    assert conversion.extraction.performing_lab is not None
    fact = conversion.extraction.performing_lab.director.as_presented_fact()
    assert fact is not None
    assert fact.value == "Nadia Okonjo-Reyes, MD"
    assert not fact.value.startswith("Laboratory Director")
    assert "lab.director" not in {note.field_path for note in conversion.chart_truth}
