"""
POST /pre-visit-brief/follow-up (app/api/follow_up.py), its pipeline
(app/graph/follow_up.py) and drafting step (app/reasoning/draft_follow_up.py).
No network and no credentials: FHIR retrieval and the Anthropic stream are
faked; the verifier, rendering, contracts and logging are real.

The live counterpart, against the real OpenEMR and model, is
scripts/smoke_test_follow_up.py.

    cd agent && python -m pytest tests/test_follow_up.py
"""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from app.fhir.exceptions import FhirAuthError, PatientNotFoundError
from app.graph import follow_up as graph
from app.main import app
from app.models.api import ErrorResponse, FollowUpResponse
from app.models.claims import Claim
from app.models.fhir_resources import (
    AllergyIntolerance,
    Condition,
    MedicationRequest,
)
from app.models.fhir_resources import (
    FhirResourceType as R,
)
from app.models.follow_up import AnswerStatus, RecordGap
from app.models.telemetry_events import TELEMETRY_EVENT_TYPES
from app.reasoning import draft_follow_up as drafting
from app.reasoning.draft_claims import ClaimDraftingError
from app.reasoning.draft_follow_up import Coverage, FollowUpDraft
from app.verification import checker

PATIENT_ID = "a2c0e096-23d5-4cf9-ad5b-4895597363a7"
SESSION_ID = "19fcd915-be87-4d6b-8cd0-026fff5be9d4"
USER = "9d1c7a4e-3b2f-4e61-8a0d-5f6b7c8d9e01"
KEY = f"{USER}:{PATIENT_ID}:{SESSION_ID}"
QUESTION = "SENTINEL-QUESTION: is the lisinopril still active?"


