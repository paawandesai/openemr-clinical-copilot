# Week 2 Cost and Latency — Clinical Co-Pilot

This is the Week 2 companion to [`docs/COST_ANALYSIS.md`](../COST_ANALYSIS.md),
which remains the source for Week 1's unit costs, published prices and rate
limits. Week 2 adds three cost centres:

- **a vision call per uploaded document**, the only new model call on the
  extraction path;
- **CPU for OCR and the local cross-encoder**, which is new local load;
- **an LLM-judge tier in CI**, which is small.

Every number carries one of Week 1's labels:

- **Measured**: read from a committed log, evidence file or document.
- **Published**: vendor figures, taken from `docs/COST_ANALYSIS.md` §1.2 and
  §4.2, fetched 2026-09-16. Re-check them before relying on the dollars.
- **Estimated**: calculated from measured or published inputs, with the
  arithmetic shown.
- **Assumed**: an input with no data behind it.

**Snapshot and branches.** Written 2026-09-22 against `main@f59b29c`. Several
sources are on branches that had not merged at the time, and three of those
branches exist **only locally**, not on `origin`. Each citation names its
branch and commit.

| Short name used below | Full path |
|---|---|
| `LOAD_TEST.md` | `feat/w2-observability@346e318:docs/w2/LOAD_TEST.md` (local only) |
| `DEPLOY.md` | `chore/w2-deploy-rehearsal@0c0f360:docs/w2/DEPLOY.md` (local only) |
| gate README | `feat/w2-eval-gate@647dd9a:agent/evals/w2/README.md` (on `origin`) |

---

## Summary

- **Actual Week 2 dev spend that can be located: $5.73** (measured,
  itemised in §1). The true total is higher by an amount nobody recorded: no
  Anthropic Console reading was taken for Week 2, and at least three sets of
  live calls left no cost in the repository. For comparison, Week 1's total
  was $0.36 (`docs/COST_ANALYSIS.md` §2.2).
- **One encounter is estimated at $0.29–$0.89, $0.52 in the middle** (§2).
  An encounter here is a Pre-Visit Brief, 4 follow-up turns and 1 uploaded
  document. The two model-call components are measured; the guideline-chunk
  overhead and the composition are estimated.
- **Projections at mid unit cost** (§3):

  | Encounters per clinic day | Per month |
  |---|---|
  | 100 | $1,097 |
  | 1K | $10,969 |
  | 10K | $109,686 |
  | 100K | $1,096,858 |

  Even 100 a day is over Anthropic's Build-tier monthly cap of $1,000
  (published). The architecture changes that each level forces are in §3.
- **Latency: the model is the wall clock and OpenEMR is the ceiling**,
  unchanged from Week 1 (§4). Extraction takes 15–33 s per document, and the
  vision call is 93–99% of it. A real conversation turn is p95 6.41 s locally
  at 10 users, with the draft 85% of the wait.
- **The new local bottleneck is the reranker.** At 50 concurrent retrievals
  its p95 was **335 s** (335,224 ms) before the concurrency limit. With at
  most two concurrent reranks (D39) it is **259 ms**, but only **1%** of
  those burst retrievals, and 6.2% at 50 paced users, were actually reranked.
  The admission bound is not on `main` and not on the live host (§4.3).

---

## 1. Actual development spend

Each row records who measured the amount and where it is written. Nothing is
rounded up.

