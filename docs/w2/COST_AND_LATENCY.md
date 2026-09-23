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

**Snapshot: `main@056cba9`**, which is on `origin`. Every path, number and
decision below is on that commit. It includes these merges:

| Merge | At | What it brought |
|---|---|---|
| `feat/w2-php-wiring` | `2ae4d98` | the Documents & Sources card, its routes, the upload guard |
| `chore/w2-deploy-rehearsal` | `21f1053` | D41 |
| its first post-deploy figures | `bc5236f` | `DEPLOY.md` §8.0 |
| its D50-redeploy figures | `5ad81c5` | `DEPLOY.md` §8.00 |
| `feat/w2-eval-gate` | `0b4ae04` | the eval gate, D42–D48 |
| `feat/w2-answer-scope` | `ea198c8` | D50 |
| the DECISIONS restore | `caa92f8` | the dropped decision entries |
| `EVAL_GATE.md` | `0318c35` | the gate's page for reviewers |
| the decisions guard | `c1c5e21` | D51 |
| the event-tags fix | `056cba9` | D52 |

**The host runs `main@ea198c8`** since 2026-09-23 17:12 UTC (`DEPLOY.md`
§8.00). It lacks only the event-tags telemetry fix (D52) and the
documentation and test commits after it. Neither changes an answer.

**Three kinds of host figure:**

- **`DEPLOY.md` §8.00:** the D50 redeploy, on `ea198c8`.
- **`DEPLOY.md` §8.0:** the earlier deploy, on `21f1053`, **before D50**.
  D50 changes only how a document answer is scoped, so these still describe
  extraction, retrieval and memory. They do not describe the answer step.
- **Before any Week 2 deploy:** labelled "rehearsal host, before the Week 2
  deploy".

Short names used below:

| Short name | Full path |
|---|---|
| `LOAD_TEST.md`, `OBSERVABILITY.md`, `ALERTS.md` | `docs/w2/…` on `main` |
| `DEPLOY.md` | `docs/w2/DEPLOY.md` |
| gate README | `agent/evals/w2/README.md` |

---

## Summary

- **Located Week 2 dev spend: about $10.66** (itemised in §1; row 9 uses
  rounded per-question costs). The true
  total is higher by an unrecorded amount: no Anthropic Console reading was
  taken for Week 2, and several sets of live calls left no cost in the
  repository. Week 1's total was $0.36 (`docs/COST_ANALYSIS.md` §2.2).
