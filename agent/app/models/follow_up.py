"""
Closed sets for the constrained follow-up (app/graph/follow_up.py) — shared
by the drafting step, the pipeline and the HTTP contract, so a value is
never free text in one place and an enum in another.

The follow-up answers one physician question from the records the
Pre-Visit Brief retrieves, and nothing else. When the answer is not in
those records, the physician is told so in fixed, code-authored wording:
the model picks from these sets, it never writes the sentence.
"""

from __future__ import annotations

from enum import Enum


class AnswerStatus(str, Enum):
    """
    Decided in code from the drafted coverage and the verification
    results (app/graph/follow_up.py:answer_status), never taken from the
    model alone: an answer with no verified claim is NOT_IN_RECORD whatever
    the model said about it.
    """

    ANSWERED = "answered"
    PARTIALLY_ANSWERED = "partially_answered"
    NOT_IN_RECORD = "not_in_record"


class RecordGap(str, Enum):
    """
    What kind of information a question needed that the retrieved records
    do not hold. Each renders as one fixed sentence; see
    app/graph/follow_up.py:GAP_WORDING.
    """

    # Observation — not retrieved for the brief (ARCHITECTURE.md §3).
    LAB_RESULTS = "lab_results"
    # Encounter — not retrieved for the brief (ARCHITECTURE.md §3).
    VISIT_HISTORY = "visit_history"
    # Anything outside the five tool-scope resource types: vitals, notes,
    # procedures, immunizations, family or social history.
    OTHER_CHART_DATA = "other_chart_data"
    # A detail the retrieved records carry no field for: why a medication
    # was started or stopped, who prescribed it, dosing instructions.
    NOT_STATED_IN_RECORD = "not_stated_in_record"
    # Characterizing, comparing or trending what the chart states — no
    # reference ranges exist (F-19) and multi-record synthesis is not
    # verifiable (ARCHITECTURE.md §5).
    INTERPRETATION = "interpretation"