| # | What | Amount | Kind | Source |
|---|---|---|---|---|
| 1 | Six recorded live vision transcriptions (`claude-opus-5`, one call per factory sample) | **$0.78119** | Measured (sum of per-file `cost_usd`) | `agent/evals/w2/staging/transcriptions/*.transcription.json`; "about $0.78 of real inference", gate README |
| 2 | Load-test vision sample, 3 sequential live extractions | **$0.34766** | Measured (0.125915 + 0.09553 + 0.126215) | `LOAD_TEST.md` §4b; `feat/w2-observability@346e318:docs/w2/evidence/load/extraction-live-vision.json` |
| | ↳ "one live extraction ~$0.126" | $0.125915 (`lab-clean-001`, 6,663 in / 3,704 out) | Measured | same file, first run |
| 3 | **Unplanned** real-LLM conversation load run, 100 turns at 10 users (the "~$3.00 overrun") | **$2.99** | Measured per turn, $0.0299 at 4,250 tokens | `LOAD_TEST.md` §2 and "What it cost". The session "assumed the stub covered the conversation route, and the module docstring says it does not" |
| 4 | API collection runs: 3 runs, 5 calls | **$0.38** ($0.150 + $0.087 + $0.145) | Measured, from logs | `LOAD_TEST.md`, "What it cost" |
| 5 | Eval-gate judge, one cold run: 24 calls, 15,825 in / 2,593 out on `claude-haiku-4-5` at $1/$5 per MTok | **$0.0288** | Measured (15,825 × $1 + 2,593 × $5 = $0.02879) | gate README, "What a run costs" |
| 6 | Deploy rehearsal on Vultr: brief + 4 turns × 3 patients | **$1.204075** ($0.180445 + $0.37474 + $0.64889) | Tokens measured from `llm_usage_event`; cost = tokens × $5/$25 per MTok | `DEPLOY.md` §8, "Cost per encounter" |
| | **Total located** | **$5.731725 ≈ $5.73** | Sum of measured rows | this table |

**Reported but not located.**

- **"An I-suite run ~$1.21"** was reported by a session, but that figure is
  not in any committed file, commit message or branch. The Week 2 I-suite
  (prompt injection in an uploaded PDF, W2-I1 to W2-I4) is entirely
  `pending_sut` and has never executed
  (`feat/w2-eval-gate@647dd9a:docs/w2/EVAL_GATE_EVIDENCE.md`, pending list).
  The nearest committed figure is row 6, $1.204, which may be what was
  remembered. **It is not added a second time.**

**Gaps: spend that happened and was not recorded.**

| Activity | Why it cost money | What is known |
|---|---|---|
| Judge meta-check fixture selection | five best-of-3 rounds per candidate fixture, plus generic-rubric comparisons (D46, D48) | no call count or cost committed |
| Planted-regression harness runs | each run re-grades the judge tier on the patched copies | the committed evidence run shows 0 calls (warm cache); earlier cold runs are not recorded |
| `agent/scripts/w2_e2e_ask.py` live runs | one drafting call per run, plus vision with `--live-vision` | no output committed on any branch |
| Week 1 live eval re-runs on Vultr, 2026-09-20 and 21 (43 cases) | real model calls | no dollar figure committed |

**Zero by design:**

- the retrieval bake-off, which used local models only; Cohere was never run
  (D25);
- the Langfuse traffic generator, which makes no Anthropic call;
- `/ready`'s LLM probe, which is `models.retrieve` (Week 1).

**Not included:** the AI coding assistant used to build the project, and
infrastructure. The Vultr host's price was never fetched, the same gap
`docs/COST_ANALYSIS.md` §2.2 records.

---

## 2. Unit cost of one encounter

**Definition (assumed).** One encounter is one Pre-Visit Brief, 4 follow-up
turns and 1 uploaded document extracted once. The 4 turns match Week 1's
usage assumption (`docs/COST_ANALYSIS.md` §3). The document is the Week 2
addition.

| Component | Low | Mid | High | Kind and source |
|---|---|---|---|---|
| Brief + 4 turns | $0.180445 (pid 5, 22 lab results) | $0.37474 (pid 38, 170) | $0.64889 (pid 40, 3,226) | **Measured** on the live host, `DEPLOY.md` §8 (27,944/1,629, 52,003/4,589 and 101,103/5,735 tokens in/out) |
| One document extraction | $0.09553 (cheapest recording) | $0.130198 (mean of six) | $0.227645 (2-page multipage lab) | **Measured**, the six recordings' `cost_usd` |
| Guideline chunks in 4 turns | $0.0174 | $0.0174 | $0.0174 | **Estimated**: 4 chunks × 4 turns × (602,041 chars ÷ 693 chunks ÷ 4 chars/token ≈ 217 tokens) ≈ 3,475 input tokens × $5/MTok. The 4 chars/token is **assumed** |
| **Encounter** | **$0.2934** | **$0.5223** | **$0.8939** | **Estimated** (sum) |

