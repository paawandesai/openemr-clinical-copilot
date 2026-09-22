"""
The staged live transcriptions replay through the worker with no network.

`evals/w2/staging/transcriptions/` holds real model output recorded by
`evals/w2/factory/record_transcriptions.py`. These tests do not grade the
model (that is the gate's job, once a human promotes the fixtures); they
check the fixtures are usable: each one is current (same PDF, same prompt
and schema), parses into the strict transcription model, and runs through
the worker to a complete extraction whose located values are real
transcriptions of the page.

`python -m tests.test_intake_extractor_recorded` prints the per-sample
accuracy line the session report quotes.
"""

from __future__ import annotations

import asyncio
import sys

import pytest
from structlog.testing import capture_logs

from app.extraction import tesseract_available
from app.models.w2.documents import ExtractionStatus
from app.workers.intake_extractor import attach_and_extract
from tests.intake_extractor_support import (
    SAMPLES,
    doc_kind,
    ground_truth,
    patient_uuid,
    pdf_bytes,
    raw_from_ground_truth,
    recorded_transcription,
    recorded_vision,
)

RECORDED = tuple(sample for sample in SAMPLES if recorded_transcription(sample) is not None)

# The recordings are not committed yet: the repository's secret scanner
# flags the schema's own `"test_key": "<analyte>"` lines as a generic API
# key, and the hook's rule is that a human decides a false positive. Until
# then they exist only where they were recorded, and these tests skip
# elsewhere rather than fail.
pytestmark = pytest.mark.skipif(
    not RECORDED,
    reason="no recorded transcriptions in evals/w2/staging/transcriptions/ "
    "(record with: python -m evals.w2.factory.record_transcriptions <sample>...)",
)


def _leaves(value, path: str = ""):  # noqa: ANN001, ANN202
    """(path, state, text) for every RawField/RawCheckbox, in document order."""
    if hasattr(value, "state") and hasattr(value, "page"):
        yield path, value.state, getattr(value, "text", getattr(value, "label", ""))
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _leaves(item, f"{path}[{index}]")
        return
    if hasattr(type(value), "model_fields"):
        for name in type(value).model_fields:
            if name in ("panel", "test_key"):
                continue
            yield from _leaves(getattr(value, name), f"{path}.{name}" if path else name)


def accuracy(sample: str) -> tuple[int, int]:
    """
    Transcription agreement with the ground-truth transcription, field by
    field in document order: (fields where state and text both agree, fields).
    Rows are compared by position, so a dropped or extra row counts against it.
    """
    recorded = asyncio.run(recorded_vision(sample)(doc_kind(sample), b"", 0)).raw
    truth = dict(
        (path, (state, text)) for path, state, text in _leaves(raw_from_ground_truth(sample))
    )
    got = dict((path, (state, text)) for path, state, text in _leaves(recorded))
    keys = set(truth) | set(got)
    agree = sum(1 for key in keys if truth.get(key) == got.get(key))
    return agree, len(keys)


def test_there_are_recorded_transcriptions() -> None:
    assert "lab-clean-001" in RECORDED
    assert any(ground_truth(s)["scenario"]["quality"] != "clean" for s in RECORDED)


@pytest.mark.skipif(not tesseract_available(), reason="scanned samples need tesseract")
@pytest.mark.parametrize("sample", RECORDED)
def test_recorded_transcription_replays_to_a_complete_extraction(sample: str) -> None:
    with capture_logs():
        report = asyncio.run(
            attach_and_extract(
                patient_uuid(sample),
                pdf_bytes(sample),
                doc_kind(sample),
                vision=recorded_vision(sample),
            )
        )
    assert report.output.status is ExtractionStatus.COMPLETE
    assert report.vision.calls >= 1 and report.vision.cost_usd is not None


def main() -> int:
    for sample in RECORDED:
        agree, total = accuracy(sample)
        usage = recorded_transcription(sample)["usage"]
        with capture_logs():
            report = asyncio.run(
                attach_and_extract(
                    patient_uuid(sample),
                    pdf_bytes(sample),
                    doc_kind(sample),
                    vision=recorded_vision(sample),
                )
            )
        counts = report.outcome_counts()
        located = counts.get("located", 0)
        attempted = located + counts.get("unlocated", 0)
        print(
            f"{sample}: transcription agrees with ground truth on {agree}/{total} "
            f"({agree / total:.1%}); located {located}/{attempted}; "
            f"${usage['cost_usd']:.5f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
