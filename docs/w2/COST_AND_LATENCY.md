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

**Snapshot: `main@bc5236f`**, which is on `origin`. A claim marked PENDING
rests on a branch not yet merged into `main`: **[PENDING `feat/w2-eval-gate`]** or **[PENDING `feat/w2-answer-scope`]**.

- **Merged, no longer marked:**
  - `feat/w2-php-wiring`, at `2ae4d98`: the Documents & Sources card, its
    routes, the upload guard on the real `/documents`, and the correlation
    header;
  - `chore/w2-deploy-rehearsal`, at `21f1053`: `docs/w2/DEPLOY.md`, D41,
    the self-closing rerank circuit, single-threaded Tesseract, and the
    agent's 4.5 GiB memory cap.
- **Still to merge:** `feat/w2-eval-gate` and `feat/w2-answer-scope`, both
  on `origin`. Every citation is then repinned to the one resulting `main`
  commit, and the markers come out.
- **The host runs `main@21f1053`**, deployed 2026-09-23 and measured after
  the deploy (`docs/w2/DEPLOY.md` §8.0). The snapshot adds only that section
  on top of it.
- **Answer-scope:** `feat/w2-answer-scope` fixes the slow whole-report
  question. Its decision is D50.
- **Host figures:**
  - post-deploy figures come from `docs/w2/DEPLOY.md` §8.0;
  - figures measured before the deploy stay labelled "rehearsal host, before the Week 2 deploy", and are kept
    only where no post-deploy figure exists, or for comparison.

Short names used below:

| Short name | Full path |
|---|---|
| `LOAD_TEST.md`, `OBSERVABILITY.md`, `ALERTS.md` | `docs/w2/…` on `main` |
| `DEPLOY.md` | `docs/w2/DEPLOY.md` |
| gate README | `agent/evals/w2/README.md` **[PENDING `feat/w2-eval-gate`]** |

---

## Summary

- **Located Week 2 dev spend: $8.59** (measured; itemised in §1). The true
  total is higher by an unrecorded amount: no Anthropic Console reading was
  taken for Week 2, and several sets of live calls left no cost in the
  repository. Week 1's total was $0.36 (`docs/COST_ANALYSIS.md` §2.2).
- **On the deployed host** (`DEPLOY.md` §8.0), one document at a time:
  - a lab page costs **$0.11–0.13** and takes 27.8–39.8 s to extract;
  - an intake page costs **about $0.05** and takes 8.3–15.0 s;
  - the document + guideline question costs **about $0.178** and answers in
    **15.3–21.2 s**, with 7 document + 2 guideline citations and 0
    unverified, 3 of 3 times.
  - With Week 1's brief + 4 turns, an encounter is an estimated
    **$0.54–1.06** (§2.3).
- **One measured Week 2 encounter costs $0.82** (§2), in each of two local runs.
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
- **Answer latency scales with the number of claims the answer makes**
  (§4.1a).
  - Answer step: about **3.7 s + 1.0 s per citation** over six cases, from
    3 to 180 citations (R² = 0.997). The fit is estimated from measured
    points in `LOAD_TEST.md` §7 on `feat/w2-answer-scope` **[PENDING `feat/w2-answer-scope`]**.
  - A whole-report question on the 22-row lab report makes 91 citations and
    writes about 11.5k output tokens. Its answer step takes **88.2–91.8 s**
    (n = 3).
  - The first live run from the chart asked about one analyte and one
    guideline topic. It made 8–12 citations and answered in **17.2–19.5 s**
    (n = 3), consistent with the same rate once the chart reads and token
    mint are included (`docs/w2/evidence/php-wiring-e2e-run.md`).
  - The model was the same in every run, so this shows the claim count sets
    the time on that model. It says nothing about other models.
  - Retrieval adds seconds, not minutes (§4.1).
  - The whole-report question breaks the 90 s budget. Scoping it
    (**[PENDING `feat/w2-answer-scope`]**) brings it to 6.9 s p50 on
    22 rows (§4.1d).
  - Upload to extraction complete is **34–35 s** on a 1-page text-layer
    report, and runs at upload (D7) (`docs/w2/evidence/php-wiring-e2e-run.md`).
