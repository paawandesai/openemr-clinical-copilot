"""
Build the chunk index from the committed corpus. Deterministic, offline.

    python -m app.retrieval.build            # rebuild corpus/chunks.json
    python -m app.retrieval.build --check    # fail if the file is stale

Deterministic means literally byte-identical: no build timestamp, no
absolute paths, no dict iteration order leaking into the output, no
network. The build's inputs are hashed into the artifact instead of a
timestamp, so `--check` can say *why* an index is stale rather than just
that it differs — and so a regenerated index with no source change is a
no-op diff rather than noise on every branch.

That matters more than it looks: chunk ids from this file end up in eval
cases under `agent/evals/w2/`, which are gate files. An index that
rebuilds differently on a different machine would silently invalidate
every case that names a chunk.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.retrieval.chunking import GuidelineChunk, chunk_document
from app.retrieval.corpus_meta import CORPUS_DIR, CorpusRegistry, load_registry

INDEX_PATH = CORPUS_DIR / "chunks.json"

#: Bump when the chunker's output shape changes in a way that invalidates
#: stored chunk ids. Read by `load_index`, which refuses an index it does
#: not understand rather than half-reading it.
SCHEMA_VERSION = 1


class CorpusIndex(BaseModel):
    """The built artifact: every chunk of every source, plus what built it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int
    as_of: str = Field(description="Copied from the registry; the `dated` reference date.")
    sources_sha256: str = Field(description="sha256 of sources.yaml as committed.")
    documents_sha256: str = Field(
        description="sha256 over every document's id and bytes, in registry order."
    )
    document_count: int = Field(ge=1)
    chunk_count: int = Field(ge=1)
    total_chars: int = Field(ge=1)
    chunks: list[GuidelineChunk] = Field(min_length=1)

    def by_id(self, chunk_id: str) -> GuidelineChunk:
        for chunk in self.chunks:
            if chunk.chunk_id == chunk_id:
                return chunk
        raise KeyError(chunk_id)


def _documents_digest(registry: CorpusRegistry) -> str:
    digest = hashlib.sha256()
    for source in registry.sources:
        digest.update(source.guideline_id.encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(source.document_path.read_bytes())
        digest.update(b"\x1e")
    return digest.hexdigest()


def build_index(registry: CorpusRegistry | None = None) -> CorpusIndex:
    """Chunk every document in the registry, in registry order."""
    registry = registry or load_registry()

    chunks: list[GuidelineChunk] = []
    total_chars = 0
    for source in registry.sources:
        markdown = source.document_path.read_text(encoding="utf-8")
        total_chars += len(markdown)
        chunks.extend(
            chunk_document(markdown, guideline_id=source.guideline_id, title=source.title)
        )

    duplicates = len(chunks) - len({c.chunk_id for c in chunks})
    if duplicates:
        raise ValueError(f"{duplicates} duplicate chunk ids across the corpus")

    from app.retrieval.corpus_meta import SOURCES_PATH

    return CorpusIndex(
        schema_version=SCHEMA_VERSION,
        as_of=registry.as_of.isoformat(),
        sources_sha256=hashlib.sha256(SOURCES_PATH.read_bytes()).hexdigest(),
        documents_sha256=_documents_digest(registry),
        document_count=len(registry.sources),
        chunk_count=len(chunks),
        total_chars=total_chars,
        chunks=chunks,
    )


def serialise(index: CorpusIndex) -> str:
    """One canonical serialisation, used by both writing and `--check`."""
    return json.dumps(index.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"


def load_index(path: Path | None = None) -> CorpusIndex:
    """Read the committed index. Never builds, never fetches."""
    raw = json.loads((path or INDEX_PATH).read_text(encoding="utf-8"))
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"chunks.json is schema_version {raw.get('schema_version')}, "
            f"this code reads {SCHEMA_VERSION}; rebuild with "
            "`python -m app.retrieval.build`"
        )
    return CorpusIndex.model_validate(raw)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the guideline chunk index.")
    ap.add_argument("--check", action="store_true", help="fail if chunks.json is stale")
    args = ap.parse_args(argv)

    registry = load_registry()
    index = build_index(registry)
    payload = serialise(index)

    if args.check:
        if not INDEX_PATH.exists():
            print(f"MISSING {INDEX_PATH}")
            return 1
        if INDEX_PATH.read_text(encoding="utf-8") != payload:
            print(f"STALE {INDEX_PATH}: rebuild with `python -m app.retrieval.build`")
            return 1
        print(f"up to date: {index.chunk_count} chunks from {index.document_count} documents")
        return 0

    INDEX_PATH.write_text(payload, encoding="utf-8")

    per_source: dict[str, int] = {}
    for chunk in index.chunks:
        per_source[chunk.guideline_id] = per_source.get(chunk.guideline_id, 0) + 1

    print(f"documents : {index.document_count}")
    print(f"corpus     : {index.total_chars:,} characters of Markdown")
    print(f"chunks     : {index.chunk_count}")
    print(f"mean chars : {sum(len(c.text) for c in index.chunks) // index.chunk_count}")
    print(f"dated      : {', '.join(registry.dated_ids()) or '(none)'}")
    print(f"written    : {INDEX_PATH.relative_to(INDEX_PATH.parents[4])}")
    print()
    for guideline_id, count in per_source.items():
        print(f"  {guideline_id:<42} {count:>4} chunks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
