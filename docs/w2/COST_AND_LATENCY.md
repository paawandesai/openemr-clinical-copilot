# Week 2 Cost and Latency — Clinical Co-Pilot

This is the Week 2 companion to [`docs/COST_ANALYSIS.md`](../COST_ANALYSIS.md),
which remains the source for Week 1's unit costs, published prices and rate
limits.

**Week 2's new cost centres:**

- a vision call per uploaded document;
- a Week 2 answer call (`/w2/ask`) that drafts over an extracted document;
- CPU for OCR and the local cross-encoder;
- a small LLM-judge tier in CI.

**Labels.** Every number carries one of Week 1's labels:

- **Measured**: read from a committed log, evidence file or document.
- **Published**: vendor figures, from `docs/COST_ANALYSIS.md` §1.2 and §4.2,
  fetched 2026-09-16. Re-check them before relying on the dollars.
- **Estimated**: calculated from measured or published inputs, with the
  arithmetic shown.
- **Assumed**: an input with no data behind it.

**Snapshot and branches.** Written 2026-09-22 against `main@fc3143b`. Two
sources are on branches that had not merged, and one of them exists **only
locally**, not on `origin`. Short names used below:

| Short name | Full path |
|---|---|
| `LOAD_TEST.md`, `OBSERVABILITY.md`, `ALERTS.md` | `docs/w2/…` on `main` (merged from `feat/w2-observability` at `fc3143b`) |
| `DEPLOY.md` | `chore/w2-deploy-rehearsal@0c0f360:docs/w2/DEPLOY.md` (local only) |
| gate README | `feat/w2-eval-gate@647dd9a:agent/evals/w2/README.md` (on `origin`) |

---

## Summary

- **Located Week 2 dev spend: $7.37** (measured; itemised in §1). The true
  total is higher by an unrecorded amount: no Anthropic Console reading was
  taken for Week 2, and several sets of live calls left no cost in the
  repository. Week 1's total was $0.36 (`docs/COST_ANALYSIS.md` §2.2).
- **One measured Week 2 encounter costs $0.82** (§2), in each of two runs.
  The encounter was an upload with live extraction, a brief, a conversation
  turn, a question about the document and a guideline question: 87,245
  tokens (`OBSERVABILITY.md` §2, `LOAD_TEST.md` §6). **Most of that is one
  call.** The question about the uploaded report sent 34,948 tokens and got
  11,523 back, which is $0.46 at list price.
- **Projections at $0.82 per encounter** (§3):

  | Encounters per clinic day | Per month |
  |---|---|
  | 100 | $1,722 |
  | 1K | $17,220 |
  | 10K | $172,200 |
  | 100K | $1,722,000 |

  Even 100 a day is over Anthropic's Build-tier cap of $1,000 a month
  (published). 10K a day is within 14% of the Scale tier's $200K cap.
- **The worst latency in the system is Week 2's own answer step.**
  - A `/w2/ask` about an uploaded lab report takes **91.9 s and 130.5 s**
    (n = 2, no load). That is past the 90 s budget between patients.
  - All of it is one drafting call, over every extracted result
    (`LOAD_TEST.md` §6).
  - A guideline question takes 10.5 s and 10.6 s.
  - Extraction takes 15–33 s per document, but runs at upload (D7).
- **The local bottleneck is the reranker.**
  - At 50 concurrent retrievals its p95 was **335 s** (335,224 ms) before
    the concurrency limit.
  - With at most two reranks at once (D39) it is **259 ms**.
  - The cost of that fix: only **1%** of those burst retrievals, and 6.2% at
    50 paced users, were actually reranked.
  - The admission bound is on `main` since `fc3143b`, but not on the live
    host (§4.3).

---

## 1. Actual development spend

Each row names who measured the amount and where it is written. Nothing is
rounded up.

