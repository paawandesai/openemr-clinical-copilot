"""
Answer scope (D49; app/supervisor/scope.py and the answer node in
app/graph/supervisor.py).

What a document answer itemises is decided by rule before the drafter sees
anything: critical, abnormal, not comparable and named rows are offered;
in-range rows are counted in one cited statement; a cap bounds the rest and
says so; an output ceiling that binds leaves what code states on its own.
None of it may hide a panic value, state an unlocated value, or leave an
omission unsaid.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest
from structlog.testing import capture_logs

from app.conversation.state import ConversationStore
from app.graph import supervisor
from app.models.w2.ask import AnswerLimitation, AskResponse, AskStatus
from app.models.w2.source_types import SourceType
from app.reasoning.draft_answer import AnswerTruncatedError, DraftUsage, build_user_content
from app.supervisor import scope
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
    chart_records,
    offline_retriever,
    stored_document,
)

CRITICAL_SAMPLE = "lab-critical-potassium-007"
MULTIPAGE_SAMPLE = "lab-multipage-003"
WHOLE_REPORT = "What were the results on the uploaded lab report?"
LAB_ROWS = 22


def ask(
    question: str,
    sample: str = LAB_SAMPLE,
    *,
    vision=None,  # noqa: ANN001
    drafter: ScriptedDrafter | None = None,
    chart_sample: str = LAB_SAMPLE,
) -> tuple[AskResponse, ScriptedDrafter, list[dict]]:
    drafter = drafter or ScriptedDrafter()

    async def run() -> AskResponse:
        documents = DocumentStore(vision=vision or ground_truth_vision(sample))
        document_id = await stored_document(documents, sample, chart_sample=chart_sample)
        return await supervisor.run_ask_turn(
            TOKEN,
            USER,
            PATIENT,
            "session-scope-0001",
            question,
            [document_id],
            store=ConversationStore(),
            documents=documents,
            retriever=offline_retriever,
            drafter=drafter,
        )

    with capture_logs() as logs:
        response = asyncio.run(run())
    return response, drafter, logs


def offered_rows(drafter: ScriptedDrafter) -> set[str]:
    """Lab-row keys (results.<panel>.<test>) the drafter was shown."""
    return {
        fact.group_key[1]
        for facts in drafter.calls[0]["documents"].values()
        for fact in facts
        if fact.field_path.startswith("results.")
    }


def turn_event(logs: list[dict]) -> dict:
    (turn,) = [e for e in logs if e.get("event_type") == supervisor.TURN_EVENT]
    return turn["event_data"]


# --------------------------------------------------------------------------
# the rule, in isolation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (WHOLE_REPORT, False),
        ("What was her glucose?", False),
        ("Show me all the results on this report", True),
        ("List every value", True),
        ("the full report please", True),
        ("including normal ones", True),
        ("Which results are normal results?", True),
    ],
)
def test_asking_for_every_result_is_worded_not_guessed(question: str, expected: bool) -> None:
    assert scope.asks_for_every_result(question) is expected


def test_the_drafter_is_told_how_many_rows_code_reports_itself() -> None:
    chart = chart_records()
    content = build_user_content(chart, {}, [], "q", (), withheld_rows=21)
    assert "<answer_scope>\n21 lab result(s)" in content
    assert "<answer_scope>" not in build_user_content(chart, {}, [], "q", ())


# --------------------------------------------------------------------------
# the answer path
# --------------------------------------------------------------------------


def test_a_whole_report_question_counts_in_range_rows_in_one_cited_statement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeChart().install(monkeypatch)
    response, drafter, logs = ask(WHOLE_REPORT)

    # Every row on the clean report is in range: none is offered to the model.
    assert offered_rows(drafter) == set()
    assert drafter.calls[0]["withheld_rows"] == LAB_ROWS

    facts = response.answer_view.answer.patient_facts  # type: ignore[union-attr]
    (summary,) = [f for f in facts if f.statement.startswith("Not listed individually")]
    assert f"{LAB_ROWS} result(s)" in summary.statement
    # A value and its printed range per row, each located on the page.
    assert len(summary.citations) == 2 * LAB_ROWS
    assert all(c.source_type is SourceType.DOCUMENT and c.bbox for c in summary.citations)
    # D20: counted by arithmetic against the printed range, never called normal.
    assert not re.search(r"\bnormal\b", summary.statement, re.IGNORECASE)

    assert scope.SCOPE_RULE in response.message
    assert summary.statement in response.message
    assert response.status is AskStatus.PARTIALLY_ANSWERED
    assert AnswerLimitation.ANSWER_SCOPED in response.limitations
    assert turn_event(logs)["answer_scope"]["summarised"] == LAB_ROWS


def test_a_named_test_is_itemised_and_the_rest_still_disclosed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeChart().install(monkeypatch)
    response, drafter, _ = ask("What was her glucose on this report?")
    assert offered_rows(drafter) == {"results.cmp.glucose"}
    assert drafter.calls[0]["withheld_rows"] == LAB_ROWS - 1
    assert "Glucose: result: 89" in response.message
    assert f"Not listed individually: {LAB_ROWS - 1} result(s)" in response.message
    # Answering the question asked is not "partial" because other rows were counted.
    assert response.status is AskStatus.ANSWERED


def test_a_named_panel_is_itemised(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeChart().install(monkeypatch)
    _, drafter, _ = ask("What did her lipid panel show on this report?")
    offered = offered_rows(drafter)
    assert len(offered) == 5 and {row.split(".")[1] for row in offered} == {"lipid"}


def test_a_critical_value_still_leads_and_is_offered_whatever_the_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeChart().install(monkeypatch)
    # Even a cap of zero cannot keep a critical row from the drafter.
    monkeypatch.setattr(scope, "MAX_ITEMISED_ROWS", 0)
    response, drafter, logs = ask(WHOLE_REPORT, CRITICAL_SAMPLE)
    assert offered_rows(drafter) == {"results.cmp.potassium"}
    (critical,) = response.critical_values
    assert critical.stated and critical.analyte == "Potassium"
    lines = response.message.splitlines()
    assert lines[2].startswith("CRITICAL VALUES") and "6.8 mmol/L" in lines[3]
    counts = turn_event(logs)["answer_scope"]
    assert counts["itemised_critical"] == 1 and counts["capped"] == 0


def test_an_abnormal_value_is_itemised_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """lab-multipage-003 prints one L flag, on ferritin, among 44 rows."""
    FakeChart().install(monkeypatch)
    _, drafter, logs = ask(WHOLE_REPORT, MULTIPAGE_SAMPLE, chart_sample=MULTIPAGE_SAMPLE)
    offered = offered_rows(drafter)
    assert any(row.endswith(".ferritin") for row in offered)
    assert turn_event(logs)["answer_scope"]["itemised_abnormal"] >= 1


def test_an_unlocated_value_is_not_counted_as_in_range_and_never_stated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant 4: a reading the locate step could not place is disclosed, not summarised."""
    FakeChart().install(monkeypatch)
    raw = raw_from_ground_truth(LAB_SAMPLE)
    rows = [
        row.model_copy(update={"value": row.value.model_copy(update={"text": "93"})})
        if row.test_key == "glucose"
        else row
        for row in raw.results
    ]
    misread = raw.model_copy(update={"results": rows})

    async def vision(kind, pdf, pages) -> VisionResult:  # noqa: ANN001
        return VisionResult(
            raw=misread, usage=(await ground_truth_vision(LAB_SAMPLE)(kind, pdf, pages)).usage
        )

    response, drafter, _ = ask(WHOLE_REPORT, vision=vision)
    facts = response.answer_view.answer.patient_facts  # type: ignore[union-attr]
    (summary,) = [f for f in facts if f.statement.startswith("Not listed individually")]
    # Not counted as in range, and not cited by the count.
    assert f"{LAB_ROWS - 1} result(s)" in summary.statement
    assert not any(
        c.field_or_chunk_id.startswith("results.cmp.glucose.") for c in summary.citations
    )
    # Disclosed as unsupported; the misread number is never stated.
    unsupported = response.answer_view.answer.unsupported  # type: ignore[union-attr]
    assert any(
        u.citation is not None and u.citation.field_or_chunk_id == "results.cmp.glucose.value"
        for u in unsupported
    )
    assert "93" not in response.message


