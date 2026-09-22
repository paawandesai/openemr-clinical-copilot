# Guideline corpus and retrieval bake-off

Evidence for **D5** (embeddings local vs. hosted; reranker Cohere vs. local
cross-encoder). Produced by work order 08 on branch `feat/w2-retrieval`.
D5 is **not** settled by this document — it proposes, and the proposal is
recorded as `D23` in [`DECISIONS.md`](DECISIONS.md) with status `PROPOSED`.

Everything below is reproducible offline from what is committed:

```bash
cd agent
python -m app.retrieval.build --check          # is chunks.json current?
python scripts/retrieval_label_dev_set.py --check   # do the labels still resolve?
python scripts/retrieval_bakeoff.py --repeats 5     # the table in section 3
```

Only `scripts/fetch_guidelines.py` touches the network, and it is run by
hand when the corpus is refreshed.

---

## 1. The corpus

28 documents, 696 chunks, 605,429 characters of Markdown.
`sha256(documents) = 152653224db817f9…` (full value in
`agent/app/retrieval/corpus/chunks.json`).

**Licensing.** Every source is a work of the United States federal
government and is therefore not subject to copyright in the US
(17 U.S.C. §105). Nothing paywalled; specifically **no ADA Standards of
Medical Care and no KDIGO guidelines**, both of which are copyrighted by
their societies and could not be redistributed here however clinically
useful they are. `tests/test_retrieval_corpus.py` fails if a source
appears without a licence basis or from a publisher outside the list.

**Dates.** `published` and `last_reviewed` are the publisher's own stamps,
harvested by `scripts/fetch_guidelines.py --probe` and then written into
`sources.yaml` by hand. Age is judged on the later of the two, against the
pinned `as_of: 2026-09-22` rather than against the clock, so the build is
reproducible.

