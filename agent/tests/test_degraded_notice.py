"""
A heavily degraded document is said to be so first (D60; app/supervisor/degraded.py
and its use in app/graph/supervisor.py).

The threshold is pinned two ways: at its edge with made-up counts, and
against the six committed samples themselves — the clean and light-scan
documents stay under it and the heavy scan does not — so a threshold moved
off the measurements it was chosen from fails here.
"""

from __future__ import annotations

import asyncio

import pytest

from app.conversation.state import ConversationStore
from app.extraction import tesseract_available
from app.graph import supervisor
from app.models.w2.ask import AnswerLimitation, AskResponse
from app.models.w2.documents import DocumentKind, ExtractionStatus
from app.models.w2.workers import ExtractionFailureReason, IntakeExtractorOutput
from app.supervisor.degraded import (
    DEGRADED_BELOW,
    DocumentQuality,
    degraded_notice,
    document_quality,
)
from app.workers.document_store import DocumentStore
from app.workers.intake_extractor.report import ExtractionReport, FieldConfidence
from app.workers.intake_extractor.vision import VisionResult
from tests.intake_extractor_support import (
    SAMPLES,
    ground_truth_vision,
    patient_uuid,
    raw_from_ground_truth,
    recorded_vision,
)
from tests.w2_graph_support import (
    LAB_SAMPLE,
    PATIENT,
    TOKEN,
    USER,
    FakeChart,
    ScriptedDrafter,
    offline_retriever,
    stored_document,
)

HEAVY = "lab-scanned-heavy-abnormal-002"
#: lab-clean-001 with potassium at 6.8 mmol/L (tests/test_w2_critical.py).
CRITICAL_SAMPLE = "lab-critical-potassium-007"
HEAVY_PATIENT = patient_uuid(HEAVY)
needs_ocr = pytest.mark.skipif(not tesseract_available(), reason="scanned samples need tesseract")


def quality(located: int, unlocated: int, *, scanned: bool = True) -> DocumentQuality:
    return DocumentQuality("d" * 128, DocumentKind.LAB_PDF, located, located + unlocated, scanned)


# --------------------------------------------------------------------------
# the measure and the threshold
# --------------------------------------------------------------------------


def test_the_threshold_is_75_percent_and_strict() -> None:
    assert DEGRADED_BELOW == 0.75
    assert not quality(3, 1).degraded  # exactly 75%
    assert quality(74, 26).degraded
    assert not quality(0, 0).degraded  # nothing read: a failed extraction, not a degraded one


def test_missing_and_illegible_are_not_counted_either_way() -> None:
    """They describe the document, not the reading (the located rate since D24)."""

    def field(outcome: str) -> FieldConfidence:
        return FieldConfidence(
            field_path="x", schema_path="x", value_class="numeric", outcome=outcome
        )

    report = ExtractionReport(
        output=IntakeExtractorOutput(
            status=ExtractionStatus.FAILED,
            document_id="d" * 128,
            kind=DocumentKind.LAB_PDF,
            failure_reason=ExtractionFailureReason.INTERNAL_ERROR,
            correlation_id="0a543f6a-f653-4ec3-b7d9-6c0e4a1c2343",
        ),
        fields=[field("located")] * 3
        + [field("unlocated")]
        + [field("missing")] * 40
        + [field("illegible")] * 5,
        has_text_layer=True,
    )
    measured = document_quality("d" * 128, report)
    assert (measured.located, measured.read, measured.scanned) == (3, 4, False)
    assert not measured.degraded


def test_the_notice_states_counts_and_nothing_else() -> None:
    assert degraded_notice(quality(36, 71)) == (
        "This scan is heavily degraded: 36 of 107 values read from it could be located on the "
        "page and verified; the other 71 are listed as unsupported and are not stated."
    )
    assert degraded_notice(quality(1, 9, scanned=False)).startswith("This document is heavily")
    named = degraded_notice(quality(1, 9), name_document=True)
    assert named.startswith("Uploaded lab report (dddddddddddd…): This scan is heavily degraded")


@pytest.mark.parametrize(
    ("stated", "possible", "tail"),
    [
        (0, 0, "are not stated."),
        (1, 0, "The critical value above from this scan was located on the page and verified."),
        (
            2,
            0,
            "The 2 critical values above from this scan were each located on the page and "
            "verified.",
        ),
        (
            0,
            1,
            "The possible critical value above could not be located, so its number is not stated.",
        ),
        (
            1,
            2,
            "was located on the page and verified. The 2 possible critical values above could "
            "not be located, so their numbers are not stated.",
        ),
    ],
)
def test_the_notice_says_which_critical_values_above_it_were_verified(
    stated: int, possible: int, tail: str
) -> None:
    text = degraded_notice(quality(36, 71), stated_criticals=stated, possible_criticals=possible)
    assert text.endswith(tail)


