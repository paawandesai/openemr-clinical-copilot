"""
Intake-extractor pipeline — D3's three stages plus the domain checks.

    read_document -> vision -> locate -> verify

  read_document  Validate the bytes as a PDF, hash them (SHA3-512, OpenEMR's
                 algorithm — D16), and read every page's words: PyMuPDF text
                 layer, Tesseract where a page has none (D3 stage 1).
  vision         One Anthropic vision call per document (paginated above 10
                 pages; app/workers/intake_extractor/vision.py). The only
                 model call in the graph (D3 stage 2).
  locate         Every transcribed value located on its page with its
                 declared value class; ExtractedField statuses and per-field
                 confidence (D3 stage 3, assemble.py).
  verify         The chart read (identity; intake meds/allergies), the
                 wrong-patient flag, the abnormal-flag cross-check (lab), the
                 discrepancy check (intake). Deterministic, no model.

Any node may end the run with a typed failure (`ExtractionFailureReason`),
which routes straight to END. Nothing raises out of the graph for an
expected failure; `app/workers/intake_extractor/worker.py` turns the final
state into an `ExtractionReport`.

**Tracing.** Graph state holds page words, extracted values and chart records,
so it is built exactly like the Pre-Visit Brief graph (findings.md F-56):
every node is wrapped in Week 1's `_untraced` guard, which refuses to run
with a LangSmith tracer attached, and the compiled graph is reachable only
through Week 1's `UntracedGraph`, which forces tracing off and accepts no
RunnableConfig. `tests/test_langsmith_tracing_guard.py` already asserts that
no module in `app.graph` holds a raw compiled graph; this module is covered
by that test by being here. The document bytes are runtime context, not
state, so the graph's input — the one thing a directly attached tracer posts
before a node can refuse — is `{"node_seconds": {}}`, exactly as the brief's.
"""

from __future__ import annotations

import asyncio
import hashlib
import operator
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

import pymupdf
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import ValidationError

from app.extraction import DocumentWords, OcrUnavailableError, TextSource, extract_words
from app.graph.pre_visit_brief import UntracedGraph, _timed, _untraced
from app.models.w2.documents import DocumentChecksums, DocumentKind, SourceDocument
from app.models.w2.intake_form import IntakeFormExtraction
from app.models.w2.lab_pdf import LabPdfExtraction
from app.models.w2.workers import ExtractionFailureReason
from app.models.w2.writeback import document_extraction_id
from app.reasoning.draft_claims import MODEL
from app.workers.intake_extractor.assemble import (
    AssembledExtraction,
    assemble_intake,
    assemble_lab,
)
from app.workers.intake_extractor.chart import ChartReader, ChartSnapshot
from app.workers.intake_extractor.checks import FlagCrossCheckSummary, cross_check_report
from app.workers.intake_extractor.discrepancies import (
    DiscrepancyCheckStatus,
    DiscrepancyReport,
    compare_intake_to_chart,
)
from app.workers.intake_extractor.identity import IdentityCheck, check_identity
from app.workers.intake_extractor.vision import (
    MAX_PAGES,
    VisionCallError,
    VisionExtractor,
    VisionResult,
)
from app.workers.intake_extractor.vision_schema import RawIntakeDocument, RawLabDocument

#: Bumped when extraction behaviour changes meaning. Part of the extraction
#: id (D15: `document_extraction_id`), so re-extracting with a changed
#: extractor never reuses the old run's citations.
EXTRACTOR_VERSION = f"intake-extractor/1.0.0+{MODEL}"

_PDF_MAGIC = b"%PDF-"


@dataclass(frozen=True)
class IntakeExtractorContext:
    """Per-run inputs. Immutable runtime context, never graph state."""

    patient_uuid: str
    #: The document itself. Runtime context, never graph state or graph
    #: input: a LangSmith tracer that reaches the compiled graph directly
    #: posts the root run's *input* before any node's guard can refuse, so
    #: the input must hold nothing from the document — the same reason the
    #: Pre-Visit Brief keeps its bearer token in context (F-56).
    pdf: bytes
    kind: DocumentKind
    vision: VisionExtractor
    chart_reader: ChartReader | None
    document_id: str | None
    filename: str
    read_words: Callable[[bytes], DocumentWords] = extract_words


class IntakeExtractorState(TypedDict, total=False):
    document: SourceDocument
    words: DocumentWords
    vision_result: VisionResult
    assembled: AssembledExtraction
    identity: IdentityCheck
    flag_cross_check: FlagCrossCheckSummary
    discrepancies: DiscrepancyReport
    failure: ExtractionFailureReason
    node_seconds: Annotated[dict[str, float], operator.or_]


def _failed(reason: ExtractionFailureReason) -> dict[str, Any]:
    return {"failure": reason}