def _jwt(claims: dict[str, Any]) -> str:
    def segment(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return f"{segment({'alg': 'RS256', 'typ': 'JWT'})}.{segment(claims)}.c2lnbmF0dXJl"


TOKEN = _jwt({"sub": USER})
AUTH = {"Authorization": f"Bearer {TOKEN}"}

LISINOPRIL = MedicationRequest(
    source_id="med-1",
    display="lisinopril 10 MG Oral Tablet",
    status="completed",
    rxnorm_code="314076",
    authored_date=datetime(2015, 10, 25, 20, 17, 33, tzinfo=UTC),
)
NAPROXEN = MedicationRequest(
    source_id="med-2",
    display="Naproxen 500 MG Oral Tablet",
    status="active",
    authored_date=datetime.min,
)
HTN = Condition(
    source_id="cond-1", display="Essential hypertension (disorder)", clinical_status="active"
)
ASPIRIN = AllergyIntolerance(source_id="allergy-1", display="Aspirin", clinical_status="active")


def _retrieved(allergies: list[AllergyIntolerance] | None = None) -> dict[R, list[Any]]:
    return {
        R.CONDITION: [HTN],
        R.MEDICATION_REQUEST: [LISINOPRIL, NAPROXEN],
        R.ALLERGY_INTOLERANCE: [ASPIRIN] if allergies is None else allergies,
    }


class FakePipeline:
    """Fakes FHIR retrieval and drafting; everything after them is real."""

    def __init__(self, draft: FollowUpDraft, retrieved: dict[R, list[Any]] | None = None) -> None:
        self.draft = draft
        self.retrieved = retrieved or _retrieved()
        self.tokens_seen: list[str] = []
        self.drafted_from: dict[R, list[Any]] | None = None
        self.questions_seen: list[str] = []

    def patches(self) -> list[Any]:
        async def patient_read(token: str, patient_id: str) -> None:
            self.tokens_seen.append(token)

        def fetch(resource_type: R) -> Any:
            async def fetch_records(token: str, patient_id: str, **kwargs: Any) -> list[Any]:
                assert kwargs["use_case"].value == "follow_up_drilling"
                assert kwargs["patient_session_key"] == KEY
                self.tokens_seen.append(token)
                return self.retrieved[resource_type]

            return fetch_records

        async def draft(retrieved: dict[R, list[Any]], question: str, **_: Any) -> FollowUpDraft:
            self.drafted_from = retrieved
            self.questions_seen.append(question)
            return self.draft

        return [
            mock.patch.object(graph.fhir_client, "ensure_patient_exists", patient_read),
            mock.patch.object(graph.fhir_client, "get_conditions", fetch(R.CONDITION)),
            mock.patch.object(
                graph.fhir_client, "get_medication_requests", fetch(R.MEDICATION_REQUEST)
            ),
            mock.patch.object(
                graph.fhir_client, "get_allergy_intolerances", fetch(R.ALLERGY_INTOLERANCE)
            ),
            mock.patch.object(graph, "draft_follow_up", draft),
        ]


def _ask(
    pipeline: FakePipeline,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
):
    patches = pipeline.patches()
    for patcher in patches:
        patcher.start()
    try:
        with capture_logs() as logs:
            response = TestClient(app).post(
                "/pre-visit-brief/follow-up",
                json=body
                or {"patient_id": PATIENT_ID, "session_id": SESSION_ID, "question": QUESTION},
                headers={**AUTH, "X-Correlation-ID": "corr-follow-up-1"}
                if headers is None
                else headers,
            )
    finally:
        for patcher in patches:
            patcher.stop()
    return response, logs


def _claim(resource_type: R, source_id: str, field: str, value: str | None) -> Claim:
    return Claim(resource_type=resource_type, source_id=source_id, field=field, value=value)


# -- (a) an answer from the retrieved set --------------------------------------


def test_answer_contract_attribution_and_events() -> None:
    pipeline = FakePipeline(
        FollowUpDraft(
            claims=[
                _claim(R.MEDICATION_REQUEST, "med-1", "display", "lisinopril 10 MG Oral Tablet"),
                _claim(R.MEDICATION_REQUEST, "med-1", "status", "completed"),
                _claim(R.MEDICATION_REQUEST, "med-1", "authored_date", "2015-10-25"),
            ],
            coverage=Coverage.FULL,
            gaps=[],
        )
    )

    response, logs = _ask(pipeline)

    assert response.status_code == 200, response.text
    body = FollowUpResponse.model_validate_json(response.text)
    assert body.answer_status is AnswerStatus.ANSWERED
    assert body.patient_session_key == KEY
    assert body.correlation_id == "corr-follow-up-1" == response.headers["X-Correlation-ID"]
    assert (body.verification.claims, body.verification.verified, body.verification.unverified) == (
        3,
        3,
        0,
    )
    assert [(c.source_id, c.field, c.verified) for c in body.claims] == [
        ("med-1", "display", True),
        ("med-1", "status", True),
        ("med-1", "authored_date", True),
    ]
    assert body.answer.splitlines()[2:6] == [
        "From the records retrieved for this visit:",
        "  - Medication: lisinopril 10 MG Oral Tablet",
        '  - Medication "lisinopril 10 MG Oral Tablet", status: completed',
        '  - Medication "lisinopril 10 MG Oral Tablet", date prescribed: 2015-10-25',
    ]
    assert [(s.resource_type, s.retrieved, s.in_brief) for s in body.sections] == [
        (R.CONDITION, 1, 1),
        (R.MEDICATION_REQUEST, 2, 1),
        (R.ALLERGY_INTOLERANCE, 1, 1),
    ]
    # The token went to FHIR on every read, and the draft saw exactly what was retrieved.
    assert pipeline.tokens_seen == [TOKEN] * 4
    assert pipeline.drafted_from == pipeline.retrieved
    assert TOKEN not in response.text

    events = [
        log
        for log in logs
        if log.get("event") == "agent_event" and log.get("event_type") not in TELEMETRY_EVENT_TYPES
    ]
    assert [e["event_type"] for e in events] == ["claim_verified_event"] * 3 + ["latency_event"]
    assert {e["use_case"] for e in events} == {"follow_up_drilling"}
    assert {e["patient_session_key"] for e in events} == {KEY}
    assert {e["event_data"]["correlation_id"] for e in events} == {"corr-follow-up-1"}
    assert QUESTION not in json.dumps(logs, default=str)


def test_record_with_no_name_is_labelled_as_such_and_its_null_name_never_verifies() -> None:
    """F-57: the model is shown display null; a null display claim fails, other fields still verify."""
    unnamed = AllergyIntolerance(
        source_id="allergy-2",
        display=None,
        clinical_status="active",
        recorded_date=datetime(2020, 5, 1, tzinfo=UTC),
    )
    pipeline = FakePipeline(
        FollowUpDraft(
            claims=[
                _claim(R.ALLERGY_INTOLERANCE, "allergy-2", "display", None),
                _claim(R.ALLERGY_INTOLERANCE, "allergy-2", "recorded_date", "2020-05-01"),
            ],
            coverage=Coverage.FULL,
            gaps=[],
        ),
        retrieved=_retrieved(allergies=[ASPIRIN, unnamed]),
    )

    body = FollowUpResponse.model_validate_json(_ask(pipeline)[0].text)

    assert body.answer_status is AnswerStatus.PARTIALLY_ANSWERED
    assert [(c.field, c.verified, c.unverified_reason) for c in body.claims] == [
        ("display", False, checker.FailureReason.NO_NAME_ON_SOURCE.value),
        ("recorded_date", True, None),
    ]
    assert body.answer.splitlines()[2:5] == [
        "Partly answered from the records retrieved for this visit:",
        '  - UNVERIFIED: Allergy "(name not available)" (the chart record has no usable name; review it in the chart)',
        "  - Allergy (name not available), date recorded: 2020-05-01",
    ]
    assert "None" not in body.answer


# -- (b) refusal when the retrieved set does not hold the answer ---------------


def test_question_outside_the_retrieved_set_is_refused_with_fixed_wording() -> None:
    pipeline = FakePipeline(
        FollowUpDraft(claims=[], coverage=Coverage.NONE, gaps=[RecordGap.LAB_RESULTS])
    )

    response, _ = _ask(pipeline)

    body = FollowUpResponse.model_validate_json(response.text)
    assert body.answer_status is AnswerStatus.NOT_IN_RECORD
    assert body.gaps == [RecordGap.LAB_RESULTS]
    assert body.answer.splitlines()[2:4] == [
        "That information isn't in the retrieved record for this visit.",
        "  - Lab results were not retrieved for this visit.",
    ]
    assert body.verification.claims == 0


def test_refusal_with_only_failed_claims_discloses_them_apart_from_the_gaps() -> None:
    pipeline = FakePipeline(
        FollowUpDraft(
            claims=[_claim(R.MEDICATION_REQUEST, "med-9", "display", "Metformin 500 MG")],
            coverage=Coverage.FULL,
            gaps=[RecordGap.LAB_RESULTS],
        )
    )

    body = FollowUpResponse.model_validate_json(_ask(pipeline)[0].text)

    assert body.answer_status is AnswerStatus.NOT_IN_RECORD
    assert body.answer.splitlines()[2:7] == [
        "That information isn't in the retrieved record for this visit.",
        '  - UNVERIFIED: Medication "Metformin 500 MG" (no matching record in the chart)',
        "",
        "Not in the retrieved record:",
        "  - Lab results were not retrieved for this visit.",
    ]


def test_empty_allergy_retrieval_reads_not_recorded_never_none() -> None:
    pipeline = FakePipeline(
        FollowUpDraft(claims=[], coverage=Coverage.NONE, gaps=[]),
        retrieved=_retrieved(allergies=[]),
    )

    body = FollowUpResponse.model_validate_json(_ask(pipeline)[0].text)

    assert body.answer.splitlines()[-1] == (
        "Answered only from what was retrieved for this visit: 1 problem records (1 active), "
        "2 medication records (1 active), allergies not recorded. Nothing else from the chart was retrieved."
    )


@pytest.mark.parametrize(
    ("coverage", "gaps", "claims", "expected"),
    [
        # The model saying "full" never outranks verification.
        (Coverage.FULL, [], [("med-1", "display", "Metformin")], AnswerStatus.NOT_IN_RECORD),
        (Coverage.FULL, [], [], AnswerStatus.NOT_IN_RECORD),
        (
            Coverage.FULL,
            [],
            [("med-1", "display", "lisinopril 10 MG Oral Tablet"), ("med-1", "status", "active")],
            AnswerStatus.PARTIALLY_ANSWERED,
        ),
        (
            Coverage.FULL,
            [RecordGap.NOT_STATED_IN_RECORD],
            [("med-1", "status", "completed")],
            AnswerStatus.PARTIALLY_ANSWERED,
        ),
        (Coverage.NONE, [], [("med-1", "status", "completed")], AnswerStatus.PARTIALLY_ANSWERED),
        (Coverage.FULL, [], [("med-1", "status", "completed")], AnswerStatus.ANSWERED),
    ],
)
def test_answer_status_is_bounded_by_verification(
    coverage: Coverage,
    gaps: list[RecordGap],
    claims: list[tuple[str, str, str]],
    expected: AnswerStatus,
) -> None:
    results = checker.verify_claims(
        [_claim(R.MEDICATION_REQUEST, *claim) for claim in claims],
        _retrieved(),
        use_case=graph._USE_CASE,
        patient_session_key=KEY,
    )
    assert graph.answer_status(coverage, gaps, results) is expected


# -- (c) unverifiable claims are disclosed, never shown as fact ----------------


def test_injected_unverifiable_claims_are_disclosed_not_stated() -> None:
    injected = [
        (
            _claim(R.MEDICATION_REQUEST, "med-1", "status", "active"),
            checker.FailureReason.VALUE_MISMATCH,
        ),
        (
            _claim(R.MEDICATION_REQUEST, "med-999", "display", "Warfarin 5 MG"),
            checker.FailureReason.SOURCE_NOT_FOUND,
        ),
        (
            _claim(R.ALLERGY_INTOLERANCE, "allergy-1", "severity", "severe"),
            checker.FailureReason.FIELD_NOT_ON_SOURCE,
        ),
        (
            _claim(R.CONDITION, "allergy-1", "display", "Aspirin"),
            checker.FailureReason.SOURCE_NOT_FOUND,
        ),
    ]
    pipeline = FakePipeline(
        FollowUpDraft(
            claims=[
                _claim(R.MEDICATION_REQUEST, "med-1", "display", "lisinopril 10 MG Oral Tablet")
            ]
            + [claim for claim, _ in injected],
            coverage=Coverage.FULL,
            gaps=[],
        )
    )

    response, logs = _ask(pipeline)

    body = FollowUpResponse.model_validate_json(response.text)
    assert body.answer_status is AnswerStatus.PARTIALLY_ANSWERED
    assert [(c.verified, c.unverified_reason) for c in body.claims[1:]] == [
        (False, reason.value) for _, reason in injected
    ]
    items = [line for line in body.answer.splitlines() if line.startswith("  - ")]
    assert items == [
        "  - Medication: lisinopril 10 MG Oral Tablet",
        '  - UNVERIFIED: Medication status "active" (does not match the chart record)',
        '  - UNVERIFIED: Medication "Warfarin 5 MG" (no matching record in the chart)',
        '  - UNVERIFIED: Allergy severity "severe" (not a field the chart record has)',
        '  - UNVERIFIED: Problem "Aspirin" (no matching record in the chart)',
    ]
    assert (
        "4 item(s) above could not be verified against the chart and are marked UNVERIFIED."
        in body.answer
    )
    failed_events = [
        e["event_data"]
        for e in logs
        if e.get("event_type") == "claim_verified_event" and not e["event_data"]["passed"]
    ]
    assert len(failed_events) == 4 and all(e["disclosed_as_unverified"] for e in failed_events)


def test_follow_up_runs_the_brief_verifier_itself() -> None:
    assert graph.verify_claims is checker.verify_claims


# -- request contract and failures --------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"patient_id": PATIENT_ID, "question": QUESTION},
        {"patient_id": PATIENT_ID, "session_id": SESSION_ID, "question": "   \n "},
        {"patient_id": PATIENT_ID, "session_id": SESSION_ID, "question": "x" * 501},
        {"patient_id": PATIENT_ID.upper(), "session_id": SESSION_ID, "question": QUESTION},
        {
            "patient_id": PATIENT_ID,
            "session_id": SESSION_ID,
            "question": QUESTION,
            "retrieved": {"MedicationRequest": [{"source_id": "fake", "display": "Warfarin"}]},
        },
        {
            "patient_id": PATIENT_ID,
            "session_id": SESSION_ID,
            "question": QUESTION,
            "claims": [{"source_id": "fake", "field": "display", "value": "Warfarin"}],
        },
    ],
    ids=[
        "no session",
        "blank question",
        "long question",
        "uppercase id",
        "caller records",
        "caller claims",
    ],
)
def test_body_outside_the_contract_is_422_before_retrieval(body: dict[str, Any]) -> None:
    pipeline = FakePipeline(FollowUpDraft(claims=[], coverage=Coverage.NONE, gaps=[]))

    response, _ = _ask(pipeline, body=body)

    assert response.status_code == 422
    assert ErrorResponse.model_validate_json(response.text).error.value == "invalid_request"
    assert pipeline.tokens_seen == [] and pipeline.questions_seen == []
    assert "SENTINEL-QUESTION" not in response.text and "Warfarin" not in response.text