@needs_ocr
@pytest.mark.parametrize("sample", SAMPLES)
def test_only_the_heavy_scan_of_the_six_samples_is_degraded(sample: str) -> None:
    documents = DocumentStore(vision=ground_truth_vision(sample))
    document_id = asyncio.run(stored_document(documents, sample, patient_uuid(sample)))
    report = documents.report(document_id)
    assert report is not None and report.is_complete
    measured = document_quality(document_id, report)
    assert measured.degraded is (sample == HEAVY), (sample, measured.located, measured.read)
    if sample == HEAVY:
        assert (measured.located, measured.read, measured.scanned) == (36, 107, True)


# --------------------------------------------------------------------------
# the answer
# --------------------------------------------------------------------------


def ask_about(sample: str, vision, patient: str) -> AskResponse:  # noqa: ANN001
    async def run() -> AskResponse:
        documents = DocumentStore(vision=vision)
        document_id = await stored_document(documents, sample, patient)
        return await supervisor.run_ask_turn(
            TOKEN,
            USER,
            patient,
            "session-degraded-0001",
            "What is on this report?",
            [document_id],
            store=ConversationStore(),
            documents=documents,
            retriever=offline_retriever,
            drafter=ScriptedDrafter(),
        )

    return asyncio.run(run())


@needs_ocr
@pytest.mark.parametrize("vision", ["replay", "recorded real vision"])
def test_the_heavy_scans_answer_opens_with_the_notice_when_it_has_no_critical_value(
    monkeypatch: pytest.MonkeyPatch, vision: str
) -> None:
    FakeChart(patients=(HEAVY_PATIENT,)).install(monkeypatch)
    extractor = ground_truth_vision(HEAVY) if vision == "replay" else recorded_vision(HEAVY)
    response = ask_about(HEAVY, extractor, HEAVY_PATIENT)

    lines = response.message.splitlines()
    assert lines[0].startswith("Answer (turn")
    # This scan has no critical value, so nothing sits above the notice.
    assert response.critical_values == []
    assert lines[2].startswith("This scan is heavily degraded: 36 of 107 values")
    assert AnswerLimitation.DEGRADED_DOCUMENT in response.limitations
    # Said once, at the top; not repeated among the closing caveats.
    assert response.message.count("could be located on the page") == 1
    # Every value the answer states is a located one: nothing unverified is stated.
    view = response.answer_view
    assert view is not None
    for claim in view.answer.patient_facts:
        for citation in claim.citations:
            assert citation.source_type.value != "document" or citation.bbox is not None


def test_a_clean_document_gets_no_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeChart().install(monkeypatch)
    response = ask_about(LAB_SAMPLE, ground_truth_vision(LAB_SAMPLE), PATIENT)
    assert "heavily degraded" not in response.message
    assert AnswerLimitation.DEGRADED_DOCUMENT not in response.limitations


def test_the_critical_values_lead_and_the_notice_follows_saying_they_were_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A located critical value on a document most of whose other values were
    misread: the critical block leads (D40 item 9), the notice follows it at
    once (D60), and the notice says the critical value was located on the
    page and verified — it is stated because it was.
    """
    FakeChart().install(monkeypatch)
    raw = raw_from_ground_truth(CRITICAL_SAMPLE)

    def misread(field):  # noqa: ANN001, ANN202
        return field.model_copy(update={"text": "zq/zq"})

    rows = [
        row
        if row.test_key == "potassium"
        else row.model_copy(
            update={"unit": misread(row.unit), "reference_range": misread(row.reference_range)}
        )
        for row in raw.results
    ]
    misread_raw = raw.model_copy(update={"results": rows})

    async def vision(kind, pdf, pages) -> VisionResult:  # noqa: ANN001
        usage = (await ground_truth_vision(CRITICAL_SAMPLE)(kind, pdf, pages)).usage
        return VisionResult(raw=misread_raw, usage=usage)

    async def run() -> AskResponse:
        documents = DocumentStore(vision=vision)
        document_id = await stored_document(documents, CRITICAL_SAMPLE, chart_sample=LAB_SAMPLE)
        return await supervisor.run_ask_turn(
            TOKEN,
            USER,
            PATIENT,
            "session-degraded-0002",
            "What is on this report?",
            [document_id],
            store=ConversationStore(),
            documents=documents,
            retriever=offline_retriever,
            drafter=ScriptedDrafter(),
        )

    response = asyncio.run(run())
    lines = response.message.splitlines()
    assert lines[2].startswith("CRITICAL VALUES")
    assert "6.8 mmol/L" in lines[3]
    assert lines[4].startswith("(Flagged by rule")  # the critical block's footnote
    notice = lines[6]
    assert notice.startswith("This document is heavily degraded:")
    assert notice.endswith(
        "The critical value above from this document was located on the page and verified."
    )
    (critical,) = response.critical_values
    assert critical.stated and all(c.bbox is not None for c in critical.citations)
    assert "zq/zq" not in response.message
