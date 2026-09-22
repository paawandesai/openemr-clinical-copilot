"""
The guideline corpus and what retrieval returns about it.

Two things are being defended here.

**Licensing.** Every document in `app/retrieval/corpus/documents/` is
redistributed inside this repository. That is only lawful because each one
is a work of the US federal government (17 U.S.C. 105). A source added
later without a licence basis — an ADA Standard of Care, a KDIGO guideline
— makes the repository undistributable, and it would arrive as a plausible
one-line addition to a YAML file. The test is the review step.

**The citation contract.** CLAUDE.md invariant 2 requires every clinical
claim to carry `{source_type, source_id, page_or_section, field_or_chunk_id,
quote_or_value}`. For guideline evidence, all five come from the retriever,
so a chunk that comes back without its bibliography is an unciteable claim
waiting to happen.

Offline: BM25 needs nothing, and the dense path is exercised with a
deterministic fake embedder rather than a downloaded model.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from urllib.parse import urlparse

import numpy as np
import pytest

from app.models.w2.workers import RetrievalMethod
from app.retrieval.build import load_index
from app.retrieval.corpus_meta import (
    CORPUS_DIR,
    LicenseBasis,
    SourceRecord,
    load_registry,
)
from app.retrieval.dense import l2_normalise
from app.retrieval.retriever import GuidelineRetriever, parse_source_version

#: Only these publish into this corpus. A URL outside the list is either a
#: typo or a licensing problem, and both should fail loudly.
ALLOWED_HOSTS = frozenset(
    {
        "www.uspreventiveservicestaskforce.org",
        "www.niddk.nih.gov",
        "www.nhlbi.nih.gov",
        "www.cdc.gov",
        "medlineplus.gov",
        "www.nia.nih.gov",
    }
)


class FakeEmbedder:
    """
    Deterministic pseudo-embeddings from a hash of the text.

    Nonsense as semantics, exact as machinery: it exercises the dense
    index, the cache path and RRF without downloading 67 MB of ONNX in a
    suite that is supposed to run offline.
    """

    name = "fake:sha256-32"
    dimensions = 32

    def _vector(self, text: str) -> np.ndarray:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = np.frombuffer((digest * 4)[: self.dimensions * 4], dtype=np.uint32)
        return (raw.astype(np.float32) / np.float32(2**32)) - 0.5

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return l2_normalise(np.stack([self._vector(t) for t in texts]))

    def embed_query(self, text: str) -> np.ndarray:
        return l2_normalise(self._vector(text))


@pytest.fixture(scope="module")
def registry():
    return load_registry()


@pytest.fixture(scope="module")
def index():
    return load_index()


@pytest.fixture(scope="module")
def retriever(index, registry):
    # cache_dir=None: the offline suite does not write into the corpus.
    return GuidelineRetriever(
        index, registry, embedder=FakeEmbedder(), reranker=None, cache_dir=None
    )


# --------------------------------------------------------------------------
# the registry
# --------------------------------------------------------------------------


def test_corpus_is_the_size_the_work_order_asked_for(registry):
    assert 15 <= len(registry.sources) <= 30


def test_every_source_is_openly_licensed(registry):
    """
    Two bases are permitted and no others. A third arriving as a one-line
    YAML addition is a licensing decision, and it should fail a test rather
    than pass review.
    """
    for source in registry.sources:
        assert source.license_basis in set(LicenseBasis)
        assert len(source.license_note) > 20, f"{source.guideline_id}: licence note is boilerplate"


def test_uspstf_sources_are_recorded_as_licensed_not_public_domain(registry):
    """
    USPSTF statements are jointly copyrighted (AMA/US Government) and
    redistributable only by express permission — see corpus_meta. Recording
    them as public domain would be a false statement in a file whose whole
    job is to be the licensing record.
    """
    uspstf = [s for s in registry.sources if s.publisher.startswith("U.S. Preventive")]
    assert uspstf
    for source in uspstf:
        assert source.license_basis is LicenseBasis.USPSTF_AHRQ_REDISTRIBUTION_PERMITTED
        assert source.verbatim_only
        assert "without any changes" in source.license_note
    for source in registry.sources:
        if source not in uspstf:
            assert source.license_basis is LicenseBasis.US_GOV_PUBLIC_DOMAIN
            assert not source.verbatim_only


def test_no_committed_document_carries_a_third_party_rights_notice(registry):
    """
    nhlbi.nih.gov captions embedded animations with a Nucleus Medical Media
    copyright, and the JAMA-published USPSTF statements carry the AMA joint
    copyright in a metadata paragraph. Neither is clinical guidance, and a
    corpus that keeps them can retrieve "All rights reserved" as evidence
    and quote it back in a citation. They are stripped at fetch time.
    """
    for source in registry.sources:
        text = source.document_path.read_text(encoding="utf-8").lower()
        assert "all rights reserved" not in text, source.guideline_id
        assert "nucleus medical media" not in text, source.guideline_id


def test_every_source_url_is_https_and_from_a_known_publisher(registry):
    for source in registry.sources:
        for url in source.all_urls:
            parsed = urlparse(url)
            assert parsed.scheme == "https", f"{source.guideline_id}: {url}"
            assert parsed.netloc in ALLOWED_HOSTS, f"{source.guideline_id}: {parsed.netloc}"


def test_every_source_records_when_it_was_published_and_retrieved(registry):
    for source in registry.sources:
        assert source.published or source.last_reviewed
        assert source.retrieved >= source.effective_date
        assert source.retrieved <= registry.as_of


def test_dated_sources_are_flagged(registry):
    """Older than ten years at `as_of` is flagged, and the flag is used."""
    flagged = set(registry.dated_ids())
    for source in registry.sources:
        age_years = (registry.as_of - source.effective_date).days / 365.25
        if age_years > registry.dated_after_years + 0.5:
            assert source.guideline_id in flagged, f"{source.guideline_id} is {age_years:.1f}y old"
    assert flagged, "a corpus where nothing is dated has not been age-checked"


def test_dated_is_computed_against_as_of_not_today():
    """Reproducibility: a build tomorrow must flag the same sources."""
    source = SourceRecord(
        guideline_id="x",
        title="T",
        publisher="P",
        url="https://www.cdc.gov/x",
        license_basis=LicenseBasis.US_GOV_PUBLIC_DOMAIN,
        license_note="CDC content; US federal government work, public domain.",
        published=date(2015, 6, 1),
        retrieved=date(2026, 9, 22),
        topics=["x"],
    )
    assert source.is_dated(date(2026, 9, 22))
    assert not source.is_dated(date(2024, 1, 1))


def test_every_source_has_a_document_on_disk(registry):
    for source in registry.sources:
        assert source.document_path.exists(), source.guideline_id
        assert source.document_path.stat().st_size > 1000, source.guideline_id


def test_fetch_manifest_covers_every_source(registry):
    manifest = json.loads((CORPUS_DIR / "fetch_manifest.json").read_text(encoding="utf-8"))
    assert set(manifest) == {s.guideline_id for s in registry.sources}
    for source in registry.sources:
        entry = manifest[source.guideline_id]
        assert entry["http_status"] == 200
        assert entry["urls"] == source.all_urls
        digest = hashlib.sha256(source.document_path.read_bytes()).hexdigest()
        assert entry["markdown_sha256"] == digest, (
            f"{source.guideline_id}: the committed document is not the one that was "
            "fetched; re-run scripts/fetch_guidelines.py"
        )


def test_the_corpus_covers_every_topic_the_lab_pdfs_contain(registry):
    required = {
        "diabetes",
        "a1c",
        "lipids",
        "statins",
        "blood-pressure",
        "kidney",
        "egfr",
        "creatinine",
        "thyroid",
        "anemia",
        "iron",
        "vitamin-d",
        "medication-safety",
    }
    covered = {topic for source in registry.sources for topic in source.topics}
    assert required <= covered, f"uncovered: {sorted(required - covered)}"


# --------------------------------------------------------------------------
# retrieval returns a citable source for every chunk
# --------------------------------------------------------------------------


def test_every_chunk_in_the_index_belongs_to_a_registered_source(index, registry):
    known = {s.guideline_id for s in registry.sources}
    assert {c.guideline_id for c in index.chunks} <= known


@pytest.mark.parametrize(
    "method", [RetrievalMethod.LEXICAL, RetrievalMethod.DENSE, RetrievalMethod.HYBRID]
)
def test_retrieval_returns_full_source_metadata_for_every_chunk(retriever, registry, method):
    results = retriever.retrieve(
        ["statin therapy for primary prevention of cardiovascular disease"],
        top_k=10,
        method=method,
    )
    assert results
    for chunk in results:
        source = chunk.source
        assert source.guideline_id
        assert source.title
        assert source.section, f"{chunk.chunk_id} has no section to cite"
        assert source.publisher
        assert source.url and source.url.startswith("https://")
        assert source.version, "version carries the effective date; a citation needs it"
        record = registry.by_id(source.guideline_id)
        effective, dated = parse_source_version(source.version)
        assert effective == record.effective_date.isoformat()
        assert dated == record.is_dated(registry.as_of, registry.dated_after_years)
        assert source.title == record.title
        assert chunk.text
        assert chunk.method is method


def test_a_chunk_whose_guideline_is_unregistered_raises_rather_than_losing_its_citation(
    index, registry
):
    trimmed = registry.model_copy(update={"sources": registry.sources[:1]})
    partial = GuidelineRetriever(index, trimmed, embedder=None, reranker=None, cache_dir=None)
    with pytest.raises(KeyError, match="sources.yaml"):
        partial.retrieve(
            ["chronic kidney disease staging"], top_k=10, method=RetrievalMethod.LEXICAL
        )


def test_rerank_score_is_none_when_no_reranker_ran(retriever):
    results = retriever.retrieve(
        ["A1C diagnostic threshold"], top_k=5, method=RetrievalMethod.HYBRID
    )
    assert all(c.rerank_score is None for c in results)


def test_retrieval_returns_the_contract_type_not_a_local_copy(retriever):
    results = retriever.retrieve(
        ["A1C diagnostic threshold"], top_k=3, method=RetrievalMethod.LEXICAL
    )
    assert type(results[0]).__module__ == "app.models.w2.workers"
    assert type(results[0].source).__module__ == "app.models.w2.workers"


def test_hybrid_without_an_embedder_refuses_rather_than_returning_lexical_results(index, registry):
    lexical_only = GuidelineRetriever(index, registry, embedder=None, cache_dir=None)
    with pytest.raises(ValueError, match="hybrid retrieval needs an embedder"):
        lexical_only.retrieve(["thyroid screening"], method=RetrievalMethod.HYBRID)


def test_results_are_ordered_and_deduplicated(retriever):
    results = retriever.retrieve(["eGFR and creatinine"], top_k=10, method=RetrievalMethod.HYBRID)
    ids = [c.chunk_id for c in results]
    assert len(ids) == len(set(ids))
    scores = [c.retrieval_score for c in results]
    assert scores == sorted(scores, reverse=True)


def test_retrieval_is_deterministic(retriever):
    first = retriever.retrieve(["vitamin D deficiency screening"], top_k=10)
    second = retriever.retrieve(["vitamin D deficiency screening"], top_k=10)
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]


def test_bm25_finds_the_clinical_tokens_a_general_tokeniser_would_destroy(retriever):
    """`A1C`, `eGFR` and `TSH` are the corpus's most load-bearing tokens."""
    for query, expected_guideline in (
        ("A1C test and estimated average glucose", "niddk-a1c-test"),
        ("eGFR staging of chronic kidney disease", "niddk-evaluate-ckd"),
        ("TSH thyroid stimulating hormone test", "medlineplus-tsh-test"),
    ):
        results = retriever.retrieve([query], top_k=5, method=RetrievalMethod.LEXICAL)
        assert expected_guideline in {c.source.guideline_id for c in results}, query
