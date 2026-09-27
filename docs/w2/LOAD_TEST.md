# Week 2 load and stress tests (local stack)

Run 2026-09-22 against the **local** stack. Not Vultr: Week 1 measured that
host, and its numbers are in `agent/load-test/RESULTS.md`. Raw k6 summaries,
resource samples and bench output are in `docs/w2/evidence/load/`; how to
re-run is in `agent/load-test/w2/README.md`.

## Summary

- **The ask path still saturates in OpenEMR, and Week 2 does not change
  that.** A question still does its FHIR reads through OpenEMR's PHP tier. At
  10 users OpenEMR already used 420% CPU and nothing failed. At 50 users,
  58% of requests failed, all of them `fhir_unavailable`, while the agent sat
  at ≤20% of one core. That is Week 1's finding, reproduced locally.
- **Week 2's new costs land in the agent process, not OpenEMR.** Nothing
  from them enters the 10-user OpenEMR ceiling directly, but on a shared
  4-vCPU host they compete with it for CPU and memory:
  - **The vision call** dominates each document: 15–31 s and $0.10–0.13 per
    document. It is Anthropic-bound, not local-CPU-bound.
  - **OCR** is the local CPU cost of extraction: about 1.1 CPU-seconds per
    document; 8.25 cores busy at 50 concurrent extractions.
  - **The cross-encoder reranker** was the worst behaved under concurrency.
    At 50 concurrent retrievals, in-flight reranks, which are never
    cancelled, thrashed the CPU: **p95 335 s**. **Fixed on this branch
    (D39):** at most two reranks run at once, and the rest fall back to the
    fused order immediately. Burst p95 at 50 is now 259 ms. Under load most
    retrievals go unreranked (6% at 50 paced users), and memory is still
    3.1 GiB (§5).
  - **Embedding** is not a bottleneck: p95 77 ms at 50 concurrent.

## Setup

| | |
|---|---|
| Machine | Apple M5 Pro, 15 cores, 24 GiB. The load generator (k6) ran on the same machine. |
| OpenEMR + MariaDB | `docker/development-easy`, in a Docker VM with **6 CPUs, 11.65 GiB** |
| Agent | This branch, on the host (`start_local_agent.sh`), one uvicorn worker; `LANGFUSE` **off** for every baseline below (Langfuse's six containers share the Docker VM with OpenEMR and would contaminate it; `agent/docs/observability.md` §1) |
| LLM | Stubbed (`AGENT_STUB_LLM_DRAFTING=true`) for the brief runs; **real** for the conversation run and the vision sample (the stub does not cover them; see "What it cost") |
| Patient | Phil Belford, `a2c36e54-af42-4c38-b86e-ade616ed5494`: a 5-record chart, the Week 1 patient, so the numbers compare |
| Load shape | k6, one VU = one clinician, a request then `sleep(1)`; 60 s per level; every iteration a new session |
| Resources | `sample_resources.sh`: `docker stats` for OpenEMR/MariaDB, `ps` for the agent, about every 3–4 s |

## 1. Ask path — Pre-Visit Brief, LLM stubbed

`k6-ask.js ROUTE=brief`. Everything but the model call is real: FHIR reads
under the clinician's token, verification, render, logging.

| Users | Requests | Error rate | p50 | p95 | p99 | Throughput |
|---|---|---|---|---|---|---|
| 10 | 280 | **0.00%** | 1.14 s | 1.52 s | 1.72 s | 4.53 req/s |
| 50 | 200 | **58.0%** (116) | 16.08 s | 28.05 s | 31.55 s | 2.70 req/s |

By step (server-side timing): FHIR `retrieve` p95 is **1.52 s at 10 users
and 29.7 s at 50**. Draft and verify stay under 1 ms (stub) at both levels.

| Container | At rest | 10 users (peak CPU / mem) | 50 users (peak CPU / mem) |
|---|---|---|---|
| agent (host process) | 0% / 28–69 MiB | 20.0% / 135 MiB | 14.4% / 149 MiB |
| OpenEMR (Apache/PHP) | 0.2% / 554 MiB | **419.9%** / 1,009 MiB | **508.7% / 4,888 MiB** |
| MariaDB | 0% / 357 MiB | 60.0% / 378 MiB | 48.5% / 442 MiB |

The failures:

- All 116 were `502 fhir_unavailable`. OpenEMR answered 157 of 2,225 FHIR
  reads with 500 (7.1%), and the agent's `tool_call_event`s record exactly
  that.
- OpenEMR's resident memory went from 0.55 to 4.9 GiB at 50 users: PHP
  workers spawned faster than they retired. That is Week 1's finding,
  unchanged.
- Throughput *fell* from 4.5 to 2.7 req/s as users went from 10 to 50. That
  is the signature of a saturated backend.

## 2. Ask path — conversation turn, real LLM, 10 users

`k6-ask.js` (the conversation route). This run used **real model calls**:
the load-test stub covers the brief only, as `draft_follow_up.py` says. It
is still the most honest ask-path number in this document, because it
includes Anthropic.

| Users | Requests | Error rate | p50 | p95 | p99 | Throughput |
|---|---|---|---|---|---|---|
| 10 | 100 | **0.00%** | 5.25 s | 6.41 s | 6.72 s | 1.54 req/s |

By step: `retrieve` p95 1.41 s, `draft` (LLM) p95 **5.56 s**, `verify`
0.4 ms. The model is 85% of the wait.

- Resources: agent 9.7% / 136 MiB peak; OpenEMR 384% / 643 MiB peak;
  MariaDB 48%.
- Cost: 100 turns × **$0.0299** (4,250 tokens each) ≈ **$3.00**.

It was **not** run at 50 users: that would be about 250 real model calls,
and it would measure Anthropic's rate limits, not this system (the Week 1
convention).