async def _read_document(
    state: IntakeExtractorState, runtime: Runtime[IntakeExtractorContext]
) -> dict[str, Any]:
    ctx = runtime.context
    pdf = ctx.pdf
    if not pdf.startswith(_PDF_MAGIC):
        return _failed(ExtractionFailureReason.UNSUPPORTED_MIME_TYPE)
    try:
        handle = pymupdf.open(stream=pdf, filetype="pdf")
        page_count = handle.page_count
        handle.close()
    except Exception:  # noqa: BLE001 - any PyMuPDF failure is an unreadable file
        return _failed(ExtractionFailureReason.UNREADABLE_FILE)
    if page_count < 1:
        return _failed(ExtractionFailureReason.UNREADABLE_FILE)
    if page_count > MAX_PAGES:
        return _failed(ExtractionFailureReason.PAGE_LIMIT_EXCEEDED)

    try:
        words = await asyncio.to_thread(ctx.read_words, pdf)
    except OcrUnavailableError:
        return _failed(ExtractionFailureReason.NO_TEXT_LAYER_AND_OCR_FAILED)
    except Exception:  # noqa: BLE001 - a PDF that opens but cannot be read
        return _failed(ExtractionFailureReason.UNREADABLE_FILE)
    if not any(page.words for page in words.pages):
        # No text layer, and OCR (which ran) found nothing: nothing the
        # model reads could ever be located, so no call is made.
        return _failed(ExtractionFailureReason.NO_TEXT_LAYER_AND_OCR_FAILED)

    digest = hashlib.sha3_512(pdf).hexdigest()
    document = SourceDocument(
        document_id=ctx.document_id or f"sha3-{digest[:32]}",
        kind=ctx.kind,
        filename=ctx.filename,
        mime_type="application/pdf",
        page_count=page_count,
        byte_length=len(pdf),
        checksums=DocumentChecksums(sha3_512=digest),
        has_text_layer=all(page.source is TextSource.TEXT_LAYER for page in words.pages),
    )
    return {"document": document, "words": words}


async def _vision(
    state: IntakeExtractorState, runtime: Runtime[IntakeExtractorContext]
) -> dict[str, Any]:
    ctx = runtime.context
    document = state["document"]
    try:
        result = await ctx.vision(ctx.kind, ctx.pdf, document.page_count)
    except VisionCallError as error:
        return _failed(error.reason)
    expected = RawLabDocument if ctx.kind is DocumentKind.LAB_PDF else RawIntakeDocument
    if not isinstance(result.raw, expected):
        return _failed(ExtractionFailureReason.MODEL_OUTPUT_INVALID)
    return {"vision_result": result}


async def _locate(
    state: IntakeExtractorState, runtime: Runtime[IntakeExtractorContext]
) -> dict[str, Any]:
    ctx = runtime.context
    document = state["document"]
    extraction_id = document_extraction_id(
        document_sha3_512=document.checksums.sha3_512 or "",
        extractor_version=EXTRACTOR_VERSION,
    )
    raw = state["vision_result"].raw
    try:
        if ctx.kind is DocumentKind.LAB_PDF:
            assembled = assemble_lab(raw, state["words"], document, extraction_id)  # type: ignore[arg-type]
        else:
            assembled = assemble_intake(raw, state["words"], document, extraction_id)  # type: ignore[arg-type]
    except ValidationError:
        # The transcription parsed but cannot form a valid extraction (the
        # contract validators refused it). A failed extraction, never a
        # partial one.
        return _failed(ExtractionFailureReason.MODEL_OUTPUT_INVALID)
    if assembled.located_count == 0:
        return {**_failed(ExtractionFailureReason.NO_FIELDS_LOCATED), "assembled": assembled}
    return {"assembled": assembled}


async def _verify(
    state: IntakeExtractorState, runtime: Runtime[IntakeExtractorContext]
) -> dict[str, Any]:
    ctx = runtime.context
    extraction = state["assembled"].extraction
    update: dict[str, Any] = {}

    if isinstance(extraction, LabPdfExtraction):
        update["flag_cross_check"] = cross_check_report(extraction)
        header = extraction.patient_header
    else:
        header = extraction.demographics

    chart: ChartSnapshot | None = None
    chart_status = DiscrepancyCheckStatus.NOT_RUN
    if ctx.chart_reader is not None:
        try:
            chart = await ctx.chart_reader(ctx.patient_uuid)
        except Exception:  # noqa: BLE001 - a chart outage must not fail the extraction
            chart_status = DiscrepancyCheckStatus.CHART_UNAVAILABLE

    identity = check_identity(header, chart.identity if chart else None)
    update["identity"] = identity

    if isinstance(extraction, IntakeFormExtraction):
        if chart is None:
            update["discrepancies"] = DiscrepancyReport(status=chart_status)
        elif identity.hard_flag:
            update["discrepancies"] = DiscrepancyReport(
                status=DiscrepancyCheckStatus.BLOCKED_IDENTITY_MISMATCH
            )
        else:
            update["discrepancies"] = compare_intake_to_chart(extraction, chart)
    return update


def _route(state: IntakeExtractorState) -> str:
    return "end" if state.get("failure") is not None else "next"


def _build_graph() -> Any:
    graph = StateGraph(IntakeExtractorState, context_schema=IntakeExtractorContext)
    # Week 1's wrappers are annotated with the brief's state type, and
    # LangGraph infers a node's input schema from that annotation — which
    # would filter this graph's keys out of every node's input. The schema
    # is therefore given explicitly.
    for name, node in (
        ("read_document", _read_document),
        ("vision", _vision),
        ("locate", _locate),
        ("verify", _verify),
    ):
        graph.add_node(name, _untraced(_timed(name, node)), input_schema=IntakeExtractorState)
    graph.add_edge(START, "read_document")
    graph.add_conditional_edges("read_document", _route, {"next": "vision", "end": END})
    graph.add_conditional_edges("vision", _route, {"next": "locate", "end": END})
    graph.add_conditional_edges("locate", _route, {"next": "verify", "end": END})
    graph.add_edge("verify", END)
    return graph.compile()


INTAKE_EXTRACTOR_GRAPH = UntracedGraph(_build_graph())