- **On the deployed host**, one question at a time:
  - a lab page costs **$0.11–0.13** and takes 27.8–39.8 s to extract (on
    `21f1053`, `DEPLOY.md` §8.0);
  - an intake page costs **about $0.05** and takes 8.3–15.0 s (same run);
  - **the whole-report question** ("What were the results on the uploaded
    lab report?") answers in **6.9–8.4 s** on 22 rows and 12.0–13.1 s on 44
    rows, for about **$0.084–0.089**, with 0 unverified (on `ea198c8`, after
    D50, `DEPLOY.md` §8.00);
  - the document + guideline question cost about $0.178 and took 15.3–21.2 s
    **before D50** (on `21f1053`). It has not been re-measured since.
- **One measured Week 2 encounter costs $0.82**, in each of two local runs
  **before D50** (§2.1). The whole-report question was $0.46 of it. With
  D50's scoped answer in its place, the same encounter is an estimated
  **$0.44** (§4.1d).
- **Projections, per month** (§3):

  | Encounters per clinic day | At $0.82 (measured, before D50) | At $0.44 (estimated, after D50) |
  |---|---|---|
  | 100 | $1,722 | $926 |
  | 1K | $17,220 | $9,261 |
  | 10K | $172,200 | $92,610 |
  | 100K | $1,722,000 | $926,100 |

  After D50, 100 a day fits under Anthropic's Build-tier cap of $1,000 a
  month, and 10K a day sits well under the Scale tier's $200K (published).
- **Answer latency tracks what the model writes** (§4.1a).
  - Over six cases from 461 to 24,762 output tokens, the answer step is
    about **4.1 s + 7.45 s per 1,000 output tokens** (about 134 tokens/s),
    R² = 0.9998. The fit is estimated from measured points in
    `LOAD_TEST.md` §7.
  - Before D50, the model wrote every citation, so latency was also about
    1 s per citation: 91 citations took 88.2–91.8 s.
  - D50 has **code** count the in-range rows and cite them itself. On the
    host the scoped whole-report answer shows 49 document citations from
    368–461 output tokens, in a 5.3–6.8 s answer step (`DEPLOY.md` §8.00). The fit
    predicts 6.8–7.5 s. Citations shown are no longer a proxy for latency;
    output tokens are.
  - Every run used one model, `claude-opus-5`, so this says nothing about
    other models.
- **Upload to extraction complete** runs at upload (D7): 20.8–42.2 s on the
  host by document (`DEPLOY.md` §8.0), and 34–35 s locally (`docs/w2/evidence/php-wiring-e2e-run.md`).
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
| 1 | Six recorded live vision transcriptions (`claude-opus-5`, one call per factory sample) | **$0.78119** | Measured (sum of per-file `cost_usd`) | `agent/evals/w2/staging/transcriptions/*.transcription.json`; "about $0.78 of real inference", gate README |
| 2 | Load-test vision sample, 3 sequential live extractions | **$0.34766** | Measured (0.125915 + 0.09553 + 0.126215) | `LOAD_TEST.md` §4b; `docs/w2/evidence/load/extraction-live-vision.json` |
| | ↳ "one live extraction ~$0.126" | $0.125915 (`lab-clean-001`, 6,663 in / 3,704 out) | Measured | same file, first run |
| 3 | **Unplanned** real-LLM conversation load run, 100 turns at 10 users (the "~$3.00 overrun") | **$2.99** | Measured per turn: $0.0299 at 4,250 tokens | `LOAD_TEST.md` §2 and "What it cost". The session "assumed the stub covered the conversation route, and the module docstring says it does not" |
| 4 | Week 1-route API collection runs: 3 runs, 5 calls | **$0.38** ($0.150 + $0.087 + $0.145) | Measured, from logs | `LOAD_TEST.md`, "What it cost" |
| 5 | Two real-route Week 2 encounters through the API collection (`c8661931-…`, `07c0fd35-…`) | **$1.64** (2 × $0.82; the `07c0fd35` trace reads $0.821785) | Measured: Langfuse trace cost | `OBSERVABILITY.md` §2; `LOAD_TEST.md` §6. **Not in `LOAD_TEST.md`'s "What it cost" table**, which predates them |
| 6 | Eval-gate judge, one cold run: 24 calls, 15,825 in / 2,593 out on `claude-haiku-4-5` at $1/$5 per MTok | **$0.0288** | Measured (15,825 × $1 + 2,593 × $5 = $0.02879) | gate README, "What a run costs" |
| 7 | Deploy rehearsal on Vultr: brief + 4 turns × 3 patients | **$1.204075** ($0.180445 + $0.37474 + $0.64889) | Tokens measured from `llm_usage_event`; cost = tokens × $5/$25 per MTok | `DEPLOY.md` §8, "Cost per encounter" |
| 8 | Post-deploy run on the host: 5 documents extracted (live vision) and the document + guideline question 3 times | **$1.21341** (vision $0.126 + $0.227 + $0.126 + $0.104 + $0.096 = $0.679; questions 31,109 in × $5 plus 873, 873 and 965 out × $25 per MTok = $0.53441) | Vision: the agent's own `cost_usd`; questions: tokens measured, cost = tokens × list price | `DEPLOY.md` §8.0 |
| 9 | D50 redeploy run on the host: 2 documents extracted, the whole-report question 3 times on each | **≈ $0.873** (vision $0.126 + $0.228; questions ≈ 3 × $0.084 + 3 × $0.089) | Vision: `cost_usd`; questions: §8.00's rounded per-question cost | `DEPLOY.md` §8.00 |
| 10 | Week 1 I-suite re-run after the event-tags fix: 17 model calls, 157,403 in / 16,730 out, no cache | **$1.205265** (157,403 × $5 + 16,730 × $25 per MTok) | Tokens measured; cost = tokens × list price | D52, "Result" (`docs/w2/DECISIONS.md`) |
| | **Total located** | **≈ $10.663 ≈ $10.66** (rows 1–8 and 10 sum to $9.7904; row 9 is approximate) | Sum of the rows | this table |

**The "~$1.21 I-suite run" is located:** it is row 10. It was the **Week
1** I-suite (the conversation evals), re-run after the D52 event-tags fix
(D52, "Result"), not the Week 2 prompt-injection category. That category
makes no model call where it runs.

**Gaps: spend that happened and was not recorded.**

| Activity | Why it cost money | What is known |
|---|---|---|
| Judge meta-check fixture selection | five best-of-3 rounds per candidate fixture, plus generic-rubric comparisons (D46, D48) | no call count or cost committed |
| Planted-regression harness runs | each run re-grades the judge tier on the patched copies | the committed evidence run shows 0 calls (warm cache); earlier cold runs are not recorded |
| `agent/scripts/w2_e2e_ask.py` live runs | one drafting call per run, plus vision with `--live-vision` | no output committed on any branch |
| Week 1 live eval re-runs on Vultr, 2026-09-20 and 21 (43 cases) | real model calls | no dollar figure committed |
| The first Week 1 I-suite run on the event-tags fix (`53b524e`, over `21f1053`) | real model calls | D52 records only the second run's cost |
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

### 2.1 Measured: $0.82, before D50

The runs below predate D50 (`ea198c8`): the whole-report question in them is
the unscoped one.

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
| One document + guideline question | about $0.18 | measured tokens × list price, on `21f1053`, **before D50** |
| One whole-report question | about $0.084–0.089 | measured on `ea198c8`, after D50 (`DEPLOY.md` §8.00) |
| **Encounter** | **≈ $0.54–1.06** | estimated sum |

The measured local encounter, $0.82 (§2.1), lies inside that range. Its
composition differs: one lab, no intake form, a whole-report question and a
guideline question. §3 keeps the measured $0.82.

---

## 3. Projected production cost, and what each level forces

The formula is `per month = encounters per clinic day × 21 clinic days ×
unit cost`, using Week 1's 21 clinic days. Two unit costs are shown:

- the measured $0.82, which is before D50;
- the estimated $0.44 after D50, which swaps the measured whole-report call
  for the scoped one (§4.1d).

No whole encounter has been measured on the D50 code yet. At Week 1's 20 encounters per clinician per
day, 100 encounters a day is about 5 clinicians and 100K is about 5,000.

| Encounters per clinic day | Per day at $0.82 | Per month at $0.82 (measured, before D50) | **Per month at $0.44 (estimated, after D50)** |
|---|---|---|---|
| 100 | $82 | $1,722 | **$926** |
| 1K | $820 | $17,220 | **$9,261** |
| 10K | $8,200 | $172,200 | **$92,610** |
| 100K | $82,000 | $1,722,000 | **$926,100** |

Naive per-token × N, Opus 5 list price, no caching.

### 3.1 Load at the peak minute

Estimated as daily volume ÷ 480 minutes × 2, Week 1's peak factor
(`docs/COST_ANALYSIS.md` §4.2).

- **Token columns** use the measured pre-D50 encounter: 67,967 in and
  19,278 out (§2.1).
- **After D50,** swapping the whole-report call (34,948 in / 11,523 out) for
  the scoped one (14,585 / 461) gives about 47,600 in and 8,200 out per
  encounter (estimated). Output TPM falls by about 57%, so at 100K a day it
  is about 3.4M, still over the Scale tier's 2M.

| Per day | Encounters/min | Input TPM | Output TPM | Concurrent vision calls | Concurrent document questions | OCR cores (if every page scanned) | Retrieval turns/s (if every turn retrieves) |
|---|---|---|---|---|---|---|---|
| 100 | 0.42 | 28K | 8.0K | 0.18 | 0.06 | 0.07 | 0.03 |
| 1K | 4.2 | 283K | 80K | 1.8 | 0.58 | 0.66 | 0.28 |
| 10K | 41.7 | 2.83M | 803K | 18 | 5.8 | 6.6 | 2.8 |
| 100K | 417 | **28.3M** | **8.03M** | 180 | 58 | 66 | 28 |

How each column is derived:

- **Concurrent vision calls** = encounters/min × 25.9 s (the mean of the three
  live extractions, `extraction-live-vision.json`) ÷ 60.
- **Concurrent document questions** = encounters/min × 8.4 s ÷ 60. 8.4 s is
  the slowest end-to-end whole-report answer on the host after D50
  (`DEPLOY.md` §8.00). This assumes one whole-report question per encounter. Before
  D50 the step was 89.0 s p50, which is 10.6× the column.
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

**Done before any scale: the whole-report question** (D50, deployed on
`ea198c8`, `DEPLOY.md` §8.00).

- Before D50 it broke the 90 s budget for one clinician with no load, at
  88–92 s, and was about 56% of the measured encounter's cost ($0.4628 of
  $0.82).