| # | What | Amount | Kind | Source |
|---|---|---|---|---|
| 1 | Six recorded live vision transcriptions (`claude-opus-5`, one call per factory sample) | **$0.78119** | Measured (sum of per-file `cost_usd`) | `agent/evals/w2/staging/transcriptions/*.transcription.json`; "about $0.78 of real inference", gate README |
| 2 | Load-test vision sample, 3 sequential live extractions | **$0.34766** | Measured (0.125915 + 0.09553 + 0.126215) | `LOAD_TEST.md` §4b; `docs/w2/evidence/load/extraction-live-vision.json` |
| | ↳ "one live extraction ~$0.126" | $0.125915 (`lab-clean-001`, 6,663 in / 3,704 out) | Measured | same file, first run |
| 3 | **Unplanned** real-LLM conversation load run, 100 turns at 10 users (the "~$3.00 overrun") | **$2.99** | Measured per turn: $0.0299 at 4,250 tokens | `LOAD_TEST.md` §2 and "What it cost". The session "assumed the stub covered the conversation route, and the module docstring says it does not" |
| 4 | Week 1-route API collection runs: 3 runs, 5 calls | **$0.38** ($0.150 + $0.087 + $0.145) | Measured, from logs | `LOAD_TEST.md`, "What it cost" |
| 5 | Two real-route Week 2 encounters through the API collection (`c8661931-…`, `07c0fd35-…`) | **$1.64** (2 × $0.82; the `07c0fd35` trace reads $0.821785) | Measured: Langfuse trace cost | `OBSERVABILITY.md` §2; `LOAD_TEST.md` §6. **Not in `LOAD_TEST.md`'s "What it cost" table**, which predates them |
| 6 | Eval-gate judge, one cold run: 24 calls, 15,825 in / 2,593 out on `claude-haiku-4-5` at $1/$5 per MTok | **$0.0288** | Measured (15,825 × $1 + 2,593 × $5 = $0.02879) | gate README, "What a run costs" |
| 7 | Deploy rehearsal on Vultr: brief + 4 turns × 3 patients | **$1.204075** ($0.180445 + $0.37474 + $0.64889) | Tokens measured from `llm_usage_event`; cost = tokens × $5/$25 per MTok | `DEPLOY.md` §8, "Cost per encounter" |
| | **Total located** | **$7.371725 ≈ $7.37** | Sum of measured rows | this table |

**Reported but not located.**

- **"An I-suite run ~$1.21"** was reported by a session, but that figure is
  not in any committed file, commit message or branch. The Week 2 I-suite
  (prompt injection in an uploaded PDF, W2-I1 to W2-I4) is entirely
  `pending_sut` and has never executed
  (`feat/w2-eval-gate@647dd9a:docs/w2/EVAL_GATE_EVIDENCE.md`, pending list).
  The nearest committed figure is row 7, $1.204, which may be what was
  remembered. **It is not added a second time.**

**Gaps: spend that happened and was not recorded.**

| Activity | Why it cost money | What is known |
|---|---|---|
| Judge meta-check fixture selection | five best-of-3 rounds per candidate fixture, plus generic-rubric comparisons (D46, D48) | no call count or cost committed |
| Planted-regression harness runs | each run re-grades the judge tier on the patched copies | the committed evidence run shows 0 calls (warm cache); earlier cold runs are not recorded |
| `agent/scripts/w2_e2e_ask.py` live runs | one drafting call per run, plus vision with `--live-vision` | no output committed on any branch |
| Week 1 live eval re-runs on Vultr, 2026-09-20 and 21 (43 cases) | real model calls | no dollar figure committed |

**Zero by design:**

- the retrieval bake-off, which used local models only; Cohere was never
  run (D25);
- the Langfuse traffic generator, which makes no Anthropic call;
- `/ready`'s LLM probe, which is `models.retrieve`.

**Not included:** the AI coding assistant used to build the project, and
infrastructure. The Vultr host's price was never fetched, the same gap
`docs/COST_ANALYSIS.md` §2.2 records.

---

## 2. Unit cost of one encounter

### 2.1 Measured: $0.82

The only Week 2 encounter measured end to end, against the real routes with
real model calls, is the API collection's "Week 2 core flow". It ran twice,
on patient `a2c373ec-57bf-41cb-8767-94410dcf663d`. The run evidence is
`docs/w2/evidence/api-collection-run.txt`.

| Request | Latency (run `07c0fd35`) | Model calls |
|---|---|---|
| `POST /documents` + 26 status polls | extraction ran in the background (D7) | 1 vision call |
| `POST /pre-visit-brief` | 18.5 s | 1 |
| `POST /pre-visit-brief/conversation` | 18.7 s | 1 |
| `POST /w2/ask`, about the uploaded document | **1 min 31.9 s** | 1 |
| `POST /w2/ask`, a guideline question | 10.5 s | 1 |
| **Encounter** | 2 min 52.7 s total run | **5 `llm_usage_event`s, 87,245 tokens, $0.82** |

