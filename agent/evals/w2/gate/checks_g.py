"""
Category G — retrieval and supervisor routing.

Most of this category still waits on the supervisor. Two cases do not, and
both run against the merged evidence-retriever rather than against a mock of
it: the query guard (D5) and the shape of a retrieval result set.

What is asserted here and what is not is stated case by case. G3's "the
retrieve node ran exactly once" half needs a graph; its "the result set says
what it is" half does not, and that half is the one a reranker being
switched off would break first.
"""

from __future__ import annotations

from pydantic import ValidationError

from app.models.w2 import (
    ClinicalConcept,
    EvidenceRetrieverInput,
    EvidenceRetrieverOutput,
    RetrievalMethod,
)
from app.models.w2.workers import FORBIDDEN_RETRIEVAL_FIELD_NAMES
from app.retrieval.query_guard import (
    IdentifierInQueryError,
    assert_concept_only,
    build_query,
    find_identifier_shapes,
)
from evals.w2.gate import samples
from evals.w2.gate.models import Case, CheckOutcome
from evals.w2.gate.registry import check

#: Texts the guard documents itself as catching, one per rule in
#: `query_guard.IDENTIFIER_PATTERNS`. These are the shapes "the whole question
#: got passed through as a concept" actually takes.
_IDENTIFIER_TEXTS = (
    ("iso_date", "a1c trend since 1971-04-02"),
    ("slashed_date", "a1c trend since 04/02/1971"),
    ("ssn", "patient 999-41-7772 glycaemic control"),
    ("email", "q.vandermolen-ashgrove@example.invalid a1c"),
    ("phone", "call 555-014-2993 about the a1c"),
    ("long_digit_run", "record 7413992044 a1c target"),
    ("mrn_label", "MRN for this patient, a1c target"),
    ("dob_label", "dob and a1c target"),
    ("named_patient", "patient name and a1c target"),
    ("month_name_date", "seen April 2, 1971 for a1c"),
)

#: Concepts the guard must NOT reject. A guard that refuses ordinary clinical
#: text is a guard somebody widens until it refuses nothing.
_CLEAN_CONCEPTS = (
    "haemoglobin A1c target in type 2 diabetes mellitus",
    "metformin dose titration",
    "estimated glomerular filtration rate below 30",
    "USPSTF screening age for colorectal cancer",
)


@check("chk.g_retrieval_query_carries_concepts_only")
def g_retrieval_query_carries_concepts_only(case: Case) -> CheckOutcome:
    """
    D5, at both layers the retrieval branch built.

    The structural layer: `EvidenceRetrieverInput` has no patient field, so
    there is nothing to put an identifier in. The textual layer:
    `assert_concept_only` rejects the shapes a clinical concept never needs,
    and `build_query` re-runs it over bare strings — the path the bake-off
    harness takes, which does not go through the contract at all.
    """
    del case
    declared = set(EvidenceRetrieverInput.model_fields)
    smuggled = declared & FORBIDDEN_RETRIEVAL_FIELD_NAMES
    if smuggled:
        return CheckOutcome.failed(
            f"EvidenceRetrieverInput now declares {sorted(smuggled)}; D5 says a query "
            "leaving this system carries clinical concepts and nothing else"
        )

    survived: list[str] = []
    for rule, text in _IDENTIFIER_TEXTS:
        try:
            assert_concept_only(text)
        except IdentifierInQueryError:
            continue
        survived.append(rule)
    if survived:
        return CheckOutcome.failed(
            f"assert_concept_only accepted text the guard claims to reject ({survived}); "
            "the second layer between a chart and a third-party embedder is open"
        )

    for concept in _CLEAN_CONCEPTS:
        assert_concept_only(concept)
        fired = find_identifier_shapes(concept)
        if fired:
            return CheckOutcome.failed(
                f"the guard fired {fired} on the ordinary clinical concept {concept!r}; "
                "a guard that cries wolf on real queries is a guard somebody widens"
            )

    # The bare-string path, which skips ClinicalConcept entirely — the one the
    # bake-off harness takes, and the one run that actually leaves the machine.
    try:
        build_query(["glycaemic control, MRN please"])
    except IdentifierInQueryError:
        pass
    else:
        return CheckOutcome.failed(
            "build_query accepted a bare string carrying an MRN label; the bake-off "
            "path bypasses the contract"
        )

    # What the guard does NOT catch, asserted so the limit is recorded rather
    # than assumed away. `ZZCANARY7413MRN` — the canary the PHI fixtures seed —
    # is an opaque token: no word-boundary "MRN" label, no six-digit run, so no
    # textual rule fires. That is the guard behaving as its docstring says ("it
    # is not a PHI detector"), and it is why the structural defence above is
    # the first layer and this one the second. If this assertion ever starts
    # failing, the guard grew a rule and this case should say so.
    opaque = "ZZCANARY7413MRN"
    if find_identifier_shapes(opaque):
        return CheckOutcome.failed(
            f"the guard now catches the opaque canary {opaque!r}. Good news, but this "
            "case documents that it does not — update the case rather than the guard."
        )

    query = build_query([ClinicalConcept(text=t) for t in ("metformin", "A1c target")])
    if opaque in query:
        return CheckOutcome.failed("the built query carries the canary")
    return CheckOutcome.ok(
        f"{len(_IDENTIFIER_TEXTS)} identifier rules all fire, "
        f"{len(_CLEAN_CONCEPTS)} ordinary concepts pass, the input type declares no "
        "patient field, and the bare-string path is guarded. The opaque canary token "
        "is NOT caught textually, by design — the structural layer is what stops it."
    )


