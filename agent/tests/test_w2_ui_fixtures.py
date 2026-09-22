"""
scripts/build_ui_fixtures.py — the builder behind the answer view's local
fixture mode.

Two things are worth testing here and neither is "does it run". The first
is the coordinate mapping: for the two scanned samples the recorded box is
on the clean layout and the file that gets drawn is a skewed JPEG of it, so
a builder that forgot the transform would produce a fixture that looks
right and is wrong by a few points everywhere. The second is that the
fixture obeys the same rules the real pipeline does — an `absent` or
`illegible` field arrives as an `UnsupportedField` and never as a claim
(invariant 4), a claim's citations do not mix source types (invariant 3,
D17), and a blank flag column arrives as `not_reported` (D20).

The browser end of the same question — are the boxes on the ink? — is
`interface/modules/custom_modules/oe-module-clinical-copilot/dev/check-overlay-alignment.mjs`,
which renders all six in headless Chrome.

    cd agent && python -m pytest tests/test_w2_ui_fixtures.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app.models.w2.answer_view import AnswerView
from app.models.w2.lab_values import COMPARABLE_REPORTED_FLAGS, ReportedFlag
from app.models.w2.source_types import SourceType

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "build_ui_fixtures.py"
_spec = importlib.util.spec_from_file_location("build_ui_fixtures", _SCRIPT)
assert _spec and _spec.loader
build_ui_fixtures = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = build_ui_fixtures
_spec.loader.exec_module(build_ui_fixtures)

SAMPLES = Path(__file__).resolve().parent.parent / "evals" / "w2" / "factory" / "output" / "samples"

SCENARIOS = (
    sorted(path.name for path in SAMPLES.iterdir() if path.is_dir()) if SAMPLES.is_dir() else []
)

pytestmark = pytest.mark.skipif(not SCENARIOS, reason="doc-factory samples are not present")


def ground_truth(scenario: str) -> dict:
    return json.loads((SAMPLES / scenario / f"{scenario}.ground_truth.json").read_text())


def document_path(scenario: str) -> Path:
    return SAMPLES / scenario / ground_truth(scenario)["document"]["path"]


@pytest.fixture(scope="module")
def views() -> dict[str, AnswerView]:
    return {
        scenario: build_ui_fixtures.build_answer_view(
            ground_truth(scenario), document_path(scenario)
        )
        for scenario in SCENARIOS
    }


class TestScanSpaceBox:
    def test_an_identity_mapping_at_one_pixel_per_point_changes_nothing(self) -> None:
        box = [54.0, 58.34, 326.115, 79.115]
        assert build_ui_fixtures.scan_space_box(box, [1, 0, 0, 0, 1, 0], 612.0, 612.0) == box

    def test_a_pure_scale_cancels_against_the_pixel_size(self) -> None:
        # 150 dpi: 1275px across 612pt. A mapping that is exactly that
        # scale must map a box onto itself, because the two conversions are
        # inverses.
        pixels_per_point = 1275 / 612
        mapped = build_ui_fixtures.scan_space_box(
            [10.0, 20.0, 30.0, 40.0],
            [pixels_per_point, 0, 0, 0, pixels_per_point, 0],
            612.0,
            1275.0,
        )
        assert mapped == pytest.approx([10.0, 20.0, 30.0, 40.0])

    def test_a_translation_moves_the_box_by_the_same_amount(self) -> None:
        mapped = build_ui_fixtures.scan_space_box(
            [10.0, 20.0, 30.0, 40.0], [1, 0, 5, 0, 1, -7], 612.0, 612.0
        )
        assert mapped == pytest.approx([15.0, 13.0, 35.0, 33.0])

    def test_skew_makes_the_box_bigger_because_the_hull_is_the_honest_answer(self) -> None:
        # A rotated rectangle is not a rectangle. Two corners would give a
        # box that misses ink; four corners and the hull covers it.
        import math

        angle = math.radians(2)
        mapping = [math.cos(angle), -math.sin(angle), 0, math.sin(angle), math.cos(angle), 0]
        box = [0.0, 0.0, 200.0, 10.0]
        mapped = build_ui_fixtures.scan_space_box(box, mapping, 612.0, 612.0)
        assert mapped[3] - mapped[1] > box[3] - box[1]
        assert mapped[2] - mapped[0] > box[2] - box[0]


class TestGroundTruthInDocumentSpace:
    @pytest.mark.parametrize("scenario", [s for s in SCENARIOS if "scanned" not in s])
    def test_a_clean_sample_is_left_alone(self, scenario: str) -> None:
        truth = ground_truth(scenario)
        sizes = {page["number"]: (page["width"], page["height"]) for page in truth["pages"]}
        converted = build_ui_fixtures.ground_truth_in_document_space(truth, sizes)
        assert converted["fields"] == truth["fields"]

    @pytest.mark.parametrize("scenario", [s for s in SCENARIOS if "scanned" in s])
    def test_a_scanned_sample_is_mapped_and_says_so(self, scenario: str) -> None:
        truth = ground_truth(scenario)
        sizes = {page["number"]: (page["width"], page["height"]) for page in truth["pages"]}
        converted = build_ui_fixtures.ground_truth_in_document_space(truth, sizes)

        assert truth["coordinates"]["space"] == "clean_pdf"
        assert converted["coordinates"]["space"] == "document_pdf"
        assert converted["coordinates"]["mapped_from"] == "clean_pdf"

        moved = [
            field["field_path"]
            for field, original in zip(converted["fields"], truth["fields"], strict=True)
            if field.get("bbox") != original.get("bbox")
        ]
        assert moved, "a scanned sample's boxes must move"

    @pytest.mark.parametrize("scenario", SCENARIOS)
    def test_the_committed_file_is_never_mutated(self, scenario: str) -> None:
        truth = ground_truth(scenario)
        before = json.dumps(truth, sort_keys=True)
        sizes = {page["number"]: (page["width"], page["height"]) for page in truth["pages"]}
        build_ui_fixtures.ground_truth_in_document_space(truth, sizes)
        assert json.dumps(truth, sort_keys=True) == before

    @pytest.mark.parametrize("scenario", SCENARIOS)
    def test_the_digest_survives_so_the_extraction_id_is_stable(self, scenario: str) -> None:
        from app.models.w2.factory_mapping import ground_truth_extraction_id

        truth = ground_truth(scenario)
        sizes = {page["number"]: (page["width"], page["height"]) for page in truth["pages"]}
        converted = build_ui_fixtures.ground_truth_in_document_space(truth, sizes)
        assert ground_truth_extraction_id(converted) == ground_truth_extraction_id(truth)


class TestBuiltViews:
    def test_all_six_samples_build(self, views: dict[str, AnswerView]) -> None:
        assert len(views) == 6

    @pytest.mark.parametrize("scenario", SCENARIOS)
    def test_every_box_lies_within_its_page(
        self, views: dict[str, AnswerView], scenario: str
    ) -> None:
        view = views[scenario]
        viewer = view.documents[0]
        citations = [
            *view.answer.all_citations(),
            *(flag.citation for flag in view.lab_flags if flag.citation),
            *(field.citation for field in view.answer.unsupported if field.citation),
        ]
        located = [c for c in citations if c.bbox is not None]
        assert located, "a fixture with no located citation has nothing to draw"
        for citation in located:
            page = viewer.page(citation.bbox.page)
            assert page is not None
            assert 0 <= citation.bbox.x0 <= citation.bbox.x1 <= page.width_pt + 0.001
            assert 0 <= citation.bbox.y0 <= citation.bbox.y1 <= page.height_pt + 0.001

    @pytest.mark.parametrize("scenario", SCENARIOS)
    def test_nothing_unsupported_is_stated_as_a_fact(
        self, views: dict[str, AnswerView], scenario: str
    ) -> None:
        # Invariant 4. The two fields this bites on are in
        # intake-missing-allergy-006: an absent date of birth and an
        # illegible city line.
        view = views[scenario]
        statements = " ".join(claim.statement for claim in view.answer.patient_facts)
        for field in view.answer.unsupported:
            assert field.field_path not in statements
            assert field.unverified_value is None

    def test_the_missing_and_illegible_sample_discloses_both(
        self, views: dict[str, AnswerView]
    ) -> None:
        view = views["intake-missing-allergy-006"]
        by_path = {field.field_path: field for field in view.answer.unsupported}
        assert by_path["patient.dob"].reason.value == "not_present_in_document"
        assert by_path["patient.dob"].citation is None
        # Illegible ink has a box: something is there, none of it readable.
        illegible = by_path["patient.city_state_zip"]
        assert illegible.reason.value == "illegible_on_page"
        assert illegible.citation is not None and illegible.citation.bbox is not None

    @pytest.mark.parametrize("scenario", SCENARIOS)
    def test_a_claim_never_mixes_source_types(
        self, views: dict[str, AnswerView], scenario: str
    ) -> None:
        # Invariant 3 and D17. Enforced by the contract; asserted here so a
        # change to the builder's claim selection cannot quietly rely on a
        # validator being relaxed later.
        view = views[scenario]
        for claim in view.answer.patient_facts:
            kinds = {citation.source_type for citation in claim.citations}
            assert len(kinds) == 1
            assert kinds <= {SourceType.CHART, SourceType.DOCUMENT}
        for claim in view.answer.guideline_evidence:
            assert all(c.source_type is SourceType.GUIDELINE for c in claim.citations)

    def test_intake_samples_keep_the_forms_lists_out_of_the_chart(
        self, views: dict[str, AnswerView]
    ) -> None:
        # D2 and invariant 5: an intake medication is a document fact, and
        # the chart's version of the same thing is a separate claim.
        view = views["intake-scanned-light-med-005"]
        document_statements = [c.statement for c in view.answer.document_facts]
        chart_statements = [c.statement for c in view.answer.chart_facts]
        assert any("The form lists amLODIPine" in s for s in document_statements)
        assert any("amLODIPine 2.5 MG Oral Tablet" in s for s in chart_statements)

    def test_the_omitted_allergy_appears_as_a_chart_fact_only(
        self, views: dict[str, AnswerView]
    ) -> None:
        view = views["intake-missing-allergy-006"]
        chart = " ".join(c.statement for c in view.answer.chart_facts)
        document = " ".join(c.statement for c in view.answer.document_facts)
        assert "Fish (substance)" in chart
        assert "Fish (substance)" not in document

    @pytest.mark.parametrize("scenario", [s for s in SCENARIOS if s.startswith("lab-")])
    def test_a_blank_flag_column_is_not_reported(
        self, views: dict[str, AnswerView], scenario: str
    ) -> None:
        # D20's honest count: lab-clean-001 has 22 rows and no printed flag
        # on any of them, so nothing is claimed as normal.
        view = views[scenario]
        assert view.lab_flags
        for flag in view.lab_flags:
            if flag.cross_check.reported is ReportedFlag.NOT_REPORTED:
                assert flag.cross_check.outcome.value == "not_reported"

    def test_the_clean_lab_reports_no_flags_at_all(self, views: dict[str, AnswerView]) -> None:
        view = views["lab-clean-001"]
        assert len(view.lab_flags) == 22
        comparable = [
            f for f in view.lab_flags if f.cross_check.reported in COMPARABLE_REPORTED_FLAGS
        ]
        assert comparable == []

    def test_the_abnormal_lab_claims_only_the_rows_it_flagged(
        self, views: dict[str, AnswerView]
    ) -> None:
        view = views["lab-scanned-heavy-abnormal-002"]
        comparable = [
            f for f in view.lab_flags if f.cross_check.reported in COMPARABLE_REPORTED_FLAGS
        ]
        assert {f.test_name for f in comparable} == {"Glucose", "Potassium", "ALT (SGPT)"}
        for flag in comparable:
            assert flag.cross_check.outcome.value == "agree"

    @pytest.mark.parametrize("scenario", SCENARIOS)
    def test_guideline_evidence_says_it_is_a_fixture(
        self, views: dict[str, AnswerView], scenario: str
    ) -> None:
        # There is no guideline corpus yet. Placeholder text that reads like
        # guidance would be a sentence on a clinician's screen that no
        # guideline ever said.
        view = views[scenario]
        assert view.answer.guideline_evidence
        for claim in view.answer.guideline_evidence:
            assert "FIXTURE" in claim.statement
        for chunk in view.evidence_chunks:
            assert "FIXTURE" in chunk.text
            assert "FIXTURE" in chunk.source.title


class TestBuildOutput:
    def test_build_writes_a_pdf_a_view_and_a_manifest(self, tmp_path: Path) -> None:
        written = build_ui_fixtures.build(SAMPLES, tmp_path, ["lab-clean-001"])
        assert written == ["lab-clean-001"]

        scenario = tmp_path / "lab-clean-001"
        assert (scenario / "document.pdf").read_bytes()[:5] == b"%PDF-"
        assert (scenario / "ground-truth.json").read_bytes() == (
            SAMPLES / "lab-clean-001" / "lab-clean-001.ground_truth.json"
        ).read_bytes()

        view = AnswerView.model_validate_json((scenario / "answer-view.json").read_text())
        assert view.documents[0].document.document_id == "lab-clean-001"

        manifest = json.loads((tmp_path / "manifest.json").read_text())
        assert manifest["scenarios"][0]["scenario_id"] == "lab-clean-001"
        assert manifest["schema_version"] == view.schema_version

    def test_an_unknown_scenario_is_refused_rather_than_skipped(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit, match="no such scenario"):
            build_ui_fixtures.build(SAMPLES, tmp_path, ["not-a-scenario"])