## 3. Extraction path — upload surface (HTTP)

`k6-upload.js`: `POST /documents`, then poll status once a second until
complete. Every upload is distinct bytes. **On `main` the route is still the
stub**: it hashes the body and runs no extractor. So this measures the
upload surface: body read, SHA3-512, the D7 status machine.

| Users | Requests | Error rate | p50 | p95 | p99 | Throughput | upload → complete |
|---|---|---|---|---|---|---|---|
| 10 | 750 | 0.00% | 2.9 ms | 7.3 ms | 115 ms | 12.4 req/s | 4.03 s (the stub's timer) |
| 50 | 3,750 | 0.00% | 7.3 ms | 37.4 ms | 61.8 ms | 61.5 req/s | 4.05 s |

The agent peaked at 2.8% CPU / 155 MiB, and OpenEMR at 21.7% (the only
OpenEMR traffic was token checks). The surface is not a bottleneck. What
matters is the work behind it:

## 4. Extraction path — the extractor, in process

`extraction_bench.py` drives the real intake-extractor worker
(`attach_and_extract`) over the six doc-factory samples: 2 clean labs,
1 multipage lab, 1 heavy-scan lab, 1 clean and 1 light-scan intake form.
Two modes.

### 4a. Vision replayed; every other stage real

The vision call is replaced by the sample's perfect transcription (the
extractor's own test stub). Everything else runs for real: PyMuPDF text
layer, Tesseract OCR, locate, checks, discrepancies.

| Concurrent | Extractions | Failed | p50 | p95 | p99 | Throughput | Cores busy | Peak RSS |
|---|---|---|---|---|---|---|---|---|
| 1 | 2 | 0 | 1.17 s | 2.21 s | 2.30 s | 0.85 /s | 0.95 | 251 MiB |
| 10 | 20 | 0 | 0.28 s | 4.07 s | 4.24 s | 4.62 /s | 4.9 | 655 MiB |
| 50 | 100 | 0 | 4.81 s | 10.20 s | 10.99 s | 7.51 /s | **8.25** | **1,233 MiB** |

By step at 50 concurrent: `read_document` (text layer / OCR) p95 is
**10.2 s**; `locate` p95 39 ms; `verify` 8 ms. OCR is the entire local cost.
It runs in `asyncio.to_thread`, so the event loop stays free, but the cores
do not.

On the Vultr host's 4 vCPUs, which OpenEMR alone already pinned at 10 users,
the same load would be CPU-bound at roughly half this throughput, competing
directly with OpenEMR. That is an inference from these numbers, not a
measurement on that host.

### 4b. Real vision calls, sequential (N = 3)

| Sample | Total | Vision step | Tokens in / out | Cost | Located / read |
|---|---|---|---|---|---|
| lab-clean-001 | 29.5 s | 29.5 s | 6,663 / 3,704 | $0.126 | 102 / 104 (98%) |
| intake-clean-004 | 15.3 s | 15.3 s | 11,531 / 1,515 | $0.096 | 45 / 49 (92%) |
| lab-scanned-heavy-abnormal-002 | 32.9 s | 30.6 s | 6,663 / 3,716 | $0.126 | 36 / 107 (34%) |

The vision call is **93–99% of a document's extraction time** and all of its
cost. It is why D7 makes extraction an upload-time path. It was not run
concurrently: 10 or 50 concurrent calls would measure Anthropic's rate
limits and cost $1–6. Concurrent vision throughput is **unmeasured**.

## 5. Guideline retrieval (hybrid RAG), in process

`retrieval_bench.py`: `run_evidence_retriever` over the 30 concept-only dev
queries, from a thread pool, with the committed corpus (693 chunks), local
bge-small embeddings and the local cross-encoder reranker. No HTTP route
calls the retriever yet (no supervisor), so this is the only way to load it.

**Without rerank** (BM25 + dense + RRF):

| Concurrent | Retrievals | p50 | p95 | p99 | Throughput | Cores busy |
|---|---|---|---|---|---|---|
| 1 | 4 | 3.1 ms | 3.9 ms | 4.0 ms | 295 /s | 6.7 |
| 10 | 40 | 9.9 ms | 14.0 ms | 15.3 ms | 891 /s | 10.8 |
| 50 | 200 | 52.5 ms | 76.7 ms | 85.1 ms | 892 /s | 10.3 |

RSS about 250 MiB. **Embedding and fusion are not a bottleneck.**

**With rerank requested** (the default path; D25's 2.5 s rerank budget and
its circuit breaker):

| Concurrent | Retrievals | p50 | p95 | p99 | Reranked | Circuit open after |
|---|---|---|---|---|---|---|
| 1 | 4 | 239 ms | 264 ms | 266 ms | **100%** | no |
| 10 | 40 | 13.8 ms | 4,260 ms | 4,332 ms | **5%** (2/40) | yes |
| 50 | 200 | 9.2 ms | **335,224 ms** | 343,579 ms | **0%** | yes |

What this shows:

- **Rerank does not survive concurrency.** With 10 simultaneous reranks,
  each blew the 2.5 s budget. The first to finish opened the circuit, and
  every retrieval after it skipped reranking. That is by design (D25), and it
  is why `reranked` true-rate is on the dashboard: it is the one number that
  shows the reranker silently turned itself off.
- **The circuit does not cancel reranks already running.** At 50, fifty
  cross-encoder calls started before the circuit could open, and competed
  for the CPU until they finished, about 5.6 minutes later. Only 2 cores were
  busy through it: ONNX threads oversubscribed and thrashed. p50 stays low
  because most requests arrived after the circuit opened and skipped rerank.
- **Memory.**
  - Steady state with the reranker loaded is about **1.1 GiB RSS** (0.24 GiB
    without it); one rerank takes about 230 ms.
  - The first, cold run peaked at **5.3 GiB RSS** with a 39 s load. The
    cause was not isolated: a warm re-run loaded in 0.4 s.
  - On the 7.24 GiB Vultr host, where OpenEMR peaks at 4.9 GiB (Week 1),
    that peak would not fit.

### 5b. After the admission bound (D39)

The fix: `MAX_CONCURRENT_RERANKS = 2`, taken non-blocking. A request that
finds both slots taken returns the fused order at once (`rerank_busy`).
Admitted reranks run on a fixed two-thread pool. Same bench, same queries.
"Paced" adds a 1 s think time per user, like the k6 tests; "burst" fires
back to back.

| Load | p50 | p95 | p99 | Reranked | Busy (turned away) | Circuit | Peak RSS |
|---|---|---|---|---|---|---|---|
| burst, 1 | 255 ms | 339 ms | 350 ms | 100% | 0 | closed | 1.0 GiB |
| burst, 10 | 38 ms | **143 ms** (was 4,260) | 632 ms | 5% | 38 / 40 | closed (was open) | 1.3 GiB |
| burst, 50 | 81 ms | **259 ms** (was 335,224) | 372 ms | 1% | 198 / 200 | closed (was open) | 1.7 GiB |
| paced, 10 users | 12 ms | **588 ms** | 746 ms | **35%** | 65 / 100 | closed | 3.1 GiB |
| paced, 50 users | 18 ms | **426 ms** | 686 ms | **6.2%** | 469 / 500 | closed | 3.1 GiB |

Retrieval latency is now bounded by one rerank, not by the queue. What the
bound cannot do is make reranking cheaper: two slots at about 250 ms each
is roughly 8 reranks a second. Under load most retrievals are unreranked,
and the dashboard's reranked-true rate shows exactly that.

**Memory is not fixed.** The growth follows input-shape variety: on one
thread, 100 varied reranks grew RSS from 967 MiB to about 2.7 GiB before
plateauing. Measured and not adopted:

- arena off: 4.1 GiB, worse;
- fixed-length padding: 512 tokens bounds growth at +0.6 GiB but costs
  +50% latency; 256 tokens is flat and faster but truncates and would
  change rankings.

D39 has the numbers. The next step is re-running the retrieval bakeoff at a
fixed length.

## 6. The real ask path, one encounter at a time (after `feat/w2-graph`)

Not a load test: two single encounters through the API collection, real
model calls, against the real routes. Recorded because the step events found
something no load level had: the **`/w2/ask` answer over a whole uploaded
lab report takes 90–130 s**.

| Run | `/w2/ask` about the document | `/w2/ask` guideline question | Encounter cost |
|---|---|---|---|
| `c8661931-…` | **130.5 s**: one call, 34,948 tokens in / 11,523 out | 10.6 s | $0.82 |
| `07c0fd35-…` | **91.9 s** | 10.5 s | $0.82 |

The step view shows where the time goes: all of it is `supervisor.answer`,
one model call. It is not routing, not retrieval, and not the verifier. The
answer is drafted over every extracted result on the report, and it writes
more than 11k tokens back.

This is past the 90 s budget (KEY_METRICS.md Metric 4) on a single request
with no load at all, and the `answer_p95_latency` alert (45 s) fires on it.
`/w2/ask` is an answer route for that alert. It was not in the first draft
of the dashboard script, which predated the route.

The fix belongs to the ask path, not observability. For example: bound
the output, or answer from the results the question names rather than all
of them.

## 7. The answer step, scoped (after `feat/w2-answer-scope`, D50)

Work order 19. `agent/load-test/w2/answer_bench.py`: the real ask path in
process, one encounter at a time, no load — recorded vision (text layer and
locate live on the PDF), live FHIR reads on the Vultr host under the eval
physician's token, one live drafting call per run. It adds no
instrumentation: every number is read back from the `step_event`
(`supervisor.answer`), `tool_call_event` (`llm`), `llm_usage_event` and
`w2_ask_turn_completed` that `feat/w2-observability` put on this path. Raw
output: `docs/w2/evidence/load/answer-{before,after}-*.json`.

**Before** — "What were the results on the uploaded lab report?", 3 runs:

| Report | Answer step p50 / worst | Model call p50 | Tokens in / out (p50) | Citations | Cost / run |
|---|---|---|---|---|---|
| `lab-clean-001`, 22 rows | 89.0 s / 91.8 s | 87.9 s | 32,776 / 11,511 | 91 | $0.45 |
| `lab-multipage-003`, 44 rows | 188.8 s / 195.9 s | 188.0 s | 49,465 / 24,762 | 180 | $0.87 |

The model call is 99% of the answer step, and it is output-bound: ~131
output tokens/s and ~130–140 output tokens (thinking included) per citation,
so about a second per citation. The drafter cited every value on the report,
five fields a row.

**After** — the same question and reports, 3 runs, plus the case where the
cap binds and the staged critical scenario:

| Case | Answer step p50 / worst | Tokens in / out (p50) | Citations | Cost / run | Rows itemised / counted / capped |
|---|---|---|---|---|---|
| 22 rows | **6.9 s / 9.1 s** | 14,585 / 461 | 3 | $0.084 | 0 / 22 / 0 |
| 44 rows | **11.7 s / 13.0 s** | 12,095 / 1,151 | 8 | $0.089 | 1 (ferritin, printed L) / 43 / 0 |
| 44 rows, "Show me all the results on this report" | 43.4 s / 45.5 s | 18,194 / 5,092 | 36 | $0.22 | 8 / 0 / 36 |
| `lab-critical-potassium-007`, 22 rows, 1 run | 12.5 s | 15,515 / 1,005 | 8 | $0.10 | 1 critical / 21 / 0 |

The whole-report question is 13× faster on 22 rows and 16× on 44, at a fifth
to a tenth of the cost. The worst case — a request for every result on the
44-row report, where the cap of 8 binds — is 41–45 s: well under 90 s, but
at the 45 s `answer_p95_latency` alert (ALERTS.md). In the critical run the
6.8 mmol/L potassium leads the answer, stated and cited, and the rest of
the report is counted.

Input tokens fell by less than output (33k → 15k): most of what is left is
the chart (the bounded base set and lab Observations the lab wording
reads), which this change does not scope.

Cost of these runs: before $3.97 (6 runs), after $1.28 (10 runs).

## 8. The live host, `main@d61f739`: conversation turn, real LLM, 10 users

**Setup:**

- **Target:** the deployed agent on `64.177.42.210` (4 vCPU, 7.2 GiB),
  reached through its Caddy proxy on `:8443`, as the chart card reaches it.
- **Build:** `main@d61f739`, with drafting thinking **disabled**
  (D-unassigned, answer-step thinking). The model is real, with no stub.
- **Load:** k6 `k6-ask.js` from the operator's laptop: 10 VUs for 3 min on
  the conversation route. The question was "What medications is the patient
  on?", for patient `a2c373ec-…` (pid 38), with a new session every
  iteration.
- **Token:** from a temporary password-grant client, deleted afterwards
  with its tokens.
- **Resources:** `docker stats` every 5 s.
- **Stop rule:** a watchdog stops k6 if the agent passes 80% of its 4,608
  MiB cap (3,686 MiB). It did not trip.
- **When:** 2026-09-26 21:18:38–21:21:53 UTC.
- **Raw files:** `evidence/load/host-d61f739-10vu/`, holding
  `k6-summary.json`, `k6.txt`, `resources.csv` and the runner,
  `load-run.sh`.

| Users | Requests | Error rate | p50 | p95 | p99 | Max | Throughput |
|---|---|---|---|---|---|---|---|
| 10 | 125 | **0.00%** (125/125 were 200) | 13.25 s | 19.47 s | 23.58 s | 23.68 s | 0.64 req/s |

**By step, from the response body:**

| Step | p50 | p95 |
|---|---|---|
| `draft` (the model) | 12.00 s | 18.52 s |
| `retrieve` | 0.94 s | 2.82 s |
| `verify` | 0.01 s | 0.02 s |

**The model is about 90% of the wait.**

**Resources, peak over 28 samples:**

| Container | Memory | CPU |
|---|---|---|
| Agent | **818 MiB** (794 at rest; **18% of its cap**) | 35% |
| OpenEMR | 675 MiB | 281% |
| MariaDB | 231 MiB | — |

Memory stayed flat through the run: this route does no rerank and no OCR,
which are the memory drivers D39 names.

**Against §2** (the local stack, the same route and question, 10 users): p95
is 19.5 s here against 6.4 s there, and a turn costs $0.089 against $0.030.

- **Not like for like.** §2 ran another patient (`a2c36e54-…`) on another
  host, so this is not a build-against-build comparison.
- **The input is much larger.** Here a turn reads about 9.7k input tokens,
  against 4,250 tokens in all for §2's turn, and writes a median of 1,431
  output tokens.
- **Thinking was disabled on every call**, so thinking is not the
  difference.

**Cost: 125 model calls; 1,217,125 input and 201,969 output tokens; $11.13**
at $5 / $25 per MTok. That was over the $6–8 estimated before the run,
because the estimate used §2's per-turn cost.

## Where the new bottleneck is

| Path | Bottleneck | Evidence |
|---|---|---|
| Ask (question → answer) | **OpenEMR's PHP tier**, unchanged from Week 1: saturated at 10 users locally (420% CPU), failing at 50 (58%) | §1 |
| Ask, wall-clock | The LLM draft: 85% of a real turn. A `/w2/ask` over a whole lab report was **90–196 s** (one call writing a citation per value); scoped by rule (D50) it is **7–13 s**, and 41–45 s when a question asks for every result and the cap binds | §2, §6, §7 |
| Extraction, wall-clock and cost | **The vision call**: 15–31 s, $0.10–0.13 per document | §4b |
| Extraction, local CPU | **OCR**: 8.25 cores at 50 concurrent, p95 10 s | §4a |
| Guideline retrieval | **The cross-encoder reranker**: p95 335 s at 50 before the admission bound, **259 ms after** (D39); throughput is about 8 reranks/s, and memory is still up to 3.1 GiB | §5, §5b |
| Embedding | Not a bottleneck: p95 77 ms at 50 | §5 |

**Does Week 2 change the ~10-user ceiling?**

- Not for questions: it is still OpenEMR.
- It adds a second, independent ceiling inside the agent process. Week 1
  could say "the agent is ≤20% of one core". With OCR and the reranker, that
  is no longer true. On a single 4-vCPU host shared with OpenEMR, upload and
  retrieval bursts will take CPU the PHP tier needs.
- Extraction should not run in the request-serving process, or on the
  OpenEMR host, once it is wired to the upload route.

## What it cost

Four sets of real model calls, **about $14.85** in total: $3.72 for the
first three, and $11.13 for the live-host run added in §8. The unplanned
$3.00 was a mistake: I assumed the stub covered the conversation route, and
the module docstring says it does not.

| Run | Calls | Cost |
|---|---|---|
| §2, real-LLM conversation run | 100 turns | $2.99 |
| §4b, vision sample | 3 | $0.35 |
| API collection runs | 3 runs, 5 calls (the brief was stubbed in one run) | $0.38 ($0.150 + $0.087 + $0.145, from the logs) |
| §8, live host, 10 users, `main@d61f739` | 125 turns | $11.13 (from the host's `llm_usage_event`s) |

## Not measured

- Concurrent vision throughput and Anthropic rate limits.
- 50 concurrent real conversation turns.
- §1–§7 on Vultr. §8 is the one run on the live host, and it covers the
  conversation route only.
- The upload route under load with the real extractor behind it. It is
  wired on `main` (`feat/w2-graph`); §4a replayed vision, and the host ran
  uploads one at a time (`DEPLOY.md` §8.0).
- A fix for the reranker's memory growth (shape-driven; D39 has the options measured).