@check("chk.g_result_set_declares_whether_it_was_reranked")
def g_result_set_declares_whether_it_was_reranked(case: Case) -> CheckOutcome:
    """
    G3's deterministic half: a retrieval returns hits, and the result set
    says truthfully whether it was reranked.

    The reranked arm is built from real chunks rather than by running the
    cross-encoder — that needs an ONNX model fetched at first use, which is a
    download inside a CI job. What is being asserted is the contract, not the
    model: `reranked=True` with a score on every chunk is accepted, and the
    two ways of lying about it are not.
    """
    del case
    output = samples.retrieval_output()
    chunks = list(output.chunks)
    if not chunks:
        return CheckOutcome.failed("a real lexical retrieval returned no hits")
    if output.reranked:
        return CheckOutcome.failed("a run with rerank=False reported reranked=True")
    if any(chunk.rerank_score is not None for chunk in chunks):
        return CheckOutcome.failed("a chunk carries a rerank_score from a run that did not rerank")

    reranked = EvidenceRetrieverOutput(
        chunks=[
            chunk.model_copy(update={"rerank_score": 0.9 - i * 0.1})
            for i, chunk in enumerate(chunks)
        ],
        method=RetrievalMethod.HYBRID,
        reranked=True,
        correlation_id=samples.RETRIEVAL_CORRELATION_ID,
    )
    if len(reranked.chunks) != len(chunks):
        return CheckOutcome.failed("the reranked arm lost chunks")
    return CheckOutcome.ok(
        f"{len(chunks)} real hits, reranked=False and no rerank_score; a reranked "
        "result set with a score on every chunk is accepted"
    )


@check("chk.f_reranker_cannot_be_silently_off")
def f_reranker_cannot_be_silently_off(case: Case) -> CheckOutcome:
    """
    The deterministic sibling to W2-F4. Named `chk.f_*` and used by W2-F10
    because it belongs to that case; it lives in this module because what it
    reasons about is a retrieval result set.

    A reranker that stops running is, in the literature, a quality drop you
    find with a judge. Here it is a validation error: `reranked` and the
    per-chunk `rerank_score` have to agree, so a reranker that silently stops
    either declares itself off — which the log line then shows — or the
    result set does not construct at all. Neither outcome needs a model to
    notice.
    """
    del case
    chunks = samples.retrieved_chunks()
    scored = [c.model_copy(update={"rerank_score": 0.5}) for c in chunks]

    # claim reranked, carry no scores
    try:
        EvidenceRetrieverOutput(
            chunks=chunks,
            method=RetrievalMethod.HYBRID,
            reranked=True,
            correlation_id=samples.RETRIEVAL_CORRELATION_ID,
        )
    except ValidationError:
        pass
    else:
        return CheckOutcome.failed(
            "a result set claimed reranked=True with no rerank_score on any chunk; a "
            "reranker that had silently stopped would still look reranked"
        )

    # claim not reranked, carry scores
    try:
        EvidenceRetrieverOutput(
            chunks=scored,
            method=RetrievalMethod.HYBRID,
            reranked=False,
            correlation_id=samples.RETRIEVAL_CORRELATION_ID,
        )
    except ValidationError:
        pass
    else:
        return CheckOutcome.failed(
            "a result set claimed reranked=False while carrying rerank_scores"
        )

    # one chunk missing its score is enough
    partial = [*scored[:-1], scored[-1].model_copy(update={"rerank_score": None})]
    try:
        EvidenceRetrieverOutput(
            chunks=partial,
            method=RetrievalMethod.HYBRID,
            reranked=True,
            correlation_id=samples.RETRIEVAL_CORRELATION_ID,
        )
    except ValidationError:
        return CheckOutcome.ok(
            "reranked and rerank_score must agree: claiming a rerank without scores, "
            "denying one while carrying them, and missing a single chunk's score are "
            "all rejected"
        )
    return CheckOutcome.failed(
        "a reranked result set was accepted with one chunk missing its score; a "
        "partly-run reranker would pass"
    )
