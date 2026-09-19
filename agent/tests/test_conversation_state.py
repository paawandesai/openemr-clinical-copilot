"""
app/conversation/state.py — what a conversation stores, how it is keyed,
and when it is discarded. No network, no model.

    cd agent && python -m pytest tests/test_conversation_state.py
"""

from __future__ import annotations

import dataclasses

import pytest

from app.conversation.state import (
    MAX_CLAIMS_PER_TURN,
    MAX_CONTEXT_TURNS,
    ConversationStore,
)
from app.models.claims import Claim, VerificationResult
from app.models.conversation import ConversationTransition as T
from app.models.fhir_resources import FhirResourceType as R

USER = "user-1"
SESSION = "session-0001"
PATIENT_A = "a0000000-0000-4000-8000-00000000000a"
PATIENT_B = "b0000000-0000-4000-8000-00000000000b"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _passed(source_id: str, value: str = "lisinopril 10 MG Oral Tablet") -> VerificationResult:
    claim = Claim(source_id=source_id, field="display", value=value, resource_type=R.MEDICATION_REQUEST)
    return VerificationResult(claim=claim, passed=True, reason=None, disclosed_as_unverified=False)


def _failed(source_id: str, value: str = "Warfarin 5 MG") -> VerificationResult:
    claim = Claim(source_id=source_id, field="display", value=value, resource_type=R.MEDICATION_REQUEST)
    return VerificationResult(claim=claim, passed=False, reason="value does not match", disclosed_as_unverified=True)


def _store(clock: Clock | None = None, **kwargs: object) -> ConversationStore:
    return ConversationStore(clock=clock or Clock(), **kwargs)  # type: ignore[arg-type]


def test_a_conversation_keeps_verified_claims_only_and_nothing_else() -> None:
    store = _store()
    first = store.open(USER, SESSION, PATIENT_A)
    assert (first.turn, first.transition, first.context) == (1, T.STARTED, ())

    assert store.commit(first, [_passed("med-1"), _failed("med-9"), _passed("med-2", "Naproxen")])

    second = store.open(USER, SESSION, PATIENT_A)
    assert (second.turn, second.transition) == (2, T.CONTINUED)
    (turn,) = second.context
    assert turn.turn == 1
    assert [(c.source_id, c.value) for c in turn.claims] == [
        ("med-1", "lisinopril 10 MG Oral Tablet"), ("med-2", "Naproxen"),
    ]
    assert second.context_resource_types == {R.MEDICATION_REQUEST}
    # The stored shape has nowhere to put a question or an answer.
    assert {f.name for f in dataclasses.fields(turn)} == {"turn", "claims"}
    assert set(Claim.model_fields) == {"source_id", "field", "value", "resource_type"}


def test_a_turn_with_no_verified_claim_adds_no_context() -> None:
    store = _store()
    store.commit(store.open(USER, SESSION, PATIENT_A), [_failed("med-9")])
    store.commit(store.open(USER, SESSION, PATIENT_A), [])

    third = store.open(USER, SESSION, PATIENT_A)
    assert (third.turn, third.context) == (3, ())


def test_patient_switch_discards_everything_and_going_back_does_not_bring_it_back() -> None:
    store = _store()
    store.commit(store.open(USER, SESSION, PATIENT_A), [_passed("med-1"), _passed("med-2")])
    store.commit(store.open(USER, SESSION, PATIENT_A), [_passed("med-3")])

    switched = store.open(USER, SESSION, PATIENT_B)

    assert (switched.turn, switched.transition, switched.context) == (1, T.RESET_PATIENT_CHANGED, ())
    assert switched.patient_id == PATIENT_B
    dropped = switched.invalidation
    assert dropped is not None
    assert (dropped.previous_patient_id, dropped.turns_discarded, dropped.claims_discarded) == (PATIENT_A, 2, 3)
    assert len(store) == 1

    back = store.open(USER, SESSION, PATIENT_A)
    assert (back.turn, back.transition, back.context) == (1, T.RESET_PATIENT_CHANGED, ())
    assert back.invalidation is not None and back.invalidation.previous_patient_id == PATIENT_B


def test_a_turn_in_flight_when_its_conversation_was_invalidated_cannot_write() -> None:
    store = _store()
    in_flight = store.open(USER, SESSION, PATIENT_A)
    replacement = store.open(USER, SESSION, PATIENT_B)

    assert store.commit(in_flight, [_passed("med-1")]) is False

    assert store.commit(replacement, [_passed("b-med-1", "Metformin")]) is True
    after = store.open(USER, SESSION, PATIENT_B)
    assert [c.source_id for turn in after.context for c in turn.claims] == ["b-med-1"]


