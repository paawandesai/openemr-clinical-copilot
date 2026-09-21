"""
Tests for the synthetic document factory (evals/factory).

None of these need OpenEMR: the charts the sample scenarios reference are
committed under ``evals/factory/cache/``, which is the point of the cache.

Two different notions of "deterministic" are checked separately, because they
hold to different degrees:

* the ground truth (fields, bboxes, perturbations) is stable anywhere — it
  comes out of PyMuPDF font metrics and seeded ``random.Random`` streams only;
* the PDF bytes of a *clean* document are stable anywhere for a given PyMuPDF,
  so the committed clean samples are compared byte for byte;
* the PDF bytes of a *scanned* document additionally go through Pillow's
  resampling and JPEG encoder, so those are only checked for stability within
  one environment, by generating twice.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf
import pytest

from evals.factory.chart import ChartRecord, read_cache
from evals.factory.degrade import _invert_affine, _page_transform
from evals.factory.generate import (
    DEFAULT_CACHE_DIR,
    DEFAULT_SPEC_DIR,
    generate,
    ground_truth_for,
)
from evals.factory.spec import SpecError, load_specs, parse_spec
from evals.factory.validate import validate

FACTORY_DIR = Path(__file__).resolve().parents[1] / "evals" / "factory"
SAMPLES_DIR = FACTORY_DIR / "output" / "samples"

SPECS = load_specs(DEFAULT_SPEC_DIR)
SPEC_BY_ID = {s.scenario_id: s for s in SPECS}
SPEC_IDS = sorted(SPEC_BY_ID)


def _chart(spec) -> ChartRecord:
    record = read_cache(DEFAULT_CACHE_DIR, spec.patient_ref)
    assert record is not None, (
        f"no committed chart cache for {spec.scenario_id}; "
        f"run: python -m evals.factory generate evals/factory/specs --refresh"
    )
    return record


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_six_sample_scenarios_are_committed():
    assert SPEC_IDS == [
        "intake-clean-004",
        "intake-missing-allergy-006",
        "intake-scanned-light-med-005",
        "lab-clean-001",
        "lab-multipage-003",
        "lab-scanned-heavy-abnormal-002",
    ]


# -- determinism -----------------------------------------------------------

@pytest.mark.parametrize("scenario_id", SPEC_IDS)
def test_ground_truth_is_deterministic(scenario_id):
    """Same spec + same seed -> identical ground truth, twice in a row."""
    spec = SPEC_BY_ID[scenario_id]
    chart = _chart(spec)
    first = ground_truth_for(spec, chart)
    second = ground_truth_for(spec, chart)
    assert first == second


@pytest.mark.parametrize("scenario_id", SPEC_IDS)
def test_ground_truth_matches_the_committed_sample(scenario_id):
    """The committed ground truth is what this code still produces."""
    spec = SPEC_BY_ID[scenario_id]
    committed = json.loads(
        (SAMPLES_DIR / scenario_id / f"{scenario_id}.ground_truth.json").read_text(encoding="utf-8")
    )
    regenerated = ground_truth_for(spec, _chart(spec))
    assert regenerated["fields"] == committed["fields"]
    assert regenerated["perturbations"] == committed["perturbations"]
    assert regenerated["pages"] == committed["pages"]
    assert regenerated["scenario"] == committed["scenario"]


@pytest.mark.parametrize("scenario_id", SPEC_IDS)
def test_clean_pdf_bytes_reproduce_the_committed_sample(tmp_path, scenario_id):
    """
    The clean layout regenerates byte for byte.

    For a scanned scenario this is the ``.clean.pdf`` companion; the scanned
    PDF itself is covered by the within-run test below.
    """
    spec = SPEC_BY_ID[scenario_id]
    result = generate(spec, output_root=tmp_path, cache_dir=DEFAULT_CACHE_DIR)
    committed = SAMPLES_DIR / scenario_id / result.clean_path.name
    assert _sha256(result.clean_path) == _sha256(committed)


@pytest.mark.parametrize("scenario_id", ["lab-scanned-heavy-abnormal-002", "intake-scanned-light-med-005"])
def test_scanned_pdf_bytes_are_stable_within_a_run(tmp_path, scenario_id):
    spec = SPEC_BY_ID[scenario_id]
    first = generate(spec, output_root=tmp_path / "a", cache_dir=DEFAULT_CACHE_DIR)
    second = generate(spec, output_root=tmp_path / "b", cache_dir=DEFAULT_CACHE_DIR)
    assert _sha256(first.document_path) == _sha256(second.document_path)


# -- the committed samples -------------------------------------------------

def test_validate_passes_on_every_committed_sample():
    report = validate(SAMPLES_DIR)
    assert len(report.scenarios) == len(SPEC_IDS)
    failures = {s.scenario_id: s.failures for s in report.scenarios if not s.ok}
    assert not failures, failures
    assert report.checks > 0


@pytest.mark.parametrize("scenario_id", SPEC_IDS)
def test_text_layer_matches_the_declared_quality(scenario_id):
    spec = SPEC_BY_ID[scenario_id]
    document = SAMPLES_DIR / scenario_id / f"{scenario_id}.pdf"
    with pymupdf.open(document) as opened:
        per_page = [opened[i].get_text("text").strip() for i in range(opened.page_count)]

    if spec.quality.is_scanned:
        assert not any(per_page), f"{scenario_id}: scanned output still carries a text layer"
    else:
        assert all(per_page), f"{scenario_id}: clean output is missing a text layer"


def test_every_sample_records_its_perturbations():
    for scenario_id in SPEC_IDS:
        spec = SPEC_BY_ID[scenario_id]
        payload = json.loads(
            (SAMPLES_DIR / scenario_id / f"{scenario_id}.ground_truth.json").read_text(encoding="utf-8")
        )
        recorded = {p["kind"] for p in payload["perturbations"]}
        requested = {p.kind.value for p in spec.perturbations}
        assert requested <= recorded, f"{scenario_id}: {requested - recorded} never reached the ground truth"
        for entry in payload["perturbations"]:
            assert entry["expected_discrepancy"], f"{scenario_id}: {entry['kind']} has no expected_discrepancy"


def test_absent_and_illegible_fields_keep_the_chart_value():
    payload = json.loads(
        (SAMPLES_DIR / "intake-missing-allergy-006" / "intake-missing-allergy-006.ground_truth.json")
        .read_text(encoding="utf-8")
    )
    by_path = {f["field_path"]: f for f in payload["fields"]}

    missing = by_path["patient.dob"]
    assert missing["status"] == "absent"
    assert missing["bbox"] is None and missing["page"] is None
    assert missing["value"], "a missing field must still carry the chart truth"

    illegible = by_path["patient.city_state_zip"]
    assert illegible["status"] == "illegible"
    assert illegible["rendered_text"] == ""
    assert illegible["bbox"] is not None, "an illegible field still has an ink region"
    assert illegible["value"], "an illegible field must still carry the chart truth"


def test_scanned_samples_record_a_usable_transform():
    payload = json.loads(
        (SAMPLES_DIR / "lab-scanned-heavy-abnormal-002" / "lab-scanned-heavy-abnormal-002.ground_truth.json")
        .read_text(encoding="utf-8")
    )
    transform = payload["transform"]
    assert transform["kind"] == "scan"
    for page in transform["pages"]:
        a, c, e, b, d, f = page["pdf_points_to_image_px"]
        width_pt, height_pt = page["page_size_pt"]
        width_px, height_px = page["image_size_px"]
        # The page centre maps to the image centre under any pure rotation
        # about that centre, whatever the skew angle.
        x, y = width_pt / 2, height_pt / 2
        assert a * x + c * y + e == pytest.approx(width_px / 2, abs=1.0)
        assert b * x + d * y + f == pytest.approx(height_px / 2, abs=1.0)


def test_scan_affine_inverse_round_trips():
    forward = _page_transform(1275, 1650, 150 / 72, 1.7)
    a, c, e, b, d, f = forward
    ia, ic, ie, ib, id_, if_ = _invert_affine(forward)
    for x, y in ((0.0, 0.0), (612.0, 792.0), (123.5, 456.75)):
        px, py = a * x + c * y + e, b * x + d * y + f
        assert ia * px + ic * py + ie == pytest.approx(x, abs=1e-6)
        assert ib * px + id_ * py + if_ == pytest.approx(y, abs=1e-6)


# -- spec validation -------------------------------------------------------

def _spec(**overrides):
    base = {
        "scenario_id": "t-001",
        "seed": 1,
        "patient_ref": "18",
        "doc_type": "lab_pdf",
        "quality": "clean",
    }
    base.update(overrides)
    return base


def test_perturbation_must_apply_to_the_doc_type():
    with pytest.raises(SpecError, match="does not apply to doc_type"):
        parse_spec(_spec(perturbations=[{"kind": "med_discrepancy", "mode": "add"}]))


def test_perturbation_requires_its_parameters():
    with pytest.raises(SpecError, match="requires key 'field'"):
        parse_spec(_spec(perturbations=[{"kind": "missing_field"}]))


def test_perturbation_enum_parameters_are_checked():
    with pytest.raises(SpecError, match="direction="):
        parse_spec(_spec(perturbations=[{"kind": "abnormal_value", "test": "glucose", "direction": "up"}]))


def test_unknown_top_level_keys_are_rejected():
    with pytest.raises(SpecError, match="unknown top-level key"):
        parse_spec(_spec(qualtiy="clean"))


def test_abnormal_value_on_an_unordered_panel_fails(tmp_path):
    spec = parse_spec(_spec(
        scenario_id="t-unordered",
        perturbations=[{"kind": "abnormal_value", "test": "ferritin", "direction": "low"}],
    ))
    with pytest.raises(SpecError, match="which this report does not order"):
        generate(spec, output_root=tmp_path, cache_dir=DEFAULT_CACHE_DIR)


def test_perturbation_naming_a_field_the_document_never_renders_fails(tmp_path):
    spec = parse_spec(_spec(
        scenario_id="t-unknown-field",
        perturbations=[{"kind": "missing_field", "field": "patient.favourite_colour"}],
    ))
    with pytest.raises(SpecError, match="are not rendered by this document"):
        generate(spec, output_root=tmp_path, cache_dir=DEFAULT_CACHE_DIR)


def test_a_field_cannot_be_both_missing_and_illegible(tmp_path):
    spec = parse_spec(_spec(
        scenario_id="t-conflict",
        perturbations=[
            {"kind": "missing_field", "field": "patient.dob"},
            {"kind": "illegible_field", "field": "patient.dob"},
        ],
    ))
    with pytest.raises(SpecError, match="both missing_field and illegible_field"):
        generate(spec, output_root=tmp_path, cache_dir=DEFAULT_CACHE_DIR)


# -- reproducible PDF bytes ------------------------------------------------

def test_canonical_pdf_bytes_survives_a_literal_string_trailer_id():
    """
    MuPDF writes the trailer /ID as two hex strings most of the time, but for
    roughly one save in fifty one of them comes out as a PDF literal string
    because the random bytes were printable. Both forms have to be rewritten,
    or that save keeps a random id and the output stops being reproducible.
    """
    from evals.factory.canvas import canonical_pdf_bytes

    digests = set()
    for _ in range(300):
        document = pymupdf.open()
        document.new_page(width=200, height=200)
        document.set_metadata({
            "title": "t", "producer": "p", "creator": "c", "author": "a",
            "subject": "s", "keywords": "k",
            "creationDate": "D:20260101000000Z", "modDate": "D:20260101000000Z",
        })
        digests.add(hashlib.sha256(canonical_pdf_bytes(document.tobytes(garbage=4, deflate=True))).hexdigest())
    assert len(digests) == 1, "identical content produced more than one byte sequence"


def test_canonical_pdf_bytes_refuses_a_pdf_it_cannot_canonicalise():
    from evals.factory.canvas import NonReproduciblePdf, canonical_pdf_bytes

    with pytest.raises(NonReproduciblePdf):
        canonical_pdf_bytes(b"%PDF-1.7\ntrailer\n<</Size 1/Root 1 0 R>>\nstartxref\n9\n%%EOF\n")
