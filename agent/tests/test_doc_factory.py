"""
Tests for the synthetic document factory (evals/w2/factory).

None of these need OpenEMR: the charts the sample scenarios reference are
committed under ``evals/w2/factory/cache/``, which is the point of the cache.

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
import re
from pathlib import Path

import pymupdf
import pytest

from evals.w2.factory.chart import ChartRecord, read_cache
from evals.w2.factory.degrade import _invert_affine, _page_transform
from evals.w2.factory.generate import (
    DEFAULT_CACHE_DIR,
    DEFAULT_SPEC_DIR,
    generate,
    ground_truth_for,
)
from evals.w2.factory.spec import SpecError, load_specs, parse_spec
from evals.w2.factory.validate import validate

FACTORY_DIR = Path(__file__).resolve().parents[1] / "evals" / "w2" / "factory"
SAMPLES_DIR = FACTORY_DIR / "output" / "samples"

SPECS = load_specs(DEFAULT_SPEC_DIR)
SPEC_BY_ID = {s.scenario_id: s for s in SPECS}
SPEC_IDS = sorted(SPEC_BY_ID)


def _chart(spec) -> ChartRecord:
    record = read_cache(DEFAULT_CACHE_DIR, spec.patient_ref)
    assert record is not None, (
        f"no committed chart cache for {spec.scenario_id}; "
        f"run: python -m evals.w2.factory generate evals/w2/factory/specs --refresh"
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


@pytest.mark.parametrize(
    "scenario_id", ["lab-scanned-heavy-abnormal-002", "intake-scanned-light-med-005"]
)
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
            (SAMPLES_DIR / scenario_id / f"{scenario_id}.ground_truth.json").read_text(
                encoding="utf-8"
            )
        )
        recorded = {p["kind"] for p in payload["perturbations"]}
        requested = {p.kind.value for p in spec.perturbations}
        assert requested <= recorded, (
            f"{scenario_id}: {requested - recorded} never reached the ground truth"
        )
        for entry in payload["perturbations"]:
            assert entry["expected_discrepancy"], (
                f"{scenario_id}: {entry['kind']} has no expected_discrepancy"
            )


def test_absent_and_illegible_fields_keep_the_chart_value():
    payload = json.loads(
        (
            SAMPLES_DIR
            / "intake-missing-allergy-006"
            / "intake-missing-allergy-006.ground_truth.json"
        ).read_text(encoding="utf-8")
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
        (
            SAMPLES_DIR
            / "lab-scanned-heavy-abnormal-002"
            / "lab-scanned-heavy-abnormal-002.ground_truth.json"
        ).read_text(encoding="utf-8")
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


#: The spec-validation cases below still run a real generate, so they must name
#: a patient the committed cache already holds — otherwise they fall through to
#: OpenEMR, which both needs a live database and writes a stray cache file into
#: the committed cache directory.
CACHED_PATIENT_REF = "a2c373ec-57bf-41cb-8767-94410dcf663d"


def _spec(**overrides):
    base = {
        "scenario_id": "t-001",
        "seed": 1,
        "patient_ref": CACHED_PATIENT_REF,
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
        parse_spec(
            _spec(perturbations=[{"kind": "abnormal_value", "test": "glucose", "direction": "up"}])
        )


def test_unknown_top_level_keys_are_rejected():
    with pytest.raises(SpecError, match="unknown top-level key"):
        parse_spec(_spec(qualtiy="clean"))


def test_abnormal_value_on_an_unordered_panel_fails(tmp_path):
    spec = parse_spec(
        _spec(
            scenario_id="t-unordered",
            perturbations=[{"kind": "abnormal_value", "test": "ferritin", "direction": "low"}],
        )
    )
    with pytest.raises(SpecError, match="which this report does not order"):
        generate(spec, output_root=tmp_path, cache_dir=DEFAULT_CACHE_DIR)


def test_perturbation_naming_a_field_the_document_never_renders_fails(tmp_path):
    spec = parse_spec(
        _spec(
            scenario_id="t-unknown-field",
            perturbations=[{"kind": "missing_field", "field": "patient.favourite_colour"}],
        )
    )
    with pytest.raises(SpecError, match="are not rendered by this document"):
        generate(spec, output_root=tmp_path, cache_dir=DEFAULT_CACHE_DIR)


def test_a_field_cannot_be_both_missing_and_illegible(tmp_path):
    spec = parse_spec(
        _spec(
            scenario_id="t-conflict",
            perturbations=[
                {"kind": "missing_field", "field": "patient.dob"},
                {"kind": "illegible_field", "field": "patient.dob"},
            ],
        )
    )
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
    from evals.w2.factory.canvas import canonical_pdf_bytes

    digests = set()
    for _ in range(300):
        document = pymupdf.open()
        document.new_page(width=200, height=200)
        document.set_metadata(
            {
                "title": "t",
                "producer": "p",
                "creator": "c",
                "author": "a",
                "subject": "s",
                "keywords": "k",
                "creationDate": "D:20260101000000Z",
                "modDate": "D:20260101000000Z",
            }
        )
        digests.add(
            hashlib.sha256(
                canonical_pdf_bytes(document.tobytes(garbage=4, deflate=True))
            ).hexdigest()
        )
    assert len(digests) == 1, "identical content produced more than one byte sequence"


def test_canonical_pdf_bytes_refuses_a_pdf_it_cannot_canonicalise():
    from evals.w2.factory.canvas import NonReproduciblePdf, canonical_pdf_bytes

    with pytest.raises(NonReproduciblePdf):
        canonical_pdf_bytes(b"%PDF-1.7\ntrailer\n<</Size 1/Root 1 0 R>>\nstartxref\n9\n%%EOF\n")


def test_ground_truth_carries_nothing_that_depends_on_the_run():
    """
    The ground truth must be a function of the spec and the chart, nothing else.

    `chart_source` once recorded whether that run read the cache or OpenEMR,
    which made a `--refresh` run and a cached run produce different bytes for
    the same spec — the determinism tests above only missed it because they
    happen to take the same path every time.
    """
    expected = {
        "system",
        "patient_ref",
        "patient_uuid",
        "medications_on_chart",
        "allergies_on_chart",
        "conditions_on_chart",
    }
    for scenario_id in SPEC_IDS:
        payload = json.loads(
            (SAMPLES_DIR / scenario_id / f"{scenario_id}.ground_truth.json").read_text(
                encoding="utf-8"
            )
        )
        assert set(payload["chart_source"]) == expected, scenario_id


@pytest.mark.parametrize("scenario_id", SPEC_IDS)
def test_specs_reference_a_patient_by_uuid(scenario_id):
    """
    patient_ref is the FHIR UUID, not a pid.

    pid and pubpid are assigned per OpenEMR instance at import, so a spec keyed
    on one cannot be checked against another deployment — which is exactly how
    the first parity run came back all-MISMATCH.
    """
    spec = SPEC_BY_ID[scenario_id]
    assert re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", spec.patient_ref), (
        spec.patient_ref
    )
    chart = _chart(spec)
    assert chart.uuid == spec.patient_ref


# -- nothing is cut without saying so --------------------------------------


def _present_text_fields(payload):
    return [
        f
        for f in payload["fields"]
        if f["status"] == "present" and f["kind"] == "text" and f["rendered_text"]
    ]


@pytest.mark.parametrize("scenario_id", SPEC_IDS)
def test_no_rendered_text_is_silently_cut(scenario_id):
    """
    A field's rendered_text must be the whole of what was drawn.

    The failure this guards against is a field that wrapped or overflowed and
    kept only its first line or first few characters, which reads in the ground
    truth like a short value rather than a lost one. Three shapes are illegal:
    a trailing hyphen, a rendered_text that is a strict prefix of its value
    without declaring why, and a `truncated` field with no ellipsis to show for
    it.
    """
    payload = json.loads(
        (SAMPLES_DIR / scenario_id / f"{scenario_id}.ground_truth.json").read_text(encoding="utf-8")
    )
    problems = []
    for f in _present_text_fields(payload):
        drawn, value, rendering = f["rendered_text"], f["value"], f["rendering"]

        if drawn.rstrip().endswith("-"):
            problems.append(f"{f['field_path']}: rendered_text ends in a hyphen: {drawn!r}")

        if rendering == "truncated" and not drawn.endswith("..."):
            problems.append(f"{f['field_path']}: truncated but no ellipsis: {drawn!r}")

        if (
            isinstance(value, str)
            and value
            and drawn != value
            and value.startswith(drawn)
            and rendering == "verbatim"
        ):
            problems.append(
                f"{f['field_path']}: rendered_text is a strict prefix of value but claims to be "
                f"verbatim — {drawn!r} vs {value!r}"
            )
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("scenario_id", SPEC_IDS)
def test_derived_fields_explain_themselves(scenario_id):
    """A field whose drawn wording differs on purpose has to say why."""
    payload = json.loads(
        (SAMPLES_DIR / scenario_id / f"{scenario_id}.ground_truth.json").read_text(encoding="utf-8")
    )
    for f in _present_text_fields(payload):
        if f["rendering"] != "derived":
            continue
        # A note, or a self-evident case-only change (the letterhead in caps).
        explained = f.get("note") or f["rendered_text"].upper() == str(f["value"]).upper()
        assert explained, f"{scenario_id}: {f['field_path']} is derived with no note"


def _chart_with(**overrides):
    """A minimal ChartRecord, for exercising paths the samples do not reach."""
    base = dict(
        patient_ref="a2c373ec-57bf-41cb-8767-94410dcf663d",
        uuid="a2c373ec-57bf-41cb-8767-94410dcf663d",
        mrn="1",
        first_name="A",
        middle_name="",
        last_name="B",
        dob="1980-01-01",
        sex="Female",
        street="1 Road",
        city="Town",
        state="MA",
        postal_code="01000",
        phone="",
    )
    base.update(overrides)
    return ChartRecord(**base)


def test_a_wrapped_field_joins_its_lines_and_unions_its_boxes():
    """
    No sample wraps, so build one that must.

    The chief concern is the only field drawn over more than one line. It has
    to come back as ONE field carrying both lines, not a first line plus a
    `.line2` sibling.
    """
    from evals.w2.factory.chart import ChartCondition

    long_name = (
        "Chronic obstructive pulmonary disease with acute exacerbation and "
        "associated respiratory insufficiency of the lower airways (disorder)"
    )
    chart = _chart_with(conditions=(ChartCondition(name=long_name, code=None, onset="2020-01-01"),))
    spec = parse_spec(_spec(scenario_id="t-wrap", doc_type="intake_form"))
    payload = ground_truth_for(spec, chart)

    by_path = {f["field_path"]: f for f in payload["fields"]}
    assert "chief_concern.line2" not in by_path, "a wrapped field must not be split into siblings"

    field = by_path["chief_concern"]
    assert field["rendering"] == "wrapped", field["rendering"]

    boxes = field["line_bboxes"]
    assert boxes is not None and len(boxes) > 1, "a wrapped field records one box per line"

    # rendered_text is every line, so the last word of the value survives.
    assert field["rendered_text"].split()[-1] == field["value"].split()[-1]
    assert len(field["rendered_text"]) > len(boxes[0]) * 0  # readability only

    # bbox is the union of the line boxes.
    x0, y0, x1, y1 = field["bbox"]
    assert x0 == pytest.approx(min(b[0] for b in boxes), abs=0.01)
    assert y0 == pytest.approx(min(b[1] for b in boxes), abs=0.01)
    assert x1 == pytest.approx(max(b[2] for b in boxes), abs=0.01)
    assert y1 == pytest.approx(max(b[3] for b in boxes), abs=0.01)
    # and it really is taller than any single line.
    assert (y1 - y0) > max(b[3] - b[1] for b in boxes)


def test_a_truncated_field_says_so_and_carries_an_ellipsis():
    """No sample truncates either; force one and check it is declared."""
    from evals.w2.factory.chart import ChartAllergy

    allergen = (
        "Hydrolysed wheat protein and fermented soya derivative used in "
        "industrial food preparation (substance)"
    )
    chart = _chart_with(
        allergies=(ChartAllergy(allergen=allergen, reaction="Mild", severity=None),)
    )
    spec = parse_spec(_spec(scenario_id="t-trunc", doc_type="intake_form"))
    payload = ground_truth_for(spec, chart)

    field = {f["field_path"]: f for f in payload["fields"]}["allergies[0].allergen"]
    assert field["rendering"] == "truncated", field["rendering"]
    assert field["rendered_text"].endswith("...")
    assert field["value"] == allergen, "the chart's full wording is still recorded"
    assert not field["rendered_text"].rstrip(". ").endswith("-")


def test_fit_breaks_at_a_word_boundary():
    from evals.w2.factory.canvas import fit

    drawn, cut = fit("Laboratory Director: Nadia Okonjo-Reyes, MD", "sans", 8.0, 60.0)
    assert cut is True
    assert drawn.endswith("...")
    # the cut lands between words, not inside "Okonjo-Reyes"
    assert not drawn.rstrip(".").endswith("-")
    assert drawn.rstrip(".").strip() in "Laboratory Director: Nadia Okonjo-Reyes, MD"

    drawn, cut = fit("short", "sans", 8.0, 200.0)
    assert (drawn, cut) == ("short", False)


def test_multiline_field_refuses_to_span_a_page_break():
    from evals.w2.factory.canvas import DocumentCanvas, PageBreakInField, PageGeometry

    canvas = DocumentCanvas(PageGeometry(), seed=1)
    with pytest.raises(PageBreakInField, match="page break"):
        canvas.multiline_field(
            "t.overflow",
            72,
            canvas.geometry.content_bottom - 4,
            ["first line", "second line"],
            line_height=40,
        )