- **The local bottleneck is the reranker.**
  - At 50 concurrent retrievals its p95 was **335 s** (335,224 ms) before
    the concurrency limit.
  - With at most two reranks at once (D39) it is **259 ms**.
  - The cost of that fix: only **1%** of those burst retrievals, and 6.2% at
    50 paced users, were actually reranked.
  - The admission bound is deployed (`main@21f1053`). In the post-deploy
    questions every rerank ran, and none was over budget, busy or skipped (§4.3).

---

## 1. Actual development spend

Each row names who measured the amount and where it is written. Nothing is
rounded up.

| # | What | Amount | Kind | Source |
|---|---|---|---|---|
| 1 | Six recorded live vision transcriptions (`claude-opus-5`, one call per factory sample) | **$0.78119** | Measured (sum of per-file `cost_usd`) | `agent/evals/w2/staging/transcriptions/*.transcription.json`; "about $0.78 of real inference", gate README **[PENDING `feat/w2-eval-gate`]** |
| 2 | Load-test vision sample, 3 sequential live extractions | **$0.34766** | Measured (0.125915 + 0.09553 + 0.126215) | `LOAD_TEST.md` §4b; `docs/w2/evidence/load/extraction-live-vision.json` |
| | ↳ "one live extraction ~$0.126" | $0.125915 (`lab-clean-001`, 6,663 in / 3,704 out) | Measured | same file, first run |
| 3 | **Unplanned** real-LLM conversation load run, 100 turns at 10 users (the "~$3.00 overrun") | **$2.99** | Measured per turn: $0.0299 at 4,250 tokens | `LOAD_TEST.md` §2 and "What it cost". The session "assumed the stub covered the conversation route, and the module docstring says it does not" |
| 4 | Week 1-route API collection runs: 3 runs, 5 calls | **$0.38** ($0.150 + $0.087 + $0.145) | Measured, from logs | `LOAD_TEST.md`, "What it cost" |
| 5 | Two real-route Week 2 encounters through the API collection (`c8661931-…`, `07c0fd35-…`) | **$1.64** (2 × $0.82; the `07c0fd35` trace reads $0.821785) | Measured: Langfuse trace cost | `OBSERVABILITY.md` §2; `LOAD_TEST.md` §6. **Not in `LOAD_TEST.md`'s "What it cost" table**, which predates them |
| 6 | Eval-gate judge, one cold run: 24 calls, 15,825 in / 2,593 out on `claude-haiku-4-5` at $1/$5 per MTok | **$0.0288** | Measured (15,825 × $1 + 2,593 × $5 = $0.02879) | gate README **[PENDING `feat/w2-eval-gate`]**, "What a run costs" |
| 7 | Deploy rehearsal on Vultr: brief + 4 turns × 3 patients | **$1.204075** ($0.180445 + $0.37474 + $0.64889) | Tokens measured from `llm_usage_event`; cost = tokens × $5/$25 per MTok | `DEPLOY.md` §8, "Cost per encounter" |
| 8 | Post-deploy run on the host: 5 documents extracted (live vision) and the document + guideline question 3 times | **$1.21341** (vision $0.126 + $0.227 + $0.126 + $0.104 + $0.096 = $0.679; questions 31,109 in × $5 plus 873, 873 and 965 out × $25 per MTok = $0.53441) | Vision: the agent's own `cost_usd`; questions: tokens measured, cost = tokens × list price | `DEPLOY.md` §8.0 |
| | **Total located** | **$8.585135 ≈ $8.59** | Sum of measured rows | this table |

**Reported but not located.**

- **"An I-suite run ~$1.21"** was reported by a session, but that figure is
  not in any committed file, commit message or branch. The Week 2 I-suite
  (prompt injection in an uploaded PDF) makes no model call where it runs:
  W2-I1 **[PENDING `feat/w2-eval-gate`]** to W2-I3 are active on a ground-truth vision stand-in, and I4 and
  I5 are pending (`docs/w2/EVAL_GATE_EVIDENCE.md`) **[PENDING `feat/w2-eval-gate`]**.
  The nearest committed figure is row 7, $1.204, which may be what was
  remembered. **It is not added a second time.**