def test_a_stale_snapshot_of_the_same_patient_cannot_write_into_a_restarted_conversation() -> None:
    store = _store()
    stale = store.open(USER, SESSION, PATIENT_A)
    store.open(USER, SESSION, PATIENT_B)
    restarted = store.open(USER, SESSION, PATIENT_A)

    assert stale.conversation_id != restarted.conversation_id
    assert store.commit(stale, [_passed("med-1")]) is False
    assert store.open(USER, SESSION, PATIENT_A).context == ()


def test_conversations_are_separate_per_user_and_per_session() -> None:
    store = _store()
    store.commit(store.open(USER, SESSION, PATIENT_A), [_passed("med-1")])

    assert store.open("user-2", SESSION, PATIENT_A).context == ()
    assert store.open(USER, "session-0002", PATIENT_A).context == ()
    # Neither of those touched the first conversation.
    assert store.open(USER, SESSION, PATIENT_A).context_claim_count == 1


def test_idle_ttl_and_maximum_age_both_end_a_conversation() -> None:
    clock = Clock()
    store = _store(clock, idle_ttl_seconds=600.0, max_age_seconds=1500.0)
    snapshot = store.open(USER, SESSION, PATIENT_A)
    assert snapshot.expires_in_seconds == 600.0
    store.commit(snapshot, [_passed("med-1")])

    clock.now += 599.0
    assert store.open(USER, SESSION, PATIENT_A).transition is T.CONTINUED
    clock.now += 601.0
    expired = store.open(USER, SESSION, PATIENT_A)
    assert (expired.transition, expired.turn, expired.context) == (T.RESET_EXPIRED, 1, ())

    # Kept active, it still ends at the maximum age.
    for _ in range(3):
        clock.now += 500.0
        latest = store.open(USER, SESSION, PATIENT_A)
    assert latest.transition is T.RESET_EXPIRED
    assert len(store) == 1


def test_expired_conversations_are_purged_without_being_asked_for() -> None:
    clock = Clock()
    store = _store(clock, idle_ttl_seconds=60.0)
    store.commit(store.open(USER, SESSION, PATIENT_A), [_passed("med-1")])
    clock.now += 61.0

    store.open("user-2", "session-0009", PATIENT_B)

    assert len(store) == 1


def test_context_and_store_size_are_bounded() -> None:
    store = _store(max_conversations=3)
    for turn in range(MAX_CONTEXT_TURNS + 4):
        store.commit(store.open(USER, SESSION, PATIENT_A), [_passed(f"med-{turn}")])
    store.commit(
        store.open(USER, SESSION, PATIENT_A),
        [_passed(f"bulk-{n}") for n in range(MAX_CLAIMS_PER_TURN + 25)],
    )

    context = store.open(USER, SESSION, PATIENT_A).context
    assert len(context) == MAX_CONTEXT_TURNS
    assert [turn.turn for turn in context] == sorted(turn.turn for turn in context)
    assert len(context[-1].claims) == MAX_CLAIMS_PER_TURN

    for n in range(5):
        store.open(f"user-{n}", "session-000x", PATIENT_B)
    assert len(store) == 3
    # Least recently used went first: the original conversation is gone.
    assert store.open(USER, SESSION, PATIENT_A).context == ()


def test_end_and_discard_if_bound_elsewhere() -> None:
    store = _store()
    store.commit(store.open(USER, SESSION, PATIENT_A), [_passed("med-1")])

    assert store.discard_if_bound_elsewhere(USER, SESSION, PATIENT_A) is None
    assert len(store) == 1
    dropped = store.discard_if_bound_elsewhere(USER, SESSION, PATIENT_B)
    assert dropped is not None and dropped.claims_discarded == 1
    assert len(store) == 0

    store.open(USER, SESSION, PATIENT_A)
    assert store.end(USER, SESSION) is True
    assert store.end(USER, SESSION) is False


def test_stored_claims_are_copies() -> None:
    store = _store()
    result = _passed("med-1")
    store.commit(store.open(USER, SESSION, PATIENT_A), [result])

    result.claim.value = "tampered after commit"

    (turn,) = store.open(USER, SESSION, PATIENT_A).context
    assert turn.claims[0].value == "lisinopril 10 MG Oral Tablet"


@pytest.mark.parametrize("blank", ["user", "session_id", "patient_id"])
def test_open_requires_all_three_parts_of_the_key(blank: str) -> None:
    parts = {"user": USER, "session_id": SESSION, "patient_id": PATIENT_A, blank: "  "}
    with pytest.raises(ValueError, match=blank):
        _store().open(**parts)