| # | Source id | Publisher | Title | Pub. | Reviewed | Retrieved | Chunks | Dated |
|---|---|---|---|---|---|---|---|---|
| 1 | `uspstf-diabetes-screening` | USPSTF | Prediabetes and Type 2 Diabetes: Screening | 2021-08-24 | — | 2026-09-22 | 47 | no |
| 2 | `uspstf-statin-primary-prevention` | USPSTF | Statin Use for the Primary Prevention of CVD in Adults | 2022-08-23 | — | 2026-09-22 | 49 | no |
| 3 | `uspstf-hypertension-screening` | USPSTF | Hypertension in Adults: Screening | 2021-04-27 | — | 2026-09-22 | 42 | no |
| 4 | `uspstf-thyroid-screening` | USPSTF | Thyroid Dysfunction: Screening | 2015-03-24 | — | 2026-09-22 | 63 | **yes** |
| 5 | `uspstf-vitamin-d-deficiency-screening` | USPSTF | Vitamin D Deficiency in Adults: Screening | 2021-04-13 | — | 2026-09-22 | 40 | no |
| 6 | `uspstf-vitamin-d-calcium-fractures` | USPSTF | Vitamin D, Calcium, or Combined Supplementation … Fractures | 2018-04-17 | — | 2026-09-22 | 45 | no |
| 7 | `uspstf-iron-deficiency-anemia-pregnancy` | USPSTF | Iron Deficiency … During Pregnancy: Screening and Supplementation | 2024-08-20 | — | 2026-09-22 | 50 | no |
| 8 | `uspstf-aspirin-cvd-prevention` | USPSTF | Aspirin Use to Prevent Cardiovascular Disease | 2022-04-26 | — | 2026-09-22 | 47 | no |
| 9 | `niddk-a1c-test` | NIDDK (NIH) | The A1C Test and Diabetes | — | 2018-04-20 | 2026-09-22 | 21 | no |
| 10 | `niddk-diabetes-heart-disease-stroke` | NIDDK (NIH) | Diabetes, Heart Disease, and Stroke | — | 2021-04-01 | 2026-09-22 | 23 | no |
| 11 | `niddk-evaluate-ckd` | NIDDK (NIH) | Identify and Evaluate Patients with Chronic Kidney Disease | — | 2015-04-28 | 2026-09-22 | 10 | **yes** |
| 12 | `niddk-ckd-tests-diagnosis` | NIDDK (NIH) | Chronic Kidney Disease: Tests and Diagnosis | — | 2016-10-25 | 2026-09-22 | 6 | no |
| 13 | `niddk-hypothyroidism` | NIDDK (NIH) | Hypothyroidism (Underactive Thyroid) | — | 2021-03-01 | 2026-09-22 | 20 | no |
| 14 | `niddk-hyperthyroidism` | NIDDK (NIH) | Hyperthyroidism (Overactive Thyroid) | — | 2021-08-01 | 2026-09-22 | 23 | no |
| 15 | `nhlbi-blood-cholesterol` | NHLBI (NIH) | Blood Cholesterol | — | 2024-04-17 | 2026-09-22 | 29 | no |
| 16 | `nhlbi-high-blood-pressure` | NHLBI (NIH) | High Blood Pressure | — | 2024-04-25 | 2026-09-22 | 39 | no |
| 17 | `nhlbi-iron-deficiency-anemia` | NHLBI (NIH) | Iron-Deficiency Anemia | — | 2022-03-24 | 2026-09-22 | 11 | no |
| 18 | `nhlbi-blood-tests` | NHLBI (NIH) | Blood Tests | — | 2022-03-24 | 2026-09-22 | 8 | no |
| 19 | `cdc-chronic-kidney-disease` | CDC | Chronic Kidney Disease Basics | 2024-05-15 | 2024-05-15 | 2026-09-22 | 6 | no |
| 20 | `cdc-diabetes-testing` | CDC | Testing for Diabetes and Prediabetes | 2024-05-15 | 2024-05-15 | 2026-09-22 | 12 | no |
| 21 | `cdc-diabetes-treatment` | CDC | Treatment and Management of Diabetes | 2024-05-15 | 2024-05-15 | 2026-09-22 | 8 | no |
| 22 | `cdc-high-blood-pressure` | CDC | About High Blood Pressure | 2024-05-15 | 2026-01-28 | 2026-09-22 | 13 | no |
| 23 | `cdc-medication-safety` | CDC | About Medication Safety | 2024-05-15 | 2024-11-18 | 2026-09-22 | 13 | no |
| 24 | `cdc-cholesterol` | CDC | About Cholesterol | 2024-05-15 | 2024-05-15 | 2026-09-22 | 9 | no |
| 25 | `medlineplus-vitamin-d-test` | MedlinePlus (NLM/NIH) | Vitamin D Test | — | 2024-12-04 | 2026-09-22 | 13 | no |
| 26 | `medlineplus-tsh-test` | MedlinePlus (NLM/NIH) | TSH (Thyroid-Stimulating Hormone) Test | — | 2024-10-30 | 2026-09-22 | 13 | no |
| 27 | `medlineplus-creatinine-test` | MedlinePlus (NLM/NIH) | Creatinine Test | — | 2023-12-05 | 2026-09-22 | 14 | no |
| 28 | `nia-taking-medicines-safely` | NIA (NIH) | Taking Medicines Safely as You Age | — | 2022-09-22 | 2026-09-22 | 22 | no |

By publisher: USPSTF 8, CDC 6, NIDDK 6, NHLBI 4, MedlinePlus 3, NIA 1.
By topic: diabetes/A1c, lipids/statins, blood pressure, kidney
(eGFR/creatinine), thyroid, anemia/iron, vitamin D, medication safety —
matched to what the lab PDFs from `evals/w2/factory/` contain.

### Two sources are flagged dated

- `uspstf-thyroid-screening` — 2015-03-24, an **I statement**. It is still
  the USPSTF's current recommendation on thyroid screening, so it belongs
  in the corpus; it is eleven years old, so it is flagged.
- `niddk-evaluate-ckd` — last reviewed 2015-04-28. This one matters more
  than its age suggests: CKD staging guidance has moved since, and the
  2021 race-free eGFR equations post-date it. **Anything the co-pilot
  surfaces from this source about eGFR should be treated as needing a
  second look.**