**Gaps: spend that happened and was not recorded.**

| Activity | Why it cost money | What is known |
|---|---|---|
| Judge meta-check fixture selection | five best-of-3 rounds per candidate fixture, plus generic-rubric comparisons (D46 **[PENDING `feat/w2-eval-gate`]**, D48) | no call count or cost committed |
| Planted-regression harness runs | each run re-grades the judge tier on the patched copies | the committed evidence run shows 0 calls (warm cache); earlier cold runs are not recorded |
| `agent/scripts/w2_e2e_ask.py` live runs | one drafting call per run, plus vision with `--live-vision` | no output committed on any branch |
| Week 1 live eval re-runs on Vultr, 2026-09-20 and 21 (43 cases) | real model calls | no dollar figure committed |
| The Week 1 embed check after the deploy (brief 24/24, medication and lab turns) | real model calls | no cost recorded (`DEPLOY.md` §8.0) |

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
| `POST /w2/ask`, about the uploaded document (the whole-report question) | **1 min 31.9 s** | 1 |
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
| Brief + 4 turns | $0.180445 (pid 5) | $0.37474 (pid 38) | $0.64889 (pid 40) | **Measured** on the rehearsal host, before the Week 2 deploy, `DEPLOY.md` §8 |
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

### 2.3 On the deployed host

`DEPLOY.md` §8.0 builds the encounter from host measurements:

| Component | Cost | Kind |
|---|---|---|
| Week 1 brief + 4 turns, by chart size | $0.18–0.65 | measured on the rehearsal host, before the Week 2 deploy (`DEPLOY.md` §8) |
| One lab upload | about $0.13 | measured after the deploy (`cost_usd`) |
| One intake form | $0.05–0.10 | measured after the deploy (`cost_usd`) |
| One document + guideline question | about $0.18 | measured tokens × list price, after the deploy |
| **Encounter** | **≈ $0.54–1.06** | estimated sum |

The measured local encounter, $0.82 (§2.1), lies inside that range. Its
composition differs: one lab, no intake form, a whole-report question and a
guideline question. §3 keeps the measured $0.82.

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
| 100 | 0.42 | 28K | 8.0K | 0.18 | 0.6 | 0.07 | 0.03 |
| 1K | 4.2 | 283K | 80K | 1.8 | 6.2 | 0.66 | 0.28 |
| 10K | 41.7 | 2.83M | 803K | 18 | 62 | 6.6 | 2.8 |
| 100K | 417 | **28.3M** | **8.03M** | 180 | 618 | 66 | 28 |

How each column is derived:

- **Concurrent vision calls** = encounters/min × 25.9 s (the mean of the three
  live extractions, `extraction-live-vision.json`) ÷ 60.
- **Concurrent document questions** = encounters/min × 89.0 s ÷ 60. 89.0 s
  is the p50 of the unscoped whole-report answer step, in `LOAD_TEST.md` §7
  **[PENDING `feat/w2-answer-scope`]**. This assumes one whole-report question per encounter. After the
  scoping fix, a whole-report answer is 6.9 s p50 and the column falls about
  13×.
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

**Before any scale: fix the whole-report question** (in progress on
`feat/w2-answer-scope` **[PENDING `feat/w2-answer-scope`]**). At 88–92 s for its answer step it
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
- Deploy the upload guard, which is on `main` since `2ae4d98`: `/documents`
  authorizes, rate-limits and caps before reading a body (`DEPLOY.md` §2.2
 ).
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
- About 6 document questions are in flight at the peak minute. Each holds a
  connection for about 1.5 minutes, so the host needs connection and timeout
  headroom for that, unless the fix above lands.
- Retrieval fits: 0.28 turns/s against about 0.82–1.45 reranks/s on the
  host.
- Memory is the next wall.
  - Measured on the host, the agent peaks at **3.7 GiB** at demo load (2
    rerank threads + 1 OCR worker), leaving about 2.2 GiB of the 7.2 GiB.
  - A heavier mix was OOM-killed at a 5 GiB test cap.
  - The agent is therefore capped at 4.5 GiB. Past demo load it restarts,
    and the database is never the victim (`DEPLOY.md` §8).