The encounter total is Langfuse's figure (`OBSERVABILITY.md` §2). The other
run, `c8661931`, took 130.5 s for the document question and cost the same
$0.82 (`LOAD_TEST.md` §6).

**Where the $0.82 goes (estimated).**

- The document question alone: 34,948 in and 11,523 out (run `c8661931`,
  `LOAD_TEST.md` §6) × $5/$25 per MTok = **$0.4628**, more than half the
  encounter.
- The vision call is about $0.126: the same sample and prompt measured
  $0.125915 (§1 row 2).
- That leaves about $0.23 for the brief, the conversation turn and the
  guideline question together.
- The whole encounter is **67,967 prompt + 19,278 completion = 87,245
  tokens, $0.821785** (measured, from the trace header in
  `docs/w2/evidence/screenshots/langfuse-encounter-trace.png`).
  Opus 5 list price reproduces the cost exactly: 67,967 × $5 + 19,278 × $25
  per MTok = $0.821785. The per-call splits inside it, other than the
  document question, are not committed.

**Why the document question is expensive.** The answer step drafts over
every extracted result on the report and writes more than 11k tokens back
(`LOAD_TEST.md` §6). That is a design problem in the ask path, and it is the
single biggest lever on both cost and latency. `LOAD_TEST.md` names two
fixes: bound the output, or answer from the results the question names.

### 2.2 A component estimate, for an encounter without a whole-report question