The flag is computed by `SourceRecord.is_dated()` and asserted in
`tests/test_retrieval_corpus.py::test_dated_sources_are_flagged`. It is
**not yet surfaced to the reader** — `GuidelineSource` (the contract type
on `feat/w2-contracts`) has no `dated` field, so the only thing a caller
sees today is `version`, which carries the effective date. Raised in the
session report.

### Two sources that were wanted and could not be used

- **NIH Office of Dietary Supplements** health-professional fact sheets on
  vitamin D and iron. `ods.od.nih.gov` returns HTTP 403 behind Cloudflare
  to a non-browser client. MedlinePlus and NHLBI cover the same ground and
  were used instead.
- The `cdc.gov` edge returns 403 for a `User-Agent` containing a
  `name/1.0` version token, which is what a first attempt at a polite
  identifying UA looks like. The working UA is recorded in
  `scripts/fetch_guidelines.py`.

---

## 2. Method

**Chunking** is section-aware (`app/retrieval/chunking.py`). Headings are
kept as `section_path`, which becomes the citation's `page_or_section`;
the chunk's `text` is body only, because that is what gets quoted back as
`quote_or_value`. Chunk ids are `sha256(guideline_id ⧉ section path ⧉
text)`, truncated to 16 hex characters and prefixed with the guideline id.

A short section is packed together with **its own subsections and nothing
else**. An earlier version packed forward into whatever came next, which
produced a chunk carrying the NIDDK hyperthyroidism *symptoms* text under
the heading *"What are the complications of hyperthyroidism?"* — a
citation pointing a clinician at a heading the quote did not come from.
That is invariant 2 failing quietly, and
`test_a_chunk_is_never_attributed_to_a_heading_it_did_not_come_from`
exists to keep it fixed.

**Dev queries** — 30 of them, in
`agent/evals/w2/staging/retrieval_dev.jsonl`, written as clinical concepts
only: no names, no dates, no identifiers. Every one is checked against the
D5 guard by
`tests/test_retrieval_query_guard.py::test_every_staged_dev_query_is_concept_only`,
because D5 extends its constraint to evaluation runs and these queries go
to a hosted reranker the moment a key is configured.

**Relevance judgments** — 110 across the 30 queries, mean 3.7 per query,
assigned by reading the pooled top-8 of BM25 and of the dense retriever
for each query (`scripts/retrieval_pool_candidates.py`), plus a handful of
chunks found by reading the corpus that neither retriever surfaced. They
are stored as `(guideline_id, distinctive phrase)` in
`scripts/retrieval_label_dev_set.py` and resolved to chunk ids, so a
rebuild that changes the chunking fails loudly instead of scoring zero and
looking like a regression.

**Metrics** — `recall@5` (5 is what an answer can cite without the
citation list becoming the answer) and `MRR` over the returned top-10.

---

## 3. Results

Apple M5 Pro, 15 cores, 24 GB, macOS 26.5.2, Python 3.12.13. 696 chunks in
memory, vectors cached, 5 timing passes over the 30 queries (150 samples
per row). Latency is the `retrieve()` call only — index construction and
model load are excluded and reported separately.

| config | recall@5 | MRR | p50 ms | p95 ms | perfect | zero |
|---|---|---|---|---|---|---|
| `bm25` (sparse only) | 0.570 | 0.733 | 0.2 | 0.3 | 5/30 | 2 |
| `dense` (bge-small-en-v1.5, 384-dim) | 0.718 | 0.854 | 2.6 | 4.1 | 11/30 | 1 |
| `hybrid-rrf` (RRF k=60, depth 30, 1:1) | 0.692 | 0.826 | 2.8 | 3.5 | 9/30 | 1 |
| **`hybrid-rrf-dense2x`** (RRF, dense weighted 2×) | **0.733** | **0.887** | **2.7** | **3.4** | **12/30** | 1 |
| `hybrid-rrf` + local cross-encoder | 0.702 | 0.889 | 510.6 | 771.6 | 10/30 | **0** |
| `hybrid-rrf-dense2x` + local cross-encoder | 0.702 | 0.889 | 530.7 | 750.3 | 10/30 | **0** |
| `hybrid-rrf-dense2x` + Cohere `rerank-v3.5` | — | — | — | — | — | — |