What this unit cost does not capture:

- **The Week 2 ask turn itself is unmeasured.** The measured turns are
  Week 1 conversation turns. `/w2/ask` also sends the document's located
  fields to the model, and that token count has never been logged from a live
  run (§1, gaps). The chunk row is a floor for the difference, not a
  measurement of it.
- **Chart size is still the largest uncertainty.** Largest chart against
  smallest is 3.6× on the session cost ($0.649 ÷ $0.180). Week 1 found the
  same (`docs/COST_ANALYSIS.md` §1.3).
- **Extraction is per document, not per question.** It runs once at upload
  (D7). A re-upload of the same bytes is deduplicated by SHA3-512 (D1), but
  only within one process's memory. After a restart, or on a second worker,
  the same file is extracted and billed again (D40 item 10).
- **CI judge cost is negligible**: $0.0288 per cold run (§1 row 5).

---

## 3. Projected production cost, and what each level forces

`per month = encounters per clinic day × 21 clinic days × unit cost`. The 21
clinic days are Week 1's assumption. The mid unit cost is used, with the
low–high range beside it. At Week 1's 20 encounters per clinician per day, 100
encounters a day is about 5 clinicians and 100K is about 5,000.

| Encounters per clinic day | Per day (mid) | Per month (mid) | Per month (low – high) |
|---|---|---|---|
| 100 | $52 | **$1,097** | $616 – $1,877 |
| 1K | $522 | **$10,969** | $6,160 – $18,772 |
| 10K | $5,223 | **$109,686** | $61,603 – $187,721 |
| 100K | $52,231 | **$1,096,858** | $616,035 – $1,877,211 |

Naive per-token × N, Opus 5 at $5/$25 per MTok, no caching.

### 3.1 Load at the peak minute

The peak minute is estimated as daily volume ÷ 480 minutes × 2, Week 1's
peak factor (`docs/COST_ANALYSIS.md` §4.2).

| Per day | Encounters/min | Input TPM | Output TPM | Concurrent vision calls | OCR cores (if every page scanned) | Retrieval turns/s (if every turn retrieves) |
|---|---|---|---|---|---|---|
| 100 | 0.42 | 27K | 3.3K | 0.18 | 0.07 | 0.03 |
| 1K | 4.2 | 272K | 33K | 1.8 | 0.66 | 0.28 |
| 10K | 41.7 | 2.72M | 326K | 18 | 6.6 | 2.8 |
| 100K | 417 | **27.2M** | **3.26M** | 180 | 66 | 28 |

Inputs to the table:

- **Tokens per encounter.** Input is the mid session's 52,003, plus the mean
  vision input of 9,891, plus 3,475 chunk tokens. Output is 4,589 plus the
  mean vision output of 3,230.
- **Concurrent vision calls** = encounters/min × 25.9 s (the mean of the
  three live extractions, `extraction-live-vision.json`) ÷ 60.
- **OCR cores** = 1.67 pages per document (10 pages across the six samples)
  × 5.7 s per page (host p50, `DEPLOY.md` §8). This is an upper bound: a
  document with a text layer needs no OCR.
- **Retrieval turns/s** = encounters/min × 4 turns ÷ 60.

**Rerank capacity (estimated from measurements).** Two slots (D39) and one
rerank at a time per slot give:

| Host condition | Rerank duration | Capacity |
|---|---|---|
| Live host, idle | 1.38 s p50 | 2 ÷ 1.38 s ≈ **1.45 reranks/s** |
| Live host, OCR running | 2.45 s p50 | ≈ **0.82 reranks/s** |
| Laptop (M5 Pro) | ~250 ms | ≈ 8/s (D39's own figure) |

The host durations are from `DEPLOY.md` §8.

### 3.2 What each level needs

**100 encounters a day (about 5 clinicians). Cost is not the problem; the
missing pieces are.**

- **Move up an Anthropic tier before launch.** $1,097 a month exceeds the
  Build cap of $1,000, and Start's is $500 (published). Week 1 already found
  a new HIPAA organisation starts on a low tier (`docs/COST_ANALYSIS.md` §4.3).
- **Finish the product, not the scale:**
  - register the source-documents card and the module upload routes
    (`docs/w2/ui-integration.md` §2–3);
  - merge the upload guard, so `/documents` authorizes, rate-limits and caps
    before reading a body (`DEPLOY.md` §2.2, `feat/w2-php-wiring@405b5ac`);
  - persist the document and extraction store, so a restart does not forget
    uploads or re-bill them.
- **The single host holds at this load.** Everything is well under one
  process's capacity: 0.18 concurrent vision calls, 0.03 retrievals a second.

**1K encounters a day (about 50 clinicians). The shared host becomes the
constraint.**

- **Cost is on the Scale tier**, well under its $200K cap.
- **Take extraction out of the serving process.** Vision calls hold 1.8
  concurrent slots against a default of 2 (`AGENT_EXTRACTION_MAX_CONCURRENT`,
  `DEPLOY.md` §9). Any burst returns 503 `extraction_capacity`.
- **Take it off the OpenEMR host.** OCR in the same container more than
  doubles rerank p95, from 2.08 s to 3.39 s (`DEPLOY.md` §8). A separate
  extraction worker on its own CPU is the recommendation `LOAD_TEST.md`
  ("Where the new bottleneck is") already makes.
- **Retrieval fits.** 0.28 turns/s is under the host's ~0.82–1.45 reranks/s.
- **Memory is the next wall.** Reranker RSS reaches 3.1 GiB under paced
  load (D39), against a 7.2 GiB host where OpenEMR peaks at 4.9 GiB.
- **Merge D39 and fix the circuit.** The circuit breaker never closes, so
  one slow rerank disables reranking until the process restarts (D41).
  Both are needed before this level.

**10K encounters a day (about 500 clinicians, many EHR deployments, per
Week 1's user model). Replicas, and a real reranking tier.**

- **Replicas are mandatory, and they break in-memory state.** Every store
  that lives in process memory has to move out (D40 item 10):
  - Redis for conversations;
  - a persisted document and extraction store;
  - a shared idempotency view of uploads.
- **Extraction needs a queue with backpressure.** 18 concurrent vision calls
  and up to 6.6 cores of OCR is a worker pool, not a thread inside the API.
- **Reranking needs its own compute.** 2.8 retrieval turns/s against
  0.8–1.45 reranks/s per host means most answers go unreranked under the D39
  admission rule. D39 says it plainly: "A higher rate needs a faster reranker
  or more CPU". The options are a dedicated rerank service on its own CPU or
  GPU, or re-deciding D25.
- **Token rates fit the Scale tier.** 2.72M input and 326K output TPM against
  10M and 2M (published).
- **Spend levers become worth building.** Week 1 estimated that a
  static-prefix cache saves 21% per brief (`docs/COST_ANALYSIS.md` Summary,
  §4.5); it was never measured. The same analysis now applies
  to the extraction prompt and schema, which are identical on every document.
  Tiering the vision model is **unmeasured**: no cheaper model has been run
  against the six recordings.

**100K encounters a day (about 5,000 clinicians). Past the published
Scale tier.**

- **A Custom tier is required** (published). Input 27.2M TPM exceeds 10M,
  output 3.26M TPM exceeds 2M, and $1.1M a month exceeds the $200K cap.
- **Vision is the output-heavy call.** A multipage lab emitted 6,820 output
  tokens against a whole session's 4,589, and output is 5× the price of
  input. Extraction cost is where a model-tier evaluation pays first.
- **Local compute becomes a service.** 180 concurrent vision calls, up to 66
  OCR cores, and 28 retrieval turns/s need a reranking tier sized to that
  rate, or the reranker becomes a rare event.
- **Batching is not available.** The Message Batches API is not
  HIPAA-eligible (`docs/COST_ANALYSIS.md` §4.6). Upload-time extraction
  cannot be batched to save money.

---

## 4. Latency

### 4.1 p50 / p95 by path

| Path | Where | n | p50 | p95 | Other | Source |
|---|---|---|---|---|---|---|
| Document extraction, real vision | local, sequential | 3 | — | — | 15.3 s, 29.5 s, 32.9 s (mean 25.9 s); vision is 93–99% | `LOAD_TEST.md` §4b |
| Extraction, vision replayed, 1 concurrent | local | 2 | 1.17 s | 2.21 s | | `LOAD_TEST.md` §4a |
| Extraction, vision replayed, 50 concurrent | local | 100 | 4.81 s | 10.20 s | 8.25 cores busy, 1,233 MiB RSS | `LOAD_TEST.md` §4a |
| OCR per page, Tesseract at 300 dpi | **live host** | 3 pages, best of 3 | 5.7 s | — | max 6.2 s | `DEPLOY.md` §8 |
| Text layer per page | **live host** | 5 pages | 6 ms | — | max 14 ms | `DEPLOY.md` §8 |
| Retrieval, hybrid, no rerank | **live host** | 60 | 7.8 ms | 9.4 ms | | `DEPLOY.md` §8 |
| Rerank stage, idle host | **live host** | 90 | 1.38 s | 2.08 s | max 2.28 s | `DEPLOY.md` §8 |
| Rerank stage, OCR in the same container | **live host** | 60 | 2.45 s | 3.39 s | max 4.09 s; 43.3% over 2.5 s | `DEPLOY.md` §8 |
| Conversation turn, real LLM, 10 users | local | 100 | 5.25 s | 6.41 s | draft p95 5.56 s | `LOAD_TEST.md` §2 |
| Pre-Visit Brief, real LLM | **live host** | 3 patients | 19.7 s | — | max 22.8 s | `DEPLOY.md` §8 |
| Follow-up turn, real LLM | **live host** | 3 patients × 4 | 9.2 s | — | max 18.2 s | `DEPLOY.md` §8 |
| Brief, LLM stubbed, 10 users | local | 280 | 1.14 s | 1.52 s | 0% errors | `LOAD_TEST.md` §1 |
| Brief, LLM stubbed, 50 users | local | 200 | 16.08 s | 28.05 s | **58% errors**, all `fhir_unavailable` | `LOAD_TEST.md` §1 |
| **`POST /w2/ask`, end to end** | — | **0** | **unmeasured** | **unmeasured** | | no committed live run (§1, gaps) |

"—" means the source gives no percentile for that row. A p95 is not
meaningful from three samples, so none is invented.

**An estimate for `/w2/ask`, labelled as one.** The turn runs a Patient read
and chart reads, then retrieval (bounded by the rerank budget of 2.5 s on
`main`, 4 s on the deploy branch), then one drafting call, then
deterministic verification. From the parts measured separately:

| Part | p95 | Source |
|---|---|---|
| FHIR retrieve | 1.41 s | `LOAD_TEST.md` §2 |
| Rerank, contended host | 3.39 s | `DEPLOY.md` §8 |
| Draft | 5.56 s | `LOAD_TEST.md` §2 |
| **Sum** | **≈ 10.4 s** | |

That sum is a p95-of-parts on mixed hosts, not a measured p95. It sits
inside the 45 s answer-latency alert and the 90 s budget (`ALERTS.md` §1).

### 4.2 The bottleneck on each path

| Path | Bottleneck | Evidence |
|---|---|---|
| Question → answer, capacity | **OpenEMR's PHP tier**, unchanged from Week 1: 420% CPU at 10 users, 58% failures at 50, throughput *falling* from 4.53 to 2.70 req/s | `LOAD_TEST.md` §1 |
| Question → answer, wall clock | **The LLM draft**: 85% of a real turn | `LOAD_TEST.md` §2 |
| Document → extraction, wall clock and cost | **The vision call**: 15–33 s and $0.10–0.23 per document | `LOAD_TEST.md` §4b; §1 row 1 |
| Document → extraction, local CPU | **OCR**: 5.7 s per scanned page on the host, and 8.25 cores busy at 50 concurrent locally | `DEPLOY.md` §8; `LOAD_TEST.md` §4a |
| Guideline retrieval | **The cross-encoder reranker** (§4.3) | `LOAD_TEST.md` §5, §5b; D39 |
| Embedding and fusion | not a bottleneck: p95 77 ms at 50 concurrent, locally | `LOAD_TEST.md` §5 |

### 4.3 The reranker, stated plainly

D25 put a cross-encoder on every retrieval, with a 2.5 s budget and a circuit
breaker. Under concurrency the budget did not protect anything. It is checked
when a rerank *finishes*, and the breaker cannot cancel reranks already
running (D39).

| Load (local, 30 dev queries) | Before the bound: p95 / reranked | After D39 (≤2 concurrent, the rest fall back at once): p95 / reranked |
|---|---|---|
| burst, 10 concurrent | 4,260 ms / 5% | 143 ms / 5% |
| **burst, 50 concurrent** | **335,224 ms / 0%**, 5.3 GiB peak RSS on a cold run | **259 ms / 1%** |
| paced, 10 users (1 s think) | not measured | 588 ms / **35%** |
| paced, 50 users | not measured | 426 ms / **6.2%** |

Source: D39 on `feat/w2-observability@346e318`; `LOAD_TEST.md` §5, §5b.

**The cost of the fix is that most requests go unreranked.** The latency
problem is solved by not reranking. At 50 paced users, 469 of 500 retrievals
were turned away (`LOAD_TEST.md` §5b). The reranker was chosen because it is
the only configuration with no query at zero recall (D25). Under load that
property holds for 1 in 16 retrievals.

Three things remain open:

- **Memory is not fixed.** Peak RSS is 3.1 GiB with the reranker against
  0.26 GiB without. Growth follows input-shape variety. Two fixes were
  measured and rejected (D39).
- **The circuit never closes** (D41). After one slow rerank on the host, 35
  of the next 60 turns ran unreranked, and the circuit stays open until the
  process restarts.
- **Neither fix is deployed.** The live host has the 4 s budget (D41) but
  not the admission bound (D39); `main` has neither (`retriever.py:65`
  reads `RERANK_BUDGET_SECONDS = 2.5`).

---

## 5. What is measured and what is not

| Measured | Not measured |
|---|---|
| Vision cost per document, six samples (one call each) and three more live | Concurrent vision throughput and Anthropic rate limits (`LOAD_TEST.md`, "Not measured") |
| Brief + 4-turn session cost on three live charts | **The `/w2/ask` turn's tokens, cost and latency** |
| Rerank, OCR and retrieval latency on the live host | Any Week 2 load test on the live host: all load runs were local |
| Judge cost per cold CI run | Judge calls spent selecting fixtures and on cold harness runs |
| Six rows of dev spend (§1) | A Week 2 Anthropic Console total; the "$1.21 I-suite run" |
| | Extraction with a cheaper vision model; prompt caching on the extraction prompt |

The two measurements that would move this document most, in order:

1. **One live `w2_e2e_ask.py` run on each of the three charts, with the
   logged turn line committed.** It replaces the only estimated
   model-call component (§2) and gives `/w2/ask` a latency.
2. **A Console reading for the Week 2 window.** It closes §1's gaps.