def test_missing_bearer_is_401_before_retrieval() -> None:
    pipeline = FakePipeline(FollowUpDraft(claims=[], coverage=Coverage.NONE, gaps=[]))

    response, _ = _ask(pipeline, headers={})

    assert response.status_code == 401
    assert response.json()["error"] == "missing_bearer_token"
    assert pipeline.tokens_seen == []


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (FhirAuthError("FHIR request rejected with 401"), 401, "fhir_token_rejected"),
        (PatientNotFoundError("FHIR Patient read returned 404"), 404, "patient_not_found"),
        (
            ClaimDraftingError("Model output did not match the follow-up schema"),
            502,
            "follow_up_generation_failed",
        ),
        (RuntimeError("KeyError: 'retrieved'"), 500, "internal_error"),
    ],
)
def test_pipeline_failures_map_to_errors_without_internals(
    error: Exception, status: int, code: str
) -> None:
    with mock.patch("app.api.follow_up.run_follow_up", mock.AsyncMock(side_effect=error)):
        with capture_logs() as logs:
            response = TestClient(app).post(
                "/pre-visit-brief/follow-up",
                json={"patient_id": PATIENT_ID, "session_id": SESSION_ID, "question": QUESTION},
                headers=AUTH,
            )

    assert response.status_code == status
    assert ErrorResponse.model_validate_json(response.text).error.value == code
    for internal in ("schema", "KeyError", "SENTINEL-QUESTION"):
        assert internal not in response.text
    assert QUESTION not in json.dumps(logs, default=str)


