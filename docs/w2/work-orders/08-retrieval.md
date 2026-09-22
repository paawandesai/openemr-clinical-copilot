WORK ORDER 08: guideline corpus + retrieval bake-off (branch C,
part 1). Branch feat/w2-retrieval. Do not merge.

STEP 0: attempt a Write to agent/evals/w2/cases/probe.json. It must be
BLOCKED. If not, STOP.

READ FIRST: CLAUDE.md, docs/w2/DECISIONS.md (D5 is OPEN: embeddings
local vs API, reranker Cohere vs local cross-encoder; hard constraint:
no patient identifiers in any query sent to a third party), and the
EvidenceChunk / EvidenceRetrieverInput types on the contracts branch:
  git show feat/w2-contracts:agent/app/models/w2/workers.py
Match their shape; the adapter comes later.

GOAL
A small, openly licensed guideline corpus for a primary-care follow-up
physician, a hybrid retriever, and evidence to settle D5. No Anthropic
API calls.

1. Corpus (15-30 documents' worth, public domain or openly licensed
   ONLY: USPSTF, CDC, NIH/NIDDK/NHLBI; no ADA Standards, no KDIGO, no
   paywalled or copyrighted guidelines). Topics matched to what the lab
   PDFs contain: diabetes/A1c, lipids/statins, blood pressure, kidney
   function (eGFR/creatinine), thyroid, anemia/iron, vitamin D, and
   medication-safety basics. For each source record URL, publisher,
   license basis, publication/review date, retrieval date. Flag any
   source older than 10 years as dated.

2. Chunking: section-aware, headings retained, stable chunk ids (hash
   of source id + section path + content). Deterministic build script;
   report corpus size and chunk count.

3. Retrieval: BM25 (sparse) + local dense embeddings (a small model,
   e.g. bge-small via fastembed/onnx to avoid a torch dependency) +
   fusion (RRF). Rerank options: local cross-encoder, and Cohere Rerank
   only if COHERE_API_KEY is present in agent/.env (never print it).

4. Bake-off:
   - Write 25-30 dev queries as clinical concepts only (no names, dates,
     identifiers), each labelled with the expected relevant chunk ids.
     Save to agent/evals/w2/staging/retrieval_dev.jsonl (staging is
     ungated; promotion to cases is a later gate-session step).
   - Configs: BM25 only; dense only; hybrid RRF; hybrid + local rerank;
     hybrid + Cohere (if key).
   - Metrics per config: recall@5, MRR, latency p50/p95 on this Mac;
     note the expected effect of Vultr's CPU on local models.
   - RECOMMEND a D5 choice with the numbers. Do NOT mark D5 DECIDED;
     append a D-entry with status PROPOSED (use the next free D-number
     on main; the contracts branch holds D9-D20, so start at D21).

5. Tests: chunk-id stability, determinism of the build, retrieval
   returns source metadata for every chunk, concept-only query guard.

OUT OF SCOPE: supervisor wiring, contracts changes, anything under
agent/evals/ except the staging file above.
ACCEPTANCE: tests pass; full unit suite passes; ruff clean; corpus
source table and bake-off table in the report.
/report. STOP. Do not merge.