def test_rows_past_the_cap_are_named_not_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeChart().install(monkeypatch)
    monkeypatch.setattr(scope, "MAX_ITEMISED_ROWS", 2)
    response, drafter, logs = ask("Show me all the results on this report")
    assert len(offered_rows(drafter)) == 2
    facts = response.answer_view.answer.patient_facts  # type: ignore[union-attr]
    (capped,) = [f for f in facts if "past this answer's limit" in f.statement]
    assert f"{LAB_ROWS - 2} more result(s)" in capped.statement
    assert len(capped.citations) == LAB_ROWS - 2  # each row's located test name
    assert AnswerLimitation.ANSWER_CAPPED in response.limitations
    assert response.status is AskStatus.PARTIALLY_ANSWERED
    assert turn_event(logs)["answer_scope"]["capped"] == LAB_ROWS - 2


def test_an_output_limit_renders_what_code_states_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeChart().install(monkeypatch)
    usage = DraftUsage(input_tokens=5_000, output_tokens=scope.ANSWER_MAX_TOKENS, cost_usd=0.3)
    drafter = ScriptedDrafter(fail_with=AnswerTruncatedError(usage))
    response, _, logs = ask(WHOLE_REPORT, CRITICAL_SAMPLE, drafter=drafter)
    # Not a refusal: the critical value still leads, the count is still there.
    assert response.status is AskStatus.PARTIALLY_ANSWERED
    assert response.message.splitlines()[2].startswith("CRITICAL VALUES")
    assert "Not listed individually" in response.message
    assert scope.OUTPUT_LIMIT_WORDING in response.message
    assert AnswerLimitation.OUTPUT_LIMIT_REACHED in response.limitations
    turn = turn_event(logs)
    assert turn["output_limited"] is True
    assert turn["llm_output_tokens"] == scope.ANSWER_MAX_TOKENS
    assert "Potassium" not in json.dumps(logs, default=str)