*"perfect" = queries where all relevant chunks are in the top 5. "zero" =
queries where nothing relevant is in the top 10.*

**The Cohere row was not run.** `COHERE_API_KEY` is not present in
`agent/.env` or the environment, so the bake-off reports it as SKIPPED
rather than silently rerunning the local reranker. The code path exists
(`app/retrieval/rerank.py::CohereReranker`, plain `urllib`, no SDK) and
runs the D5 guard on the query before the request is built, but it is
**unexercised**. Any comparison between the two rerankers is unmeasured.

One-off costs, same machine: model download 67 MB (embedder) + 80 MB
(cross-encoder), first time only; embedding all 696 chunks ~4 s cold, and
the vectors are then cached per (model, corpus fingerprint), so building
the dense index takes **0.5 s** warm.

### Fusion depth was the thing that mattered

The first implementation fused each retriever's top-`top_k` — 10 lists
into 10 results — and scored **recall@5 0.663**, *below* dense alone. That
is fusion throwing away the evidence that one retriever found something
the other missed. Searching each half to depth 30 and then returning 5
gives 0.692 with nothing else changed, and 60 and 100 give 0.692 as well,
so 30 is where it flattens. `DEFAULT_FUSION_DEPTH = 30`.

| per-retriever depth | 1:1 | dense 2× | dense 3× |
|---|---|---|---|
| 10 | 0.663 | 0.702 | 0.709 |
| 30 | 0.692 | **0.733** | 0.745 |
| 60 | 0.692 | 0.733 | 0.745 |
| 100 | 0.692 | 0.733 | 0.745 |

Weighting the dense half 2× is worth a further ~4 points of recall@5 and
~6 points of MRR, and is stable across depths 30–100 and RRF k 20–60.
**It is nonetheless a parameter fitted on 30 queries.** Dense 3× scores
higher recall (0.745) but lower MRR (0.854), which is the shape of
overfitting, not of a better setting. The default in the code stays 1:1
(`EQUAL_FUSION_WEIGHTS`); 2× is passed explicitly by the bake-off and is
what D23 proposes adopting **after** it is confirmed on the eval cases.

### What the reranker actually bought

Mean recall@5 went *down* (0.733 → 0.702) and MRR went up by 0.002. On
those numbers the cross-encoder is not worth 500 ms.

But it is the only configuration with **no query at zero recall**, and the
query it rescues is the interesting one:

| query | bm25 | dense | hybrid | hybrid-dense2x | + cross-encoder |
|---|---|---|---|---|---|
| `q19-levothyroxine-followup` | 0.00 | 0.00 | 0.00 | 0.00 | **1.00** |
| `q18-tsh-hypothyroid` | 0.33 | 0.33 | 0.33 | 0.33 | 0.17 |
| `q06-statin-primary-prevention-eligibility` | 0.60 | 0.40 | 0.40 | 0.40 | 0.40 |
| `q23-iron-in-pregnancy` | 0.40 | 0.20 | 0.60 | 0.40 | 0.60 |

q19 asks about levothyroxine dosing and follow-up testing. The corpus has
exactly one chunk that answers it, inside a consumer-facing NIDDK page
(*"You will take levothyroxine, a thyroid hormone medicine…"*), and
neither retriever puts it in the top 10 — the query is written in clinical
register and the chunk is written for patients. The cross-encoder, reading
the candidate pool at depth 30, finds it.

So the reranker's measured value is not "better ranking on average". It is
**turning a total miss into a hit**, which is the failure mode that
matters when the alternative is a confident answer with no evidence behind
it.

### Expected effect of the deployment host