- Now it answers in 6.9–8.4 s for about $0.084.
- **What is still open:** the "Show me all the results" case on a 44-row
  report takes 43.4 s p50, at the 45 s alert (`LOAD_TEST.md` §7).

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
- Under one document question is in flight at the peak minute after D50
  (0.58). Before D50 it was about six, each holding a connection for about
  1.5 minutes.
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
| `/w2/ask` about a whole uploaded lab report, answer step, **before D50** | local | 3 | 89.0 s | — | **88.2–91.8 s**; 91 citations, 11,511 out | `LOAD_TEST.md` §7 |
| `/w2/ask` about a whole uploaded lab report, whole HTTP request, **before D50** | local, real routes, real LLM | 2 | — | — | 91.9 s, 130.5 s (the second unexplained, §4.1a) | `LOAD_TEST.md` §6 |
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
| **Whole-report question, end to end, after D50** | **host, `ea198c8`** | 3 per report | — | — | 22 rows: **6.9–8.4 s** (answer step 5.3–6.8 s), 49 document citations; 44 rows: **12.0–13.1 s** (10.5–11.3 s), 97; 0 unverified | `DEPLOY.md` §8.00 |
| Document + guideline question, end to end, before D50 | host, `21f1053` | 3 | — | — | 15.3, 20.9, 21.2 s; drafting call 12.7–18.5 s; retrieval incl. rerank 1.55–1.68 s | `DEPLOY.md` §8.0 |
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

