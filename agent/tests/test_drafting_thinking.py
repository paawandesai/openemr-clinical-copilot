"""
Every drafting call sets its thinking explicitly, and sets it off
(D62, answer-step thinking; app/reasoning/draft_claims.BRIEF_THINKING).

On claude-opus-5 an omitted `thinking` means adaptive thinking, whose depth
the provider decides per request. On 2026-09-25 it grew the answer step's
output from ~400 to ~6,600 tokens with no code change, pushing the chart
card's document question past the module's ask timeout. Each call below
copies values into a schema and every claim is then verified
deterministically, so thinking buys latency, not safety.

What these pin, per path (the answer step, the brief, a follow-up turn),
by reading the arguments the Anthropic client receives:
  - `thinking` is `{"type": "disabled"}`;
  - `effort` is one the API accepts with thinking disabled (high or below;
    xhigh or max with thinking disabled is a 400);
  - the llm_usage_event records the thinking mode, so the dashboards can
    tell a thinking-off call from a thinking-on one.

No network: the client is the fake from tests/test_follow_up.py.

    cd agent && python -m pytest tests/test_drafting_thinking.py
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any
from unittest import mock

import pytest
from structlog.testing import capture_logs

from app.models.fhir_resources import FhirResourceType as R
from app.reasoning import draft_answer, draft_claims, draft_follow_up
from app.reasoning.draft_claims import ClaimDraftingError
from tests.test_follow_up import QUESTION, _fake_client, _retrieved

DISABLED = {"type": "disabled"}
#: What the API accepts alongside thinking disabled on claude-opus-5.
EFFORTS_WITH_THINKING_OFF = ("low", "medium", "high")

KEY = "user:patient:session"
CHART = {r: [] for r in (R.CONDITION, R.MEDICATION_REQUEST, R.ALLERGY_INTOLERANCE)}


def _answer_step(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = {"chart_facts": [], "coverage": "none", "gaps": []}
    with mock.patch.object(
        draft_follow_up, "_client", return_value=_fake_client(json.dumps(output), calls)
    ):
        with capture_logs() as logs, contextlib.suppress(ClaimDraftingError):
            asyncio.run(
                draft_answer.draft_answer(CHART, {}, [], QUESTION, (), patient_session_key=KEY)
            )
    return logs


def _brief(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = {"claims": [], "coverage": "full", "gaps": []}
    with (
        mock.patch.object(draft_claims, "_STUB_LLM_DRAFTING", False),
        mock.patch.object(
            draft_claims, "_client", return_value=_fake_client(json.dumps(output), calls)
        ),
    ):
        with capture_logs() as logs, contextlib.suppress(ClaimDraftingError):
            asyncio.run(draft_claims.draft_pre_visit_brief(CHART, patient_session_key=KEY))
    return logs


def _follow_up(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = {"claims": [], "coverage": "full", "gaps": []}
    with mock.patch.object(
        draft_follow_up, "_client", return_value=_fake_client(json.dumps(output), calls)
    ):
        with capture_logs() as logs, contextlib.suppress(ClaimDraftingError):
            asyncio.run(
                draft_follow_up.draft_follow_up(_retrieved(), QUESTION, patient_session_key=KEY)
            )
    return logs


PATHS = {"answer step": _answer_step, "brief": _brief, "follow-up": _follow_up}


@pytest.mark.parametrize("path", list(PATHS))
def test_thinking_is_set_explicitly_and_off(path: str) -> None:
    calls: list[dict[str, Any]] = []
    PATHS[path](calls)
    (call,) = calls
    assert call["thinking"] == DISABLED
    assert call["output_config"]["effort"] in EFFORTS_WITH_THINKING_OFF


@pytest.mark.parametrize("path", list(PATHS))
def test_the_usage_event_records_the_thinking_mode(path: str) -> None:
    logs = PATHS[path]([])
    (usage,) = [e["event_data"] for e in logs if e.get("event_type") == "llm_usage_event"]
    assert usage["thinking"] == "disabled"
    assert usage["effort"] in EFFORTS_WITH_THINKING_OFF


def test_the_answer_step_passes_its_own_settings_not_the_follow_up_defaults() -> None:
    """The answer step's constants reach the call even if the shared default differs."""
    calls: list[dict[str, Any]] = []
    with (
        mock.patch.object(draft_follow_up, "FOLLOW_UP_THINKING", {"type": "adaptive"}),
        mock.patch.object(draft_follow_up, "EFFORT", "high"),
    ):
        _answer_step(calls)
    (call,) = calls
    assert call["thinking"] == draft_answer.ANSWER_THINKING == DISABLED
    assert call["output_config"]["effort"] == draft_answer.ANSWER_EFFORT
