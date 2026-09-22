"""
The intake-extractor worker: a lab PDF or intake form in, verified facts out.

D3's three stages meet here — text layer / OCR (`app.extraction.words`), one
vision call into a strict transcription schema (`vision`), and the
deterministic locate step that decides what may be presented as fact
(`assemble`) — followed by the domain checks: the abnormal-flag cross-check
(`checks`), the intake-vs-chart discrepancies (`discrepancies`, D2) and the
wrong-patient flag (`identity`). The pipeline runs as an untraced LangGraph
(`app/graph/intake_extractor.py`); `worker` is the entry point.

    from app.workers.intake_extractor import attach_and_extract, ExtractionJobs
"""

from app.workers.intake_extractor.chart import ChartSnapshot, PatientIdentity, fhir_chart_reader
from app.workers.intake_extractor.report import ExtractionReport, FieldConfidence

_LAZY = {"ExtractionJobs", "attach_and_extract"}


def __getattr__(name: str) -> object:
    # The worker imports the graph, and the graph imports this package's
    # step modules; resolving the entry points lazily keeps
    # `import app.graph.intake_extractor` from importing itself halfway.
    if name in _LAZY:
        from app.workers.intake_extractor import worker

        return getattr(worker, name)
    raise AttributeError(name)


__all__ = [
    "ChartSnapshot",
    "ExtractionJobs",
    "ExtractionReport",
    "FieldConfidence",
    "PatientIdentity",
    "attach_and_extract",
    "fhir_chart_reader",
]
