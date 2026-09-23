"""
Critical-value escalation (H6; app/supervisor/critical.py and the answer
path in app/graph/supervisor.py).

A panic value is surfaced first, by a committed table, whatever the question
was — and the lab-result cap can never be the reason it is not. A document
value the locate step could not place is surfaced as *possible* and its
number is never stated (invariant 4).

The located-document case uses `lab-critical-potassium-007`, a doc-factory
sample staged by this work order (evals/w2/staging/samples/): lab-clean-001's
patient and layout with potassium forced to 6.8 mmol/L.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytest
from structlog.testing import capture_logs

from app.conversation.routing import LAB_RESULTS_GENERAL
from app.conversation.state import ConversationStore
from app.graph import supervisor
from app.models.fhir_resources import FhirResourceType, Observation
from app.models.w2.ask import AskResponse
from app.models.w2.source_types import SourceType
from app.supervisor.critical import (
    CRITICAL_THRESHOLDS,
    Direction,
    evaluate,
    keep_critical,
    observation_direction,
    threshold_for,
)
from app.workers.document_store import DocumentStore
from app.workers.intake_extractor.vision import VisionResult
from tests.intake_extractor_support import ground_truth_vision, raw_from_ground_truth
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

CRITICAL_SAMPLE = "lab-critical-potassium-007"
POTASSIUM = next(row for row in CRITICAL_THRESHOLDS if row.analyte == "Potassium")


def observation(source_id: str, value: float, day: int, code: str = "2823-3", unit="mmol/L"):
    return Observation(
        source_id=source_id,
        value=value,
        unit=unit,
        loinc_code=code,
        effective_date=datetime(2026, 1, 1).replace(day=day % 28 + 1, month=day // 28 + 1),
    )


# --------------------------------------------------------------------------
# the rules
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "comparator", "expected"),
    [
        (6.8, None, Direction.HIGH),
        (6.0, None, None),  # strictly beyond the limit
        (2.4, None, Direction.LOW),
        (4.1, None, None),
        ("2.0", "<", Direction.LOW),  # <2.0 lies entirely below 2.5
        ("3.0", "<", None),  # <3.0 straddles 2.5: not called either way
        ("2.5", "<", Direction.LOW),  # <2.5 is entirely below 2.5
        ("6.0", ">", Direction.HIGH),
        ("5.0", ">", None),
    ],
)
def test_evaluate_treats_censored_values_as_intervals(value, comparator, expected) -> None:
    assert evaluate(POTASSIUM, float(value), comparator) is expected


def test_units_are_matched_exactly_or_not_at_all() -> None:
    """D24: a mg/dL limit never judges a mmol/L number."""
    assert threshold_for(loinc_code="2345-7", unit="mg/dL").analyte == "Glucose"
    assert threshold_for(loinc_code="2345-7", unit="mmol/L") is None
    assert threshold_for(loinc_code="2823-3", unit="mmol/l") is None
    assert threshold_for(loinc_code="14749-6", unit="mmol/L").high == 27.8


def test_a_row_without_a_code_is_matched_by_printed_name() -> None:
    assert threshold_for(loinc_code=None, unit="mmol/L", name="Potassium") is POTASSIUM


def test_the_cap_never_drops_a_critical_result() -> None:
    critical = observation("old-k", 7.1, 0)
    newer = [observation(f"na-{i}", 140.0, i + 1, code="2951-2") for i in range(60)]
    narrowed = sorted(newer, key=lambda o: o.effective_date, reverse=True)[:40]
    assert critical not in narrowed
    kept, restored = keep_critical(narrowed, [critical, *newer])
    assert restored == 1 and critical in kept
    assert kept == sorted(kept, key=lambda o: o.effective_date, reverse=True)


def test_nothing_is_restored_when_nothing_is_critical() -> None:
    rows = [observation("k", 4.0, 1)]
    assert keep_critical([], rows) == ([], 0)
    assert observation_direction(rows[0]) is None


# --------------------------------------------------------------------------
# the answer path
# --------------------------------------------------------------------------


def run_with_document(sample: str, vision=None, question: str = "What is on this report?"):
    async def run() -> AskResponse:
        documents = DocumentStore(vision=vision or ground_truth_vision(sample))
        document_id = await stored_document(documents, sample, chart_sample=LAB_SAMPLE)
        return await supervisor.run_ask_turn(
            TOKEN,
            USER,
            PATIENT,
            "session-critical-0001",
            question,
            [document_id],
            store=ConversationStore(),
            documents=documents,
            retriever=offline_retriever,
            drafter=ScriptedDrafter(),
        )

    return asyncio.run(run())


def test_a_located_critical_value_leads_the_answer_and_is_a_verified_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeChart().install(monkeypatch)
    with capture_logs() as logs:
        response = run_with_document(CRITICAL_SAMPLE)
    (critical,) = response.critical_values
    assert critical.stated and critical.source_type is SourceType.DOCUMENT
    assert (critical.analyte, critical.direction, critical.critical_limit) == (
        "Potassium",
        "high",
        6.0,
    )
    assert all(c.bbox is not None for c in critical.citations)
    lines = response.message.splitlines()
    assert lines[2].startswith("CRITICAL VALUES")
    assert "6.8 mmol/L" in lines[3]
    # The drafter only cited glucose; the critical row is stated anyway.
    facts = response.answer_view.answer.patient_facts  # type: ignore[union-attr]
    assert facts[0].statement == critical.line
    (turn,) = [e for e in logs if e.get("event_type") == supervisor.TURN_EVENT]
    assert turn["event_data"]["critical_values"] == 1
    assert "Potassium" not in json.dumps(logs, default=str)


def test_an_unlocated_critical_reading_is_surfaced_without_its_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant 4 and H6 together: named, paged, never stated."""
    FakeChart().install(monkeypatch)
    raw = raw_from_ground_truth(LAB_SAMPLE)
    rows = [
        row.model_copy(update={"value": row.value.model_copy(update={"text": "6.9"})})
        if row.test_key == "potassium"
        else row
        for row in raw.results
    ]
    misread = raw.model_copy(update={"results": rows})

    async def vision(kind, pdf, pages) -> VisionResult:  # noqa: ANN001
        return VisionResult(
            raw=misread, usage=(await ground_truth_vision(LAB_SAMPLE)(kind, pdf, pages)).usage
        )

    response = run_with_document(LAB_SAMPLE, vision=vision)
    (critical,) = response.critical_values
    assert not critical.stated and critical.citations == []
    assert critical.page == 1
    assert "POSSIBLE CRITICAL VALUE" in response.message
    assert "6.9" not in response.message