- Deployed 2026-09-23 (`main@21f1053`): the D39 admission bound and the D41
  circuit that closes itself. After the deploy every rerank ran, but only
  one question at a time (`DEPLOY.md` §8.0).

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
| **`/w2/ask` about a whole uploaded lab report**, answer step | local | 3 | 89.0 s | — | **88.2–91.8 s**; 91 citations, 11,511 out | `LOAD_TEST.md` §7 on `feat/w2-answer-scope` **[PENDING `feat/w2-answer-scope`]** |
| `/w2/ask` about a whole uploaded lab report, whole HTTP request | local, real routes, real LLM | 2 | — | — | 91.9 s, 130.5 s (the second unexplained, §4.1a) | `LOAD_TEST.md` §6 |
| `/w2/ask`, one analyte + one guideline topic, from the chart | local, module routes | 3 | — | — | 17.2, 17.7, 19.5 s; 8–12 citations, 0 unverified | `docs/w2/evidence/php-wiring-e2e-run.md` |
| `/w2/ask`, guideline question | local, real routes | 2 | — | — | 10.5 s, 10.6 s | `LOAD_TEST.md` §6 |
| Pre-Visit Brief, real LLM | local, real routes | 1 | — | — | 18.5 s | `api-collection-run.txt` |
| Conversation turn, real LLM | local, real routes | 1 | — | — | 18.7 s | `api-collection-run.txt` |
| Document extraction, real vision | local, sequential | 3 | — | — | 15.3 s, 29.5 s, 32.9 s (mean 25.9 s); vision is 93–99% | `LOAD_TEST.md` §4b |
| Extraction, vision replayed, 1 concurrent | local | 2 | 1.17 s | 2.21 s | | `LOAD_TEST.md` §4a |
| Extraction, vision replayed, 50 concurrent | local | 100 | 4.81 s | 10.20 s | 8.25 cores busy, 1,233 MiB RSS | `LOAD_TEST.md` §4a |
| OCR per page, Tesseract at 300 dpi | **rehearsal host, before the Week 2 deploy** | 3 pages, best of 3 | 5.7 s | — | max 6.2 s | `DEPLOY.md` §8 |
| Text layer per page | **rehearsal host, before the Week 2 deploy** | 5 pages | 6 ms | — | max 14 ms | `DEPLOY.md` §8 |
| Retrieval, hybrid, no rerank | **rehearsal host, before the Week 2 deploy** | 60 | 7.8 ms | 9.4 ms | | `DEPLOY.md` §8 |
| Rerank stage, idle host | **rehearsal host, before the Week 2 deploy** | 90 | 1.38 s | 2.08 s | max 2.28 s | `DEPLOY.md` §8 |
| Rerank stage, OCR in the same container | **rehearsal host, before the Week 2 deploy** | 60 | 2.45 s | 3.39 s | max 4.09 s; 43.3% over 2.5 s | `DEPLOY.md` §8 |
| Conversation turn, real LLM, 10 users | local | 100 | 5.25 s | 6.41 s | draft p95 5.56 s | `LOAD_TEST.md` §2 |
| Pre-Visit Brief, real LLM | **rehearsal host, before the Week 2 deploy** | 3 patients | 19.7 s | — | max 22.8 s | `DEPLOY.md` §8 |
| Follow-up turn, real LLM | **rehearsal host, before the Week 2 deploy** | 3 patients × 4 | 9.2 s | — | max 18.2 s | `DEPLOY.md` §8 |
| **Document + guideline question, end to end** | **host, after the deploy** | 3 | — | — | 15.3, 20.9, 21.2 s; drafting call 12.7–18.5 s; retrieval incl. rerank 1.55–1.68 s | `DEPLOY.md` §8.0 |
| **Vision extraction per document** | **host, after the deploy** | 5 | — | — | 15.7–54.5 s; OCR +4.9–6.1 s per scanned page; locate + verify ~1 s | `DEPLOY.md` §8.0 |
| **Upload → extraction complete, as the card sees it** | **host, after the deploy** | 3 | — | — | 20.8 s (clean intake), 35.3 s (scanned intake), 42.2 s (scanned lab) | `DEPLOY.md` §8.0 |
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

