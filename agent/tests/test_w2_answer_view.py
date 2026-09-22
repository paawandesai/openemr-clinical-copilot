"""
`AnswerView` (app/models/w2/answer_view.py) — the checks that stop a view
promising a source it cannot show.

    cd agent && python -m pytest tests/test_w2_answer_view.py
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.w2.answer import FinalAnswer, GuidelineClaim, PatientFactClaim
from app.models.w2.answer_view import (
    ANSWER_VIEW_SCHEMA_VERSION,
    AnswerView,
    PageGeometry,
    ReportedFlagView,
    ViewerDocument,
)
from app.models.w2.citations import BBox, Citation
from app.models.w2.documents import DocumentChecksums, DocumentKind, SourceDocument
from app.models.w2.extraction import UnsupportedField, UnsupportedReason
from app.models.w2.lab_values import (
    AbnormalFlag,
    CrossCheckOutcome,
    FlagBasis,
    FlagCrossCheck,
    ReportedFlag,
)
from app.models.w2.source_types import SourceType
from app.models.w2.workers import EvidenceChunk, GuidelineSource, RetrievalMethod

CORRELATION_ID = "3f2a7c18-9b41-4d6e-8a02-5c1f9e3b7d44"
EXTRACTION_ID = "w2x-0123456789abcdef0123456789abcdef"
DOCUMENT_ID = "lab-clean-001"


def source_document(pages: int = 1) -> SourceDocument:
    return SourceDocument(
        document_id=DOCUMENT_ID,
        kind=DocumentKind.LAB_PDF,
        filename="lab-clean-001.pdf",
        mime_type="application/pdf",
        page_count=pages,
        byte_length=32032,
        checksums=DocumentChecksums(sha256="e4" + "0" * 62),
        has_text_layer=True,
    )


def viewer_document(pages: int = 1) -> ViewerDocument:
    return ViewerDocument(
        document=source_document(pages),
        pages=[PageGeometry(page=n + 1, width_pt=612.0, height_pt=792.0) for n in range(pages)],
    )


def document_citation(page: int = 1, field_path: str = "patient.name") -> Citation:
    return Citation.located_document(
        source_id=DOCUMENT_ID,
        field_path=field_path,
        quote="Merlyn225 Sanda877 Mueller846",
        bbox=BBox(page=page, x0=130.0, y0=137.54, x1=259.615, y1=149.631),
        extraction_id=EXTRACTION_ID,
    )


def guideline_chunk(chunk_id: str = "fixture-chunk-0001") -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        text="FIXTURE TEXT. Not clinical guidance.",
        source=GuidelineSource(
            guideline_id="fixture-guideline-0001",
            title="FIXTURE SOURCE",
            section="Placeholder section",
            publisher="tests",
            version="0",
            url=None,
        ),
        retrieval_score=0.0,
        rerank_score=None,
        method=RetrievalMethod.LEXICAL,
    )


def guideline_citation(chunk: EvidenceChunk) -> Citation:
    return Citation(
        source_type=SourceType.GUIDELINE,
        source_id=chunk.source.guideline_id,
        page_or_section=chunk.source.section,
        field_or_chunk_id=chunk.chunk_id,
        quote_or_value=chunk.text,
    )


def answer(**overrides) -> FinalAnswer:
    fields = {
        "answer": "An answer.",
        "patient_facts": [],
        "guideline_evidence": [],
        "unsupported": [],
        "correlation_id": CORRELATION_ID,
    }
    fields.update(overrides)
    return FinalAnswer(**fields)


class TestPageGeometry:
    def test_a_quarter_turn_is_the_only_rotation_accepted(self) -> None:
        for rotation in (0, 90, 180, 270):
            assert PageGeometry(page=1, width_pt=612, height_pt=792, rotation=rotation)

    @pytest.mark.parametrize("rotation", [45, -90, 360, 1])
    def test_anything_else_is_refused_here_rather_than_in_the_browser(self, rotation: int) -> None:
        with pytest.raises(ValidationError, match="rotation"):
            PageGeometry(page=1, width_pt=612, height_pt=792, rotation=rotation)

    def test_a_page_has_a_positive_size(self) -> None:
        with pytest.raises(ValidationError):
            PageGeometry(page=1, width_pt=0.0, height_pt=792.0)


class TestViewerDocument:
    def test_geometry_must_cover_every_page_the_document_declares(self) -> None:
        with pytest.raises(ValidationError, match="page_count"):
            ViewerDocument(
                document=source_document(pages=2),
                pages=[PageGeometry(page=1, width_pt=612, height_pt=792)],
            )

    def test_a_page_cannot_be_listed_twice(self) -> None:
        with pytest.raises(ValidationError, match="twice"):
            ViewerDocument(
                document=source_document(pages=2),
                pages=[
                    PageGeometry(page=1, width_pt=612, height_pt=792),
                    PageGeometry(page=1, width_pt=612, height_pt=792),
                ],
            )

    def test_page_zero_is_refused_by_the_page_itself(self) -> None:
        # `ge=1` on PageGeometry.page, so this never reaches the document.
        with pytest.raises(ValidationError, match="greater than or equal to 1"):
            PageGeometry(page=0, width_pt=612, height_pt=792)

    def test_pages_must_be_contiguous_from_one(self) -> None:
        # A two-page document with geometry for pages 1 and 3 would let a
        # citation on page 2 pass the page check by having no page to fail
        # against.
        with pytest.raises(ValidationError, match="page_count"):
            ViewerDocument(
                document=source_document(pages=2),
                pages=[
                    PageGeometry(page=1, width_pt=612, height_pt=792),
                    PageGeometry(page=3, width_pt=612, height_pt=792),
                ],
            )

    def test_page_lookup_returns_none_rather_than_raising(self) -> None:
        viewer = viewer_document(pages=2)
        assert viewer.page(2).page == 2
        assert viewer.page(3) is None


class TestReportedFlagView:
    def test_a_printed_flag_is_cited_from_the_document_that_printed_it(self) -> None:
        with pytest.raises(ValidationError, match="document that printed it"):
            ReportedFlagView(
                test_name="Glucose",
                cross_check=FlagCrossCheck(
                    reported=ReportedFlag.HIGH,
                    computed=AbnormalFlag.HIGH,
                    basis=FlagBasis.NUMERIC_ABOVE_HIGH,
                    outcome=CrossCheckOutcome.AGREE,
                ),
                citation=Citation(
                    source_type=SourceType.CHART,
                    source_id="abc",
                    page_or_section="Observation",
                    field_or_chunk_id="valueQuantity",
                    quote_or_value=111,
                ),
            )

    def test_a_blank_flag_column_is_a_value_and_needs_no_citation(self) -> None:
        # D20: `not_reported` is what a blank column reports. It is a
        # member, not a missing field, and the row still renders.
        view = ReportedFlagView(
            test_name="BUN (Urea Nitrogen)",
            panel="cmp",
            cross_check=FlagCrossCheck(
                reported=ReportedFlag.NOT_REPORTED,
                computed=AbnormalFlag.NORMAL,
                basis=FlagBasis.NUMERIC_IN_RANGE,
                outcome=CrossCheckOutcome.NOT_REPORTED,
            ),
        )
        assert view.cross_check.reported is ReportedFlag.NOT_REPORTED
        assert view.citation is None


class TestAnswerViewSources:
    def test_a_document_citation_needs_a_document_in_the_view(self) -> None:
        with pytest.raises(ValidationError, match="does not carry"):
            AnswerView(
                answer=answer(
                    patient_facts=[
                        PatientFactClaim(statement="A fact.", citations=[document_citation()])
                    ]
                ),
                documents=[],
            )

    def test_a_citation_cannot_point_past_the_end_of_its_document(self) -> None:
        with pytest.raises(ValidationError, match="page 2"):
            AnswerView(
                answer=answer(
                    patient_facts=[
                        PatientFactClaim(statement="A fact.", citations=[document_citation(page=2)])
                    ]
                ),
                documents=[viewer_document(pages=1)],
            )

    def test_a_guideline_citation_needs_its_chunk(self) -> None:
        chunk = guideline_chunk()
        with pytest.raises(ValidationError, match="does not carry"):
            AnswerView(
                answer=answer(
                    guideline_evidence=[
                        GuidelineClaim(statement="Guidance.", citations=[guideline_citation(chunk)])
                    ]
                ),
                evidence_chunks=[],
            )

    def test_a_complete_view_validates(self) -> None:
        chunk = guideline_chunk()
        view = AnswerView(
            answer=answer(
                patient_facts=[
                    PatientFactClaim(statement="A fact.", citations=[document_citation()])
                ],
                guideline_evidence=[
                    GuidelineClaim(statement="Guidance.", citations=[guideline_citation(chunk)])
                ],
            ),
            documents=[viewer_document()],
            evidence_chunks=[chunk],
        )
        assert view.schema_version == ANSWER_VIEW_SCHEMA_VERSION
        assert view.document(DOCUMENT_ID) is not None
        assert view.chunk(chunk.chunk_id) is not None
        assert view.document("nope") is None
        assert view.chunk("nope") is None

    def test_two_documents_cannot_share_an_id(self) -> None:
        with pytest.raises(ValidationError, match="share a document_id"):
            AnswerView(answer=answer(), documents=[viewer_document(), viewer_document()])

    def test_two_chunks_cannot_share_an_id(self) -> None:
        with pytest.raises(ValidationError, match="share a chunk_id"):
            AnswerView(answer=answer(), evidence_chunks=[guideline_chunk(), guideline_chunk()])

    def test_an_unsupported_fields_citation_is_checked_too(self) -> None:
        # An illegible field carries a bbox — the ink is there, it could not
        # be read — so its page has to exist just like a fact's does.
        illegible = UnsupportedField(
            field_path="patient.city_state_zip",
            reason=UnsupportedReason.ILLEGIBLE_ON_PAGE,
            citation=document_citation(page=2, field_path="patient.city_state_zip"),
        )
        with pytest.raises(ValidationError, match="page 2"):
            AnswerView(answer=answer(unsupported=[illegible]), documents=[viewer_document(pages=1)])

    def test_a_lab_flags_citation_is_checked_too(self) -> None:
        with pytest.raises(ValidationError, match="does not carry"):
            AnswerView(
                answer=answer(),
                documents=[],
                lab_flags=[
                    ReportedFlagView(
                        test_name="Glucose",
                        cross_check=FlagCrossCheck(
                            reported=ReportedFlag.HIGH,
                            computed=AbnormalFlag.HIGH,
                            basis=FlagBasis.NUMERIC_ABOVE_HIGH,
                            outcome=CrossCheckOutcome.AGREE,
                        ),
                        citation=document_citation(field_path="results.cmp.glucose.flag"),
                    )
                ],
            )

    def test_the_schema_version_is_pinned(self) -> None:
        with pytest.raises(ValidationError):
            AnswerView(schema_version="w2-ui/answer-view/2.0.0", answer=answer())
