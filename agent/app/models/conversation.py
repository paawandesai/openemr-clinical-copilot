"""
Closed sets for the multi-turn conversation (app/graph/conversation.py) —
USERS.md §3 Use Case 3 (Follow-Up Drilling), ARCHITECTURE.md §4.

Shared by routing, drafting, the pipeline and the HTTP contract, so a value
is never free text in one place and an enum in another. As in
app/models/follow_up.py, everything the physician reads about what could
not be answered is fixed, code-authored wording chosen from these sets; the
model picks a member, it never writes the sentence.
"""

from __future__ import annotations

from enum import Enum


class TurnStatus(str, Enum):
    """
    Decided in code (app/graph/conversation.py:turn_status). The first
    three mean what app/models/follow_up.py:AnswerStatus means. The two
    refusals are decided by rule before any record is read or any model is
    called (app/conversation/routing.py).
    """

    ANSWERED = "answered"
    PARTIALLY_ANSWERED = "partially_answered"
    NOT_IN_RECORD = "not_in_record"
    # The question asks for a part of the chart outside the five tool-scope
    # resource types (ARCHITECTURE.md §3).
    REFUSED_OUT_OF_SCOPE = "refused_out_of_scope"
    # The question asks for a judgment, comparison or trend: an answer the
    # verifier cannot check (ARCHITECTURE.md §5, multi-record synthesis and
    # lab interpretation).
    REFUSED_UNVERIFIABLE = "refused_unverifiable"


class ConversationGap(str, Enum):
    """
    app/models/follow_up.py:RecordGap, plus the one gap only a conversation
    can have. The shared members keep RecordGap's values.
    """

    LAB_RESULTS = "lab_results"
    VISIT_HISTORY = "visit_history"
    OTHER_CHART_DATA = "other_chart_data"
    NOT_STATED_IN_RECORD = "not_stated_in_record"
    INTERPRETATION = "interpretation"
    # "that medication", "it", "last time" — and nothing verified in an
    # earlier turn of this conversation names what it refers to.
    UNRESOLVED_REFERENCE = "unresolved_reference"


class RefusalCategory(str, Enum):
    """Why routing refused a question. Each renders as one fixed sentence."""

    # Outside tool scope (ARCHITECTURE.md §3).
    VITALS = "vitals"
    CLINICAL_NOTES = "clinical_notes"
    PROCEDURES = "procedures"
    IMMUNIZATIONS = "immunizations"
    IMAGING = "imaging"
    FAMILY_SOCIAL_HISTORY = "family_social_history"
    SCHEDULING = "scheduling"
    DEMOGRAPHICS_BILLING = "demographics_billing"
    OTHER_PATIENTS = "other_patients"
    # Unverifiable (ARCHITECTURE.md §5).
    TREND_OR_COMPARISON = "trend_or_comparison"
    CLINICAL_JUDGMENT = "clinical_judgment"
    CHANGE_DETECTION = "change_detection"


class RetrievalRule(str, Enum):
    """Which rule put a resource type into a turn's retrieval."""

    # Condition, MedicationRequest, AllergyIntolerance: read on every turn.
    BASE_SET = "base_set"
    # The question's own wording matched a defined retrieval.
    QUESTION_RULE = "question_rule"
    # An earlier turn of this conversation verified claims on this type,
    # so a reference back to them can be re-cited against fresh records.
    CONTEXT_CARRY = "context_carry"


class ConversationTransition(str, Enum):
    """What happened to the conversation's state when this turn arrived."""

    STARTED = "started"
    CONTINUED = "continued"
    # The session held another patient's conversation. All of it was
    # discarded before this turn ran (ARCHITECTURE.md §4, F-50).
    RESET_PATIENT_CHANGED = "reset_patient_changed"
    RESET_EXPIRED = "reset_expired"
