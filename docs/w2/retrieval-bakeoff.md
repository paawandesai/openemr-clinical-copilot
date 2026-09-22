# Guideline corpus and retrieval bake-off

Evidence for **D5** (embeddings local vs. hosted; reranker Cohere vs. local
cross-encoder), now settled as **[D25](DECISIONS.md)**. The licensing audit
in §2 is **[D26](DECISIONS.md)**. Deployment:
[`retrieval-deploy.md`](retrieval-deploy.md).

Everything below is reproducible offline from what is committed:

```bash
cd agent
python -m app.retrieval.build --check               # is chunks.json current?
python scripts/retrieval_label_dev_set.py --check   # do the labels still resolve?
python scripts/audit_corpus_licensing.py --offline  # committed text, no network
python scripts/retrieval_bakeoff.py --repeats 5     # the table in §4
```

Two scripts touch the network and are run by hand:
`scripts/fetch_guidelines.py` refreshes the corpus, and
`scripts/audit_corpus_licensing.py` without `--offline` re-fetches every
page to read its raw HTML.

---

## 1. The corpus

28 documents, 693 chunks, 602,041 characters of Markdown.
`sha256(documents) = de5158a0a342253d…`

| # | Source id | Publisher | Licence | Effective | Chunks | Dated |
|---|---|---|---|---|---|---|
| 1 | `uspstf-diabetes-screening` | USPSTF | **USPSTF/AHRQ** | 2021-08-24 | 47 | |
| 2 | `uspstf-statin-primary-prevention` | USPSTF | **USPSTF/AHRQ** | 2022-08-23 | 48 | |
| 3 | `uspstf-hypertension-screening` | USPSTF | **USPSTF/AHRQ** | 2021-04-27 | 42 | |
| 4 | `uspstf-thyroid-screening` | USPSTF | **USPSTF/AHRQ** | 2015-03-24 | 63 | **yes** |
| 5 | `uspstf-vitamin-d-deficiency-screening` | USPSTF | **USPSTF/AHRQ** | 2021-04-13 | 40 | |
| 6 | `uspstf-vitamin-d-calcium-fractures` | USPSTF | **USPSTF/AHRQ** | 2018-04-17 | 45 | |
| 7 | `uspstf-iron-deficiency-anemia-pregnancy` | USPSTF | **USPSTF/AHRQ** | 2024-08-20 | 50 | |
| 8 | `uspstf-aspirin-cvd-prevention` | USPSTF | **USPSTF/AHRQ** | 2022-04-26 | 46 | |
| 9 | `niddk-a1c-test` | NIDDK | PD | 2018-04-20 | 21 | |
| 10 | `niddk-diabetes-heart-disease-stroke` | NIDDK | PD | 2021-04-01 | 23 | |
| 11 | `niddk-evaluate-ckd` | NIDDK | PD | 2015-04-28 | 10 | **yes** |
| 12 | `niddk-ckd-tests-diagnosis` | NIDDK | PD | 2016-10-25 | 6 | |
| 13 | `niddk-hypothyroidism` | NIDDK | PD | 2021-03-01 | 20 | |
| 14 | `niddk-hyperthyroidism` | NIDDK | PD | 2021-08-01 | 23 | |
| 15 | `nhlbi-blood-cholesterol` | NHLBI | PD | 2024-04-17 | 28 | |
| 16 | `nhlbi-high-blood-pressure` | NHLBI | PD | 2024-04-25 | 39 | |
| 17 | `nhlbi-iron-deficiency-anemia` | NHLBI | PD | 2022-03-24 | 11 | |
| 18 | `nhlbi-blood-tests` | NHLBI | PD | 2022-03-24 | 8 | |
| 19 | `cdc-chronic-kidney-disease` | CDC | PD | 2024-05-15 | 6 | |
| 20 | `cdc-diabetes-testing` | CDC | PD | 2024-05-15 | 12 | |
| 21 | `cdc-diabetes-treatment` | CDC | PD | 2024-05-15 | 8 | |
| 22 | `cdc-high-blood-pressure` | CDC | PD | 2026-01-28 | 13 | |
| 23 | `cdc-medication-safety` | CDC | PD | 2024-11-18 | 13 | |
| 24 | `cdc-cholesterol` | CDC | PD | 2024-05-15 | 9 | |
| 25 | `medlineplus-vitamin-d-test` | MedlinePlus | PD | 2024-12-04 | 13 | |
| 26 | `medlineplus-tsh-test` | MedlinePlus | PD | 2024-10-30 | 13 | |
| 27 | `medlineplus-creatinine-test` | MedlinePlus | PD | 2023-12-05 | 14 | |
| 28 | `nia-taking-medicines-safely` | NIA | PD | 2022-09-22 | 22 | |