### 4.1a Answer latency tracks what the model writes

The `/w2/ask` answer step is one drafting call on `claude-opus-5`, 99% of
the step, and it is output-bound. D50's own measurement says so ("its time
is output… input size barely matters", `docs/w2/DECISIONS.md` D50).

| Case | Output tokens p50 | Answer step p50 (worst) | Citations counted by the bench | Source |
|---|---|---|---|---|
| 22-row report, "What were the results…", **scoped** | 461 | 6.9 s (9.1 s) | 3 | `LOAD_TEST.md` §7 |
| critical-potassium report, **scoped**, 1 run | 1,005 | 12.5 s | 8 | same |
| 44-row report, same question, **scoped** | 1,151 | 11.7 s (13.0 s) | 8 | same |
| 44-row report, "Show me all the results", **scoped**, cap binds | 5,092 | 43.4 s (45.5 s) | 36 | same |
| 22-row report, "What were the results…", **before** scoping | 11,511 | 89.0 s (91.8 s; min 88.2 s) | 91 | same; raw: `docs/w2/evidence/load/answer-before-22row.json` |
| 44-row report, same question, **before** scoping | 24,762 | 188.8 s (195.9 s) | 180 | same |

**The fit (estimated from the rows above).**

- Answer step ≈ **4.1 s + 7.45 s per 1,000 output tokens**, which is about
  134 tokens/s. R² = 0.9998, least squares over the six p50s.
