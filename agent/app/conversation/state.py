"""
Conversation state for Follow-Up Drilling — ARCHITECTURE.md §4.

What is stored, per conversation: the claims that PASSED verification in
earlier turns (resource_type, source_id, field, value) and the turn each
belongs to. Nothing else. No question text, no answer text, no failed
claim, no record that was retrieved but not cited. A later turn is given
these as structured context for working out what "that medication" refers
to (app/reasoning/draft_conversation_turn.py); it is never given prose.

Keying and the patient binding (the cross-patient-leakage defense, F-50):
a conversation is looked up by (user, session_id) and is bound to exactly
one patient_id for its whole life. `open()` is the only way to reach one,
and it requires all three. When a turn arrives for a session whose stored
conversation is bound to a different patient, that conversation is
deleted — all of it, before anything else happens — and a new, empty one
is started. It is never rebound, merged or resumed: going back to the
first patient starts from nothing too. OpenEMR will not stop one token
reading two charts (F-50), so this store is where the agent refuses to let
one chart's facts follow the physician into the next.

The caller must not call `open()` until OpenEMR has accepted the bearer
token this turn: `user` is the token's `sub`, read without checking its
signature (app/api/pre_visit_brief.py), so only a token the FHIR API has
just accepted proves it (app/graph/conversation.py orders the steps).

Writes go through `commit()`, which takes the snapshot `open()` returned
and drops the write unless the very same conversation is still stored. A
turn that was in flight while its conversation was invalidated can
therefore never write into the conversation that replaced it.

Lifetime: an idle TTL and an absolute maximum age (OpenEMR access tokens
live one hour), a cap on stored conversations (least recently used goes
first) and caps on turns and claims kept per conversation.

In-memory, per process. That is enough for the deployment as built — one
uvicorn worker (agent/Dockerfile) — and wrong for more than one: a second
worker would not see the first's conversations, so a turn routed to it
would start a new conversation (it fails safe: a reference is reported as
unresolved, never resolved against the wrong state). The production answer
is Redis (or equivalent) with the same key, the same patient binding
checked on every read, and server-side TTLs; `ConversationStore`'s three
methods are the interface to keep.
"""

from __future__ import annotations

import os
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field

from app.models.claims import Claim, VerificationResult
from app.models.conversation import ConversationTransition
from app.models.fhir_resources import FhirResourceType

DEFAULT_IDLE_TTL_SECONDS = 1800.0
DEFAULT_MAX_AGE_SECONDS = 3600.0
DEFAULT_MAX_CONVERSATIONS = 500

# Bounds on what one conversation keeps, and so on what a later turn's
# model call is given as context.
MAX_CONTEXT_TURNS = 6
MAX_CLAIMS_PER_TURN = 60


@dataclass(frozen=True)
class VerifiedTurn:
    """The claims one turn verified. `claims` never holds a failed claim."""

    turn: int
    claims: tuple[Claim, ...]

    @property
    def resource_types(self) -> frozenset[FhirResourceType]:
        return frozenset(claim.resource_type for claim in self.claims)


@dataclass(frozen=True)
class Invalidation:
    """What `open()` discarded. Counts only — never what the claims said."""

    reason: ConversationTransition
    previous_patient_id: str
    turns_discarded: int
    claims_discarded: int


@dataclass(frozen=True)
class ConversationSnapshot:
    """
    One turn's read-only view of its conversation. `context` is a copy:
    nothing the pipeline does with it reaches the store except through
    `commit()`.
    """

    conversation_id: str
    user: str
    session_id: str
    patient_id: str
    turn: int
    context: tuple[VerifiedTurn, ...]
    transition: ConversationTransition
    invalidation: Invalidation | None
    expires_in_seconds: float

    @property
    def context_resource_types(self) -> frozenset[FhirResourceType]:
        return frozenset().union(*(turn.resource_types for turn in self.context))

    @property
    def context_claim_count(self) -> int:
        return sum(len(turn.claims) for turn in self.context)


@dataclass
class _Conversation:
    conversation_id: str
    user: str
    session_id: str
    patient_id: str
    created_at: float
    last_active_at: float
    turn_count: int = 0
    turns: list[VerifiedTurn] = field(default_factory=list)


def _seconds_from_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    value = float(raw)
    if value <= 0:
        raise ValueError(f"{name} must be a positive number of seconds")
    return value


