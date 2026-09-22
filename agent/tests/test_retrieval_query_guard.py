"""
The concept-only query guard (D5).

D5's hard constraint — "no patient identifiers in retrieval queries sent
to third parties" — has two enforcement points. The structural one is that
`EvidenceRetrieverInput` has no patient field at all, which is tested on
the contracts branch. The one tested here is the text guard, which catches
the case the type cannot: the whole question, or a chart line, passed
through as the text of a "concept".

D5 extends the constraint to evaluation runs explicitly, so the dev query
set itself is checked here. A staged query with a date in it would be sent
to a hosted reranker the first time someone configures a key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.retrieval.query_guard import (
    MAX_CONCEPT_CHARS,
    ClinicalConcept,
    IdentifierInQueryError,
    assert_concept_only,
    build_query,
    find_identifier_shapes,
)

DEV_PATH = Path(__file__).resolve().parents[1] / "evals/w2/staging/retrieval_dev.jsonl"

CLINICAL_CONCEPTS = [
    "type 2 diabetes mellitus",
    "hemoglobin A1c above target",
    "statin therapy for primary prevention",
    "eGFR 45 to 59 mL/min/1.73 m2",
    "blood pressure 130/80 mm Hg",
    "25-hydroxyvitamin D deficiency",
    "levothyroxine dose adjustment",
    "iron deficiency anemia with low ferritin",
    "chronic kidney disease stage 3a",
    "TSH 8.2 mIU/L",
]

IDENTIFIER_SHAPED = [
    ("patient born 1962-03-14", "iso_date"),
    ("A1c drawn 3/14/2026", "slashed_date"),
    ("SSN 123-45-6789", "ssn"),
    ("contact jane.doe@example.com", "email"),
    ("call 415-555-0100 with results", "phone"),
    ("MRN for this patient", "mrn_label"),
    ("record number 00982311", "long_digit_run"),
    ("DOB and weight", "dob_label"),
    ("patient name and A1c", "named_patient"),
    ("visit on August 3, 1971", "month_name_date"),
]


@pytest.mark.parametrize("text", CLINICAL_CONCEPTS)
def test_clinical_concepts_pass(text):
    assert_concept_only(text)
    assert ClinicalConcept(text=text).text == text


@pytest.mark.parametrize(("text", "rule"), IDENTIFIER_SHAPED)
def test_identifier_shapes_are_blocked(text, rule):
    assert rule in find_identifier_shapes(text)
    with pytest.raises(IdentifierInQueryError):
        assert_concept_only(text)
    with pytest.raises(ValidationError):
        ClinicalConcept(text=text)


@pytest.mark.parametrize(("text", "rule"), IDENTIFIER_SHAPED)
def test_the_error_never_quotes_what_it_caught(text, rule):
    """
    An exception that echoes the identifier writes it into the traceback,
    the log line and the error response — CLAUDE.md invariant 1. The
    message names the rule that fired and nothing else.
    """
    with pytest.raises(IdentifierInQueryError) as caught:
        assert_concept_only(text)
    message = str(caught.value)
    assert rule in message
    for token in text.split():
        if any(ch.isdigit() for ch in token) or "@" in token:
            assert token not in message


def test_a_whole_question_is_too_long_to_be_a_concept():
    question = (
        "Given this patient's declining kidney function over the last two years "
        "and the fact that they are already on a statin and metformin, what "
        "should the next step in management be, and does the guidance differ?"
    )
    assert len(question) > MAX_CONCEPT_CHARS
    with pytest.raises(IdentifierInQueryError, match="over the"):
        assert_concept_only(question)


def test_build_query_guards_bare_strings_too():
    """
    The type guards `ClinicalConcept`. `build_query` also takes plain
    strings — which is what a harness or a hand-written call reaches for,
    and exactly the path that would otherwise skip the guard.
    """
    assert build_query(["statin therapy", "LDL cholesterol"]) == "statin therapy LDL cholesterol"
    with pytest.raises(IdentifierInQueryError):
        build_query(["statin therapy", "started 2024-01-02"])


def test_build_query_refuses_an_empty_concept_list():
    with pytest.raises(ValueError, match="at least one concept"):
        build_query([])


def test_a_code_needs_its_code_system():
    ClinicalConcept(text="type 2 diabetes", code_system="snomed", code="44054006")
    with pytest.raises(ValidationError):
        ClinicalConcept(text="type 2 diabetes", code="44054006")


def test_every_staged_dev_query_is_concept_only():
    """
    D5 covers the bake-off, not just production: these queries are sent to
    a hosted reranker whenever a key is configured.
    """
    rows = [json.loads(line) for line in DEV_PATH.read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) >= 25, "the work order asks for 25-30 dev queries"
    for row in rows:
        assert not find_identifier_shapes(row["query"]), row["query_id"]
        assert_concept_only(row["query"])


def test_every_staged_dev_query_has_judgments_and_a_stable_id():
    rows = [json.loads(line) for line in DEV_PATH.read_text(encoding="utf-8").splitlines() if line]
    ids = [row["query_id"] for row in rows]
    assert len(ids) == len(set(ids))
    for row in rows:
        assert row["relevant_chunk_ids"], f"{row['query_id']} has no relevance judgments"
        assert row["topics"]


def test_staged_judgments_point_at_chunks_that_exist():
    from app.retrieval.build import load_index

    known = {c.chunk_id for c in load_index().chunks}
    rows = [json.loads(line) for line in DEV_PATH.read_text(encoding="utf-8").splitlines() if line]
    for row in rows:
        missing = [c for c in row["relevant_chunk_ids"] if c not in known]
        assert not missing, (
            f"{row['query_id']} names {len(missing)} chunk ids that no longer exist; "
            "re-run scripts/retrieval_label_dev_set.py"
        )
