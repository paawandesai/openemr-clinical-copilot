"""
Guideline retrieval — the evidence-retriever's corpus, index and search.

Everything in this package is about **guideline evidence**, never patient
record facts. CLAUDE.md invariant 3 keeps the two apart, and the split is
physical here: nothing in `app/retrieval/` imports from `app/fhir/`, and
`GuidelineSource` has no field a chart fact could be written into.

D5 (docs/w2/DECISIONS.md) additionally forbids a patient identifier in any
query that leaves this system. `app/retrieval/query_guard.py` is the
enforcement point for the queries this package builds.
"""