- Every case is within 1.4 s of the line.
- Fitted on citations instead, the same points give 3.7 s + 1.0 s per
  citation (R² = 0.997). That held while the model drafted every citation.

**After D50, citations and latency come apart.**

- D50 has code count the in-range rows in one statement of its own, citing
  each row's located value and range (D50 item 3). Those citations cost the
  model nothing.
- On the host, the scoped whole-report answer on 22 rows shows **49
  document citations** from **368–461 output tokens**, in a **5.3–6.8 s**
  answer step (`DEPLOY.md` §8.00).
- The token fit predicts 6.8–7.5 s for that output. The citation fit
  predicts 53 s, eight to ten times too slow.
- The bench's 3 citations for the same question and report (`LOAD_TEST.md`
  §7) against the host's 49 are consistent with the bench counting only
  model-drafted citations. That is my reading: neither source states it.
  22 rows × (value + range) = 44 of the 49.

**The chart runs before D50** fit the same line (`docs/w2/evidence/php-wiring-e2e-run.md`). One analyte plus
one guideline topic made 8–12 citations and answered in 17.2–19.5 s, client
wall clock, local stack. That includes the module's token mint and the chart
reads.

**What this does and does not show.**

- **Shown.** On one model, answer latency is set by how much the model
  writes, over a 54× range (461 to 24,762 tokens). The lever is what the
  answer asks the model to write, not the model's speed or the retriever.
- **Retrieval is small.** Retrieval including rerank took 1.55–1.68 s on
  the host (`DEPLOY.md` §8.0, on `21f1053`).
- **Not shown:**
  - **other models**: every run used `claude-opus-5`;
  - **input size, separated**: the document + guideline question sent
    31,109 tokens and drafted 873–965, taking 12.7–18.5 s on `21f1053`,
    above what its output alone predicts (10.6–11.3 s). Its input or host
    contention may account for it; neither is established;
  - **concurrency**: every run was one question at a time.

**How §6 and §7 relate, before D50.**

- `LOAD_TEST.md` §6 timed the same whole-report question as a whole HTTP
  request: 91.9 s and 130.5 s.
- The 91.9 s run agrees with §7's answer step, 88.2–91.8 s.
- The 130.5 s run is unexplained.

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

The host was deployed from `main@21f1053` on 2026-09-23 and measured through
the real chart card, one document at a time (`DEPLOY.md` §8.0). It was then
redeployed from `main@ea198c8`, adding D50 and the gate, and the whole-report
question was measured (`DEPLOY.md` §8.00).

| Measurement | Before the Week 2 deploy (rehearsal host) | After the deploy |
|---|---|---|
| Vision extraction, cost per page | not measured on the host | **lab $0.11–0.13; intake about $0.05.** Output tokens are most of it: 3,704–6,811 per lab |
| Vision extraction, latency per page | not measured on the host. OCR alone: p50 5.7 s per page; 5.6 s with two workers under `OMP_THREAD_LIMIT=1`, against ~162 s without it (`DEPLOY.md` §8) | **lab 27.8–39.8 s; intake 8.3–15.0 s.** Vision is 15.7–54.5 s per document, OCR adds 4.9–6.1 s per scanned page |
| Peak memory under OCR + reranking | 3.7 GiB at demo load (2 rerank threads + 1 OCR worker); OOM-killed at a 5 GiB test cap under a heavier mix. Measured on the host, in a throwaway container from the merged image's code, before the deploy (`DEPLOY.md` §8) | **1.32 GiB** over the whole run, one document at a time, 0 OOM kills. The 3.7 GiB figure stays the bound under concurrent reranking |
| One answer citing a document and a guideline | the host did not run `/w2/ask` | on `21f1053`, **before D50**: 3 of 3, 7 document + 2 guideline citations, 0 unverified, 15.3–21.2 s end to end, about $0.178 each. Not re-measured on `ea198c8` |
| The whole-report question | the host did not run `/w2/ask` | on `ea198c8`, after D50: 22 rows 6.9–8.4 s, 44 rows 12.0–13.1 s, 0 unverified, about $0.084–0.089 (`DEPLOY.md` §8.00) |