### 4.1a Answer latency scales with the number of claims

The same `/w2/ask` answer step, on the same model (`claude-opus-5`), takes
seconds or minutes depending on how many claims the answer makes. The model
call is 99% of the step, and it is output-bound: about 131 output tokens/s
and one citation a second.

| Case | Citations | Answer step p50 (worst) | Output tokens p50 | Source |
|---|---|---|---|---|
| 22-row report, "What were the results…", **scoped** | 3 | 6.9 s (9.1 s) | 461 | `LOAD_TEST.md` §7 on `feat/w2-answer-scope` **[PENDING `feat/w2-answer-scope`]** |
| 44-row report, same question, **scoped** | 8 | 11.7 s (13.0 s) | 1,151 | same |
| critical-potassium report, **scoped**, 1 run | 8 | 12.5 s | 1,005 | same |
| 44-row report, "Show me all the results", **scoped**, cap binds | 36 | 43.4 s (45.5 s) | 5,092 | same |
| 22-row report, "What were the results…", **before** scoping | 91 | 89.0 s (91.8 s; min 88.2 s) | 11,511 | same; raw: `docs/w2/evidence/load/answer-before-22row.json` on that branch |
| 44-row report, same question, **before** scoping | 180 | 188.8 s (195.9 s) | 24,762 | same |

**The fit (estimated from the rows above).**

- Answer step ≈ **3.7 s + 1.0 s per citation**, R² = 0.997. It is least
  squares over the six p50s.
- Every case is within 7 s of the line, and five of the six are within
  3.5 s.
- The constant is the fixed part of a turn. The slope is the model writing
  about 130 output tokens per citation at about 131 tokens/s.

**The chart run fits the same line** (`docs/w2/evidence/php-wiring-e2e-run.md`, on `main`). It was the
question "What was her glucose on the uploaded lab report, and what does the
USPSTF recommend about screening for prediabetes and type 2 diabetes?", on
the local stack:

| Run | Citations (document / chart / guideline) | Ask, client wall clock | Agent `total_ms` |
|---|---|---|---|
| A | 6 / 4 / 2 = 12 | 19.5 s | 18,511 |
| C | 6 / 0 / 2 = 8 | 17.7 s | not reported |
| D | 6 / 0 / 2 = 8 | 17.2 s | not reported |

- The fit predicts an answer step of 11.8 s for 8 citations and 15.8 s for
  12.
- **On the deployed host** the same question made 9 citations (7 document + 2
  guideline). Its drafting call took 12.7–18.5 s, against 12.8 s predicted
  for 9 citations (`DEPLOY.md` §8.0).
- The client figures are 4–6 s higher. They also include the module's token
  mint and the chart reads, which the answer-step figure does not.

**What this does and does not show.**

- **Shown.** On one model, the number of claims sets answer latency, over a
  60× range of claims (3 to 180).
- **Retrieval is small.**
  - A retrieval with reranking is milliseconds to about 2 s on the idle
    rehearsal host (§4.1); the budget is 4.0 s on `main` (D41).
  - A guideline-only question takes 10.5–10.6 s end to end
    (`LOAD_TEST.md` §6).
- **Not shown:**
  - **other models**: every run used `claude-opus-5`;
  - **concurrency**: every run was a single encounter with no load.

**Why this matters.**

- A single latency figure would mislead either way. 88–92 s makes every
  answer look broken; 17–19 s hides the question that breaks the budget.
- The honest description is a rate. Answers take about a second per claim,
  so the lever is how many claims an answer makes, not the model or the
  retriever.

**How §6 and §7 relate.**

- `LOAD_TEST.md` §6 (on `main`) timed the **same** whole-report question
  from the API collection as a whole HTTP request, n = 2: 91.9 s and 130.5 s.
- §7 timed the answer step alone, n = 3: 88.2–91.8 s.
- The 91.9 s run agrees with §7.
- The 130.5 s run is 39 s slower than any §7 run, and nothing committed
  explains why. It is kept here as a measured end-to-end outlier, not as
  the typical figure.

