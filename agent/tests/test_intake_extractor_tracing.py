"""
LangSmith can never trace the intake-extractor graph (findings.md F-56).

Built on the Week 1 harness in `test_langsmith_tracing_guard.py`: tracing
on, the LangSmith endpoint pointed at a loopback recorder, and every
non-loopback DNS lookup refused, so a broken guard fails a test instead of
sending a document anywhere. The control switches both guard layers off
and shows the recorder does receive extracted content — without it, a
recorder that silently stopped receiving would make every "nothing sent"
assertion pass.
"""

from __future__ import annotations

import asyncio

from langchain_core.tracers.langchain import LangChainTracer, wait_for_all_tracers
from structlog.testing import capture_logs

from app.graph.intake_extractor import INTAKE_EXTRACTOR_GRAPH, IntakeExtractorContext
from app.graph.pre_visit_brief import TracingEnabledError
from app.models.w2.documents import DocumentKind
from app.workers.intake_extractor import attach_and_extract
from tests.intake_extractor_support import ground_truth_vision, patient_uuid, pdf_bytes
from tests.test_langsmith_tracing_guard import _reset_langsmith, _TracingEnabled

SAMPLE = "lab-clean-001"
#: Strings drawn on the sample page: the patient's name, the lab's CLIA
#: number, and a result row's test name. None may reach the recorder.
DOCUMENT_MARKERS = ("Merlyn225", "Mueller846", "22D2118740", "Creatinine")


def _context() -> IntakeExtractorContext:
    return IntakeExtractorContext(
        patient_uuid=patient_uuid(SAMPLE),
        pdf=pdf_bytes(SAMPLE),
        kind=DocumentKind.LAB_PDF,
        vision=ground_truth_vision(SAMPLE),
        chart_reader=None,
        document_id=None,
        filename="x.pdf",
    )


class IntakeExtractorTracingGuardTest(_TracingEnabled):
    def _extract(self):  # noqa: ANN202
        with capture_logs():
            return asyncio.run(
                attach_and_extract(
                    patient_uuid(SAMPLE),
                    pdf_bytes(SAMPLE),
                    DocumentKind.LAB_PDF,
                    vision=ground_truth_vision(SAMPLE),
                )
            )

    def assert_no_document_content_sent(self) -> None:
        wait_for_all_tracers()
        payload = self.recorder.payload()
        for marker in DOCUMENT_MARKERS:
            with self.subTest(absent=marker):
                self.assertNotIn(marker.encode(), payload)
        self.assertEqual(self.smith_lookups(), [])

    def test_attach_and_extract_sends_nothing(self) -> None:
        report = self._extract()
        self.assertEqual(report.output.status.value, "complete")  # the graph really ran
        self.assert_nothing_sent()

    def test_tracer_passed_as_a_callback_is_refused_and_sees_no_document(self) -> None:
        import os

        os.environ.pop("LANGSMITH_TRACING")
        _reset_langsmith()
        compiled = INTAKE_EXTRACTOR_GRAPH._compiled
        with capture_logs(), self.assertRaises(TracingEnabledError):
            asyncio.run(
                compiled.ainvoke(
                    {"node_seconds": {}}, {"callbacks": [LangChainTracer()]}, context=_context()
                )
            )
        self.assert_no_document_content_sent()


class IntakeExtractorGuardControlTest(_TracingEnabled):
    def test_extracted_content_reaches_the_recorder_when_the_guard_is_gone(self) -> None:
        self.disable_both_guards()
        with capture_logs():
            asyncio.run(
                attach_and_extract(
                    patient_uuid(SAMPLE),
                    pdf_bytes(SAMPLE),
                    DocumentKind.LAB_PDF,
                    vision=ground_truth_vision(SAMPLE),
                )
            )
        wait_for_all_tracers()
        payload = self.recorder.payload()
        self.assertTrue(any(marker.encode() in payload for marker in DOCUMENT_MARKERS))