For comparison, this builds an encounter the Week 1 way: one brief, 4
follow-up turns (Week 1's usage assumption, `docs/COST_ANALYSIS.md` §3) and
1 uploaded document.

| Component | Low | Mid | High | Kind and source |
|---|---|---|---|---|
| Brief + 4 turns | $0.180445 (pid 5) | $0.37474 (pid 38) | $0.64889 (pid 40) | **Measured** on the live host, `DEPLOY.md` §8 |
| One extraction | $0.09553 | $0.130198 (mean of six) | $0.227645 (2-page multipage) | **Measured**, the six recordings' `cost_usd` |
| Guideline chunks in 4 turns | $0.0174 | $0.0174 | $0.0174 | **Estimated**: 4 chunks × 4 turns × (602,041 chars ÷ 693 chunks ÷ 4 chars/token, the 4 **assumed**) ≈ 3,475 tokens × $5/MTok |
| **Encounter** | **$0.29** | **$0.52** | **$0.89** | **Estimated** (sum) |

This is what an encounter costs when nobody asks the co-pilot to read a
whole report back. The gap between $0.52 and the measured $0.82 is
essentially the whole-report question.

What neither unit cost captures:

- **Chart size.** The largest chart against the smallest is 3.6× on the
  session cost ($0.649 ÷ $0.180), as Week 1 found
  (`docs/COST_ANALYSIS.md` §1.3). Both Week 2 runs used one mid-sized chart.
- **Document size.** The document question's input grows with the number of
  extracted results. The measured runs used `lab-clean-001`, 104 transcribed
  values.
- **Re-extraction.** Extraction is billed once per document per process: a
  re-upload of the same bytes is deduplicated by SHA3-512 in memory. After a
  restart, or on a second worker, the same file is extracted and billed
  again (D40 item 10).
- **CI.** The judge costs $0.0288 per cold run (§1 row 6).

---

## 3. Projected production cost, and what each level forces

The formula is `per month = encounters per clinic day × 21 clinic days ×
unit cost`, using Week 1's 21 clinic days. The measured $0.82 is the primary
unit. The §2.2 estimate is shown as what the bill falls to if the
whole-report question is fixed. At Week 1's 20 encounters per clinician per
day, 100 encounters a day is about 5 clinicians and 100K is about 5,000.

| Encounters per clinic day | Per day at $0.82 | **Per month at $0.82 (measured unit)** | Per month at $0.52 (§2.2 mid, estimated) |
|---|---|---|---|
| 100 | $82 | **$1,722** | $1,097 |
| 1K | $820 | **$17,220** | $10,969 |
| 10K | $8,200 | **$172,200** | $109,686 |
| 100K | $82,000 | **$1,722,000** | $1,096,858 |

Naive per-token × N, Opus 5 list price, no caching.

### 3.1 Load at the peak minute

Estimated as daily volume ÷ 480 minutes × 2, Week 1's peak factor
(`docs/COST_ANALYSIS.md` §4.2). Per-encounter tokens are the measured
67,967 in and 19,278 out (§2.1).

| Per day | Encounters/min | Input TPM | Output TPM | Concurrent vision calls | Concurrent document questions | OCR cores (if every page scanned) | Retrieval turns/s (if every turn retrieves) |
|---|---|---|---|---|---|---|---|
| 100 | 0.42 | 28K | 8.0K | 0.18 | 0.8 | 0.07 | 0.03 |
| 1K | 4.2 | 283K | 80K | 1.8 | 7.7 | 0.66 | 0.28 |
| 10K | 41.7 | 2.83M | 803K | 18 | 77 | 6.6 | 2.8 |
| 100K | 417 | **28.3M** | **8.03M** | 180 | 772 | 66 | 28 |

How each column is derived:

- **Concurrent vision calls** = encounters/min × 25.9 s (the mean of the three
  live extractions, `extraction-live-vision.json`) ÷ 60.
- **Concurrent document questions** = encounters/min × 111.2 s (the mean of
  91.9 s and 130.5 s) ÷ 60. This assumes one whole-report question per
  encounter.
- **OCR cores** = 1.67 pages per document (10 pages across the six samples)
  × 5.7 s per page (host p50, `DEPLOY.md` §8). It is an upper bound: a
  text-layer page needs no OCR.
- **Retrieval turns/s** = encounters/min × 4 turns ÷ 60.

**Rerank capacity (estimated from measurements).** Two slots (D39) and one
rerank at a time per slot give:

| Host condition | Rerank p50 | Capacity |
|---|---|---|
| Live host, idle | 1.38 s | 2 ÷ 1.38 s ≈ **1.45 reranks/s** |
| Live host, OCR running | 2.45 s | ≈ **0.82 reranks/s** |
| Laptop (M5 Pro) | about 250 ms | about 8/s (D39's own figure) |

The host figures are from `DEPLOY.md` §8.

### 3.2 What each level needs

**Before any scale: fix the whole-report question.** At 92–131 s it
already breaks the 90 s budget for a single clinician with no load
(`LOAD_TEST.md` §6), and it is about 56% of the measured encounter's cost
($0.4628 of $0.82). Bounding its output, or answering only from the rows the
question names, is the one change that moves latency, cost and throughput
together. It is a product fix, not a scale fix, so it heads every level.

**100 encounters a day (about 5 clinicians). The problem is the product,
not cost.**

- Move up an Anthropic tier before launch. $1,722 a month is over the Build
  cap of $1,000, and Start's is $500 (published). A new HIPAA organisation
  starts on a low tier (`docs/COST_ANALYSIS.md` §4.3).
- Register the documents card and the module upload routes
  (`docs/w2/ui-integration.md` §2–3).
- Merge the upload guard, so `/documents` authorizes, rate-limits and caps
  before reading a body (`DEPLOY.md` §2.2, `feat/w2-php-wiring@405b5ac`).
- Persist the document and extraction store, so a restart does not forget
  uploads or bill them again.
- One process holds this load: under one concurrent document question, and
  0.03 retrievals a second.

**1K encounters a day (about 50 clinicians). The shared host becomes the
constraint.**

- The Scale tier is needed; $17,220 a month is well under its $200K cap.
- Take extraction out of the serving process. Vision needs 1.8 concurrent
  slots against a default of 2 (`AGENT_EXTRACTION_MAX_CONCURRENT`,
  `DEPLOY.md` §9), so any burst returns 503 `extraction_capacity`.
- Take extraction off the OpenEMR host. OCR in the same container raises
  rerank p95 from 2.08 s to 3.39 s (`DEPLOY.md` §8). A separate extraction
  worker is what `LOAD_TEST.md` ("Where the new bottleneck is") already
  recommends.
- About 8 document questions are in flight at the peak minute. Each holds a
  connection for 1.5–2 minutes, so the host needs connection and timeout
  headroom for that, unless the fix above lands.
- Retrieval fits: 0.28 turns/s against about 0.82–1.45 reranks/s on the
  host.
- Memory is the next wall. The reranker reaches 3.1 GiB under paced load
  (D39), on a 7.2 GiB host where OpenEMR peaks at 4.9 GiB.
- Deploy D39 (on `main`, not on the host), and fix the circuit breaker
  that never closes (D41). Both are needed at this level.

**10K encounters a day (about 500 clinicians, many EHR deployments).
Replicas, and a real reranking tier.**

- Spend is $172,200 a month, within 14% of the Scale tier's $200K cap. That
  is too close for a production service to plan against; request a Custom
  tier or cut the unit cost (see "before any scale" above).
- Token rates fit Scale: 2.83M input and 803K output TPM against 10M and 2M
  (published).
- Replicas are mandatory, and they break in-memory state. The conversation
  store, the document and extraction store and the rerank circuit all have
  to move out of process (D40 item 10): Redis for conversations, and a
  persisted document store with a shared view of uploads.
- Extraction needs a queue with backpressure. 18 concurrent vision calls and
  up to 6.6 cores of OCR is a worker pool, not a thread inside the API.
- Reranking needs its own compute. 2.8 retrieval turns/s against 0.8–1.45
  reranks/s per host means most answers go unreranked under D39. D39 says
  it: "A higher rate needs a faster reranker or more CPU".
- Spend levers are worth building here:
  - Week 1 *estimated* a static-prefix cache saves 21% per brief
    (`docs/COST_ANALYSIS.md` Summary, §4.5); it was never measured. The
    extraction prompt and schema are identical on every document, so the
    same analysis applies to them.
  - Tiering the vision or answer model is **unmeasured**: no cheaper model
    has been run against the recordings.

**100K encounters a day (about 5,000 clinicians). Past the published Scale
tier.**

- **A Custom tier is required** (published). Input at 28.3M TPM exceeds
  10M, output at 8.03M TPM exceeds 2M by 4×, and $1.72M a month exceeds the
  $200K cap.
- **Output tokens are the binding limit.** They come mostly from two calls,
  the document question (11,523 out) and vision (1,515–6,820 out). Both are
  bounded by design choices, not by clinical need.
- Local compute becomes a service: 180 concurrent vision calls, up to 66 OCR
  cores and 28 retrieval turns a second.
- Batching is not available. The Message Batches API is not HIPAA-eligible
  (`docs/COST_ANALYSIS.md` §4.6).

---

## 4. Latency

### 4.1 p50 / p95 by path

| Path | Where | n | p50 | p95 | Other | Source |
|---|---|---|---|---|---|---|
| **`/w2/ask` about an uploaded lab report** | local, real routes, real LLM | **2** | — | — | **91.9 s, 130.5 s**: one call, 34,948 in / 11,523 out | `LOAD_TEST.md` §6; `api-collection-run.txt` |
| `/w2/ask`, guideline question | local, real routes | 2 | — | — | 10.5 s, 10.6 s | `LOAD_TEST.md` §6 |
| Pre-Visit Brief, real LLM | local, real routes | 1 | — | — | 18.5 s | `api-collection-run.txt` |
| Conversation turn, real LLM | local, real routes | 1 | — | — | 18.7 s | `api-collection-run.txt` |
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

A "—" means the source gives no percentile. A p95 from two or three samples
would be invented, so none is shown. `/w2/ask` has **not** been load-tested.
The two runs above are single encounters.

**An estimate that the measurements confirmed.** Before §6 of
`LOAD_TEST.md` existed, a sum of measured parts predicted about 10.4 s for a
retrieving `/w2/ask`:

| Part | p95 | Source |
|---|---|---|
| FHIR retrieve | 1.41 s | `LOAD_TEST.md` §2 |
| Rerank, contended host | 3.39 s | `DEPLOY.md` §8 |
| Draft | 5.56 s | `LOAD_TEST.md` §2 |

The guideline question then measured 10.5 s and 10.6 s. The same method
could not have predicted the document question, whose draft is 20× longer.

### 4.2 The bottleneck on each path

| Path | Bottleneck | Evidence |
|---|---|---|
| **Question about an uploaded document, wall clock** | **The answer step's single drafting call**: 91.9–130.5 s, writing 11.5k tokens over every extracted result. It fires the 45 s `answer_p95_latency` alert on its own | `LOAD_TEST.md` §6; `ALERTS.md` §1 |
| Question → answer, capacity | **OpenEMR's PHP tier**, unchanged from Week 1: 420% CPU at 10 users, 58% failures at 50, and throughput *falling* from 4.53 to 2.70 req/s | `LOAD_TEST.md` §1 |
| Other questions, wall clock | **The LLM draft**: 85% of a real turn | `LOAD_TEST.md` §2 |
| Document → extraction, wall clock and cost | **The vision call**: 15–33 s and $0.10–0.23 per document, off the question path (D7) | `LOAD_TEST.md` §4b; §1 row 1 |
| Document → extraction, local CPU | **OCR**: 5.7 s per scanned page on the host; 8.25 cores busy at 50 concurrent locally | `DEPLOY.md` §8; `LOAD_TEST.md` §4a |
| Guideline retrieval | **The cross-encoder reranker** (§4.3) | `LOAD_TEST.md` §5, §5b; D39 |
| Embedding and fusion | Not a bottleneck: p95 77 ms at 50 concurrent, locally | `LOAD_TEST.md` §5 |

### 4.3 The reranker, stated plainly

D25 put a cross-encoder on every retrieval, with a 2.5 s budget and a
circuit breaker. Under concurrency the budget protected nothing: it is
checked when a rerank *finishes*, and the breaker cannot cancel reranks
already running (D39).

| Load (local, 30 dev queries) | Before the bound: p95 / reranked | After D39 (≤2 concurrent; the rest fall back at once): p95 / reranked |
|---|---|---|
| burst, 10 concurrent | 4,260 ms / 5% | 143 ms / 5% |
| **burst, 50 concurrent** | **335,224 ms / 0%**, 5.3 GiB peak RSS on a cold run | **259 ms / 1%** |
| paced, 10 users (1 s think) | not measured | 588 ms / **35%** |
| paced, 50 users | not measured | 426 ms / **6.2%** |

Source: D39; `LOAD_TEST.md` §5, §5b.

**The cost of the fix is that most requests go unreranked.** The latency
problem is solved by not reranking. At 50 paced users, 469 of 500
retrievals were turned away (`LOAD_TEST.md` §5b). The reranker was chosen
because it is the only configuration with no query at zero recall (D25).
Under that load, that property holds for 1 retrieval in 16.

**Still open:**

- **Memory.** Peak RSS is 3.1 GiB with the reranker against 0.26 GiB
  without. The growth follows input-shape variety, and two fixes were
  measured and rejected (D39).
- **The circuit never closes** (D41). After one slow rerank on the host, 35
  of the next 60 turns ran unreranked. The circuit stays open until the
  process restarts.
- **Deployment.** The live host has the 4 s budget (D41) but not the
  admission bound (D39). `main` has the admission bound
  (`MAX_CONCURRENT_RERANKS = 2`, `retriever.py:77`) but still the 2.5 s
  budget (`retriever.py:67`).

---

## 5. What is measured and what is not

| Measured | Not measured |
|---|---|
| Two real-route Week 2 encounters: cost ($0.82 each) and per-request latency | `/w2/ask` under any concurrent load |
| Vision cost per document: six samples plus three more live runs | Concurrent vision throughput and Anthropic rate limits (`LOAD_TEST.md`, "Not measured") |
| Brief + 4-turn session cost on three live-host charts | Any Week 2 route on the live host: the host does not run it |
| Rerank, OCR and retrieval latency on the live host | The per-call token split inside the $0.82 encounter, other than the document question |
| Judge cost per cold CI run | Judge calls spent selecting fixtures and on cold harness runs |
| Seven rows of dev spend (§1) | A Week 2 Anthropic Console total; the "$1.21 I-suite run" |
| | A cheaper vision or answer model; prompt caching on the extraction prompt |

The two measurements that would move this document most, in order:

1. **The whole-report answer after a fix** that bounds its output. It is
   more than half of both the cost and the latency of an encounter.
2. **A Console reading for the Week 2 window**, which closes §1's gaps.
