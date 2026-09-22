"""
Chunking: stable ids and a deterministic build.

Chunk ids leave this repository. They land in `EvidenceChunk.chunk_id`,
in the citation's `field_or_chunk_id`, and — once the Week 2 eval tree has
cases in it — in gate files under `agent/evals/w2/cases/`, which an
implementation session cannot edit. An id that moves when an unrelated
part of the corpus changes silently invalidates every one of those, and
the failure looks like a retrieval regression rather than like what it is.

So these are not style tests. They are the reason the id is a content
hash rather than an index.
"""

from __future__ import annotations

import pytest

from app.retrieval.build import build_index, load_index, serialise
from app.retrieval.chunking import (
    DROP_BELOW_CHARS,
    MAX_CHUNK_CHARS,
    chunk_document,
    compute_chunk_id,
)

DOC = """# Thyroid Testing

## What is a TSH test?

A TSH test measures thyroid stimulating hormone in the blood. It is the
first test a clinician orders when thyroid dysfunction is suspected.

### How the sample is taken

A health care professional draws blood from a vein in your arm.

## What the results mean

A high TSH usually means an underactive thyroid. A low TSH usually means
an overactive thyroid. Results are interpreted against the laboratory's
own reference interval.
"""


def test_chunk_ids_are_stable_across_runs():
    first = chunk_document(DOC, guideline_id="demo", title="Thyroid Testing")
    second = chunk_document(DOC, guideline_id="demo", title="Thyroid Testing")
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]


def test_chunk_id_is_independent_of_the_rest_of_the_document():
    """The whole point of a content hash: editing section 3 must not
    renumber section 1."""
    original = chunk_document(DOC, guideline_id="demo", title="Thyroid Testing")
    extended = chunk_document(
        DOC + "\n## Preparing for the test\n\nNo fasting is required before a TSH test, and "
        "no medicine needs to be stopped unless your provider says otherwise.\n",
        guideline_id="demo",
        title="Thyroid Testing",
    )
    original_ids = [c.chunk_id for c in original]
    assert original_ids == [c.chunk_id for c in extended[: len(original_ids)]]
    assert len(extended) > len(original)


def test_chunk_id_changes_when_the_text_changes():
    edited = DOC.replace("underactive thyroid", "hypothyroidism")
    before = {c.chunk_id for c in chunk_document(DOC, guideline_id="demo", title="T")}
    after = {c.chunk_id for c in chunk_document(edited, guideline_id="demo", title="T")}
    assert before != after


def test_chunk_id_depends_on_the_source_id():
    a = chunk_document(DOC, guideline_id="one", title="T")
    b = chunk_document(DOC, guideline_id="two", title="T")
    assert {c.chunk_id for c in a}.isdisjoint({c.chunk_id for c in b})


def test_chunk_id_separator_is_not_forgeable():
    """A section literally named 'a > b' must not collide with ['a', 'b']."""
    assert compute_chunk_id("g", ["a > b"], "x") != compute_chunk_id("g", ["a", "b"], "x")


def test_headings_are_retained_in_the_section_path():
    chunks = chunk_document(DOC, guideline_id="demo", title="Thyroid Testing")
    assert all(c.section_path[0] == "Thyroid Testing" for c in chunks)
    paths = {" > ".join(c.section_path) for c in chunks}
    assert any("What the results mean" in p for p in paths)


def test_indexed_text_carries_the_heading_trail_but_text_does_not():
    chunk = chunk_document(DOC, guideline_id="demo", title="Thyroid Testing")[-1]
    assert chunk.indexed_text.startswith(" > ".join(chunk.section_path))
    assert not chunk.text.startswith("Thyroid Testing")


def test_a_chunk_is_never_attributed_to_a_heading_it_did_not_come_from():
    """
    Short sections are packed together, but only with their own
    subsections. Packing 'Symptoms' into the preceding 'Complications'
    because both were short is how a citation ends up pointing a clinician
    at the wrong heading (CLAUDE.md invariant 2).
    """
    siblings = "# Doc\n\n## Alpha\n\nShort alpha body.\n\n## Beta\n\nShort beta body.\n"
    chunks = chunk_document(siblings, guideline_id="demo", title="Doc")
    bodies = {c.section_path[-1]: c.text for c in chunks}
    assert "Beta" not in bodies.get("Alpha", "")
    assert "alpha" not in bodies.get("Beta", "").lower()


def test_no_chunk_exceeds_the_embedding_window_by_packing():
    chunks = chunk_document(DOC * 40, guideline_id="demo", title="Thyroid Testing")
    oversize = [c for c in chunks if len(c.text) > MAX_CHUNK_CHARS]
    # A single sentence longer than the window is passed through whole
    # rather than cut mid-sentence; anything else is a packing bug.
    assert not [c for c in oversize if ". " in c.text[:MAX_CHUNK_CHARS]]


def test_chunks_carry_a_usable_span_and_are_not_fragments():
    for chunk in chunk_document(DOC, guideline_id="demo", title="Thyroid Testing"):
        assert chunk.char_end > chunk.char_start
        assert len(chunk.text) >= DROP_BELOW_CHARS


@pytest.fixture(scope="module")
def committed():
    return load_index()


class TestCommittedCorpus:
    """The built artifact must match what the sources actually say."""

    def test_build_is_deterministic(self, committed):
        assert serialise(build_index()) == serialise(build_index())

    def test_committed_index_is_not_stale(self, committed):
        assert serialise(build_index()) == serialise(committed), (
            "chunks.json does not match a fresh build; run `python -m app.retrieval.build`"
        )

    def test_chunk_ids_are_unique_across_the_whole_corpus(self, committed):
        ids = [c.chunk_id for c in committed.chunks]
        assert len(ids) == len(set(ids))

    def test_every_chunk_id_matches_its_own_content(self, committed):
        """A hand-edited chunks.json fails here rather than downstream."""
        for chunk in committed.chunks:
            assert chunk.chunk_id == compute_chunk_id(
                chunk.guideline_id, chunk.section_path, chunk.text
            )