def test_a_report_with_no_panic_value_raises_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeChart().install(monkeypatch)
    response = run_with_document(LAB_SAMPLE)
    assert response.critical_values == []
    assert "CRITICAL" not in response.message


def test_a_chart_panic_value_the_cap_would_drop_is_restored_and_surfaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_critical = observation("obs-k-critical", 7.1, 0)
    newer = [
        observation(f"obs-na-{i}", 140.0, i + 1, code="2951-2")
        for i in range(LAB_RESULTS_GENERAL + 20)
    ]
    FakeChart(observations=[old_critical, *newer]).install(monkeypatch)
    drafter = ScriptedDrafter()
    with capture_logs() as logs:
        response = asyncio.run(
            supervisor.run_ask_turn(
                TOKEN,
                USER,
                PATIENT,
                "session-critical-0002",
                "What are her recent lab results?",
                store=ConversationStore(),
                documents=DocumentStore(),
                retriever=offline_retriever,
                drafter=drafter,
            )
        )
    bounded = drafter.calls[0]["chart"][FhirResourceType.OBSERVATION]
    assert old_critical in bounded
    (critical,) = response.critical_values
    assert critical.source_type is SourceType.CHART and critical.source_id == "obs-k-critical"
    assert "not the most recent" not in critical.line  # the only potassium on file
    assert "7.1 mmol/L" in response.message.splitlines()[3]
    (turn,) = [e for e in logs if e.get("event_type") == supervisor.TURN_EVENT]
    assert turn["event_data"]["critical_restored_past_cap"] == 1