*PD = US federal government work, 17 U.S.C. §105. USPSTF/AHRQ = jointly
copyrighted, expressly redistributable verbatim — see §2. "Effective" is
the later of publication and last review; full dates and per-source licence
notes are in `sources.yaml`.*

Topics are matched to what the lab PDFs from `evals/w2/factory/` contain:
diabetes/A1c, lipids/statins, blood pressure, kidney (eGFR/creatinine),
thyroid, anemia/iron, vitamin D, medication safety.

### Two sources are flagged dated

- `uspstf-thyroid-screening` — 2015, an **I statement**, and still the
  USPSTF's current recommendation on thyroid screening. It belongs in the
  corpus; it is eleven years old, so it is flagged.
- `niddk-evaluate-ckd` — last reviewed 2015. This one matters more than
  its age suggests: CKD staging guidance has moved since, and the 2021
  race-free eGFR equations post-date it. **Anything surfaced from this
  source about eGFR needs a second look.**

The flag reaches the caller on `GuidelineSource.version`, which reads
`2015-03-24 (dated)` rather than `2015-03-24`. That is a workaround, not a
design: the contract has no `dated` field and forbids extra ones, and
`version` is the field a UI shows. `retriever.parse_source_version()` is
the supported way to split it — see the Conflicts section of the session
report.

### Two sources that were wanted and could not be used

- **NIH Office of Dietary Supplements** health-professional fact sheets.
  `ods.od.nih.gov` returns HTTP 403 behind Cloudflare to a non-browser
  client. MedlinePlus and NHLBI cover the same ground.
- The `cdc.gov` edge returns 403 for a `User-Agent` containing a `name/1.0`
  version token, which is what a first attempt at a polite identifying UA
  looks like. The working UA is recorded in `scripts/fetch_guidelines.py`.

---

## 2. Licensing audit (D26)

`scripts/audit_corpus_licensing.py` re-fetches every page and reads the
**raw HTML**, because a rights notice lives in the chrome that extraction
strips. It found three things.

**USPSTF statements are not public domain.** All eight are published by
JAMA — or, for the 2015 thyroid statement, Annals of Internal Medicine —
under arrangement with AHRQ, and carry "©2022 AMA and United States
Government … All rights reserved." What makes them usable is the express
permission on the same page: AHRQ permits the public to "reproduce,
redistribute, publicly display, and incorporate USPSTF work into other
materials provided that it is reproduced without any changes to the work or
portions thereof". Verbatim chunks with attribution are portions reproduced
without changes; a paraphrase is not.
`worker.citation_for()` refuses a quote that is not literally a substring
of its chunk, which makes that a property of the code rather than a promise.

**Two third-party rights notices were in the corpus text.**
nhlbi.nih.gov captions its embedded animations "Medical Animation Copyright
© Nucleus Medical Media, All rights reserved", and the JAMA-published
USPSTF pages carry the AMA joint copyright inside a paragraph of
publication metadata. Neither is clinical guidance. Left in, a retrieval
could return "All rights reserved" as evidence and a citation could quote
it back to a clinician. They are stripped at fetch time and recorded in
`sources.yaml` instead.

**MedlinePlus is mixed, and these three pages are the clean part.**
`/lab-tests/` pages are NLM's own; `/ency/` is copyright A.D.A.M. Inc. and
`/druginfo/` is copyright ASHP. All three sources here are `/lab-tests/`,
and the audit confirms no A.D.A.M. or ASHP content on them.

**Effect on the numbers: none.** The strip removed 3 chunks (696 → 693) and
3,388 characters. Every metric in §4 is identical to three decimal places
before and after — which is the evidence that what was removed was chrome.

No source was removed. That is a decision with a conflict attached; it is
argued in [D26](DECISIONS.md) and flagged for human sign-off.

---

## 3. Method