class ConversationStore:
    def __init__(
        self,
        *,
        idle_ttl_seconds: float = DEFAULT_IDLE_TTL_SECONDS,
        max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
        max_conversations: int = DEFAULT_MAX_CONVERSATIONS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._idle_ttl = idle_ttl_seconds
        self._max_age = max_age_seconds
        self._max_conversations = max_conversations
        self._clock = clock
        # (user, session_id) -> conversation, least recently used first.
        self._conversations: OrderedDict[tuple[str, str], _Conversation] = OrderedDict()
        # Keys whose conversation expired, so the next open() on one can
        # say "reset_expired" rather than "started". Same cap.
        self._expired_keys: set[tuple[str, str]] = set()

    def open(self, user: str, session_id: str, patient_id: str) -> ConversationSnapshot:
        """
        The conversation for (user, session_id), if it is live and bound to
        `patient_id`; otherwise a new, empty one bound to `patient_id`,
        after deleting whatever was there. Counts this turn.
        """
        for name, value in (("user", user), ("session_id", session_id), ("patient_id", patient_id)):
            if not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        now = self._clock()
        self._purge_expired(now)

        key = (user, session_id)
        existing = self._conversations.get(key)
        invalidation: Invalidation | None = None
        expired_here = key in self._expired_keys
        self._expired_keys.discard(key)

        if existing is not None and existing.patient_id != patient_id:
            invalidation = self._discard(key, ConversationTransition.RESET_PATIENT_CHANGED)
            existing = None

        if existing is None:
            existing = _Conversation(
                conversation_id=str(uuid.uuid4()),
                user=user,
                session_id=session_id,
                patient_id=patient_id,
                created_at=now,
                last_active_at=now,
            )
            self._conversations[key] = existing
            self._evict_over_capacity()
            if invalidation is not None:
                transition = invalidation.reason
            elif expired_here:
                transition = ConversationTransition.RESET_EXPIRED
            else:
                transition = ConversationTransition.STARTED
        else:
            transition = ConversationTransition.CONTINUED

        existing.turn_count += 1
        existing.last_active_at = now
        self._conversations.move_to_end(key)

        # The binding every read is checked against, not only the lookup.
        if existing.patient_id != patient_id or existing.user != user:
            raise RuntimeError("conversation binding does not match the turn that opened it")

        return ConversationSnapshot(
            conversation_id=existing.conversation_id,
            user=user,
            session_id=session_id,
            patient_id=patient_id,
            turn=existing.turn_count,
            context=tuple(existing.turns),
            transition=transition,
            invalidation=invalidation,
            expires_in_seconds=self._expires_in(existing, now),
        )

    def commit(self, snapshot: ConversationSnapshot, results: list[VerificationResult]) -> bool:
        """
        Records the turn's verified claims. Failed claims are not kept: a
        value the chart did not confirm must never become context a later
        turn resolves a reference against. Returns False, storing nothing,
        if the snapshot's conversation is no longer the stored one.
        """
        current = self._conversations.get((snapshot.user, snapshot.session_id))
        if (
            current is None
            or current.conversation_id != snapshot.conversation_id
            or current.patient_id != snapshot.patient_id
        ):
            return False
        verified = tuple(result.claim.model_copy() for result in results if result.passed)[
            :MAX_CLAIMS_PER_TURN
        ]
        if verified:
            current.turns.append(VerifiedTurn(turn=snapshot.turn, claims=verified))
            current.turns.sort(key=lambda turn: turn.turn)
            del current.turns[:-MAX_CONTEXT_TURNS]
        current.last_active_at = self._clock()
        return True

    def discard_if_bound_elsewhere(
        self, user: str, session_id: str, patient_id: str
    ) -> Invalidation | None:
        """
        For a turn that named `patient_id` but could not run (OpenEMR has
        no such patient): if the session holds another patient's
        conversation, it still ends here. The physician moved on; the old
        chart's context must not outlive that.
        """
        existing = self._conversations.get((user, session_id))
        if existing is None or existing.patient_id == patient_id:
            return None
        return self._discard((user, session_id), ConversationTransition.RESET_PATIENT_CHANGED)

    def end(self, user: str, session_id: str) -> bool:
        """Deletes the conversation for (user, session_id). True if one existed."""
        self._expired_keys.discard((user, session_id))
        return self._conversations.pop((user, session_id), None) is not None

    def __len__(self) -> int:
        return len(self._conversations)

    # -- internals -------------------------------------------------------------

    def _expires_in(self, conversation: _Conversation, now: float) -> float:
        return max(
            0.0,
            min(
                conversation.last_active_at + self._idle_ttl,
                conversation.created_at + self._max_age,
            )
            - now,
        )

    def _purge_expired(self, now: float) -> None:
        expired = [
            key
            for key, conversation in self._conversations.items()
            if self._expires_in(conversation, now) <= 0.0
        ]
        for key in expired:
            del self._conversations[key]
            self._expired_keys.add(key)
        while len(self._expired_keys) > self._max_conversations:
            self._expired_keys.pop()

    def _discard(self, key: tuple[str, str], reason: ConversationTransition) -> Invalidation:
        conversation = self._conversations.pop(key)
        return Invalidation(
            reason=reason,
            previous_patient_id=conversation.patient_id,
            turns_discarded=conversation.turn_count,
            claims_discarded=sum(len(turn.claims) for turn in conversation.turns),
        )

    def _evict_over_capacity(self) -> None:
        while len(self._conversations) > self._max_conversations:
            self._conversations.popitem(last=False)


def store_from_env() -> ConversationStore:
    return ConversationStore(
        idle_ttl_seconds=_seconds_from_env(
            "AGENT_CONVERSATION_TTL_SECONDS", DEFAULT_IDLE_TTL_SECONDS
        ),
        max_age_seconds=_seconds_from_env(
            "AGENT_CONVERSATION_MAX_AGE_SECONDS", DEFAULT_MAX_AGE_SECONDS
        ),
    )
