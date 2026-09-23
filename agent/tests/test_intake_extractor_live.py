"""
One real vision call on lab-clean-001 — opt-in, skipped by default.

    cd agent && W2_EXTRACTOR_LIVE=1 python -m pytest tests/test_intake_extractor_live.py -s

Costs one Anthropic call (roughly 4k image tokens in, a few thousand out).
It needs credentials the SDK can find (ANTHROPIC_API_KEY or an `ant auth`
profile) and never runs in the default suite, which has no network.

Accuracy is measured against the ground truth's `rendered_text`: for each
field the ground truth says is drawn, whether the model transcribed exactly
those characters, and separately whether the worker then located it. The
printed line is what goes in the session report.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from app.models.w2.documents import DocumentKind, ExtractionStatus
from app.workers.intake_extractor import attach_and_extract
from app.workers.intake_extractor.vision import extract_with_claude
from app.workers.intake_extractor.vision_schema import RawLabDocument
from tests.intake_extractor_support import ground_truth, patient_uuid, pdf_bytes

SAMPLE = "lab-clean-001"

pytestmark = pytest.mark.skipif(
    os.environ.get("W2_EXTRACTOR_LIVE") != "1",
    reason="live vision call; set W2_EXTRACTOR_LIVE=1 to run (costs one model call)",
)


def _transcribed(raw: RawLabDocument) -> dict[str, str]:
    """The model's transcription keyed by factory field path, matched on test name."""
    out: dict[str, str] = {}
    header = {
        "patient.name": raw.patient.name,
        "patient.dob": raw.patient.date_of_birth,
        "patient.sex": raw.patient.sex,
        "patient.mrn": raw.patient.mrn,
        "order.accession": raw.order.accession,
        "order.collected": raw.order.collected_at,
        "lab.clia": raw.performing_lab.clia,
    }
    for path, field in header.items():
        if field.state == "present":
            out[path] = field.text
    names = {
        record["rendered_text"]: record["field_path"].rsplit(".", 1)[0]
        for record in ground_truth(SAMPLE)["fields"]
        if record["field_path"].endswith(".name") and record["field_path"].startswith("results.")
    }
    for row in raw.results:
        base = names.get(row.test_name.text)
        if base is None:
            continue
        for attr, field in (
            ("value", row.value),
            ("unit", row.unit),
            ("reference_range", row.reference_range),
            ("flag", row.flag),
        ):
            if field.state == "present":
                out[f"{base}.{attr}"] = field.text
    return out


def test_live_vision_call_on_lab_clean_001() -> None:
    captured: dict[str, object] = {}

    async def recording(kind, pdf, pages):  # noqa: ANN001, ANN202
        result = await extract_with_claude(kind, pdf, pages)
        captured["raw"] = result.raw
        return result

    report = asyncio.run(
        attach_and_extract(
            patient_uuid(SAMPLE), pdf_bytes(SAMPLE), DocumentKind.LAB_PDF, vision=recording
        )
    )
    assert report.output.status is ExtractionStatus.COMPLETE, report.output.failure_reason

    raw = captured["raw"]
    assert isinstance(raw, RawLabDocument)
    transcribed = _transcribed(raw)
    truth = {
        record["field_path"]: record["rendered_text"]
        for record in ground_truth(SAMPLE)["fields"]
        if record["status"] == "present"
        and (record["field_path"] in _HEADER_PATHS or record["field_path"].startswith("results."))
        and not record["field_path"].endswith(".name")
    }
    exact = sum(1 for path, text in truth.items() if transcribed.get(path, None) == text)
    counts = report.outcome_counts()
    located = counts.get("located", 0)
    attempted = located + counts.get("unlocated", 0)
    rows_expected = len({p.rsplit(".", 1)[0] for p in truth if p.startswith("results.")})

    print(
        f"\nLIVE lab-clean-001: rows {len(raw.results)}/{rows_expected}; "
        f"transcription exact-match {exact}/{len(truth)} ({exact / len(truth):.1%}); "
        f"located {located}/{attempted}; "
        f"cross-check comparable {report.flag_cross_check.comparable}, "
        f"disagree {report.flag_cross_check.disagree}; "
        f"tokens in {report.vision.input_tokens} out {report.vision.output_tokens}; "
        f"cost ${report.vision.cost_usd}; latency {report.vision.latency_ms} ms; "
        f"model {report.vision.served_models}"
    )
    assert len(raw.results) == rows_expected
    assert exact / len(truth) >= 0.9
    assert report.flag_cross_check.disagree == 0


_HEADER_PATHS = {
    "patient.name",
    "patient.dob",
    "patient.sex",
    "patient.mrn",
    "order.accession",
    "order.collected",
    "lab.clia",
}