### 4.1b The first end-to-end run from a chart

Source: `docs/w2/evidence/php-wiring-e2e-run.md`, on `main`.

- **What:** five runs on 2026-09-23 through the Documents & Sources card:
  - the module's routes, `upload.php`, `upload-status.php` and `ask.php`,
    with a real `physician` login;
  - live vision on first extraction, one live drafting call per question;
  - pid 38, and `lab-clean-001` (1 page, 22 rows, text layer).
- **Where:** the **local** stack, not the Vultr host.

| Measure | Value | Runs |
|---|---|---|
| Upload → extraction complete (live vision) | **35.3 s**, **34 s** | A, E (new bytes) |
| Ask → answer, client wall clock | **19.5 s, 17.7 s, 17.2 s** | A, C, D |
| Citations | 6 document + 4 chart + 2 guideline (A); 6 + 0 + 2 (C, D) | A, C, D |
| Unverified | **0** in every timed run | A, C, D |
| Chip → box | the glucose value chip draws one box over the value's ink | C |

- **The first committed live answer that cites a document and a guideline
  together**, from the real chart.
- **Run B is excluded.** It completed 19 minutes late, alongside a Pre-Visit
  Brief from the same chart open. That fits the host or Docker VM being
  suspended, but the cause is not established. Runs C and D repeated it
  without the delay.

### 4.1c Post-deploy re-measurement

The host was redeployed from `main@21f1053` on 2026-09-23 and re-measured
through the real chart card, one document at a time (`DEPLOY.md` §8.0):

| Measurement | Before the Week 2 deploy (rehearsal host) | After the deploy |
|---|---|---|
| Vision extraction, cost per page | not measured on the host | **lab $0.11–0.13; intake about $0.05.** Output tokens are most of it: 3,704–6,811 per lab |
| Vision extraction, latency per page | not measured on the host. OCR alone: p50 5.7 s per page; 5.6 s with two workers under `OMP_THREAD_LIMIT=1`, against ~162 s without it (`DEPLOY.md` §8) | **lab 27.8–39.8 s; intake 8.3–15.0 s.** Vision is 15.7–54.5 s per document, OCR adds 4.9–6.1 s per scanned page |
| Peak memory under OCR + reranking | 3.7 GiB at demo load (2 rerank threads + 1 OCR worker); OOM-killed at a 5 GiB test cap under a heavier mix. Measured on the host, in a throwaway container from the merged image's code, before the deploy (`DEPLOY.md` §8) | **1.32 GiB** over the whole run, one document at a time, 0 OOM kills. The 3.7 GiB figure stays the bound under concurrent reranking |
| One answer citing a document and a guideline | the host did not run `/w2/ask` | **3 of 3:** 7 document + 2 guideline citations, 0 unverified, 15.3–21.2 s end to end, about $0.178 each |

### 4.1d The document-question fix **[PENDING `feat/w2-answer-scope`]**

`feat/w2-answer-scope` scopes a document answer by rule: critical values,
abnormal rows and rows the question names are itemised, the rest is
counted, and the output is bounded.

**This branch is not on `origin`, and it numbers its decision D49**, which
`main` has already assigned to the preview URL. Both need resolving before
its numbers can be cited from `main`.

The question is "What were the results on the uploaded lab report?", 3 runs
per report, from `LOAD_TEST.md` §7 on `feat/w2-answer-scope` **[PENDING `feat/w2-answer-scope`]**:

| Report | Answer step p50 (worst): before → after | Tokens out p50: before → after | Citations | Cost per answer |
|---|---|---|---|---|
| 22 rows | 89.0 s (91.8 s) → **6.9 s (9.1 s)** | 11,511 → 461 | 91 → 3 | $0.45 → **$0.084** |
| 44 rows | 188.8 s (195.9 s) → **11.7 s (13.0 s)** | 24,762 → 1,151 | 180 → 8 | $0.87 → **$0.089** |
| 44 rows, "Show me all the results" (cap binds) | — → 43.4 s (45.5 s) | — → 5,092 | — → 36 | — → $0.22 |

