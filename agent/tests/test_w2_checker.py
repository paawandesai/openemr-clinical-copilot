"""
The Week 2 verifier extension (app/verification/w2_checker.py) and the code
that words verified values (app/supervisor/facts.py).

Plain comparison code with the same guarantee as Week 1's checker: no model,
no network, and a citation on a passed claim that is built from the source
rather than from the draft.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.models.claims import Claim
from app.models.fhir_resources import Condition, FhirResourceType
from app.models.metrics_events import UseCase
from app.models.w2.citations import BBox, Citation
from app.models.w2.documents import DocumentKind
from app.models.w2.lab_values import ReportedFlag
from app.models.w2.source_types import SourceType
from app.models.w2.workers import EvidenceChunk, GuidelineSource, RetrievalMethod
from app.supervisor.facts import DocumentFact, flag_wording, group_statement, render_value
from app.verification import w2_checker
from app.verification.w2_checker import (
    DocumentInTurn,
    DraftedDocumentFact,
    DraftedGuideline,
    W2FailureReason,
    verbatim_slice,
    verify_answer,
    verify_document_fact,
    verify_guideline,
)

DOCUMENT = "a" * 128
EXTRACTION = "w2x-" + "0" * 32


def fact(
    path: str, quote: str, value: object, group: str = "LDL Cholesterol", part: str = "result"
):
    citation = Citation.located_document(
        source_id=DOCUMENT,
        field_path=path,
        quote=quote,
        bbox=BBox(page=1, x0=10, y0=10, x1=40, y1=20),
        extraction_id=EXTRACTION,
    )
    return DocumentFact(
        document_id=DOCUMENT,
        kind=DocumentKind.LAB_PDF,
        field_path=path,
        group=group,
        part=part,
        quote=quote,
        value=value,
        citation=citation,
    )


VALUE = fact("results.lipid.ldl.value", "162", 162)
IN_TURN = {
    DOCUMENT: DocumentInTurn(
        document_id=DOCUMENT,
        facts={VALUE.field_path: VALUE},
        all_field_paths=frozenset({VALUE.field_path, "results.lipid.ldl.unit"}),
    )
}

CHUNK = EvidenceChunk(
    chunk_id="uspstf-statin-001",
    text="The USPSTF recommends that clinicians prescribe a statin for the primary\n"
    "prevention of CVD for adults aged 40 to 75 years who have 1 or more CVD risk factors.",
    source=GuidelineSource(
        guideline_id="uspstf-statin-primary-prevention",
        title="Statin Use for the Primary Prevention of Cardiovascular Disease in Adults",
        section="Recommendation Summary",
        publisher="U.S. Preventive Services Task Force",
    ),
    retrieval_score=0.5,
    method=RetrievalMethod.HYBRID,
)
CHUNKS = {CHUNK.chunk_id: CHUNK}


def test_the_verifier_imports_no_model_or_network_client() -> None:
    """Same rule as test_verifier.py for checker.py, over this module's own imports."""
    tree = ast.parse(Path(w2_checker.__file__).read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = (
        "anthropic",
        "langgraph",
        "langchain",
        "openai",
        "httpx",
        "requests",
        "urllib",
        "app.fhir",
        "app.retrieval",
        "app.reasoning",
    )
    assert [m for m in imported for f in forbidden if m == f or m.startswith(f + ".")] == []


# -- documents ---------------------------------------------------------------


def test_a_located_value_copied_exactly_passes_with_the_locate_steps_citation() -> None:
    result = verify_document_fact(DraftedDocumentFact(DOCUMENT, VALUE.field_path, "162"), IN_TURN)
    assert result.passed
    assert result.citation is VALUE.citation
    assert result.citation.bbox is not None


def test_case_and_whitespace_are_the_only_forgiven_differences() -> None:
    drafted = DraftedDocumentFact(DOCUMENT, VALUE.field_path, " 162 ")
    assert verify_document_fact(drafted, IN_TURN).passed


@pytest.mark.parametrize("value", ["16", "62", "162.0", "163", "162 mg/dL"])
def test_a_near_value_fails(value: str) -> None:
    """D24: no substring, no near match on a number."""
    result = verify_document_fact(DraftedDocumentFact(DOCUMENT, VALUE.field_path, value), IN_TURN)
    assert not result.passed and result.reason == W2FailureReason.VALUE_MISMATCH.value
    assert result.citation is None


def test_an_unlocated_field_fails_as_not_located() -> None:
    """Invariant 4: the extraction has it, the page does not place it."""
    drafted = DraftedDocumentFact(DOCUMENT, "results.lipid.ldl.unit", "mg/dL")
    result = verify_document_fact(drafted, IN_TURN)
    assert result.reason == W2FailureReason.FIELD_NOT_LOCATED.value


def test_an_invented_field_and_an_invented_document_fail() -> None:
    assert (
        verify_document_fact(
            DraftedDocumentFact(DOCUMENT, "results.x.y.value", "1"), IN_TURN
        ).reason
        == W2FailureReason.FIELD_NOT_IN_DOCUMENT.value
    )
    assert (
        verify_document_fact(
            DraftedDocumentFact("b" * 128, VALUE.field_path, "162"), IN_TURN
        ).reason
        == W2FailureReason.DOCUMENT_NOT_IN_TURN.value
    )


# -- guidelines --------------------------------------------------------------


def test_a_verbatim_excerpt_passes_and_is_cited_as_the_chunks_own_characters() -> None:
    drafted = DraftedGuideline(
        CHUNK.chunk_id, "prescribe a statin for the primary prevention of CVD"
    )
    result = verify_guideline(drafted, CHUNKS)
    assert result.passed
    assert result.citation is not None
    quote = str(result.citation.quote_or_value)
    # The chunk breaks the line after "primary"; the citation keeps the break.
    assert quote == "prescribe a statin for the primary\nprevention of CVD"
    assert quote in CHUNK.text
    assert result.citation.source_type is SourceType.GUIDELINE


@pytest.mark.parametrize(
    ("excerpt", "reason"),
    [
        ("prescribe statins for primary prevention of CVD", W2FailureReason.EXCERPT_NOT_IN_CHUNK),
        ("PRESCRIBE A STATIN FOR THE PRIMARY", W2FailureReason.EXCERPT_NOT_IN_CHUNK),
        ("a statin", W2FailureReason.EXCERPT_TOO_SHORT),
    ],
)
def test_a_paraphrase_a_recased_quote_and_a_fragment_fail(
    excerpt: str, reason: W2FailureReason
) -> None:
    result = verify_guideline(DraftedGuideline(CHUNK.chunk_id, excerpt), CHUNKS)
    assert result.reason == reason.value and result.citation is None


def test_an_unretrieved_chunk_fails() -> None:
    result = verify_guideline(DraftedGuideline("nope", "prescribe a statin for the"), CHUNKS)
    assert result.reason == W2FailureReason.CHUNK_NOT_RETRIEVED.value


def test_verbatim_slice_allows_whitespace_only() -> None:
    assert verbatim_slice("a  b\tc", "a b c") == "a  b\tc"
    assert verbatim_slice("a b c", "a b d") is None
    assert verbatim_slice("anything", "   ") is None


# -- all three, and the separation between them ------------------------------


def test_verify_answer_keeps_each_claim_on_its_own_source_type() -> None:
    chart = {
        FhirResourceType.CONDITION: [
            Condition(source_id="c1", display="Hyperlipidemia", clinical_status="active")
        ]
    }
    results = verify_answer(
        [Claim(source_id="c1", field="display", value="Hyperlipidemia", resource_type="Condition")],
        [DraftedDocumentFact(DOCUMENT, VALUE.field_path, "162")],
        [DraftedGuideline(CHUNK.chunk_id, "prescribe a statin for the primary prevention")],
        chart=chart,
        documents=IN_TURN,
        chunks=CHUNKS,
        use_case=UseCase.FOLLOW_UP_DRILLING,
        patient_session_key="u:p:s",
    )
    assert [r.source_type for r in results] == [
        SourceType.CHART,
        SourceType.DOCUMENT,
        SourceType.GUIDELINE,
    ]
    assert all(r.passed for r in results)
    assert [r.citation.source_type for r in results if r.citation] == [
        SourceType.CHART,
        SourceType.DOCUMENT,
        SourceType.GUIDELINE,
    ]


def test_a_chart_claim_cannot_be_verified_against_a_guideline_or_a_document() -> None:
    """Invariant 3: a chart claim naming a chunk id or a document id finds nothing."""
    results = verify_answer(
        [
            Claim(
                source_id=CHUNK.chunk_id,
                field="display",
                value="statin",
                resource_type="Condition",
            )
        ],
        [],
        [],
        chart={FhirResourceType.CONDITION: []},
        documents=IN_TURN,
        chunks=CHUNKS,
        use_case=UseCase.FOLLOW_UP_DRILLING,
        patient_session_key="u:p:s",
    )
    assert not results[0].passed


# -- wording (D20) -----------------------------------------------------------


@pytest.mark.parametrize("flag", list(ReportedFlag))
def test_no_flag_but_a_printed_normal_is_worded_normal(flag: ReportedFlag) -> None:
    wording = flag_wording(flag).replace("not a statement that the value is normal", "")
    says_normal = re.search(r"\bnormal\b", wording) is not None
    assert says_normal is (flag is ReportedFlag.NORMAL)


def test_a_blank_flag_says_so_and_says_it_is_not_normal() -> None:
    wording = render_value(ReportedFlag.NOT_REPORTED, "")
    assert "no flag printed" in wording
    assert "not a statement that the value is normal" in wording


def test_a_rows_values_render_as_one_statement_from_the_extraction_not_the_draft() -> None:
    row = [
        fact("results.lipid.ldl.name", "LDL Cholesterol", "LDL Cholesterol", part="test"),
        VALUE,
        fact("results.lipid.ldl.reported_flag", "", ReportedFlag.NOT_REPORTED, part="flag"),
    ]
    statement = group_statement(row)
    assert statement.startswith("Lab report, page 1: LDL Cholesterol: result: 162")
    assert "no flag printed" in statement