def test_openapi_publishes_the_follow_up_contract() -> None:
    schema = TestClient(app).get("/openapi.json").json()

    operation = schema["paths"]["/pre-visit-brief/follow-up"]["post"]
    assert set(operation["responses"]) == {"200", "401", "404", "422", "500", "502"}
    assert {"FollowUpRequest", "FollowUpResponse"} <= set(schema["components"]["schemas"])


# -- drafting step ---------------------------------------------------------------


def test_output_schema_fields_are_exactly_what_the_verifier_can_check() -> None:
    by_type = {
        option["properties"]["resource_type"]["const"]: option["properties"]["field"]["enum"]
        for option in drafting._OUTPUT_SCHEMA["properties"]["claims"]["items"]["anyOf"]
    }
    for resource_type, record in (
        (R.CONDITION, HTN),
        (R.MEDICATION_REQUEST, LISINOPRIL),
        (R.ALLERGY_INTOLERANCE, ASPIRIN),
    ):
        assert set(by_type.pop(resource_type.value)) == checker._claimable_fields(record)
    assert by_type == {}


def test_model_input_is_the_whole_bounded_set_with_absent_dates_as_null() -> None:
    serialized = json.loads(drafting._serialize_retrieved(_retrieved()))

    assert list(serialized) == ["Condition", "MedicationRequest", "AllergyIntolerance"]
    medications = serialized["MedicationRequest"]["records"]
    # Inactive records are included: a follow-up may ask about them.
    assert [m["status"] for m in medications] == ["completed", "active"]
    assert medications[1]["authored_date"] is None
    with pytest.raises(ValueError, match="AllergyIntolerance"):
        drafting._serialize_retrieved({R.CONDITION: [], R.MEDICATION_REQUEST: []})