**Chunking** is section-aware. Headings are kept as `section_path`, which
becomes the citation's `page_or_section`; the chunk's `text` is body only,
because that is what gets quoted as `quote_or_value`. Chunk ids are
`sha256(guideline_id ⧉ section path ⧉ text)`.

A short section is packed with **its own subsections and nothing else**. An
earlier version packed forward into whatever came next, producing a chunk
carrying the NIDDK hyperthyroidism *symptoms* text under the heading *"What
are the complications of hyperthyroidism?"* — a citation pointing a
clinician at a heading the quote did not come from. That is invariant 2
failing quietly, and there is a test named after it.

**Dev queries** — 30, in `agent/evals/w2/staging/retrieval_dev.jsonl`,
written as clinical concepts only. Every one is checked against the D5
guard by a test, because D5 extends its constraint to evaluation runs and
these queries go to a hosted reranker the moment a key is configured.

**Relevance judgments** — 110, mean 3.7 per query, assigned by reading the
pooled top-8 of BM25 and of the dense retriever
(`scripts/retrieval_pool_candidates.py`), plus a few chunks found by
reading the corpus that neither retriever surfaced. They are stored as
`(guideline_id, distinctive phrase)` and resolved to chunk ids, so a
rebuild that changes the chunking fails loudly instead of scoring zero and
looking like a regression. It did exactly that once, when the rights-notice
strip deleted the chunk one judgment pointed at.

**Metrics** — `recall@5` for comparison across configurations, `recall@4`
because D25 passes four chunks to the answer step, and `MRR` over the
returned top-10.

---

## 4. Results

Apple M5 Pro, 15 cores, 24 GB, macOS 26.5.2, Python 3.12.13. 693 chunks in
memory, vectors cached, 5 timing passes over the 30 queries (150 samples
per row). Latency is the `retrieve()` call only.

| config | recall@5 | recall@4 | MRR | p50 ms | p95 ms | perfect | zero |
|---|---|---|---|---|---|---|---|
| `bm25` (sparse only) | 0.570 | 0.505 | 0.733 | 0.2 | 0.3 | 5/30 | 2 |
| `dense` (bge-small-en-v1.5) | 0.718 | 0.654 | 0.854 | 3.2 | 7.0 | 11/30 | 1 |
| `hybrid-rrf` (equal weights) | 0.692 | 0.644 | 0.826 | 3.6 | 4.6 | 9/30 | 1 |
| `hybrid-rrf-dense2x` (fitted) | 0.733 | 0.672 | 0.887 | 3.9 | 4.9 | 12/30 | 1 |
| **`hybrid-rrf` + cross-encoder — D25** | 0.702 | 0.666 | **0.889** | 288.7 | 515.1 | 10/30 | **0** |
| `hybrid-rrf-dense2x` + cross-encoder | 0.702 | 0.677 | 0.889 | 283.3 | 490.6 | 10/30 | **0** |
| `hybrid-rrf-dense2x` + Cohere `rerank-v3.5` | — | — | — | — | — | — | — |

*"perfect" = all relevant chunks in the top 5. "zero" = nothing relevant in
the top 10.*

**Quality is deterministic; latency is not.** recall and MRR are identical
byte-for-byte across runs — same corpus, same tie-breaking, same fusion. The
millisecond columns move ±20% run to run on a laptop with other work on it:
the cross-encoder row has been measured at p50 289 ms and 349 ms on the same
tree. Read the latency columns as an order of magnitude, not a number.

**The Cohere row was not run.** `COHERE_API_KEY` is not present in
`agent/.env` or the environment, so the bake-off reports it SKIPPED rather
than silently rerunning the local reranker. The adapter exists
(`app/retrieval/rerank.py`, plain `urllib`, no SDK) and runs the D5 guard
before building the request, but it is **unexercised**.

One-off costs, same machine: 67 MB + 80 MB of ONNX weights, downloaded
once; embedding all 693 chunks ~4 s cold, then cached per (model, corpus
fingerprint), so a warm dense index takes 0.5 s and the full startup
preload takes ~850 ms.

### Fusion depth was the thing that mattered

The first implementation fused each retriever's top-`top_k` — 10 lists into
10 results — and scored **recall@5 0.663**, *below* dense alone. Searching
each half to depth 30 and then returning 5 gives 0.692 with nothing else
changed; 60 and 100 also give 0.692, so 30 is where it flattens.

