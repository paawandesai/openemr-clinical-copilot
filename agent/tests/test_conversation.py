"""
POST /pre-visit-brief/conversation (app/api/conversation.py), its pipeline
(app/graph/conversation.py) and drafting step
(app/reasoning/draft_conversation_turn.py).

No network and no credentials. FHIR is faked per patient — each reader
returns only the records of the patient_id it is called with, as the real
patient-scoped searches do — and the model is a scripted drafter or a fake
Anthropic stream. The conversation store, routing, the verifier, rendering,
contracts and logging are real. The live counterpart, against the real
OpenEMR and model, is evals/test_i_conversation.py.

    cd agent && python -m pytest tests/test_conversation.py
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from app.conversation.state import ConversationStore, VerifiedTurn
from app.fhir import client as fhir_client
from app.fhir.exceptions import FhirAuthError, PatientNotFoundError
from app.graph import conversation as graph
from app.main import app
from app.models.api import ConversationTurnResponse, ErrorResponse
from app.models.claims import Claim
from app.models.conversation import ConversationGap, TurnStatus
from app.models.fhir_resources import (
    AllergyIntolerance,
    Condition,
    Encounter,
    FhirResourceType as R,
    MedicationRequest,
    Observation,
)
from app.reasoning import draft_conversation_turn as drafting
from app.reasoning import draft_follow_up
from app.reasoning.draft_claims import ClaimDraftingError
from app.reasoning.draft_conversation_turn import TurnDraft
from app.reasoning.draft_follow_up import Coverage
from app.verification import checker
from tests.test_follow_up import AUTH, TOKEN, USER, _fake_client

PATIENT_A = "a2c0e096-23d5-4cf9-ad5b-4895597363a7"
PATIENT_B = "b7d1f207-34e6-4d0a-be6c-5906608474b8"
SESSION_ID = "19fcd915-be87-4d6b-8cd0-026fff5be9d4"
KEY_A = f"{USER}:{PATIENT_A}:{SESSION_ID}"
KEY_B = f"{USER}:{PATIENT_B}:{SESSION_ID}"
URL = "/pre-visit-brief/conversation"


def _utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=timezone.utc)


# Patient A: the chart the conversation starts on. Every name is unique to A.
A_LISINOPRIL = MedicationRequest(
    source_id="a-med-1", display="lisinopril 10 MG Oral Tablet", status="completed",
    rxnorm_code="314076", authored_date=_utc(2015, 10, 25, 20, 17, 33),
)
A_NAPROXEN = MedicationRequest(
    source_id="a-med-2", display="Naproxen 500 MG Oral Tablet", status="active", authored_date=datetime.min
)
A_CKD = Condition(source_id="a-cond-1", display="Chronic kidney disease stage 3 (disorder)", clinical_status="active")
A_PENICILLIN = AllergyIntolerance(source_id="a-allergy-1", display="Penicillin V", clinical_status="active")
A_CREATININE = [
    Observation(source_id=f"a-obs-cr-{n}", value=value, unit="mg/dL", loinc_code="2160-0", effective_date=when)
    for n, (value, when) in enumerate(
        [(1.4, _utc(2026, 4, 5)), (1.31, _utc(2025, 11, 2)), (1.2, _utc(2025, 5, 20))], start=1
    )
]
A_GLUCOSE = Observation(
    source_id="a-obs-glu-1", value=101.5, unit="mg/dL", loinc_code="2345-7", effective_date=_utc(2026, 4, 5)
)
A_VISITS = [
    Encounter(source_id="a-enc-1", date=_utc(2026, 4, 19, 22, 56)),
    Encounter(source_id="a-enc-2", date=_utc(2026, 4, 5, 22, 56)),
]

# Patient B: nothing in common with A.
B_METFORMIN = MedicationRequest(
    source_id="b-med-1", display="Metformin 500 MG Oral Tablet", status="active",
    rxnorm_code="860975", authored_date=_utc(2021, 3, 1),
)
B_DIABETES = Condition(source_id="b-cond-1", display="Diabetes mellitus type 2 (disorder)", clinical_status="active")

CHARTS: dict[str, dict[R, list[Any]]] = {
    PATIENT_A: {
        R.CONDITION: [A_CKD],
        R.MEDICATION_REQUEST: [A_LISINOPRIL, A_NAPROXEN],
        R.ALLERGY_INTOLERANCE: [A_PENICILLIN],
        R.OBSERVATION: [*A_CREATININE, A_GLUCOSE],
        R.ENCOUNTER: A_VISITS,
    },
    PATIENT_B: {
        R.CONDITION: [B_DIABETES],
        R.MEDICATION_REQUEST: [B_METFORMIN],
        R.ALLERGY_INTOLERANCE: [],
        R.OBSERVATION: [],
        R.ENCOUNTER: [],
    },
}
A_ONLY_TEXT = ("lisinopril", "Naproxen", "kidney", "Penicillin", "a-med", "a-cond", "a-allergy", "a-obs", "a-enc")


def _claim(resource_type: R, source_id: str, field: str, value: str | float | None) -> Claim:
    return Claim(resource_type=resource_type, source_id=source_id, field=field, value=value)


Drafter = Callable[[dict[R, list[Any]], str, tuple[VerifiedTurn, ...]], TurnDraft]


def _answer(*claims: Claim, coverage: Coverage = Coverage.FULL, gaps: list[ConversationGap] | None = None) -> Drafter:
    return lambda retrieved, question, context: TurnDraft(list(claims), coverage, gaps or [])


class FakeWorld:
    """Patient-scoped fake FHIR plus a scripted drafter; records what each was given."""

    def __init__(self) -> None:
        self.reads: list[tuple[str, str]] = []  # (what, patient_id)
        self.tokens: set[str] = set()
        self.drafts: list[dict[str, Any]] = []
        self.drafter: Drafter = _answer()
        self.patient_error: Exception | None = None

    def patches(self) -> list[Any]:
        async def patient_read(token: str, patient_id: str) -> None:
            self.tokens.add(token)
            self.reads.append(("Patient", patient_id))
            if self.patient_error is not None:
                raise self.patient_error
            if patient_id not in CHARTS:
                raise PatientNotFoundError("FHIR Patient read returned 404")

        def reader(resource_type: R) -> Any:
            async def read(token: str, patient_id: str, *args: Any, **kwargs: Any) -> list[Any]:
                assert kwargs["use_case"].value == "follow_up_drilling"
                assert kwargs["patient_session_key"].split(":")[1] == patient_id
                self.tokens.add(token)
                self.reads.append((resource_type.value, patient_id))
                return list(CHARTS[patient_id][resource_type])

            return read

        async def draft(
            retrieved: dict[R, list[Any]], question: str, context: tuple[VerifiedTurn, ...], **_: Any
        ) -> TurnDraft:
            self.drafts.append({"retrieved": retrieved, "question": question, "context": context})
            return self.drafter(retrieved, question, context)

        return [
            mock.patch.object(fhir_client, "ensure_patient_exists", patient_read),
            mock.patch.object(fhir_client, "get_conditions", reader(R.CONDITION)),
            mock.patch.object(fhir_client, "get_medication_requests", reader(R.MEDICATION_REQUEST)),
            mock.patch.object(fhir_client, "get_allergy_intolerances", reader(R.ALLERGY_INTOLERANCE)),
            mock.patch.object(fhir_client, "get_observations", reader(R.OBSERVATION)),
            mock.patch.object(fhir_client, "get_encounters", reader(R.ENCOUNTER)),
            mock.patch.object(graph, "draft_conversation_turn", draft),
        ]

    def searches(self, patient_id: str | None = None) -> list[str]:
        return [what for what, pid in self.reads if what != "Patient" and patient_id in (None, pid)]


@pytest.fixture
def world() -> Iterator[FakeWorld]:
    fake = FakeWorld()
    patches = fake.patches()
    with mock.patch.object(graph, "STORE", ConversationStore()):
        for patcher in patches:
            patcher.start()
        try:
            yield fake
        finally:
            for patcher in patches:
                patcher.stop()


def _post(question: str, patient_id: str = PATIENT_A, session_id: str = SESSION_ID, **kwargs: Any) -> Any:
    return TestClient(app).post(
        URL,
        json={"patient_id": patient_id, "session_id": session_id, "question": question},
        headers=kwargs.pop("headers", {**AUTH, "X-Correlation-ID": "corr-turn"}),
    )


def _turn(question: str, patient_id: str = PATIENT_A, **kwargs: Any) -> ConversationTurnResponse:
    response = _post(question, patient_id, **kwargs)
    assert response.status_code == 200, response.text
    return ConversationTurnResponse.model_validate_json(response.text)


def _items(body: ConversationTurnResponse) -> list[str]:
    return [line for line in body.answer.splitlines() if line.startswith("  - ")]


def _med_claims(record: MedicationRequest, *fields: str) -> list[Claim]:
    values = {"display": record.display, "status": record.status, "authored_date": "2015-10-25"}
    return [_claim(R.MEDICATION_REQUEST, record.source_id, field, values[field]) for field in fields]


# -- a verified answer, with attribution ----------------------------------------


def test_verified_answer_contract_attribution_and_events(world: FakeWorld) -> None:
    question = "SENTINEL-QUESTION is the lisinopril still active?"
    world.drafter = _answer(*_med_claims(A_LISINOPRIL, "display", "status"))

    with capture_logs() as logs:
        response = _post(question)

    assert response.status_code == 200, response.text
    body = ConversationTurnResponse.model_validate_json(response.text)
    assert body.turn_status is TurnStatus.ANSWERED
    assert (body.patient_session_key, body.correlation_id) == (KEY_A, "corr-turn")
    assert (body.conversation.turn, body.conversation.transition.value) == (1, "started")
    assert (body.conversation.context_turns, body.conversation.state_saved) == (0, True)
    assert 0 < body.conversation.expires_in_seconds <= 1800
    assert [(c.source_id, c.field, c.verified, c.text) for c in body.claims] == [
        ("a-med-1", "display", True, "Medication: lisinopril 10 MG Oral Tablet"),
        ("a-med-1", "status", True, 'Medication "lisinopril 10 MG Oral Tablet", status: completed'),
    ]
    assert _items(body) == [f"  - {c.text}" for c in body.claims]
    assert [(r.resource_type, r.rule.value, r.retrieved) for r in body.retrieval] == [
        (R.CONDITION, "base_set", 1), (R.MEDICATION_REQUEST, "base_set", 2), (R.ALLERGY_INTOLERANCE, "base_set", 1),
    ]
    assert body.answer.splitlines()[-1] == (
        "Answered only from what was retrieved for this turn: 1 problem records (1 active), "
        "2 medication records (1 active), 1 allergy records, lab results not retrieved, "
        "visit dates not retrieved. Nothing else from the chart was retrieved."
    )
    assert world.tokens == {TOKEN} and TOKEN not in response.text
    # The draft step saw exactly the set the claims were then verified against.
    assert set(world.drafts[0]["retrieved"]) == {R.CONDITION, R.MEDICATION_REQUEST, R.ALLERGY_INTOLERANCE}

    events = [log for log in logs if log.get("event") == "agent_event"]
    assert [e["event_type"] for e in events] == [
        "conversation_turn_routed", "claim_verified_event", "claim_verified_event",
        "conversation_turn_completed", "latency_event",
    ]
    assert {e["use_case"] for e in events} == {"follow_up_drilling"}
    assert {e["patient_session_key"] for e in events} == {KEY_A}
    assert {e["event_data"]["correlation_id"] for e in events} == {"corr-turn"}
    dumped = json.dumps(logs, default=str)
    assert "SENTINEL-QUESTION" not in dumped and "lisinopril" not in dumped


# -- multi-turn context: structured verified claims, never text -----------------


def test_turn_two_is_given_turn_ones_verified_claims_and_nothing_else(world: FakeWorld) -> None:
    world.drafter = _answer(
        *_med_claims(A_LISINOPRIL, "display", "status"),
        _claim(R.MEDICATION_REQUEST, "a-med-1", "status", "active"),  # fails: not stored
        _claim(R.MEDICATION_REQUEST, "a-med-999", "display", "Warfarin 5 MG Oral Tablet"),  # fails: not stored
    )
    first = _turn("SENTINEL-TURN-ONE is the lisinopril still active?")
    assert first.turn_status is TurnStatus.PARTIALLY_ANSWERED

    def resolve_that_medication(retrieved: Any, question: str, context: tuple[VerifiedTurn, ...]) -> TurnDraft:
        referent = next(c for c in context[-1].claims if c.resource_type is R.MEDICATION_REQUEST)
        return TurnDraft(
            [
                _claim(R.MEDICATION_REQUEST, referent.source_id, "display", referent.value),
                _claim(R.MEDICATION_REQUEST, referent.source_id, "authored_date", "2015-10-25"),
            ],
            Coverage.FULL,
            [],
        )

    world.drafter = resolve_that_medication
    second = _turn("When was that medication prescribed?")

    assert second.turn_status is TurnStatus.ANSWERED
    assert (second.conversation.turn, second.conversation.transition.value) == (2, "continued")
    assert (second.conversation.context_turns, second.conversation.context_claims) == (1, 2)
    assert [c.text for c in second.claims] == [
        "Medication: lisinopril 10 MG Oral Tablet",
        'Medication "lisinopril 10 MG Oral Tablet", date prescribed: 2015-10-25',
    ]
    (context_turn,) = world.drafts[1]["context"]
    assert context_turn.turn == 1
    assert [(c.source_id, c.field, c.value) for c in context_turn.claims] == [
        ("a-med-1", "display", "lisinopril 10 MG Oral Tablet"), ("a-med-1", "status", "completed"),
    ]
    # Turn 2 re-read the chart; it did not answer from turn 1's records.
    assert world.searches().count("MedicationRequest") == 2


def test_the_prompt_holds_verified_context_but_no_earlier_question_or_answer_text(world: FakeWorld) -> None:
    """The real drafting step with a fake Anthropic stream: what the model is actually sent."""
    calls: list[dict[str, Any]] = []
    outputs = iter([
        {"claims": [
            {"resource_type": "MedicationRequest", "source_id": "a-med-1", "field": "display",
             "value": "lisinopril 10 MG Oral Tablet"},
            {"resource_type": "MedicationRequest", "source_id": "a-med-404", "field": "display",
             "value": "Warfarin 5 MG Oral Tablet"},
        ], "coverage": "full", "gaps": []},
        {"claims": [
            {"resource_type": "MedicationRequest", "source_id": "a-med-1", "field": "authored_date",
             "value": "2015-10-25"},
        ], "coverage": "full", "gaps": []},
    ])

    def client() -> Any:
        return _fake_client(json.dumps(next(outputs)), calls)

    with (
        mock.patch.object(graph, "draft_conversation_turn", drafting.draft_conversation_turn),
        mock.patch.object(draft_follow_up, "_client", client),
        capture_logs() as logs,
    ):
        first = _turn("SENTINEL-TURN-ONE is the lisinopril still active?")
        second = _turn("SENTINEL-TURN-TWO when was that medication prescribed?")

    assert first.verification.unverified == 1 and "UNVERIFIED" in first.answer
    assert second.turn_status is TurnStatus.ANSWERED
    first_prompt, second_prompt = (call["messages"][0]["content"] for call in calls)
    assert json.loads(first_prompt.split("<verified_context>\n")[1].split("\n</verified_context>")[0]) == []
    context = json.loads(second_prompt.split("<verified_context>\n")[1].split("\n</verified_context>")[0])
    assert context == [{
        "turn": 1,
        "verified_claims": [{
            "resource_type": "MedicationRequest", "source_id": "a-med-1", "field": "display",
            "value": "lisinopril 10 MG Oral Tablet",
        }],
    }]
    assert "SENTINEL-TURN-TWO" in second_prompt
    # Not the first question, not the first answer's wording, not the claim that failed.
    for absent in ("SENTINEL-TURN-ONE", "Follow-up answer", "UNVERIFIED", "Warfarin", "a-med-404"):
        assert absent not in second_prompt
    assert calls[1]["system"] is drafting.SYSTEM_PROMPT
    assert "SENTINEL" not in json.dumps(logs, default=str)


def test_an_unresolved_reference_is_reported_not_guessed(world: FakeWorld) -> None:
    world.drafter = _answer(coverage=Coverage.NONE, gaps=[ConversationGap.UNRESOLVED_REFERENCE])

    body = _turn("Is she still taking that?")

    assert body.turn_status is TurnStatus.NOT_IN_RECORD
    assert body.answer.splitlines()[2:4] == [
        "That information isn't in the retrieved record for this visit.",
        "  - The question refers to something from earlier in this conversation, and nothing verified "
        "in an earlier turn identifies it. Name the medication, problem, allergy or test.",
    ]


# -- retrieval expansion, by rule -----------------------------------------------


def _lab_claims(record: Observation) -> list[Claim]:
    return [
        _claim(R.OBSERVATION, record.source_id, "loinc_code", record.loinc_code),
        _claim(R.OBSERVATION, record.source_id, "value", record.value),
        _claim(R.OBSERVATION, record.source_id, "unit", record.unit),
        _claim(R.OBSERVATION, record.source_id, "effective_date", record.effective_date.date().isoformat()),
    ]


def test_a_lab_question_retrieves_observation_and_verifies_against_it(world: FakeWorld) -> None:
    latest = A_CREATININE[0]
    world.drafter = _answer(*_lab_claims(latest))

    with capture_logs() as logs:
        body = _turn("What was her most recent creatinine?")

    assert body.turn_status is TurnStatus.ANSWERED
    # Read concurrently, so in no fixed order.
    assert sorted(world.searches()) == ["AllergyIntolerance", "Condition", "MedicationRequest", "Observation"]
    assert [(r.resource_type, r.rule.value, r.retrieved, r.considered) for r in body.retrieval][3:] == [
        (R.OBSERVATION, "question_rule", 4, 3),
    ]
    # Narrowed by rule before drafting: the glucose result never reached the model.
    assert [o.source_id for o in world.drafts[0]["retrieved"][R.OBSERVATION]] == [
        "a-obs-cr-1", "a-obs-cr-2", "a-obs-cr-3",
    ]
    name = "LOINC 2160-0 (Creatinine [Mass/volume] in Serum or Plasma) of 2026-04-05"
    tag = " [most recent of 3 results for this test (LOINC 2160-0, 38483-4)]"
    assert [c.text for c in body.claims] == [
        f"Lab result: {name}{tag}",
        f"Lab result {name}, value: 1.4{tag}",
        f"Lab result {name}, unit: mg/dL{tag}",
        f"Lab result {name}, date: 2026-04-05{tag}",
    ]
    assert all(c.verified and c.resource_type is R.OBSERVATION for c in body.claims)
    assert "3 of 4 lab results (up to the 8 most recent per test, LOINC 2160-0, 38483-4)" in body.answer
    routed = next(e for e in logs if e.get("event_type") == "conversation_turn_routed")["event_data"]
    assert routed["rules"] == ["retrieve:labs", "labs:creatinine"]
    assert routed["expansions"] == {"Observation": "question_rule"}


def test_recency_is_computed_in_code_so_an_older_pick_says_so(world: FakeWorld) -> None:
    world.drafter = _answer(*_lab_claims(A_CREATININE[2]))

    body = _turn("What was her most recent creatinine?")

    assert all(c.verified for c in body.claims)
    assert all(
        c.text.endswith("[not the most recent: 2 later of 3 results for this test (LOINC 2160-0, 38483-4)]")
        for c in body.claims
    )


def test_recency_is_ranked_across_the_codes_that_are_one_test(world: FakeWorld) -> None:
    """Found live (eval I3): the latest serum creatinine is not the latest creatinine."""
    blood = Observation(
        source_id="a-obs-cr-blood", value=1.52, unit="mg/dL", loinc_code="38483-4", effective_date=_utc(2026, 5, 1)
    )
    with mock.patch.dict(CHARTS[PATIENT_A], {R.OBSERVATION: [*A_CREATININE, blood, A_GLUCOSE]}):
        world.drafter = _answer(
            _claim(R.OBSERVATION, "a-obs-cr-1", "value", 1.4), _claim(R.OBSERVATION, "a-obs-cr-blood", "value", 1.52)
        )
        body = _turn("What was her most recent creatinine?")

    assert [c.verified for c in body.claims] == [True, True]
    assert body.claims[0].text.endswith(
        "[not the most recent: 1 later of 4 results for this test (LOINC 2160-0, 38483-4)]"
    )
    assert body.claims[1].text.endswith("[most recent of 4 results for this test (LOINC 2160-0, 38483-4)]")
    # A test with one code keeps the per-code wording.
    world.drafter = _answer(_claim(R.OBSERVATION, "a-obs-glu-1", "value", 101.5))
    glucose = _turn("And her glucose?")
    assert glucose.claims[0].text.endswith("[most recent of 1 results for this test (LOINC 2339-0, 2345-7)]")


def test_a_wrong_lab_value_or_a_result_outside_the_bounded_set_is_unverified(world: FakeWorld) -> None:
    world.drafter = _answer(
        _claim(R.OBSERVATION, "a-obs-cr-1", "value", 1.5),
        _claim(R.OBSERVATION, "a-obs-cr-1", "value", "1.4 mg/dL"),
        _claim(R.OBSERVATION, "a-obs-glu-1", "value", 101.5),  # real, but narrowed out of this turn's set
    )

    body = _turn("What was her most recent creatinine?")

    assert body.turn_status is TurnStatus.NOT_IN_RECORD
    assert [(c.verified, c.unverified_reason) for c in body.claims] == [
        (False, checker.FailureReason.VALUE_MISMATCH.value),
        (False, checker.FailureReason.VALUE_MISMATCH.value),
        (False, checker.FailureReason.SOURCE_NOT_FOUND.value),
    ]
    assert all(line.startswith("  - UNVERIFIED: Lab result ") for line in _items(body))


def test_a_visit_question_retrieves_encounter(world: FakeWorld) -> None:
    world.drafter = _answer(_claim(R.ENCOUNTER, "a-enc-1", "date", "2026-04-19"))

    body = _turn("When was she last seen?")

    assert body.turn_status is TurnStatus.ANSWERED
    assert sorted(world.searches()) == ["AllergyIntolerance", "Condition", "Encounter", "MedicationRequest"]
    assert [c.text for c in body.claims] == ["Visit: 2026-04-19 [most recent of 2 visits retrieved]"]
    assert "the 2 most recent visit dates" in body.answer.splitlines()[-1]


def test_a_type_verified_earlier_is_carried_so_a_reference_to_it_can_be_recited(world: FakeWorld) -> None:
    world.drafter = _answer(*_lab_claims(A_CREATININE[0]))
    _turn("What was her most recent creatinine?")

    world.drafter = _answer(*_lab_claims(A_CREATININE[1]))
    body = _turn("And the one before that?")

    assert body.turn_status is TurnStatus.ANSWERED
    assert [(r.resource_type, r.rule.value) for r in body.retrieval][3:] == [(R.OBSERVATION, "context_carry")]
    assert {o.loinc_code for o in world.drafts[1]["retrieved"][R.OBSERVATION]} == {"2160-0"}
    assert body.claims[0].text.endswith(
        "[not the most recent: 1 later of 3 results for this test (LOINC 2160-0, 38483-4)]"
    )


def test_no_matching_lab_result_is_said_in_fixed_words(world: FakeWorld) -> None:
    world.drafter = _answer(coverage=Coverage.NONE, gaps=[ConversationGap.LAB_RESULTS])

    body = _turn("What was her hemoglobin A1c?")

    assert body.turn_status is TurnStatus.NOT_IN_RECORD
    assert world.drafts[0]["retrieved"][R.OBSERVATION] == []
    assert "  - The lab results considered for this turn do not include it." in body.answer
    assert "none of 4 lab results match the tests asked about (LOINC 17856-6, 4548-4)" in body.answer


def test_a_lab_gap_without_lab_retrieval_says_how_to_get_them(world: FakeWorld) -> None:
    world.drafter = _answer(coverage=Coverage.NONE, gaps=[ConversationGap.LAB_RESULTS])

    body = _turn("What about her ferritin?")

    assert "Observation" not in world.searches()
    assert (
        "  - Lab results were not retrieved for this turn. Name the test, or ask about lab results, "
        "to have them retrieved."
    ) in body.answer


# -- refusals: out of scope, and what the verifier cannot check -----------------


@pytest.mark.parametrize(
    ("question", "status", "expected_lines"),
    [
        (
            "What was her blood pressure at the last visit?",
            TurnStatus.REFUSED_OUT_OF_SCOPE,
            [
                "That's not in the record I can retrieve for this visit.",
                "  - Vitals (blood pressure, pulse, weight, temperature) are outside what the co-pilot retrieves.",
            ],
        ),
        (
            "Is her kidney function worsening?",
            TurnStatus.REFUSED_UNVERIFIABLE,
            [
                graph.UNVERIFIABLE_HEADLINE,
                "  - Whether something is improving, worsening or different from before compares several "
                "records. No chart record states it, so it cannot be verified.",
            ],
        ),
    ],
)
def test_refusals_are_decided_by_rule_before_retrieval_or_drafting(
    world: FakeWorld, question: str, status: TurnStatus, expected_lines: list[str]
) -> None:
    world.drafter = _answer(*_med_claims(A_LISINOPRIL, "display"))  # must never be reached

    with capture_logs() as logs:
        body = _turn(question)

    assert body.turn_status is status
    assert body.answer.splitlines()[2:4] == expected_lines
    assert body.answer.splitlines()[-1] == graph.REFUSAL_FOOTER
    assert (body.claims, body.retrieval, body.sections, body.gaps) == ([], [], [], [])
    assert body.verification.claims == 0
    # The Patient read (token proof) is the only FHIR call; the model is not called.
    assert world.reads == [("Patient", PATIENT_A)] and world.drafts == []
    assert body.timing.draft_seconds < 0.05
    types = [e["event_type"] for e in logs if e.get("event") == "agent_event"]
    assert types == ["conversation_turn_routed", "conversation_turn_completed", "latency_event"]


def test_a_refusal_does_not_disturb_the_conversation(world: FakeWorld) -> None:
    world.drafter = _answer(*_med_claims(A_LISINOPRIL, "display"))
    _turn("Is she on lisinopril?")

    refused = _turn("Is that a concern?")
    after = _turn("When was that prescribed?")

    assert refused.turn_status is TurnStatus.REFUSED_UNVERIFIABLE and refused.conversation.turn == 2
    assert (after.conversation.turn, after.conversation.context_turns) == (3, 1)


def test_the_model_flagging_interpretation_never_yields_a_full_answer(world: FakeWorld) -> None:
    """The second layer: a judgment question the wording rules miss still cannot render as answered."""
    world.drafter = _answer(
        *_lab_claims(A_CREATININE[0]), coverage=Coverage.PARTIAL, gaps=[ConversationGap.INTERPRETATION]
    )

    body = _turn("What does her creatinine tell us?")

    assert body.turn_status is TurnStatus.PARTIALLY_ANSWERED
    assert "  - The co-pilot reports what the chart states; it does not interpret, compare or trend it." in body.answer


def test_the_output_schema_has_no_channel_for_prose() -> None:
    schema = drafting.output_schema(tuple(drafting.TURN_RESOURCE_MODELS))

    assert set(schema["properties"]) == {"claims", "coverage", "gaps"}
    assert schema["properties"]["coverage"]["enum"] == ["full", "partial", "none"]
    assert set(schema["properties"]["gaps"]["items"]["enum"]) == {g.value for g in ConversationGap}
    for option in schema["properties"]["claims"]["items"]["anyOf"]:
        assert set(option["properties"]) == {"resource_type", "source_id", "field", "value"}
        assert option["additionalProperties"] is False


# -- unverifiable claims are disclosed, never stated, never remembered ----------


def test_injected_unverifiable_claims_are_disclosed_and_never_become_context(world: FakeWorld) -> None:
    injected = [
        (_claim(R.MEDICATION_REQUEST, "a-med-1", "status", "active"), checker.FailureReason.VALUE_MISMATCH),
        (_claim(R.MEDICATION_REQUEST, "a-med-999", "display", "Warfarin 5 MG"), checker.FailureReason.SOURCE_NOT_FOUND),
        (_claim(R.ALLERGY_INTOLERANCE, "a-allergy-1", "severity", "severe"), checker.FailureReason.FIELD_NOT_ON_SOURCE),
        (_claim(R.OBSERVATION, "a-obs-cr-1", "value", 1.4), checker.FailureReason.SOURCE_NOT_FOUND),
        (_claim(R.CONDITION, "a-cond-1", "display", "Chronic kidney disease stage 4"), checker.FailureReason.VALUE_MISMATCH),
    ]
    world.drafter = _answer(*_med_claims(A_LISINOPRIL, "display"), *(claim for claim, _ in injected))

    with capture_logs() as logs:
        body = _turn("Is she on lisinopril?")

    assert body.turn_status is TurnStatus.PARTIALLY_ANSWERED
    assert [(c.verified, c.unverified_reason) for c in body.claims[1:]] == [
        (False, reason.value) for _, reason in injected
    ]
    assert _items(body) == [
        "  - Medication: lisinopril 10 MG Oral Tablet",
        '  - UNVERIFIED: Medication status "active" (does not match the chart record)',
        '  - UNVERIFIED: Medication "Warfarin 5 MG" (no matching record in the chart)',
        '  - UNVERIFIED: Allergy severity "severe" (not a field the chart record has)',
        '  - UNVERIFIED: Lab result value "1.4" (no matching record in the chart)',
        '  - UNVERIFIED: Problem "Chronic kidney disease stage 4" (does not match the chart record)',
    ]
    assert "5 item(s) above could not be verified against the chart and are marked UNVERIFIED." in body.answer
    failed = [
        e["event_data"] for e in logs
        if e.get("event_type") == "claim_verified_event" and not e["event_data"]["passed"]
    ]
    assert len(failed) == 5 and all(e["disclosed_as_unverified"] for e in failed)

    _turn("And is it active?")
    remembered = [(c.source_id, c.value) for turn in world.drafts[1]["context"] for c in turn.claims]
    assert remembered == [("a-med-1", "lisinopril 10 MG Oral Tablet")]


def test_the_conversation_runs_the_brief_verifier_itself_on_every_turn(world: FakeWorld) -> None:
    assert graph.verify_claims is checker.verify_claims

    world.drafter = _answer(*_med_claims(A_LISINOPRIL, "display"))
    with mock.patch.object(graph, "verify_claims", wraps=checker.verify_claims) as verifier:
        _turn("Is she on lisinopril?")
        _turn("Is that a concern?")  # refused
        _turn("What was her most recent creatinine?")

    assert verifier.call_count == 3
    for call, draft in zip((verifier.call_args_list[0], verifier.call_args_list[2]), world.drafts, strict=True):
        # Verified against the very mapping the draft step was given.
        assert call.args[1] is draft["retrieved"]
    assert verifier.call_args_list[1].args == ([], {})


def test_output_schema_fields_are_exactly_what_the_verifier_can_check() -> None:
    records = {
        R.CONDITION: A_CKD, R.MEDICATION_REQUEST: A_LISINOPRIL, R.ALLERGY_INTOLERANCE: A_PENICILLIN,
        R.OBSERVATION: A_GLUCOSE, R.ENCOUNTER: A_VISITS[0],
    }
    options = drafting.output_schema(tuple(records))["properties"]["claims"]["items"]["anyOf"]
    by_type = {o["properties"]["resource_type"]["const"]: o["properties"]["field"]["enum"] for o in options}

    for resource_type, record in records.items():
        assert set(by_type.pop(resource_type.value)) == checker._claimable_fields(record)
    assert by_type == {}
    # A type that was not retrieved this turn cannot be claimed at all.
    base_only = drafting.output_schema(drafting.turn_resource_types(CHARTS[PATIENT_B] | {}) [:3])
    assert [o["properties"]["resource_type"]["const"] for o in base_only["properties"]["claims"]["items"]["anyOf"]] == [
        "Condition", "MedicationRequest", "AllergyIntolerance",
    ]


# -- cross-patient leakage: the patient switch ---------------------------------


def _echo_everything_known(retrieved: Any, question: str, context: tuple[VerifiedTurn, ...]) -> TurnDraft:
    """A drafter that repeats every claim it was told about, then every record name it was given."""
    claims = [claim.model_copy() for turn in context for claim in turn.claims]
    claims += [
        _claim(resource_type, record.source_id, "display", record.display)
        for resource_type, records in retrieved.items()
        if resource_type in (R.CONDITION, R.MEDICATION_REQUEST, R.ALLERGY_INTOLERANCE)
        for record in records
    ]
    return TurnDraft(claims, Coverage.FULL, [])


def test_switching_patient_mid_conversation_leaks_nothing_and_invalidates_state(world: FakeWorld) -> None:
    world.drafter = _answer(
        *_med_claims(A_LISINOPRIL, "display", "status"),
        _claim(R.ALLERGY_INTOLERANCE, "a-allergy-1", "display", "Penicillin V"),
        _claim(R.CONDITION, "a-cond-1", "display", A_CKD.display),
    )
    _turn("What is she on, and what is she allergic to?")
    world.drafter = _answer(*_lab_claims(A_CREATININE[0]))
    second = _turn("What was her most recent creatinine?")
    assert (second.conversation.turn, second.conversation.context_claims) == (2, 4)

    # Same token, same session, a different patient — and a drafter that would
    # repeat anything of A's it was handed.
    world.drafter = _echo_everything_known
    with capture_logs() as logs:
        response = _post("Is she still taking that medication?", PATIENT_B)

    assert response.status_code == 200, response.text
    body = ConversationTurnResponse.model_validate_json(response.text)
    assert body.conversation.transition.value == "reset_patient_changed"
    assert (body.conversation.turn, body.conversation.context_turns, body.conversation.context_claims) == (1, 0, 0)
    assert (body.patient_id, body.patient_session_key) == (PATIENT_B, KEY_B)

    given = world.drafts[-1]
    assert given["context"] == ()
    # Observation was carried for A; nothing is carried into B.
    assert set(given["retrieved"]) == {R.CONDITION, R.MEDICATION_REQUEST, R.ALLERGY_INTOLERANCE}
    assert sorted(world.searches(PATIENT_B)) == ["AllergyIntolerance", "Condition", "MedicationRequest"]
    assert [(c.source_id, c.verified) for c in body.claims] == [("b-cond-1", True), ("b-med-1", True)]
    for fragment in A_ONLY_TEXT:
        assert fragment not in response.text, fragment

    invalidated = [e for e in logs if e.get("event_type") == "conversation_invalidated"]
    assert len(invalidated) == 1
    assert invalidated[0]["patient_session_key"] == KEY_B
    assert invalidated[0]["event_data"] | {"correlation_id": ""} == {
        "correlation_id": "", "reason": "reset_patient_changed", "previous_patient_session_key": KEY_A,
        "turns_discarded": 2, "claims_discarded": 8,
    }
    for fragment in ("lisinopril", "Penicillin", "kidney"):
        assert fragment not in json.dumps(logs, default=str)

    # Nothing of A is left anywhere in the store, and going back to A starts over.
    stored = graph.STORE.open(USER, SESSION_ID, PATIENT_B)
    assert {c.source_id for turn in stored.context for c in turn.claims} == {"b-cond-1", "b-med-1"}
    back = _turn("And that creatinine?", PATIENT_A)
    assert back.conversation.transition.value == "reset_patient_changed"
    assert (back.conversation.turn, back.conversation.context_turns) == (1, 0)
    assert world.drafts[-1]["context"] == ()


def test_a_question_naming_someone_else_is_answered_for_the_open_chart_and_says_whose(world: FakeWorld) -> None:
    """
    findings.md F-62. Nothing parses the question for a named third party, by
    decision: the answer is about the open chart whatever name the question
    uses. What the contract guarantees instead is that every turn states the
    patient it was answered for, so a UI can put that above the answer.
    """
    world.drafter = _echo_everything_known
    body = _turn("Which of Tonja's medications are active?", PATIENT_B)

    assert body.patient_id == PATIENT_B
    assert body.patient_session_key == KEY_B
    assert {pid for _, pid in world.reads} == {PATIENT_B}
    assert [c.source_id for c in body.claims] == ["b-cond-1", "b-med-1"]
    assert not any(text.lower() in body.answer.lower() for text in A_ONLY_TEXT)


@pytest.mark.parametrize(
    "question",
    ["What is she on?", "Is her kidney function worsening?", "What was her blood pressure?", "When was that prescribed?"],
    ids=["no_claims", "refused_unverifiable", "refused_out_of_scope", "reference_back"],
)
def test_every_turn_states_the_patient_it_was_answered_for(world: FakeWorld, question: str) -> None:
    """F-62: answered, refused or empty, the response names its patient; both UIs head the answer with it."""
    world.drafter = _answer(gaps=[ConversationGap.UNRESOLVED_REFERENCE], coverage=Coverage.NONE)
    for patient_id in (PATIENT_A, PATIENT_B):
        assert _turn(question, patient_id).patient_id == patient_id


def test_patient_a_claims_asserted_during_patient_b_never_verify(world: FakeWorld) -> None:
    """If a model 'remembered' A anyway, the verifier's bounded set is B's records only."""
    world.drafter = _answer(*_med_claims(A_LISINOPRIL, "display"))
    _turn("Is she on lisinopril?")

    world.drafter = _answer(
        _claim(R.MEDICATION_REQUEST, "a-med-1", "display", "lisinopril 10 MG Oral Tablet"),
        _claim(R.ALLERGY_INTOLERANCE, "a-allergy-1", "display", "Penicillin V"),
        _claim(R.MEDICATION_REQUEST, "b-med-1", "display", "lisinopril 10 MG Oral Tablet"),
    )
    body = _turn("Is she still on that?", PATIENT_B)

    assert body.turn_status is TurnStatus.NOT_IN_RECORD
    assert [(c.verified, c.unverified_reason) for c in body.claims] == [
        (False, checker.FailureReason.SOURCE_NOT_FOUND.value),
        (False, checker.FailureReason.SOURCE_NOT_FOUND.value),
        (False, checker.FailureReason.VALUE_MISMATCH.value),
    ]
    assert all(line.startswith("  - UNVERIFIED: ") for line in _items(body))
    assert graph.STORE.open(USER, SESSION_ID, PATIENT_B).context == ()


def test_a_turn_overtaken_by_a_patient_switch_cannot_write_into_the_new_conversation(world: FakeWorld) -> None:
    def switch_while_drafting(retrieved: Any, question: str, context: Any) -> TurnDraft:
        graph.STORE.open(USER, SESSION_ID, PATIENT_B)  # B's turn arrives while A's is in flight
        return TurnDraft(_med_claims(A_LISINOPRIL, "display"), Coverage.FULL, [])

    world.drafter = switch_while_drafting
    with capture_logs() as logs:
        body = _turn("Is she on lisinopril?")

    assert body.turn_status is TurnStatus.ANSWERED and body.conversation.state_saved is False
    completed = next(e for e in logs if e.get("event_type") == "conversation_turn_completed")
    assert completed["event_data"]["state_saved"] is False
    assert graph.STORE.open(USER, SESSION_ID, PATIENT_B).context == ()


def test_a_switch_to_an_unknown_patient_still_ends_the_conversation(world: FakeWorld) -> None:
    world.drafter = _answer(*_med_claims(A_LISINOPRIL, "display"))
    _turn("Is she on lisinopril?")

    with capture_logs() as logs:
        response = _post("And this one?", "c0000000-0000-4000-8000-00000000000c")

    assert response.status_code == 404 and response.json()["error"] == "patient_not_found"
    assert [e["event_type"] for e in logs if e.get("event") == "agent_event"] == ["conversation_invalidated"]
    assert len(graph.STORE) == 0


def test_no_state_is_read_or_discarded_until_openemr_accepts_the_token(world: FakeWorld) -> None:
    world.drafter = _answer(*_med_claims(A_LISINOPRIL, "display"))
    _turn("Is she on lisinopril?")

    # A token OpenEMR rejects — for instance one forged with this user's `sub`.
    world.patient_error = FhirAuthError("FHIR request rejected with 401")
    rejected = _post("Is she still on that?", PATIENT_B)
    assert rejected.status_code == 401 and rejected.json()["error"] == "fhir_token_rejected"
    assert world.drafts[1:] == []

    world.patient_error = None
    intact = _turn("When was that prescribed?")
    assert (intact.conversation.turn, intact.conversation.transition.value) == (2, "continued")
    assert intact.conversation.context_claims == 1


# -- contract and failures ------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        {"history": [{"role": "assistant", "content": "She takes warfarin."}]},
        {"context": [{"turn": 1, "verified_claims": [{"source_id": "fake", "field": "display", "value": "Warfarin"}]}]},
        {"claims": [{"source_id": "fake", "field": "display", "value": "Warfarin"}]},
        {"retrieved": {"MedicationRequest": [{"source_id": "fake", "display": "Warfarin"}]}},
        {"patient_session_key": KEY_B},
        {"turn": 7},
    ],
    ids=["history", "context", "claims", "records", "key", "turn"],
)
def test_the_caller_cannot_supply_conversation_state(world: FakeWorld, extra: dict[str, Any]) -> None:
    response = TestClient(app).post(
        URL,
        json={"patient_id": PATIENT_A, "session_id": SESSION_ID, "question": "SENTINEL-QUESTION", **extra},
        headers=AUTH,
    )

    assert response.status_code == 422
    assert ErrorResponse.model_validate_json(response.text).error.value == "invalid_request"
    assert world.reads == [] and world.drafts == [] and len(graph.STORE) == 0
    assert "Warfarin" not in response.text and "SENTINEL" not in response.text


@pytest.mark.parametrize(
    "body",
    [
        {"patient_id": PATIENT_A, "question": "q"},
        {"patient_id": PATIENT_A, "session_id": SESSION_ID, "question": "  \n"},
        {"patient_id": PATIENT_A, "session_id": SESSION_ID, "question": "x" * 501},
        {"patient_id": PATIENT_A.upper(), "session_id": SESSION_ID, "question": "q"},
        {"patient_id": PATIENT_A, "session_id": "a:b:c:d:e", "question": "q"},
    ],
    ids=["no session", "blank question", "long question", "uppercase id", "colon in session"],
)
def test_body_outside_the_contract_is_422_before_anything_runs(world: FakeWorld, body: dict[str, Any]) -> None:
    response = TestClient(app).post(URL, json=body, headers=AUTH)

    assert response.status_code == 422
    assert world.reads == [] and len(graph.STORE) == 0


def test_missing_or_malformed_bearer_is_401_before_anything_runs(world: FakeWorld) -> None:
    missing = _post("q", headers={})
    malformed = _post("q", headers={"Authorization": "Bearer not-a-jwt"})

    assert (missing.status_code, missing.json()["error"]) == (401, "missing_bearer_token")
    assert (malformed.status_code, malformed.json()["error"]) == (401, "malformed_bearer_token")
    assert world.reads == [] and len(graph.STORE) == 0


def test_a_failed_turn_stores_nothing_and_leaks_no_internals(world: FakeWorld) -> None:
    def fail(*_: Any) -> TurnDraft:
        raise ClaimDraftingError("Model output did not match the conversation-turn schema")

    world.drafter = fail
    with capture_logs() as logs:
        response = _post("SENTINEL-QUESTION is she on lisinopril?")

    assert response.status_code == 502
    assert ErrorResponse.model_validate_json(response.text).error.value == "follow_up_generation_failed"
    assert "schema" not in response.text and "SENTINEL" not in response.text
    assert "SENTINEL" not in json.dumps(logs, default=str)
    assert graph.STORE.open(USER, SESSION_ID, PATIENT_A).context == ()


def test_ending_a_conversation_discards_its_state(world: FakeWorld) -> None:
    world.drafter = _answer(*_med_claims(A_LISINOPRIL, "display"))
    _turn("Is she on lisinopril?")
    end = lambda **kw: TestClient(app).post(  # noqa: E731
        f"{URL}/end", json={"patient_id": PATIENT_A, "session_id": SESSION_ID}, **kw
    )

    assert end().status_code == 401 and len(graph.STORE) == 1
    world.patient_error = FhirAuthError("FHIR request rejected with 401")
    assert end(headers=AUTH).status_code == 401 and len(graph.STORE) == 1
    world.patient_error = None
    assert end(headers=AUTH).json()["ended"] is True and len(graph.STORE) == 0
    assert end(headers=AUTH).json()["ended"] is False

    fresh = _turn("When was that prescribed?")
    assert (fresh.conversation.turn, fresh.conversation.transition.value) == (1, "started")


def test_openapi_publishes_the_conversation_contract() -> None:
    schema = TestClient(app).get("/openapi.json").json()

    for path in (URL, f"{URL}/end"):
        assert set(schema["paths"][path]["post"]["responses"]) == {"200", "401", "404", "422", "500", "502"}
    assert {"ConversationTurnRequest", "ConversationTurnResponse", "TurnClaim", "ConversationInfo"} <= set(
        schema["components"]["schemas"]
    )
    assert set(schema["components"]["schemas"]["ConversationTurnRequest"]["properties"]) == {
        "patient_id", "session_id", "question",
    }


# -- the trim is stated to the model, not only to the physician -----------------


def _many_creatinine(total: int) -> list[Observation]:
    return [
        Observation(
            source_id=f"a-obs-cr-bulk-{n}", value=1.0 + n / 100, unit="mg/dL",
            loinc_code="2160-0", effective_date=_utc(2026, 4, 5) - timedelta(days=30 * n),
        )
        for n in range(total)
    ]


def _prompt_for(question: str, observations: list[Observation]) -> str:
    """One real drafting call against a patched chart; returns the user content."""
    calls: list[dict[str, Any]] = []
    output = json.dumps({"claims": [], "coverage": "none", "gaps": ["lab_results"]})
    with (
        mock.patch.dict(CHARTS[PATIENT_A], {R.OBSERVATION: observations}),
        mock.patch.object(graph, "draft_conversation_turn", drafting.draft_conversation_turn),
        mock.patch.object(draft_follow_up, "_client", lambda: _fake_client(output, calls)),
    ):
        _turn(question)
    return calls[0]["messages"][0]["content"]


def test_a_trimmed_lab_set_tells_the_model_how_many_records_it_cannot_see(world: FakeWorld) -> None:
    """
    The defect this closes: with only <retrieved_records> in the prompt, the
    model's input for a patient holding 83 creatinine results was identical
    to one holding 8, so `coverage: "full"` was drafted on a question the
    turn could not cover. The count is now in the prompt.
    """
    prompt = _prompt_for("What were her creatinine results?", _many_creatinine(83))

    scope = prompt.split("<retrieval_scope>\n")[1].split("\n</retrieval_scope>")[0]
    assert "8 of 83 lab results" in scope
    assert "75 further Observation records are omitted" in scope
    assert "LOINC 2160-0: the 8 most recent of 83 results are included; 75 older results are omitted." in scope


def test_an_untrimmed_lab_set_says_so_rather_than_staying_silent(world: FakeWorld) -> None:
    """
    Silence would be ambiguous in the same direction as before: the model
    must be able to tell a complete set from a cut one, so a complete set
    is stated too.
    """
    prompt = _prompt_for("What were her creatinine results?", list(A_CREATININE))

    scope = prompt.split("<retrieval_scope>\n")[1].split("\n</retrieval_scope>")[0]
    assert "3 of 3 lab results" in scope
    assert "omitted" not in scope


def test_the_scope_block_carries_counts_only_and_no_chart_content(world: FakeWorld) -> None:
    """Invariant 1: the disclosure adds no PHI to the prompt that the records don't already hold."""
    observations = _many_creatinine(83)
    prompt = _prompt_for("What were her creatinine results?", observations)

    scope = prompt.split("<retrieval_scope>\n")[1].split("\n</retrieval_scope>")[0]
    # No value, unit or source_id of any omitted record reaches the block.
    for observation in observations[8:]:
        assert observation.source_id not in scope
        assert str(observation.value) not in scope


def test_the_physician_and_the_model_are_told_the_same_cut(world: FakeWorld) -> None:
    """
    The closing line and <retrieval_scope> are rendered from one LabScope,
    so the answer cannot claim completeness the prompt contradicts.
    """
    with mock.patch.dict(CHARTS[PATIENT_A], {R.OBSERVATION: _many_creatinine(83)}):
        world.drafter = _answer(coverage=Coverage.NONE, gaps=[ConversationGap.LAB_RESULTS])
        body = _turn("What were her creatinine results?")

    assert "8 of 83 lab results" in body.answer
    assert "Answered only from what was retrieved for this turn" in body.answer