class _FakeStream:
    def __init__(
        self, message: SimpleNamespace, calls: list[dict[str, Any]], kwargs: dict[str, Any]
    ) -> None:
        self._message = message
        self.request_id = "req_test"
        calls.append(kwargs)

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get_final_message(self) -> SimpleNamespace:
        return self._message


def _fake_client(
    text: str, calls: list[dict[str, Any]], stop_reason: str = "end_turn"
) -> SimpleNamespace:
    message = SimpleNamespace(
        content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(
            iterations=None,
            input_tokens=900,
            output_tokens=120,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
        model="claude-opus-5",
        stop_reason=stop_reason,
        stop_details=None,
    )
    stream = lambda **kwargs: _FakeStream(message, calls, kwargs)  # noqa: E731
    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))


def test_draft_parses_claims_coverage_and_gaps_and_logs_no_question() -> None:
    calls: list[dict[str, Any]] = []
    output = {
        "claims": [
            {
                "resource_type": "MedicationRequest",
                "source_id": "med-1",
                "field": "status",
                "value": "completed",
            }
        ],
        "coverage": "partial",
        "gaps": ["not_stated_in_record", "not_stated_in_record"],
    }
    with mock.patch.object(
        drafting, "_client", return_value=_fake_client(json.dumps(output), calls)
    ):
        with capture_logs() as logs:
            draft = asyncio.run(
                drafting.draft_follow_up(_retrieved(), QUESTION, patient_session_key=KEY)
            )

    assert draft.claims == [_claim(R.MEDICATION_REQUEST, "med-1", "status", "completed")]
    assert draft.coverage is Coverage.PARTIAL
    assert draft.gaps == [RecordGap.NOT_STATED_IN_RECORD]
    (call,) = calls
    assert call["model"] == "claude-opus-5"
    assert call["output_config"]["format"]["schema"] is drafting._OUTPUT_SCHEMA
    assert "<physician_question>\n" + QUESTION in call["messages"][0]["content"]
    events = [
        log
        for log in logs
        if log.get("event") == "agent_event" and log.get("event_type") not in TELEMETRY_EVENT_TYPES
    ]
    assert [e["event_type"] for e in events] == ["llm_usage_event", "claims_drafted"]
    assert {e["use_case"] for e in events} == {"follow_up_drilling"}
    assert QUESTION not in json.dumps(logs, default=str)