That is 13× faster on 22 rows and 16× faster on 44. The worst case, where
the cap of 8 binds on the 44-row report, lands at the 45 s alert.

**Effect on the encounter (estimated).** Swapping the measured whole-report
call ($0.4628, §2.1) for the scoped one ($0.084) takes the $0.82 encounter
to about **$0.44**, since 0.82 − 0.4628 + 0.084 = 0.441. §2 and §3 keep the
measured $0.82 until an encounter is measured on the merged code.

### 4.2 The bottleneck on each path

| Path | Bottleneck | Evidence |
|---|---|---|
| **Question about a whole uploaded document, wall clock** | **The number of claims the answer makes**: about 1 s per citation plus 3.7 s (§4.1a). A whole-report answer makes 91 citations and takes 88–92 s; one analyte and one guideline topic take 17–19 s. It fires the 45 s `answer_p95_latency` alert on its own | `LOAD_TEST.md` §6; `ALERTS.md` §1 |
| Question → answer, capacity | **OpenEMR's PHP tier**, unchanged from Week 1: 420% CPU at 10 users, 58% failures at 50, and throughput *falling* from 4.53 to 2.70 req/s | `LOAD_TEST.md` §1 |
| Other questions, wall clock | **The LLM draft**: 85% of a real turn | `LOAD_TEST.md` §2 |
| Document → extraction, wall clock and cost | **The vision call**: 15–33 s and $0.10–0.23 per document, off the question path (D7) | `LOAD_TEST.md` §4b; §1 row 1 |
| Document → extraction, local CPU | **OCR**: 5.7 s per scanned page on the rehearsal host, before the Week 2 deploy; 8.25 cores busy at 50 concurrent locally | `DEPLOY.md` §8; `LOAD_TEST.md` §4a |
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

- **Memory, still open.**
  - Locally, peak RSS is 3.1 GiB with the reranker against 0.26 GiB without
    (D39). The growth follows input-shape variety, and two fixes were
    measured and rejected.
  - On the host at demo load, the agent peaks at 3.7 GiB, OCR included.
  - The 4.5 GiB cap turns overload into an agent restart, not a database
    kill (`DEPLOY.md` §8).
- **The circuit: fixed on `main`** (D41).
  - It used to latch: after one slow rerank on the host, 35 of the next 60
    turns ran unreranked.
  - It now closes after 60 s or 10 skipped turns, and a skipped turn is
    logged apart from a slow rerank (`retriever.py:91–92`).
- **Deployment.**
  - `main` has all three: the admission bound (`retriever.py:82`), the 4.0 s
    budget (`:72`) and the self-closing circuit.
  - All three are deployed (`main@21f1053`). In the three post-deploy
    questions every rerank ran, and none was over budget, busy or skipped
    (`DEPLOY.md` §8.0). None of it has been measured under load on the host.

---

## 5. What is measured and what is not

| Measured | Not measured |
|---|---|
| Two real-route Week 2 encounters: cost ($0.82 each) and per-request latency; the deployed flow one document at a time: vision cost and latency per page, the combined answer, memory (`DEPLOY.md` §8.0) | `/w2/ask` under any concurrent load |
| Vision cost per document: six samples plus three more live runs | Concurrent vision throughput and Anthropic rate limits (`LOAD_TEST.md`, "Not measured") |
| Brief + 4-turn session cost on three live-host charts | Any Week 2 route on the host **under load**: the post-deploy run was one document at a time |
| Rerank, OCR and retrieval latency on the live host (rehearsal host, before the Week 2 deploy) | The per-call token split inside the $0.82 encounter, other than the document question |
| Judge cost per cold CI run | Judge calls spent selecting fixtures and on cold harness runs |
| Eight rows of dev spend (§1) | A Week 2 Anthropic Console total; the "$1.21 I-suite run" |
| | A cheaper vision or answer model; prompt caching on the extraction prompt |

The two measurements that would move this document most, in order:

1. **The whole-report answer after a fix** that bounds its output. It is
   more than half of both the cost and the latency of an encounter.
2. **A Console reading for the Week 2 window**, which closes §1's gaps.