### 4.1d The document-question fix: D50, merged and deployed

D50 (`feat/w2-answer-scope`, merged at `ea198c8`) scopes a document answer
by rule:

- critical values, abnormal rows and the rows the question names are
  itemised;
- the rest is counted in one cited statement written by code;
- the output is bounded at 10,000 tokens.

It is deployed on the host (`DEPLOY.md` §8.00).

The question is "What were the results on the uploaded lab report?", 3 runs
per report, from `LOAD_TEST.md` §7:

| Report | Answer step p50 (worst): before → after | Tokens out p50: before → after | Citations | Cost per answer |
|---|---|---|---|---|
| 22 rows | 89.0 s (91.8 s) → **6.9 s (9.1 s)** | 11,511 → 461 | 91 → 3 | $0.45 → **$0.084** |
| 44 rows | 188.8 s (195.9 s) → **11.7 s (13.0 s)** | 24,762 → 1,151 | 180 → 8 | $0.87 → **$0.089** |
| 44 rows, "Show me all the results" (cap binds) | — → 43.4 s (45.5 s) | — → 5,092 | — → 36 | — → $0.22 |

That is 13× faster on 22 rows and 16× faster on 44. The worst case, where
the cap of 8 binds on the 44-row report, lands at the 45 s alert.

**On the host, through the chart card** (`DEPLOY.md` §8.00):

| Report | Extraction | Answer | Answer step | End to end | Per question |
|---|---|---|---|---|---|
| 22 rows | 32.2 s, $0.126 | `partially_answered`, 49 document citations, 0 unverified | 5.3–6.8 s | **6.9–8.4 s** | ~$0.084 |
| 44 rows | 60.8 s, $0.228 | `partially_answered`, 97 document citations, 0 unverified | 10.5–11.3 s | **12.0–13.1 s** | ~$0.089 |

`partially_answered` is by design: a whole-report question names no test,
so in-range rows are counted rather than itemised (D50 item 7).

**Effect on the encounter (estimated).** Swapping the measured whole-report
call ($0.4628, §2.1) for the scoped one ($0.084) takes the $0.82 encounter
to about **$0.44**, since 0.82 − 0.4628 + 0.084 = 0.441. §3 shows both.
No whole encounter has been measured on the D50 code.

### 4.2 The bottleneck on each path

| Path | Bottleneck | Evidence |
|---|---|---|
| **Any `/w2/ask`, wall clock** | **What the model writes**: about 4.1 s + 7.45 s per 1,000 output tokens (§4.1a). Before D50 a whole-report answer wrote ~11.5k tokens in 88–92 s. After D50 it writes 368–461, and takes 6.9–8.4 s end to end on the host | `LOAD_TEST.md` §7; `DEPLOY.md` §8.00 |
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
| Brief + 4-turn session cost on three live-host charts | Any Week 2 route on the host **under load**: both deploys were measured one question at a time |
| Whole-report question on the host after D50, one question at a time (`DEPLOY.md` §8.00) | A whole encounter on the D50 code; the document + guideline question after D50 |
| Rerank, OCR and retrieval latency on the live host (rehearsal host, before the Week 2 deploy) | The per-call token split inside the $0.82 encounter, other than the document question |
| Judge cost per cold CI run | Judge calls spent selecting fixtures and on cold harness runs |
| Ten rows of dev spend (§1) | A Week 2 Anthropic Console total |
| | A cheaper vision or answer model; prompt caching on the extraction prompt |

The two measurements that would move this document most, in order:

1. **The whole-report answer after a fix** that bounds its output. It is
   more than half of both the cost and the latency of an encounter.
2. **A Console reading for the Week 2 window**, which closes §1's gaps.