The agent runs on a Vultr droplet (`agent/docs/deployment.md`); the
numbers above are an Apple M5 Pro. Both models are CPU-bound ONNX, and the
droplet's vCPUs are slower per core and shared, so **these are a floor,
not a prediction**:

- **BM25** — pure Python over 696 chunks, ~0.2 ms here. Slower there, but
  on a scale where it does not matter.
- **Dense query embedding** — one forward pass through a 33 M-parameter
  encoder plus a 696×384 matrix multiply. This is the ~2.5 ms. Expect
  single-digit to low-tens of milliseconds on the droplet.
- **Cold start** — embedding all 696 chunks took ~4 s here and will be
  materially worse there. It happens once per corpus version and the
  result is cached, but the cache must be warmed at deploy, not on the
  first clinician's question.
- **Cross-encoder** — 30 full forward passes per query, 500 ms here.
  This is the one that could plausibly land in the seconds on a shared
  vCPU, which would put it outside the interactive budget in
  `docs/KEY_METRICS.md` on its own.

**None of this is measured on the droplet.** It is reasoning from what the
models do, and it is the main thing that should be checked before D5 is
settled.

---

## 4. Recommendation (proposed, not decided)

Recorded as **D23 — PROPOSED** in [`DECISIONS.md`](DECISIONS.md).

1. **Embeddings: local.** `BAAI/bge-small-en-v1.5` via fastembed/ONNX.
   MIT, 67 MB, 384-dim, no torch. It is 2.6 ms warm, it beats BM25 by 15
   points of recall@5 on its own, and — the argument that does not depend
   on the numbers — a hosted embedding API would put every retrieval query
   on the wire, which is exactly the surface D5's hard constraint is about.
   Local keeps the constraint structurally satisfied rather than
   procedurally.

2. **Fusion: hybrid RRF, per-retriever depth 30, dense weighted 2×** —
   adopted only after the weighting is confirmed on the Week 2 eval cases,
   since it is fitted on 30 dev queries. Depth 30 is not fitted and should
   be adopted now; it is a bug fix, not a tuning choice.

3. **Reranker: local cross-encoder, and not on the default path.**
   `Xenova/ms-marco-MiniLM-L-6-v2`, Apache-2.0, 80 MB, no torch. On mean
   metrics it does not pay for its 500 ms. Its measured value is
   eliminating total misses, so run it **as a fallback** — when the fused
   result set looks weak — rather than on every turn. The trigger
   condition is not designed yet and should be, with the eval cases as
   evidence.

4. **Cohere: not adopted, and not rejected either — unmeasured.** The
   adapter is written and guarded; it has never run. If it is to be
   considered, someone configures a key and reruns
   `scripts/retrieval_bakeoff.py`. Two things would have to be true for it
   to win: it would have to beat the local cross-encoder by enough to
   justify a per-query network call inside the interactive budget, and the
   concept-only guarantee would have to be something the team is
   comfortable resting on a regex rather than on the query never leaving
   the process.

---

## 5. Honest limits of these numbers

- **30 queries, 110 judgments, one judge.** Differences of a few points
  are not resolvable at this size. The ~16-point gap between BM25 and the
  hybrid is; the 3-point gap between `dense` and `hybrid-rrf-dense2x` is
  suggestive at best.
- **Pooled judgments.** Relevance was judged over the union of BM25's and
  the dense retriever's top-8. Rerankers reorder that pool and are
  unaffected. A future retriever that finds chunks *outside* the pool
  would be scored unfairly low against this file, and the pool should be
  re-judged when one is added.
- **The corpus is the ceiling.** q19 scores 0.00 for every non-reranked
  config partly because the corpus holds one patient-facing sentence on
  the topic. Retrieval quality and corpus coverage are being measured
  together here, and where a query scores badly the first question is
  which of the two is at fault.
- **Latency is this Mac.** See above.
- **`staging/`, not `cases/`.** `retrieval_dev.jsonl` is a staged dev set,
  not a gate. Promoting any of it into `agent/evals/w2/cases/` is a
  `W2_GATE_SESSION=1` step for a human, and nothing here has been
  promoted.
