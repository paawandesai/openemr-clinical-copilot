# AI Cost Analysis — Clinical Co-Pilot

This is the AI Cost Analysis deliverable that `docs/KEY_METRICS.md` Metric 6
names and leaves open. Metric 6 supplies the seed: the real cost of one
interaction. This document projects from that seed to 100, 1K, 10K and 100K
users. It also names the points where cost or load forces the architecture
in `docs/ARCHITECTURE.md` to change. Multiplying a unit cost by a user
count is the easy part. Most of this document is about where that
multiplication stops describing the system.

Every number carries one of four labels:

- **Measured.** Read from this project's own logs or code.
- **Published.** From vendor documentation fetched on 2026-09-16:
  Anthropic's [pricing](https://platform.claude.com/docs/en/about-claude/pricing),
  [rate limits](https://platform.claude.com/docs/en/api/rate-limits) and
  [API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention)
  pages, and DigitalOcean's
  [Droplet](https://www.digitalocean.com/pricing/droplets) and
  [Kubernetes](https://www.digitalocean.com/pricing/kubernetes) pricing pages.
  Prices change, so re-check them before relying on the dollar figures.
- **Estimated.** Calculated from measured or published inputs. The
  calculation is shown.
- **Assumed.** An input this project has no data for. The results scale
  with it, and §3 lists how.

Two use cases are measured: the Pre-Visit Brief, and, since 2026-09-20, the
multi-turn follow-up conversation (Follow-Up Drilling), which is deployed on
the Vultr host and in the chart embed. §1.4 holds the conversation's measured
tokens, §4.10 folds them into the projections, and everything added on that
date says so. Change Detection is not built; its numbers remain estimates and
are always shown apart from the measured baseline (USERS.md §3;
`agent/README.md`).

---

## Summary

**Unit cost: $0.0160 per Pre-Visit Brief** (measured tokens × published
price). That is 1,575 input tokens at $5/MTok plus 326 output tokens at
$25/MTok on `claude-opus-5`, in one model call. Verification adds nothing
because it is code, not a second model call (ARCHITECTURE.md §5). An LLM
judge in its place would add roughly 72% to every brief (§4.7).

**Usage assumption: 20 briefs per clinician per clinic day and 21 clinic
days per month**, so 420 briefs per clinician per month. Across all three
use cases, the estimate is 29 model calls per clinician per day (§3).

| Users | Naive linear (brief only, today's code) | As architected: brief only | As architected: all three use cases | Constraint that binds first |
|---|---|---|---|---|
| 100 | $673/mo | $531/mo | $940/mo | **Anthropic monthly spend cap.** The Start tier caps spend at $500. Throughput is about 1% of limits. |
| 1K | $6,731/mo | $5,313/mo | $9,403/mo | **The shared droplet** (estimated). The agent is contending with OpenEMR for 4 shared vCPUs. |
| 10K | $67,305/mo | $53,130/mo (Opus 5) · $26,922/mo (Sonnet 5, if it passes eval) | $94,028/mo (Opus 5) · $66,953/mo (tiered) | **Cost.** Caching is worth $14K–$25K a month and tiering $26K–$43K. The agent needs multiple replicas and serves many tenants. |
| 100K | $673,050/mo | $531,300/mo (Opus 5) · $269,220/mo (Sonnet 5) | $940,275/mo (Opus 5) · $669,532/mo (tiered) | **Anthropic output-token rate limit.** Peak output is 2.72M tokens/min against the Scale tier's 2M, with briefs alone. This requires a Custom tier. |

**The conversation, measured 2026-09-20 (§1.4, §4.10).** One follow-up turn
costs **$0.0246 on the 5-record chart the brief figure above comes from,
$0.0493 on the population's median chart (39 records) and $0.1245 on its
largest (184 records)**: measured, one model call per turn, no caching. A
turn costs more than a brief because it sends every problem, medication and
allergy record, active or not, plus about 4,400 tokens of prompt, schema and
lab-code reference: 3,970 to 22,690 input tokens against the brief's 1,243
to 4,378. A session of a brief and four follow-ups costs $0.114, $0.226 and
$0.576 on those three charts. Weighted over the 52 patients on the host, a
turn is an estimated $0.057 and a brief $0.031, about twice the 5-record
seed the table above uses. The table's "all three use cases" column was
built on an estimated $0.0275 per follow-up turn. On the same 5-record
basis the measured figure is $0.0246, so that column stands (it moves by
−2%). §4.10 shows what changes it: how often physicians ask, and chart size.

| Users | Brief only, 5-record seed (as above) | Brief + 4 follow-ups a day, 5-record basis (**measured** unit costs) | Brief + 4 a day, population-weighted (**estimated** from the measured fit) | Brief + 15 a day, population-weighted |
|---|---|---|---|---|
| 100 | $673/mo | $880/mo | $1,790/mo | $3,109/mo |
| 1K | $6,731/mo | $8,797/mo | $17,900/mo | $31,090/mo |
| 10K | $67,305/mo | $87,969/mo | $179,004/mo | $310,905/mo |
| 100K | $673,050/mo | $879,690/mo | $1,790,040/mo | $3,109,050/mo |

Naive linear, Opus 5, no caching, brief and conversation only (Change
Detection excluded, since it is not built).

"As architected" means each tier's cost after the changes that tier
requires. All projections use the measured 5-record brief. **The largest
single uncertainty is chart size.** If the average brief covers 15 records
instead of 5, every brief figure is about 2.5× higher (§1.3).

**Inflection points, in the order they trigger:**

| # | Change | Triggers at | Why then |
|---|---|---|---|
| 1 | Per-clinician rate limiting | Before launch, at any scale | One client looping on a valid token spends $277 a day, which uses up the Start tier's monthly cap in under two days. |
| 2 | Static-prefix prompt cache (system prompt and schema) | Day 1 | It is free and holds no PHI. Saves 21% per brief. |
| 3 | Raise the Anthropic tier, and do it before launch | About 75 users | Start caps spend at $500/mo. A new HIPAA org starts with no usage history. |
| 4 | Move the agent off the OpenEMR droplet | About 1K users (estimated) | Contention with the EHR itself, plus a single point of failure during clinic hours. |
| 5 | Evaluate a cheaper drafting model for the brief | Evaluate at 1K, decide by 10K | Saves $2.6K–$4.3K/mo at 1K and $26K–$43K/mo at 10K. |
| 6 | Patient-context cache for Follow-Up Drilling | When that use case ships | For the brief to write this cache, at least 28% of briefs must get a follow-up within 5 minutes. |
| 7 | Deadline-aware queue, per-tenant FHIR caps, degraded mode | 10K | About 833 briefs a minute at peak, from many EHR deployments. |
| 8 | Stateless replicas on Kubernetes, shared session store, centralized metrics | 10K | One uvicorn process and a stdout-based metrics script cannot span replicas. |
| 9 | Caching becomes required for throughput | 100K | Cache reads don't count toward input-token rate limits. Caching brings peak input for briefs from 13.1M to 6.9M tokens/min. |
| 10 | Custom API tier, multiple regions, a second provider path | 100K | Output rate limit, request rate limit and the $200K spend cap are all exceeded. |

**Batching is mostly unavailable to this product.** The Message Batches API
is not HIPAA-eligible, and its turnaround of up to 24 hours cannot serve a
90-second window (§4.6). **Prompt caching is listed as HIPAA-eligible in
Anthropic's published table.** This project's compliance discussion
flagged it as possibly outside BAA coverage. §5 sets out what that check
settles and what it leaves open.

---

## 1. Unit cost: one Pre-Visit Brief

### 1.1 The measured interaction

The agent logs every model call as an `llm_usage_event` with token counts
and no content (`agent/app/reasoning/draft_claims.py`). This document read
one real record directly, from the main checkout's local agent log. That
log is gitignored:

| Field | Value |
|---|---|
| Timestamp | 2026-09-16T19:52:24Z |
| Model requested / served | `claude-opus-5` / `claude-opus-5` (no refusal fallback) |
| Configuration | Adaptive thinking, `effort: "medium"`, structured outputs, streaming |
| Input tokens | **1,575** |
| Output tokens | **326** (thinking tokens are billed as output and included here) |
| Cache write / cache read | 0 / 0 (no `cache_control` is set in the code) |
| Model-call latency | 4,751 ms |
| Claims drafted | 5 |

This matches the ~1,901 tokens per brief the project verified against live
runs. It is the only model call in the Pre-Visit Brief graph. Retrieval,
verification and rendering are all plain code
(`agent/app/graph/pre_visit_brief.py`).

### 1.2 Pricing used (published, fetched 2026-09-16)

| Model | Input | Output | 5-min cache write | 1-hour cache write | Cache read | Batch input / output |
|---|---|---|---|---|---|---|
| Claude Opus 5 | $5.00/MTok | $25.00/MTok | $6.25/MTok | $10.00/MTok | $0.50/MTok | $2.50 / $12.50 |
| Claude Sonnet 5 | $2.00/MTok | $10.00/MTok | $2.50/MTok | $4.00/MTok | $0.20/MTok | $1.00 / $5.00 |
| Claude Haiku 4.5 | $1.00/MTok | $5.00/MTok | $1.25/MTok | $2.00/MTok | $0.10/MTok | $0.50 / $2.50 |

These are Anthropic first-party list prices with global routing. Sonnet
5's $2/$10 was introductory pricing that Anthropic has made standard.
US-only inference (`inference_geo: "us"`) multiplies every token price by
1.1×. Volume discounts are negotiated case by case and are not modeled.

**Cost of the measured brief:**

```
input   1,575 × $5.00 / 1M  = $0.007875   (49% of cost)
output    326 × $25.00 / 1M = $0.008150   (51% of cost)
total                       = $0.016025  ≈ $0.0160 per brief
```

With US-only inference the brief costs $0.0176.

### 1.3 How far one measurement goes

The measured brief covered a patient with 5 records in the brief. The
local census has 1,669 problem rows, 305 prescriptions and 43 allergies
across 65 patients (USERS.md §3, F-21). That averages about 26 problem
rows and 4.7 prescriptions per patient. The brief only reports *active*
problems and medications (`draft_claims.py`), and nobody has measured what
share is active. So the average brief could be larger than the measured
one.

Tokens grow with record count on both sides. Each record goes into the
prompt, and the prompt tells the model to draft one claim per record. The
following split is a fit to a single measurement. It assumes about 750
fixed input tokens (system prompt and output schema) and splits the rest
evenly: about 165 input and 65 output tokens per record.

| Records in brief | Input | Output | Cost per brief | × measured | Output time at the measured rate (~69 tokens/s) |
|---|---|---|---|---|---|
| 5 (measured) | 1,575 | 326 | $0.0160 | 1.0× | 4.8 s (measured end to end) |
| 10 | 2,400 | 652 | $0.0283 | 1.8× | ~9.5 s |
| 15 | 3,225 | 978 | $0.0406 | 2.5× | ~14 s |
| 25 | 4,875 | 1,630 | $0.0651 | 4.1× | ~24 s |
| ~101 (`pid=20`: 92 problems, 9 prescriptions; F-22) | ~17,400 | ~6,600 | ~$0.25 | ~16× | **~96 s, over the 90-second budget** |

Two conclusions follow. First, every projection below scales with average
chart size, and running the brief over all 65 local patients once (§7)
would replace this table with a measured distribution. Second, the
current prompt writes one claim per record, so output cost grows linearly
with chart size. For outlier charts, that design runs over both the
budget in dollars and the 90-second window (KEY_METRICS.md Metric 4).
USERS.md's own case for an agent over a dashboard is prioritizing and
compressing long lists (§3, Use Case 1). That is also the cost control for
large charts.

### 1.4 The measured conversation turn and session (2026-09-20, Vultr)

Every figure in this section is **measured** unless it says otherwise:
token counts from the agent's own `llm_usage_event` records, priced at
§1.2's published rates (re-checked 2026-09-20, unchanged).

**What was run.** On the deployed agent (`https://64.177.42.210:8443`; the
running container's `app/` is byte-identical to `main` at `aded032`, checked
by file hash on 2026-09-20; `claude-opus-5`, `effort: "medium"`, no
`cache_control`), for seven patients chosen by chart size across the
host's 52 (the smallest non-empty chart, then the 12th, 25th, 50th, 75th,
90th and 100th percentiles by record count): one `POST /pre-visit-brief`,
then four turns of `POST /pre-visit-brief/conversation` in one session,
one of each kind the routing rules distinguish. 35 model calls, read from
the host's log by correlation id. Reproduce with
`agent/scripts/measure_session_cost.py`. "Records" is the chart's problem,
medication and allergy records, active or not, which is what every turn
sends.

| Turn | Question | What it adds to the prompt |
|---|---|---|
| 1 base set | "Which of her medications are active?" | Problems, medications, allergies (every turn has these) |
| 2 reference back | "When was that prescribed?" | Turn 1's verified claims, as context |
| 3 lab expansion | "What was her most recent hemoglobin?" | The narrowed lab results for that test, plus lab-code names |
| 4 visit expansion | "When was she last seen?" | Visit dates; the lab results are carried from turn 3 |

| Chart (records) | Brief in / out | Brief | Turn 1 in / out | Turn 2 | Turn 3 | Turn 4 | Mean turn in / out | **Mean turn** | Mean turn time | **Session: brief + 4 turns** (in / out) |
|---|---|---|---|---|---|---|---|---|---|---|
| 5 (smallest; §1.1's patient) | 1,574 / 325 | $0.0160 | 3,385 / 277 | 3,727 / 363 | 4,375 / 29 | 4,395 / 94 | 3,970 / 191 | **$0.0246** | 4.5 s | **$0.1145** (17,456 / 1,088) |
| 18 | 1,623 / 330 | $0.0164 | 4,736 / 304 | 5,105 / 334 | 6,016 / 254 | 7,187 / 94 | 5,761 / 246 | **$0.0350** | 4.8 s | **$0.1562** (24,667 / 1,316) |
| 24 | 1,243 / 70 | $0.0080 | 5,444 / 1,478 | 7,157 / 228 | 7,569 / 274 | 8,761 / 95 | 7,233 / 519 | **$0.0491** | 6.7 s | **$0.2045** (30,174 / 2,145) |
| 39 (median) | 2,183 / 724 | $0.0290 | 7,077 / 490 | 7,662 / 405 | 8,615 / 270 | 9,777 / 91 | 8,283 / 314 | **$0.0493** | 5.0 s | **$0.2261** (35,314 / 1,980) |
| 55 | 2,882 / 1,267 | $0.0461 | 8,817 / 1,142 | 10,204 / 1,439 | 12,236 / 286 | 13,422 / 95 | 11,170 / 740 | **$0.0744** | 9.4 s | **$0.3435** (47,561 / 4,229) |
| 106 | 3,802 / 1,857 | $0.0654 | 13,769 / 1,160 | 15,174 / 1,418 | 17,226 / 274 | 18,411 / 95 | 16,145 / 737 | **$0.0991** | 10.5 s | **$0.4620** (68,382 / 4,804) |
| 184 (largest) | 4,378 / 2,253 | $0.0782 | 21,080 / 1,160 | 22,485 / 244 | 23,003 / 282 | 24,194 / 89 | 22,690 / 444 | **$0.1245** | 9.0 s | **$0.5764** (95,140 / 4,028) |

Over the 28 turns: input 3,385 to 24,194 tokens (mean 10,750, median
8,688); output 29 to 1,478 (mean 456, median 276); cost $0.0226 to $0.1344
a turn (mean $0.0651, median $0.0505); end-to-end 3.3 to 15.3 s (mean 7.1 s,
95th percentile 13.7 s), of which the model call averaged 5.8 s. Cache read
and write were 0 on every call. Three of the 28 turns made a model call and
answered "not in the retrieved record"; they cost the same as an answer. A
refusal (out of scope, or a judgment or trend) is decided by rule and makes
**no model call**: it costs nothing.

The eval run of the same day (`agent/evals/RESULTS_CONVERSATION.md`, "Model
usage in this run") recorded the conversation cases' calls the same way,
in-process: the small chart's turns at about 3,400 input tokens and the
largest chart's lab turn at about 21,600, inside the ranges above.

**What the measurement shows.**

- **A turn costs more than a brief, and it is input that does it.** Input is
  83% of the mean turn's cost ($0.0538 of $0.0651), against 49% of the
  brief's (§1.2). The brief is sent active records and drafts one claim per
  record; a turn is sent every problem, medication and allergy record,
  active or not, and usually drafts a few claims.
- **Input grows linearly with chart size:** mean turn input ≈ 4,375 +
  103.5 × records (least squares over the seven charts, R² 0.99). The
  intercept is the fixed part: system prompt, output schema, lab-code names
  and the question. Mean turn cost ≈ $0.0311 + $0.00055 × records (R² 0.94);
  brief cost ≈ $0.0124 + $0.00040 × records (R² 0.88; looser, because the
  brief follows *active* records, not all of them: the 24-record chart has
  one active record and an $0.008 brief).
- **Later turns cost more than earlier ones:** each turn re-sends the chart
  and adds what the conversation has accumulated (verified context, carried
  lab results). Turn 4's input is 15% to 60% above turn 1's.
- **Lab expansion is cheap, by design.** The 106-record patient has 976 lab
  results; the turn was sent 2 of them. The routing rule narrows labs to the
  test asked about before the model sees them, so lab-heavy charts do not
  cost more per turn than their problem list already makes them.
- **§1.3's single-point fit over-estimated large briefs.** It predicted
  about $0.25 and 96 s for a roughly 100-record chart. Measured: $0.065 and
  16 s at 106 records, $0.078 and 19 s at 184. The brief is sent active
  records only, and most of a long Synthea problem list is resolved.

**Population-weighted unit costs (estimated).** Applying the two cost fits
to the record count of each of the host's 52 patients (mean 47 records,
median 37.5): a follow-up turn averages **$0.057** and a brief **$0.031**.
A session of a brief and N follow-ups averages $0.145 (N = 2), $0.260
(N = 4) and $0.374 (N = 6). These rest on seven measured charts and one
synthetic population (F-41); a real panel's chart sizes will differ, and
the fits say by how much.

---

## 2. Actual development spend

### 2.1 What the evidence shows

| Source | What it contains | Real API spend |
|---|---|---|
| Main checkout's local agent log | 1 `llm_usage_event`: Opus 5, 1,575 in / 326 out (§1.1) | **$0.0160** (measured) |
| `agent/observability` worktree log | 1 `llm_usage_event` (Sonnet 5; 2,400 in, 1,800 cache-read, 350 out). It is synthetic, emitted by `scripts/send_langfuse_test_event.py` with no API call. | $0 |
| `/ready` LLM probe | `models.retrieve` consumes no tokens (`agent/app/health.py`) | $0 |
| `scripts/smoke_test_claims.py` | 2 drafting calls per run (two patients) | Not logged anywhere this document could read |
| `scripts/smoke_test_pre_visit_brief.py` | 4 drafting calls per run (runs A–D) | Not logged anywhere this document could read |
| Calls to the deployed endpoint (the Postman API collection) | Logged on the droplet, which was not read for this document | Unknown |
| Eval suite, load test | `feat/eval-suite` and `feat/load-test` have no committed results at `82ea759` | $0 so far |

The synthetic Langfuse event also serves as a price check.
2,400 × $2 + 1,800 × $0.20 + 350 × $10 per MTok comes to $0.00866, which
equals the `totalCost` Langfuse recorded (`agent/docs/observability.md`
Metric 6). Langfuse's Sonnet 5 price table therefore matches current
published pricing, which that doc asks to confirm before its numbers are
used.

### 2.2 Measured

**Total Claude API spend: $0.36 (measured).** Source: the Anthropic
Console Usage page, read 2026-09-16, for the last 30 days across all
workspaces and keys. All of it is token cost on `claude-opus-5`, the only
model the agent calls, and it is concentrated on 2026-09-16. Web search,
code execution and session runtime are all $0.00. Because the figure
covers every key, it is an upper bound on the agent's own share.

Cross-check: $0.36 ÷ $0.0160 per brief (§1.2) ≈ 22 briefs of the measured
size. That fits the smoke-test and manual-verification runs made during
development, as reported by the operator; no run log records the count.
If some of those charts were larger (§1.3), the actual number of calls
was lower.

Not included:

- **The AI coding assistant used to build the project.** It is not billed
  to the agent's API key, and nothing in the repository meters it.
- **Infrastructure.** Until late in the build the project ran on one
  DigitalOcean droplet whose plan could not be verified from inside the
  guest (F-44). DigitalOcean's list prices for 4-vCPU plans run from
  $48/mo (Basic, 4 vCPU / 8 GB, $0.071/h) to $126/mo (General Purpose,
  4 vCPU / 16 GB, $0.1875/h), prorated for the days it existed. The
  deployment now runs on a Vultr host. Vultr's prices were not fetched, so
  its cost is not included.
- **Observability backend.** Self-hosted Langfuse ran locally only. The
  planned second droplet is not deployed (ARCHITECTURE.md §1).

### 2.3 What a load test will cost

A load test that makes real model calls costs $0.016 per brief. Ten
minutes at each tier's modeled peak (§3) costs:

| Tier | Briefs in 10 min at peak | Cost | Can it run on the Scale tier? |
|---|---|---|---|
| 100 | 83 | $1.34 | Yes |
| 1K | 833 | $13 | Yes |
| 10K | 8,333 | $134 | Yes |
| 100K | 83,333 | $1,335 | **No.** Peak output (2.72M tokens/min) exceeds the tier's 2M limit. |

To test FHIR and agent capacity, stub the Anthropic call with a fixed
~4.75 s delay and a canned response. That produces the same concurrency
shape for $0. Save real calls for a small latency sample.

---

## 3. Usage assumptions

USERS.md fixes the unit of use: one brief per chart open, repeated across
"a full clinic day" (§2). It does not state a patient count. The count
below is this document's assumption.

| Input | Value | Basis |
|---|---|---|
| Encounters per clinician per clinic day | **20** | **Assumed.** A full primary-care clinic day. |
| Pre-Visit Briefs per encounter | **1** | **Assumed.** Triggered on chart open (USERS.md §3). Re-opens are not counted. |
| Change Detection per clinician per day | 5 (25% of encounters) | **Assumed.** Not built. |
| Follow-Up Drilling turns per clinician per day | 4 | **Assumed.** Built and deployed since 2026-09-19; no usage data yet. §4.10 also shows 15 and 40. |
| Clinic days per month | 21 | **Assumed.** |
| Share of users active on a clinic day | 100% | **Assumed**, as an upper bound. |
| Peak minute vs. clinic-day average | 2× over an 8-hour day | **Assumed.** Affects rate limits only, not cost. |
| Change Detection tokens | ~3,000 in / ~500 out → $0.0275 | **Estimated.** Two encounters plus conditions, medications and interval labs. |
| Follow-Up Drilling tokens per turn | 3,970 in / 191 out → $0.0246 on the 5-record chart; 8,283 / 314 → $0.0493 on the median chart; population-weighted $0.057 | **Measured** 2026-09-20 (§1.4); the population-weighted figure is **estimated** from the measured fit. Replaces the earlier estimate of ~3,500 in / ~400 out → $0.0275, which the tables in §4.1 to §4.9 still use: on the 5-record basis those tables share, the measured figure is 11% lower. |

**Who counts as a user.** A user is a clinician seat that uses the
co-pilot. At 100 and 1K users, one OpenEMR deployment is plausible: a
practice or a medical group. At 10K and 100K users the seats necessarily
span many EHR deployments, so the agent becomes a multi-tenant service
calling many FHIR endpoints. Scaling OpenEMR itself is out of scope. The
EHR exists with or without the co-pilot. It is in scope only where the
agent adds load to it.

**Sensitivity.** Each of these changes scales the results:

| If… | Effect |
|---|---|
| The average brief covers 15 records, not 5 (§1.3) | ×2.5 on brief cost |
| 50% of chart re-opens re-run the brief | ×1.5 on brief volume |
| US-only inference is required | ×1.1 on all token cost |
| 70% of seats are active on a given day | ×0.7 on all cost |
| The peak factor is 3, not 2 | ×1.5 on every rate-limit figure. Cost is unchanged. |

---

## 4. Projections and inflection points

### 4.1 Naive linear projection

Unit cost × volume, with no caching and no architecture change.

```
brief only:   users × 20 briefs/day × 21 days × $0.016025
all three:    users × 21 × (20 × $0.016025 + 5 × $0.0275 + 4 × $0.0275)
```

| Users | Briefs/month | Brief only | All three use cases |
|---|---|---|---|
| 100 | 42,000 | $673 | $1,193 |
| 1K | 420,000 | $6,731 | $11,928 |
| 10K | 4.2M | $67,305 | $119,280 |
| 100K | 42M | $673,050 | $1,192,800 |

That comes to $6.73 per clinician per month for briefs, or $11.93 for all
three use cases.

### 4.2 Peak load against Anthropic's limits

Peak rate = daily volume ÷ 480 minutes × 2.

| Users | Brief RPM | All-use-case RPM | Brief input TPM (uncached / static-cached) | Brief output TPM | All-use-case input TPM (uncached / cached) | All-use-case output TPM | Concurrent model calls (~6 s each) |
|---|---|---|---|---|---|---|---|
| 100 | 8 | 12 | 13K / 7K | 2.7K | 25K / 13K | 4.4K | ~1 |
| 1K | 83 | 121 | 131K / 69K | 27K | 252K / 132K | 44K | ~12 |
| 10K | 833 | 1,208 | 1.31M / 0.69M | 272K | 2.52M / 1.32M | 443K | ~121 |
| 100K | 8,333 | 12,083 | **13.1M** / 6.9M | **2.72M** | **25.2M / 13.2M** | **4.43M** | ~1,208 |

In this table, RPM is requests per minute and TPM is tokens per minute.
Here are Anthropic's published Opus 5 limits for comparison. Opus 5 has
its own rate-limit bucket, separate from other Opus models.

| Tier | RPM | Input TPM | Output TPM | Monthly spend cap |
|---|---|---|---|---|
| Start | 1,000 | 2M | 400K | $500 |
| Build | 5,000 | 5M | 1M | $1,000 |
| Scale | 10,000 | 10M | 2M | $200,000 |
| Custom | Negotiated | Negotiated | Negotiated | None |

**Throughput is not the constraint until 100K users. Spend caps bind
first.** At 10K users, all three use cases fit inside the Scale tier. At
100K users, briefs alone exceed the Scale tier's output limit.

### 4.3 Inflection: API tier and spend cap (about 75 users)

At $6.73 per clinician per month, the monthly spend caps are reached at:

| Cap | Brief only, uncached | Brief only, cached | All three, cached |
|---|---|---|---|
| Start, $500 | 75 users | 94 users | 53 users |
| Build, $1,000 | 149 users | 188 users | 106 users |
| Scale, $200,000 | ~29,700 users | ~37,600 users | ~21,300 users |

When an organization reaches its cap, Anthropic returns HTTP 429 with no
`retry-after` until the first of the next month (published). For this
product that means the brief stops for every clinician at once, partway
through the month. Two published facts pull this forward:

- **The production organization will be new.** HIPAA readiness is
  enforced per organization, and Anthropic says to use separate
  organizations for HIPAA and non-HIPAA work. Tier placement follows usage
  history, and new organizations "may start in the Evaluation tier, with
  limits below the standard limits". Request the tier and limits needed
  before launch, not when the cap is hit.
- **Workspace spend limits are the breaker below the org cap.** At 10K
  users and above, set them per tenant so one tenant cannot exhaust the
  organization's limit for everyone.

### 4.4 Inflection: per-clinician rate limiting (before launch)

The deployed endpoint has no rate limiting (ARCHITECTURE.md §1). Any
holder of a valid clinician token can loop on it. At one brief every 5
seconds, that is 17,280 briefs and **$277 per day**: the Start tier's
monthly cap in under two days, from one client. This does not depend on
scale. Add a per-user limit sized from the use case, at Caddy or in the
app. For example, 60 briefs per hour is 12× a clinician's modeled peak
hour (20 briefs over 8 hours, doubled at peak) and caps a runaway client
at about $23 a day.

### 4.5 Inflection: prompt caching

Three caches have three different triggers.

**(a) Static prefix: day 1.** The system prompt and output schema are the
same on every brief and contain no PHI. They are estimated at about 750
tokens: the system prompt is 2,150 characters, plus the schema. This is
not measured; `count_tokens` is free and would confirm it. Opus 5 caches
prefixes of 512 tokens or more (published). A prefix under 512 tokens
gets no error, and nothing is cached. A cache entry stays warm while
requests that share the prefix start less than 5 minutes apart in the
same workspace. At 100 users that is every clinic-hour minute. The saving
is 750 × ($5.00 − $0.50) per million tokens = **$0.0034 per brief, or
21%**. That is $142/mo at 100 users, $14,175/mo at 10K and $141,750/mo at
100K. Caches are isolated per workspace, so giving each tenant its own
workspace (§4.3) splits this cache. The cost of that is small at volume
but real. The code sets no `cache_control` today, which is consistent
with the logged 0/0 cache usage.

**(b) Patient-context block: when multi-turn Follow-Up Drilling ships.**
Follow-Up Drilling is the only use case designed to carry conversation
state (ARCHITECTURE.md §4). The single-turn follow-up built today has none,
so this lever does not apply to it yet. Once multi-turn exists, every turn
re-sends the patient's retrieved records and whatever came before. A cache
breakpoint after that block lets turns 2 and later read it at 0.1× the
input price. The estimated per-turn cost falls from
$0.0275 to $0.0175, which is 36%. (The conversation is now built and
measured: §4.10 redoes this estimate on measured tokens and arrives at
−37% for a four-turn session on the median chart.) Three design rules
follow:

- **Use the 5-minute TTL, never the 1-hour one.** The use case happens in
  the ~90 seconds between encounters (USERS.md §1–2). A 1-hour write costs
  2× the input price, to serve reads that come within minutes.
- **The brief should write the patient block only if follow-ups are
  common.** A write costs +25% on those ~825 tokens and a read saves 90%.
  Writing pays off only if at least 0.25 ÷ 0.90 = **28% of briefs get a
  follow-up within 5 minutes**. Below that rate, let the first follow-up
  turn write the cache instead. Metric 6, broken out by use case, provides
  the rate once the use case ships.
- **Caches belong to one model.** If the brief moves to a cheaper model
  (§4.7), a follow-up on Opus 5 cannot read the brief's cache.

**(c) Where caching stops being optional:**

- **10K users, on cost.** Caching is worth $14K/mo for briefs alone and
  $25K/mo across all three use cases, about 21% of the bill.
- **100K users, on throughput.** Cache reads do not count toward Opus 5's
  input-token rate limit (published). Peak brief input is 13.1M tokens/min
  uncached, over the Scale tier's 10M, and 6.9M cached, which fits. Across
  all three use cases, caching cuts 25.2M to 13.2M. That is still above
  Scale, but it halves the Custom limit to negotiate. **Caching does
  nothing for output tokens**, so the Custom tier is still required at
  100K (§4.2).

### 4.6 Batching: mostly not available

- **Message Batches API.** Batches get 50% off, but the brief cannot use
  them. Results can take up to 24 hours, against a 90-second window. The
  API is also **not HIPAA-eligible**: Anthropic's table marks it "No",
  with 29-day retention. Under HIPAA readiness, PHI cannot go through it.
- **Pre-generating briefs**, for example overnight for tomorrow's
  schedule through the standard Messages API. This would move output load
  out of the morning surge, which is the binding limit at 100K. It does
  not work here yet. This deployment has no schedule to generate against:
  the appointments table holds 11 identical rows across two unrelated
  patient populations (USERS.md §1, F-43). A pre-generated brief also goes
  stale when a medication or allergy changes the morning of the visit, so
  it needs invalidation on chart writes. And stored briefs are a new PHI
  datastore under F-41's rules. Consider it at 100K, only for tenants with
  a real schedule.
- **Re-open deduplication.** A chart re-opened within minutes runs the
  brief again. Serving a stored result keyed on (patient, hash of the
  retrieved data) avoids a second model call. That store is a PHI cache,
  also under F-41. The projections count no re-opens (§3).
- **Where batching does apply:** eval-suite and regression runs on
  synthetic data, in a separate non-HIPAA organization, at 50% off.

### 4.7 Inflection: model tiering (evaluate at 1K, decide by 10K)

**Verification is already tiered down to free.** Verification is code
(ARCHITECTURE.md §5). A second Opus 5 call judging the draft would read
the records and claims, estimated at about 1,550 tokens in and 150 out.
That would add **$0.0115 per brief, or 72%**: $4,830/mo at 1K users and
$483,000/mo at 100K. The design avoids that cost entirely.

**The remaining model call is drafting.** Options for the brief:

| Drafter | Per brief | Static-prefix cache? | Brief spend at 10K users | vs. Opus 5 cached |
|---|---|---|---|---|
| Opus 5, `medium` effort (current) | $0.0160, or $0.0127 cached | Yes (512-token minimum) | $53,130/mo | — |
| Sonnet 5 | $0.0064 | **No.** A ~750-token prefix is under Sonnet 5's 1,024-token minimum. | $26,922/mo | −$26,208/mo |
| Haiku 4.5 | ~$0.0025 | **No.** Haiku 4.5's minimum is 4,096 tokens. | $10,361/mo | −$42,769/mo |

The Sonnet 5 row assumes the same token counts as the measured brief.
Haiku 4.5 uses the older tokenizer. Anthropic says the newer one (Claude
4.7 and later) produces about 30% more tokens for the same text, so Haiku
4.5's counts are scaled by ÷1.3.
Both rows are estimates. Before switching models, try lower effort on
Opus 5. The 326 output tokens include adaptive thinking at `medium`, and
output is 51% of the brief's cost.

**The quality tradeoff.**

- **As currently prompted, the brief is an extraction task.** For each
  active record, the model copies `display` verbatim, cites `source_id`,
  and follows a schema (`draft_claims.py`). Smaller models may handle
  that well. That is a hypothesis for the eval to test, not a finding.
- **A weaker drafter's errors are visible, not silent.** A wrong value or
  an invented `source_id` is rendered as UNVERIFIED (§5), so Metric 1
  falls while Metric 2 stays at 100%. A skipped record is rendered as "N
  more … on file, not included in this brief"
  (`pre_visit_brief.py: render_brief`). Both can be counted: Metric 1 comes
  from `claim_verified_event`, and skipped records come from
  `claims_drafted` (`claim_count` against `section_counts.in_brief`).
- **Visible still has a cost.** Every UNVERIFIED line and every "not
  included" line spends the clinician's attention inside a 90-second
  window. The brief stays safe but gets less useful, so a cheaper drafter
  has to pass a bar set on Metric 1 and the skipped-record count.
- **One loss would be silent.** Once the brief prioritizes and compresses
  long lists, as USERS.md intends for charts like `pid=20`, choosing which
  facts to surface is judgment. Verification checks that each claim is
  sourced, not that the right claims were chosen. That risk is latent
  today, because the prompt asks for every record in order.
- **Keep Change Detection and Follow-Up Drilling on Opus 5.** Diffing two
  encounters and resolving "that" or "last time" are reasoning, not
  copying. Verification does not cover multi-record synthesis claims
  (ARCHITECTURE.md §5, limitations).
- **Tiering also splits rate limits.** Anthropic rate-limits each model
  separately (published). Moving briefs to Sonnet 5 puts that traffic in a
  separate bucket, which buys headroom at 100K regardless of price.

**The floor is no model call at all.** As prompted today, the drafting
step returns one verbatim claim per active record, in order. The render
step already has every retrieved record, so plain code could produce the
same list. The brief's $0.016 buys prioritization and compression, and
the prompt does not ask for those yet. The no-model rendering is also the
right degraded mode (§4.8).

**Why these triggers.** At 100 users, moving briefs to Sonnet 5 saves
$262/mo, less than the engineering and eval effort to prove it is safe.
At 1K users it saves $2.6K–$4.3K/mo, which pays for the eval. Running the
65 local patients through 3 drafters, 3 trials each, is about 585 calls:
at most about $24, even pricing every call at Opus 5's 15-record cost.
At 10K users the saving is $26K–$43K/mo, so the choice should be made
deliberately, per use case, on measured Metric 1 and skipped-record
numbers.

### 4.8 Inflection: queueing, admission control and degraded mode (1K to 10K)

**At 1K users:**

- **Global concurrency limit on model calls.** The agent should cap
  in-flight Anthropic calls to fit the organization's RPM and output-token
  limits, and honor `retry-after` on a 429.
- **Degraded mode.** When the model is unavailable, rate limited or
  spend-capped, render a deterministic brief: the retrieved record lists,
  labeled as not summarized. The render step already turns section counts
  into "not recorded" and "none active" without the model. The records
  come straight from FHIR, so there is nothing to verify. A 429 then costs
  the clinician a less compressed brief, not a missing one. This mode is a
  proposal and is not built.

**At 10K users:**

- **Priority queue with deadlines.** Serve chart-open briefs first, since
  they have a 90-second budget, then explicit Change Detection, then
  anything speculative. A brief that cannot start within its budget is
  dropped rather than run. Tokens spent on a brief the clinician never
  reads are wasted (KEY_METRICS.md Metric 4).
- **Per-tenant FHIR concurrency caps.** Each brief makes 4 FHIR reads:
  Patient plus 3 searches. At peak that is about 56 reads per second
  across tenants. One large tenant's morning surge must not saturate a
  small clinic's OpenEMR. OpenEMR's authenticated FHIR throughput has not
  been measured: F-24 has a local single-user floor of 0.375 s, and F-47
  could not run an authenticated read on the droplet.
- **Per-tenant workspace limits at Anthropic.** These stop one tenant
  from exhausting the organization's limits. The cost is that each
  workspace has its own static-prefix cache (§4.5a).
- **Self-host the queue and session store** on DigitalOcean's
  HIPAA-eligible products, never on a managed database (F-41).

**At 100K users:**

- **Contract a Custom tier.** Peak output for briefs alone is 2.72M
  tokens/min, against Scale's 2M.
- **Capacity has to be contracted.** Anthropic's API reference lists
  Opus 5 and Sonnet 5 as not supported by Priority Tier. That was not
  re-checked against live docs.
- **Consider a second provider path for availability**, such as Claude on
  Amazon Bedrock or Google Cloud. There, the cloud provider is the data
  processor under its own BAA, with its own pricing: regional endpoints
  carry a 10% premium. It needs its own BAA analysis before any PHI goes
  through it.

### 4.9 Inflection: where the single droplet breaks

This path was planned and priced on DigitalOcean, the provider F-41's BAA
analysis covers. The current Vultr host is under no BAA (findings.md F-61;
ARCHITECTURE.md §1), which is acceptable only while all data is synthetic.
At any of these scales, a real deployment starts by moving to a substrate
under an executed BAA.

ARCHITECTURE.md has no scale-out section. What it does fix are the
constraints any scale-out has to respect:

- ePHI stays on DigitalOcean's HIPAA-eligible products. The list includes
  Droplets, Kubernetes, Load Balancers, VPC, Volumes and Spaces, and never
  Managed Databases (§1, F-41).
- Every new service has its real listen address checked from outside
  before it is called internal (the F-54 rule, §1).
- A second droplet reached only over the private VPC is already the
  specified pattern, for Langfuse (`agent/docs/observability.md` §9).

The path below stays inside those constraints. Infrastructure prices are
DigitalOcean list prices and cover the agent tier only, not OpenEMR.

| Scale | Topology | What forces the change | Agent-tier infra (estimated) |
|---|---|---|---|
| **100** | Today's layout: the agent as its own Compose project on the OpenEMR host (§1). | Nothing about capacity: about 1 model call in flight at peak. The blockers are pre-launch, not scale: the hardened release build (F-48), rate limiting (§4.4), a publicly trusted certificate (F-36) and a host under an executed BAA (F-61). | $0 incremental. A DigitalOcean droplet of the audited size costs $48–$126/mo depending on plan (F-44). The current Vultr host's price was not fetched. |
| **1K** (estimated) | Agent on 2 droplets in the VPC behind a DigitalOcean Load Balancer, plus the Langfuse droplet. | The agent shares 4 shared vCPUs and ~12 GB (F-44) with OpenEMR and MariaDB, which are also serving clinicians' own EHR traffic. Briefs add about 5.6 FHIR reads per second at peak, and the droplet is a single point of failure during clinic hours. Moving off-host removes the reason the agent-to-OpenEMR hop skips TLS verification ("the hop stays on the host", §1, F-36), so that hop needs a certificate the agent can verify. The load test should confirm this break point. | ~$300–$350/mo: 2 × CPU-Optimized 4 vCPU / 8 GB at $84, a Load Balancer from $12, and Langfuse on General Purpose 4 vCPU / 16 GB at $126 (Langfuse asks for at least 4 cores and 16 GiB). |
| **10K** | Stateless agent replicas on DigitalOcean Kubernetes with a high-availability control plane, behind a Load Balancer. Self-hosted Redis for Follow-Up Drilling state and the queue. Logs shipped to Langfuse. | The Dockerfile runs one uvicorn process, and in-process state cannot span replicas. `compute_metrics.py` reads a single stdout log. About 121 model calls in flight at peak, from many tenants. | ~$1K/mo: $40 control plane, about 6 × $84 nodes, the Load Balancer, and Langfuse on General Purpose 8 vCPU / 32 GB at $252. |
| **100K** | Kubernetes clusters in more than one region, contracted API capacity, and an optional second provider (§4.8). | About 1,208 model calls in flight, output tokens beyond the Scale tier, and a region as a failure domain. | ~$5K–$10K/mo (rough). |

**Tokens are the cost story; infrastructure is the reliability story.**
From 1K users up, agent-tier infrastructure is under 4% of the bill:
roughly $330 against $9.4K at 1K, $1K against $94K at 10K, and
$7.5K against $940K at 100K. At 100 users the fixed costs weigh more,
including costs not priced here: executing the BAAs and on-call time.

### 4.10 The conversation in the projections (added 2026-09-20)

§4.1 to §4.9 were written with an estimated follow-up turn ($0.0275) and
the 5-record brief. This section redoes the linear projection with §1.4's
measured conversation, on two bases, so the effect of each change can be
seen apart:

- **5-record basis.** The same small chart as every table above: brief
  $0.0160, turn $0.0246. Both **measured**.
- **Population-weighted basis.** Brief $0.031, turn $0.057: **estimated**
  by applying §1.4's measured fits to the 52 charts on the host.

How often a physician asks is **assumed** in every row; no usage data
exists yet. Four turns a day is §3's original assumption. Fifteen is a
follow-up conversation of three turns on a quarter of encounters; forty is
four turns on half of them.

```
per clinician per month = 21 days × (20 briefs × brief + T turns × turn)
```

| Follow-up turns per clinician per day | Basis | Per clinician per month | Conversation's share | 100 users | 1K | 10K | 100K |
|---|---|---|---|---|---|---|---|
| 0 (brief only, §4.1) | 5-record | $6.73 | 0% | $673 | $6,731 | $67,305 | $673,050 |
| 4 | 5-record | $8.80 | 23% | $880 | $8,797 | $87,969 | $879,690 |
| 15 | 5-record | $14.48 | 54% | $1,448 | $14,480 | $144,795 | $1,447,950 |
| 40 | 5-record | $27.39 | 75% | $2,739 | $27,394 | $273,945 | $2,739,450 |
| 0 (brief only) | population-weighted | $13.10 | 0% | $1,310 | $13,104 | $131,040 | $1,310,400 |
| 4 | population-weighted | $17.90 | 27% | $1,790 | $17,900 | $179,004 | $1,790,040 |
| 15 | population-weighted | $31.09 | 58% | $3,109 | $31,090 | $310,905 | $3,109,050 |
| 40 | population-weighted | $61.07 | 79% | $6,107 | $61,068 | $610,680 | $6,106,800 |

Monthly, Opus 5, no caching, brief and conversation only. Adding the
estimated Change Detection (5 a day at $0.0275, §3) adds $2.89 per
clinician per month to any row: on the 5-record basis with 4 turns a day
that is $11.68, against §4.1's $11.93.

**What this changes in the sections above.**

- **The "all three use cases" figures stand on their own basis.** They
  assumed $0.0275 a turn; measured on the same 5-record chart it is
  $0.0246. At 4 turns a day the difference is −2% of the all-three total.
- **Chart size is now a measured multiplier, and it is about 2×.** On this
  population the weighted brief costs 1.9× the 5-record brief and the
  weighted turn 2.3× the 5-record turn. §1.3 called chart size the largest
  uncertainty; for this population it is no longer uncertain. Every dollar
  figure in §4.1 to §4.9 should be read as the 5-record case, roughly half
  of what this population would cost.
- **Once physicians use it, the conversation is the bill.** At 15 turns a
  day it is more than half of spend on either basis; at 40, three quarters.
  The brief is a fixed 20 calls a day. The conversation has no ceiling: §4.4's
  per-clinician rate limit is what bounds it, and it should count turns.
- **Spend caps bind sooner (§4.3).** On the population-weighted basis, 100
  users at 4 turns a day is $1,790 a month: over the Start tier's $500 and
  the Build tier's $1,000. The Scale tier is needed from the first hundred
  users, not from about 75 users on Start as §4.3 has it for briefs alone.
- **Input-token rate limits bind sooner (§4.2).** A turn carries about
  10,750 input tokens (the measured mean), seven times the 5-record brief's
  1,575. At peak (§4.2's formula), turns alone add:

  | Users | Turns/day each | Turn RPM | Turn input TPM | Turn output TPM |
  |---|---|---|---|---|
  | 1K | 4 / 15 | 17 / 63 | 0.18M / 0.67M | 8K / 29K |
  | 10K | 4 / 15 | 167 / 625 | 1.8M / 6.7M | 76K / 285K |
  | 100K | 4 / 15 | 1,667 / 6,250 | **17.9M / 67.2M** | 0.76M / **2.85M** |

  At 10K users and 15 turns a day, turns (6.7M) plus briefs (1.3M) take
  most of the Scale tier's 10M input TPM; §4.2 found all three use cases
  using a quarter of it. At 100K users the conversation alone is past the
  Scale tier on input at 4 turns a day. Output is not the constraint for
  turns: they average 456 output tokens.
- **Latency is inside the budget.** A turn took 7.1 s on average and 15.3 s
  at worst, against the 90-second window (KEY_METRICS.md Metric 4).

**Levers, in the order §4.5 to §4.7 give them, with what the measurement
says about each. None is built.**

1. **Cache the patient block within a session (§4.5b).** Now worth
   quantifying, because every turn of a session re-sends the same chart. On
   the median chart the four turns sent 33,131 input tokens, $0.166. If 85%
   of turn 1's input (**assumed**: the prompt, schema and chart records,
   placed ahead of the question and the context) is written to the 5-minute
   cache on turn 1 and read on turns 2 to 4, input falls to about $0.092
   (−45%) and the four turns from $0.197 to about $0.123 (**estimated**,
   −37%; §4.5b estimated −36% before there was anything to measure). Turn 1
   pays 21% more for the write, so this pays only for conversations that
   reach a second turn within five minutes; a single-turn conversation
   loses. The lab and visit records arrive mid-session, so they should sit
   after the breakpoint. The prompt's actual layout decides the real share;
   `usage.cache_read_input_tokens` will show it.
2. **Cache the static prefix across all turns.** The fit's intercept is
   about 4,400 tokens a turn, which includes the question and the lab-code
   names. How much of it is byte-identical across patients is unmeasured;
   `count_tokens` on the system prompt and schema answers it for free (§7).
3. **Model tiering (§4.7).** Sonnet 5 lists at 40% of Opus 5's price
   (**published**): the population-weighted turn would be about $0.023.
   Whether it holds the conversation's guarantees is **unknown** until the
   I cases of the eval suite pass on it; §4.7 recommended keeping this use
   case on Opus 5, and nothing measured here argues otherwise.
4. **Send less chart per turn.** Input is 83% of a turn's cost and grows by
   about 104 tokens for every record on the chart, active or not. Which
   records a turn needs is a retrieval-design question with safety
   consequences (a question about a resolved problem needs the resolved
   record), so this is noted as the largest lever, not recommended.

---

## 5. Compliance constraints on the cost levers

This project's compliance discussion flagged prompt caching as possibly
outside standard BAA coverage. This document checked that on 2026-09-16
against Anthropic's published feature-eligibility table.

| Lever | HIPAA-eligible (published) | What it means for this analysis |
|---|---|---|
| Prompt caching | **Yes.** "Your prompts and Claude's outputs are not stored. KV cache representations and cryptographic hashes are held in memory for the cache TTL and promptly deleted after expiry." | Both caches in §4.5 are usable under HIPAA readiness. See the caveat below. |
| Structured outputs | Yes. "Only the JSON schema is cached, for up to 24 hours since last use." PHI must not appear in schema definitions. | The current schema holds only a resource-type enum and `"display"`, so it complies. Keep patient data out of it. |
| Adaptive thinking, effort | Yes | The effort sweep in §4.7 is available. |
| Data residency (`inference_geo`) | Yes | US-only inference costs +10% (§1.2). |
| **Batch processing** | **No** (29-day retention) | No PHI through the Batch API (§4.6). |
| **Compaction, context editing** | **No** | Long Follow-Up Drilling sessions must manage context in the agent, not through server-side compaction. |
| Cache diagnostics | No | Debug cache hit rates from the `usage` fields, which the agent already logs. |
| **Server-side refusal fallback** (beta, used in `draft_claims.py`) | **Not listed.** "Features in beta are generally not covered under the BAA unless explicitly listed." | This needs confirming inside the HIPAA organization before launch. A HIPAA organization gets a 400 on non-eligible features, which would fail every brief. |

**The caching caveat is narrowed, not closed:**

- **The signed BAA is the source of truth, not the docs page.** Anthropic
  says this itself. No BAA with Anthropic has been executed (findings.md
  open item 23; ARCHITECTURE.md §6). Re-check caching eligibility against
  the BAA version that is actually signed.
- **The static-prefix cache is outside this question.** It holds no PHI,
  so it does not wait on the answer. Only the patient-context cache
  (§4.5b) does.

**Other platform facts that affect scale:**

- HIPAA readiness is available on the first-party Claude API. It is not
  available on Claude Platform on AWS or Microsoft Foundry.
- On Amazon Bedrock and Google Cloud, the cloud provider is the data
  processor.
- HIPAA readiness is enforced per organization and cannot be turned off
  once enabled. Non-PHI work, such as batch-priced evals, needs its own
  organization.

---

## 6. What is measured and what is not

| Figure | Status |
|---|---|
| 1,575 in / 326 out / 4,751 ms for one brief on Opus 5 | **Measured.** One run with 5 records. |
| Verification costs zero tokens | **Measured.** It is code: `agent/app/verification/checker.py`, with no model call in the graph apart from drafting. |
| Token prices, rate limits, spend caps, cache minimums and HIPAA eligibility | **Published**, fetched 2026-09-16. |
| DigitalOcean infrastructure prices | **Published** list prices, fetched 2026-09-16. DigitalOcean is the pre-migration provider; the current Vultr host's prices were not fetched. |
| Static prefix of ~750 tokens | **Estimated** from the prompt's size. Unmeasured. |
| Tokens per record and the outlier brief cost | **Estimated** from one data point. |
| Change Detection tokens | **Estimated.** Not built. |
| Conversation turn and session tokens, cost and latency, on seven charts from 5 to 184 records (§1.4) | **Measured** 2026-09-20 on the deployed Vultr agent: 35 model calls, from the agent's `llm_usage_event` log. One run per chart and question; model output varies run to run. |
| Brief tokens and cost on the same seven charts (§1.4) | **Measured** 2026-09-20. Replaces §1.3's single-point fit for charts of this population's kind. |
| Token and cost fits against chart size; population-weighted $0.057 a turn and $0.031 a brief (§1.4) | **Estimated** from the seven measured charts and the record counts of the host's 52 synthetic patients (F-41). |
| Cached-session saving of −37% (§4.10) | **Estimated** from measured tokens and published cache prices, with an **assumed** 85% cacheable share. No caching is built. |
| A refusal costs no model call | **Measured.** Decided by rule before retrieval or drafting (`agent/app/conversation/routing.py`); eval case I5. |
| 20 encounters/day, 21 days/month, peak factor 2, follow-up rates (4, 15 or 40 turns a day) | **Assumed.** The conversation is deployed; nobody has used it in a clinic. |
| Development spend of $0.36 | **Measured.** Anthropic Console Usage page, last 30 days, all workspaces and keys, read 2026-09-16. |
| The droplet breaking at about 1K users | **Estimated.** OpenEMR FHIR throughput is unmeasured (F-24, F-47). |
| Sonnet 5 and Haiku 4.5 quality on the brief | **Unknown** until an eval runs. |

## 7. Replacing estimates with measurements, most valuable first

1. **Partly done 2026-09-20:** the brief and a four-turn conversation were
   measured on seven charts spanning the host's population (§1.4). Running
   the brief over every patient once (52 calls, about $1.60 at the
   population-weighted $0.031) would replace §1.4's fit with the full
   distribution. Chart size is the largest multiplier on every projection.
2. **Call `count_tokens` on the system prompt and schema.** It is free and
   confirms whether the static prefix clears Opus 5's 512-token cache
   minimum and Sonnet 5's 1,024.
3. **Done:** the development spend was read from the Anthropic Console's
   Usage page on 2026-09-16 ($0.36, §2.2).
4. **Load-test with a stubbed model call** at 83 and 833 briefs per minute,
   to find where OpenEMR's FHIR endpoint on the droplet saturates (§4.9).
5. **Done for the conversation's tokens (§1.4).** Still open: how often
   physicians ask, and the rate of follow-ups within 5 minutes, which is
   the 28% threshold in §4.5b. Both need real use; Metric 6 by use case
   provides them. Change Detection is not built.
6. **Evaluate effort and model for the brief:** Opus 5 at `low`, Sonnet 5
   and Haiku 4.5, scored on Metric 1 and skipped-record counts (§4.7).
7. **In the HIPAA organization, confirm** that prompt caching is covered
   by the executed BAA and that the refusal-fallback beta is accepted
   (§5).