| per-retriever depth | 1:1 | dense 2× | dense 3× |
|---|---|---|---|
| 10 | 0.663 | 0.702 | 0.709 |
| 30 | 0.692 | **0.733** | 0.745 |
| 60 | 0.692 | 0.733 | 0.745 |
| 100 | 0.692 | 0.733 | 0.745 |

Weighting the dense half 2× is worth a further ~4 points of recall@5 and is
stable across depths 30–100 and RRF k 20–60 — and **D25 does not adopt
it**, because it was fitted on the same 30 queries it was measured on.
Dense 3× scores higher recall (0.745) with lower MRR (0.854), which is the
shape of overfitting rather than of a better setting. The code ships equal
weights; the question moves to the eval cases.

### What the reranker actually bought

Mean recall@5 moved 0.692 → 0.702 against the fusion it replaces, and MRR
0.826 → 0.889. Against the *fitted* fusion it is a regression on recall.
On means alone, ~300 ms is a hard sell.

It is the only configuration with **no query at zero recall**, and the
query it rescues is the interesting one:

| query | bm25 | dense | hybrid | hybrid-2× | + cross-encoder |
|---|---|---|---|---|---|
| `q19-levothyroxine-followup` | 0.00 | 0.00 | 0.00 | 0.00 | **1.00** |
| `q18-tsh-hypothyroid` | 0.33 | 0.33 | 0.33 | 0.33 | 0.17 |
| `q06-statin-eligibility` | 0.60 | 0.40 | 0.40 | 0.40 | 0.40 |
| `q23-iron-in-pregnancy` | 0.40 | 0.20 | 0.60 | 0.40 | 0.60 |

q19 asks about levothyroxine dosing and follow-up testing. Exactly one
chunk in the corpus answers it, inside a consumer-facing NIDDK page —
*"You will take levothyroxine, a thyroid hormone medicine…"* — and neither
retriever puts it in the top 10, because the query is written in clinical
register and the chunk is written for patients. The cross-encoder, reading
query and passage together over the depth-20 candidate pool, finds it and
ranks it first.

The reranker's value is **turning a total miss into a hit**, which is the
failure mode that matters when the alternative is an answer with no
evidence behind it. That is why D25 runs it on every retrieval rather than
behind a fallback trigger, and why it gets a 2.5 s budget rather than a
free pass.

### Expected effect of the deployment host

The agent runs on a Vultr droplet; these numbers are an Apple M5 Pro. Both
models are CPU-bound ONNX and the droplet's vCPUs are slower per core and
shared, so **these are a floor, not a prediction**:

- **BM25** — pure Python over 693 chunks, 0.2 ms here. Slower there, on a
  scale that does not matter.
- **Dense query embedding** — one forward pass through a 33 M-parameter
  encoder plus a 693×384 matrix multiply: the ~3 ms. Expect single-digit
  to low-tens of milliseconds.
- **Cold start** — embedding all 693 chunks took ~4 s here. It is baked
  into the image instead (`retrieval-deploy.md`), so it should not happen
  on the host at all; `vector_cache_prebuilt` in the preload log line says
  whether that held.
- **Cross-encoder** — 20 forward passes per query, ~300 ms here. This is
  the one that could land in the seconds on a shared vCPU. D25's budget
  exists for exactly that: over 2.5 s the fused order stands and the output
  says `reranked=false`.

**None of this is measured on the droplet.**

---

## 5. Honest limits of these numbers

- **30 queries, 110 judgments, one judge.** Differences of a few points are
  not resolvable at this size. The ~13-point gap between BM25 and the
  hybrid is; the 1-point gap between `hybrid-rrf` and the reranked row is
  not, which is why D25 rests on the zero-recall count and not on it.
- **Pooled judgments.** Relevance was judged over the union of BM25's and
  the dense retriever's top-8. Rerankers reorder that pool and are
  unaffected. A future retriever that finds chunks *outside* it would be
  scored unfairly low, and the pool should be re-judged when one is added.
- **The corpus is the ceiling.** q19 scores 0.00 for every non-reranked
  config partly because the corpus holds one patient-facing sentence on the
  topic. Retrieval quality and corpus coverage are measured together here.
- **Latency is this Mac.** See above.
- **`staging/`, not `cases/`.** `retrieval_dev.jsonl` is a staged dev set,
  not a gate. Promoting any of it is a `W2_GATE_SESSION=1` step for a
  human, and nothing here has been promoted.