@pytest.mark.parametrize(
    ("text", "stop_reason"),
    [
        ('{"claims": [], "coverage": "maybe", "gaps": []}', "end_turn"),
        ('{"claims": [], "coverage": "full"}', "end_turn"),
        ("{}", "refusal"),
        ("{}", "max_tokens"),
    ],
    ids=["bad coverage", "missing gaps", "refusal", "truncated"],
)
def test_draft_rejects_output_it_cannot_use(text: str, stop_reason: str) -> None:
    with mock.patch.object(drafting, "_client", return_value=_fake_client(text, [], stop_reason)):
        with capture_logs(), pytest.raises(ClaimDraftingError):
            asyncio.run(drafting.draft_follow_up(_retrieved(), QUESTION, patient_session_key=KEY))


def test_claim_on_a_type_outside_the_bounded_set_parses_and_then_fails_verification() -> None:
    output = {
        "claims": [
            {"resource_type": "Observation", "source_id": "obs-1", "field": "value", "value": "7.2"}
        ],
        "coverage": "full",
        "gaps": [],
    }
    with mock.patch.object(drafting, "_client", return_value=_fake_client(json.dumps(output), [])):
        with capture_logs():
            draft = asyncio.run(
                drafting.draft_follow_up(_retrieved(), QUESTION, patient_session_key=KEY)
            )
            (result,) = checker.verify_claims(
                draft.claims, _retrieved(), use_case=graph._USE_CASE, patient_session_key=KEY
            )

    assert not result.passed and result.disclosed_as_unverified
    assert result.reason == checker.FailureReason.SOURCE_NOT_FOUND.value
