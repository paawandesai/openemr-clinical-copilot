# Key Metrics — Week 2

Week 1's six metrics still stand, and their definitions and computation live
in [docs/KEY_METRICS.md](docs/KEY_METRICS.md):

1. verification pass rate
2. unverified-claim disclosure rate
3. authorization boundary integrity
4. response latency against the 90-second budget
5. retrieval coverage per query
6. cost per interaction

This file covers what Week 2 adds: documents, guideline evidence, and a gate
that has to hold. It keeps the Week 1 format. Each metric says what it
claims, how it is measured, and its current value with the source, and it
says when there is no value.

**The rule for every number here:** a rate is shown with its numerator and
denominator. A metric whose denominator is small says so. A metric with no
live measurement says "not measured", not a target.

**Snapshot: `main@54266a9`**, which is on `origin`. Every path, number and
decision below is on that commit. Numbers measured elsewhere name the build
they were measured on.

**The host runs agent `main@d61f739` and module `main@a434ffd`**, plus the
patient dashboard on port 8444 (`docs/w2/DEPLOY.md`, "What the host runs
now"). Between them and the snapshot, no deployed code differs: the agent's
code (`agent/app/`) is unchanged since `d61f739` and the module since
`a434ffd`, apart from code comments this branch renumbered to D62–D64.

**Host figures name the build they were measured on:**

| Build | Deployed | What it added | Measured in |
|---|---|---|---|
| `21f1053` | 2026-09-23 16:40 UTC | the first Week 2 deploy: card, routes, upload guard, D41 | `DEPLOY.md` §8.0 |
| `ea198c8` | 2026-09-23 17:12 UTC | D50, the scoped document answer; the eval gate | `DEPLOY.md` §8.00 |
| `06f0d45` | 2026-09-25 14:44 UTC | D54's upload scopes, D56, D58 (production Langfuse); then the patient dashboard on port 8444 | `docs/w2/evidence/prod-langfuse/deploy-2026-09-25/`, `docs/w2/evidence/dashboard-deploy-2026-09-25/` |
| **`d61f739`** (agent) | 2026-09-26 18:57 UTC | D60 (degraded documents, quarantine), drafting thinking off (D62), the duplicate-result guard (D63) | `LOAD_TEST.md` §8 |
| **`a434ffd`** (module only) | 2026-09-27 | the per-user token-fetch lock (D64); the agent was not restarted | `docs/w2/evidence/token-race-deploy-2026-09-27/` |

Figures labelled "rehearsal host, before the Week 2 deploy" predate all five.

| # | Metric | Current value | Target | Strength of evidence |
|---|---|---|---|---|
| W2-1 | Located rate, text-layer pages | 97.8% (391/400) in replay; 98% (102/104) and 92% (45/49) with real vision. Heavy scan: 33.6% on the laptop's Tesseract, 10.3% on the deployed image's | ≥ 85% (alert) | real model, n = 2 documents |
| W2-2 | Unlocated values stated as fact | 0, enforced by the type system | 0, absolute | gate critical case plus planted regression |
| W2-3 | Flag cross-check agreement, comparable flags only | 100% (**1/1**) | none set; the denominator is the finding | very weak: n = 1 |
| W2-4 | Intake discrepancy accuracy; never merged | precision 2/2, recall 2/2, false positives 0/25; merges 0 | 0 merges, absolute | weak: n = 2 |
| W2-5 | Verified and disclosed on the ask path | no rate; 6 live combined answers before D50 (3 on the host, 3 local), 0 unverified; after D50 the host's whole-report answers had 0 unverified in 6 runs; 2 real-route runs pass their citation assertions; gate det 75/75 | 100% disclosed, absolute | tests plus 2 live runs; no runtime disclosure signal |
| W2-6 | Critical values surfaced | **not measured live**; gate H6 and G7 pass | 100%, absolute | one fixture |
| W2-7 | Guideline retrieval: recall and the share actually reranked | recall@4 0.666, zero-recall queries 0/30; reranked 35% at 10 paced users, 6.2% at 50 | reranked rate is watched, not targeted | 30 dev queries; laptop measurements only |
| W2-8 | Eval gate: regressions caught | 22/22 planted regressions blocked; 81/98 cases active | every planted regression blocks | harness-proved; judge uncalibrated |
| W2-9 | Cost and latency per encounter | $0.82 measured before D50 (n = 2), about $0.44 estimated after; latency tracks what the model writes, ~4.1 s + 7.45 s per 1k output tokens; whole-report question **6.9–8.4 s** on the host after D50 (88–92 s before); conversation turn on the host at 10 users p95 **19.47 s**, 0 errors in 125 | ≤ 90 s per answer (Week 1 budget) | **passes on the host**: `/w2/ask` one question at a time, the conversation route at 10 users; 44-row "show me all" at 43.4 s |

**Limitations.** Every known limitation, with its evidence, is in
[`W2_ARCHITECTURE.md` §11](W2_ARCHITECTURE.md#11-known-limitations). The
ones that bear on these numbers: the judge's small independent measurement
(W2-8), the OCR build behind the heavy-scan rate (W2-1), unmeasured live
vision variance (W2-1), the one host load test covering the conversation
route only (W2-9), and the broad ~110-claim conversation question that has
no output bound (W2-9).

---

## W2-1. Located rate: can every value be put back on the page?

**Claims.** When the co-pilot reads a value off a clean PDF, deterministic
code finds that value on the page and can draw a box around it (D3). A value
without a box is never shown as fact, which is W2-2. This metric says how
often a real document yields values the clinician can actually use.

**Measured.**

- The rate is `located / (located + unlocated)`, over the fields the vision
  model gave a value.
- `missing` and `illegible` are left out of both sides, because they
  describe the document, not the extractor (`OBSERVABILITY.md` §3;
  `agent/tests/intake_extractor_measurement.py` docstring).
- In production it comes from `intake_extraction_completed` events, computed
  by `agent/scripts/w2_dashboard.py`.
- Text-layer pages and scans are **reported separately**. A pooled rate
  would hide a regression on clean pages behind normal scan variance
  (`ALERTS.md` §4).

**Current.**

| Pages | Real vision (live model) | Replay (vision = ground truth; everything after it real) |
|---|---|---|
| text layer | lab 98% (102/104), intake 92% (45/49) | 97.8% (391/400) across 4 samples |
| light scan | — | 87.5% (49/56) |
| **heavy scan** | **34% (36/107)** | **33.6% (36/107)** on Tesseract 5.5.3; **10.3% (11/107)** on the deployed image's 5.5.0 |
| all six samples | — | 84.5% (476/563), Tesseract 5.5.3 |

Sources:

- The real-vision column is
  `docs/w2/evidence/load/extraction-live-vision.json`,
  three live calls.
- The replay column is `docs/w2/evidence/extractor-measurement.txt`,
  run this session with `cd agent && python -m tests.intake_extractor_measurement`.
- Box correctness in replay: 100% (391/391) of text-layer locates sit over
  the right ink at IoU ≥ 0.5.
- **The heavy-scan figure depends on the OCR build.** Both columns were
  measured on the laptop's Homebrew Tesseract 5.5.3. The deployed agent
  image and CI run Debian's Tesseract 5.5.0, which locates **11 of 107
  (10.3%)** on the same replayed transcription (D60; CI pipeline 28822). The
  host's rate for a heavy scan is therefore about a third of the laptop's.
  Text-layer pages need no OCR and are unaffected.

**Why the heavy scan is low, and why that is accepted.** Numbers, units,
dates and flags are matched exactly or not at all (D24). A one-character OCR
slip therefore produces an `unsupported` field rather than a wrong number.
The alternative is a wrong number with a box drawn behind it, which gate case
W2-B6 describes as the one mode that is unrecoverable, because the chart has
no reference ranges to contradict it. The low rate is the recorded
cost of that choice.

**What a bad number means.** A text-layer drop below 85% (the alert
threshold, with a minimum of 50 fields) means the model's transcription
changed shape or the locate step regressed. Nothing else catches that:
requests still succeed. It is a quality regression, not a safety one,
because unlocated values are still disclosed (`ALERTS.md` §4).

## W2-2. Unlocated values presented as fact: an absolute, not a rate

**Claims.** Invariant 4: an extracted value the locate step could not place is
never rendered as fact and never counted as verified.

**Measured.** By construction, then by the gate:

- `PRESENTABLE_STATUSES = {LOCATED}`, and `as_presented_fact()` is the only
  route from an extracted field to a value (D13).
- Gate case W2-C3 is **critical**, so its failure blocks regardless of
  aggregates.
- The planted regression `unlocated-value-is-presentable` widens that set by
  one member. It blocks the gate, and 6 cases move from pass to fail.

Source: `docs/w2/EVAL_GATE_EVIDENCE.md`, regression 3.

**Current.** 0 by construction. The gate passes with W2-C3 active.

**What it does not cover.** A clinician is told a value is unsupported. The
metric does not measure whether the clinician then opens the page.

## W2-3. Flag cross-check agreement, over comparable flags only

**Claims.** Where the laboratory printed a flag (H, L, N, WNL…), the flag the
co-pilot computes from the value and range agrees with it.

**Measured.** `agree / comparable`. Comparable means the reported flag is one
of `normal | high | low | abnormal` and was located on the page. A blank
column is `not_reported`, never agreement (D20). The denominator is reported
beside the rate every time; D20 makes that a condition of using the metric.

**Current.** 100%, **1/1**. Across the six samples, 71 flags were
`not_reported`: 22 on the clean lab, 6 on the heavy scan, 43 on the
multipage lab (`docs/w2/evidence/extractor-measurement.txt`).

- The one comparable flag is the multipage lab's single abnormal row
  (ferritin, low).
- The heavy scan has three abnormal rows (glucose high, ALT high, potassium
  low), but none of their printed flags was located, so none was comparable.

The abnormal rows are the factory's `abnormal_value` perturbations in each
sample's `*.ground_truth.json`.

**Read it as:** the check is wired and has agreed once. It is **not** evidence
that the extractor reads flags in general. Getting a real number needs lab
reports that print flags on many rows. The committed factory prints them
only on abnormal rows.

## W2-4. Intake discrepancies: found when real, silent when not, never merged

**Claims.** An intake form that disagrees with the chart shows the
disagreement. An intake form that says the same thing in the patient's own
words shows nothing. Nothing from an intake form reaches the chart (D2,
invariant 5).

**Measured.**

- Precision and recall are scored against the discrepancies the factory
  declares.
- The false-positive rate is scored over fields the factory renders
  `derived`, where the patient writes a name differently from the chart
  (D35 item 9).
- Merges are held at zero structurally. The write-back request type refuses
  any document that is not a lab PDF. The planted regression `auto-merge-intake`
  removes that check and blocks the gate through W2-E1, which is critical.
- W2-H3 asserts that the ask path references no write-back code at all.

Sources: `EVAL_GATE_EVIDENCE.md` regression 11; the commit that activated
G1, H3, H5 and H6.

**Current.**

| Measure | Value |
|---|---|
| Precision | 100% (2/2) |
| Recall | 100% (2/2) |
| False discrepancies on `derived` fields | 0% (0/25) |
| Merges | 0 |

Source: replay, `docs/w2/evidence/extractor-measurement.txt`.

**Read it as:** two real discrepancies, both found. That is a denominator of
2. The 0/25 false-positive figure is the stronger half, because a
discrepancy list that cries wolf stops being read (planted regression
`derived-rendering-is-a-discrepancy`).

## W2-5. Answer verification and disclosure on the Week 2 ask path

**Claims.** This extends Week 1 Metrics 1 and 2 to documents and guidelines.
Every document or guideline claim on `/w2/ask` passes a deterministic check
before it renders. The checks are an exact match to located text, or a
verbatim excerpt of at least 4 words. A claim that fails is shown as
UNVERIFIED and never dropped (D40 item 7).

**Measured.**

- Per claim: Week 1's `claim_verified_event` for chart claims, and
  `w2_claim_verified_event` for document and guideline claims
  (`agent/app/verification/w2_checker.py`).
- Per turn: `w2_ask_turn_completed` carries claim counts by type
  (`agent/app/graph/supervisor.py`).

**Current.** **No live pass rate.** What exists:

- Two real-route encounters ran `/w2/ask` against the real model, one
  question about the document and one about guidance. Each passed the
  collection's assertions "an answer is drawn only from what it can cite"
  and "a routing decision was made and recorded with its reason"
  (`docs/w2/evidence/api-collection-run.txt`).
  Those assert the shape of the answer. They are not a per-claim pass rate.
- **On the deployed host.**
  - On `21f1053`, before D50 (`DEPLOY.md` §8.0), the document + guideline
    question answered 3 of 3 times with **7 document + 2 guideline
    citations, 0 unverified**.
  - On `ea198c8`, after D50, the whole-report question answered 3 of 3 on
    each of two reports, with **0 unverified**: 49 and 97 document citations
    (`DEPLOY.md` §8.00). Most of those are the cited count statement code builds (D50
    item 3).
- **From the real chart** (`docs/w2/evidence/php-wiring-e2e-run.md`, local stack), three timed answers to one
  question cited a document and a guideline together:
  - 6 document + 4 chart + 2 guideline citations, then 6 + 0 + 2 twice;
  - **0 unverified** each time.
  That is 3 answers and 28–32 claims across the three
  (12 + 8 + 8 = 28 citations). It is not a production rate.
- In-process tests: `agent/tests/test_w2_checker.py` and
  `test_w2_supervisor_graph.py`.
- Gate categories A and F, part of the deterministic tier's 75/75.
- On the Week 1 path it extends, 44/44 claims verified in a real-LLM
  encounter (`docs/w2/ALERTS.md` §5).

**Gap, stated.** Week 1's Metric 2 is computed from `claim_disclosure_event`.
That event is emitted by `audit_disclosure()`, which reads the *rendered*
text after rendering (F-59). The brief, follow-up and conversation graphs
call it. **The Week 2 supervisor does not**, so on `/w2/ask` disclosure is
covered by tests, but no runtime signal is produced. Wiring
`audit_disclosure` into the ask path's render step closes this.

## W2-6. Critical values surfaced first

**Claims.** A panic value in anything the turn read leads the answer,
whatever was asked. The lab-result cap never removes one. An unlocated
possible panic value is named without its number (H6, D40 item 9).

**Measured.**

- Counts per turn on `w2_ask_turn_completed`: `critical_values`,
  `possible_critical_values` and `critical_restored_past_cap`.
- Gate cases:

| Case | Asserts | Critical? |
|---|---|---|
| W2-H6 | potassium 6.8 mmol/L on `lab-critical-potassium-007` surfaces as a critical value with its reading | yes |
| W2-G7 | a critical row the cap dropped is restored | yes |
| W2-H10 | the threshold table is checked as data: every row has a limit and names its units | yes |

**Current.** All three cases pass, and they are active (`EVAL_GATE_EVIDENCE.md`
control run; the commits that activated the supervisor batch and H6).
**Not measured live.** The
threshold table is a demo configuration, not reviewed for any site.

## W2-7. Guideline retrieval: recall, and whether the reranker actually ran

**Claims.** When a question needs guidance, the four chunks passed to the
answer step contain what answers it. The reranker, chosen because it is the
only configuration with no total misses (D25), is actually running.

**Measured.**

- Quality comes from the bake-off: 30 concept-only dev queries and 110
  relevance judgments (`docs/w2/retrieval-bakeoff.md` §4).
- Operation is the `reranked` true-rate from `evidence_retrieval_event`, on
  the dashboard (`OBSERVABILITY.md` §3). It is the one number that shows the
  reranker has silently turned itself off (`LOAD_TEST.md` §5).

**Current.**

| Measure | Value |
|---|---|
| recall@4 | 0.666 |
| MRR | 0.889 |
| Queries at zero recall | 0/30 |
| Reranked, burst of 50 concurrent (local, with the D39 bound) | 1% |
| Reranked, 10 paced users | 35% |
| Reranked, 50 paced users | 6.2% |

The reranked rates are from `LOAD_TEST.md` §5b. On the live host (rehearsal host, before the Week 2 deploy), one slow
rerank opened the circuit and 35 of the next 60 turns ran unreranked (D41).
On `main` the circuit now closes itself after 60 s or 10 skipped turns; at
demo load on the host it opened, skipped 10 turns and closed
(`docs/w2/DEPLOY.md` §8). In the three post-deploy questions every rerank ran,
and none was over budget, busy or skipped (§8.0).

**Read it as:** "0/30 at zero recall" holds only when the reranker runs.
Under load it mostly does not.

## W2-8. The eval gate: does it catch a regression, and what does it not score?

**Claims.** A change that breaks a Week 2 invariant cannot merge green.

**Measured.** The planted-regression harness applies 22 literal regressions
to copies of the tree, runs the gate on each, and reads the catching case
from the run's own JSON (`python -m evals.w2.gate.planted_regressions`).

**Current** (`docs/w2/EVAL_GATE_EVIDENCE.md`):

- The control run passes (exit 0), and **22/22 planted regressions block**
  (exit 1).
- 98 specs: **81 active, 15 pending, 2 delegated** to the PHP CI job.
  Delegated cases are scored against the job's own result: it must have run
  and passed the named tests for the commit under test.
- Deterministic tier 75/75, judge tier 8/8. W2-L1 is the prompt
  pin: any edit to a prompt's value blocks (`EVAL_GATE.md`, "Prompt changes").
- PHI-in-logs (category J) is 8/8, including a positive control that proves
  the scanner detects a planted canary.

**What it does not score yet:**

| Not scored | Why it matters |
|---|---|
| Refusal of prompt injection inside a PDF (W2-I5), and W2-I4 | pending. W2-I1, W2-I2 and W2-I3 are active for **containment only**; I3 shows an injection cannot redirect a chart write, because the patient is a caller argument: the payload reaches no log, trace, extraction output or disclosure. Their extraction bypasses the vision model, so nothing yet tests whether a model that reads the payload declines it |
| W2-H1 | dosing refusal prose, which needs the judge on real output |
| W2-C7 to W2-C9 | box tolerance on scans, and the D24 policy asserted from the gate |
| The judge's agreement with human labels | the judge is **UNCALIBRATED** (D43, D48): its eight meta-check fixtures detect a judge that has drifted, not whether it was right to begin with. The first independent measurement (D61 addendum, blind set 2) is **82% (14/17), Wilson 95% 59–94%, kappa 0.64**: too small to calibrate on, so the label stays |

**It has blocked a real merge request.** [MR !1](https://labs.gauntletai.com/paawandesai/openemr-clinical-copilot/-/merge_requests/1), left open with a red
pipeline, plants "blank flag means normal" and is blocked by W2-D2 under
three rules at once. How to run the gate, read a verdict and set up CI:
[`EVAL_GATE.md`](EVAL_GATE.md).

## W2-9. Cost and latency per encounter

**Claims.** A day of encounters is affordable and fits between patients.

**Measured.**

- Cost comes from `llm_usage_event` tokens × published price, and per turn
  from `w2_ask_turn_completed.llm_cost_usd`.
- Latency comes from `http_request_event` and `step_event`
  (`OBSERVABILITY.md`), and per turn from `w2_ask_turn_completed.total_ms`.

**Current.**

- **Cost: $0.82 per encounter, measured**, in both of two real-route runs.
  Each encounter was an upload with live extraction, a brief, a conversation
  turn and two `/w2/ask` questions: 67,967 prompt + 19,278 completion =
  87,245 tokens (`OBSERVABILITY.md` §2; trace header in
  `docs/w2/evidence/screenshots/langfuse-encounter-trace.png` on that
  branch).
- **On the deployed host:** [the dashboard computed from the deployed host's
  logs](docs/w2/evidence/host-dashboard.html) ([JSON](docs/w2/evidence/host-dashboard.json)),
  beside the local Langfuse screenshots above.
  - Production telemetry is **stdout plus a self-hosted Langfuse inside our
    own infrastructure** (D58, `docs/w2/OBSERVABILITY.md` §7). That is safe
    because D56 and D58 took the patient uuid and chart-record uuids out of
    every emitted event, so the Langfuse we host holds none; this was
    measured (D58).
  - The numbers here come from the `ea198c8` host's stdout log, before
    export was on. The host has exported to the self-hosted Langfuse since
    the 2026-09-25 redeploy (`docs/w2/evidence/prod-langfuse/deploy-2026-09-25/`).
  - Over the `ea198c8` deploy, 2026-09-23 17:12–21:00 UTC: 19 encounters
    with model calls, mean **$0.097** and 11.8k tokens per encounter,
    answer-route p95 21.7 s, 0 5xx.
- **Answer latency tracks what the model writes**
  (`docs/w2/COST_AND_LATENCY.md` §4.1a).
  - The answer step is about **4.1 s + 7.45 s per 1,000 output tokens**
    over six cases from 461 to 24,762 tokens (R² = 0.9998, estimated from
    `LOAD_TEST.md` §7).
  - **Before D50,** a whole-report answer wrote all 91 citations, ~11.5k
    tokens, in **88.2–91.8 s** for about **$0.45** an answer (answer step,
    n = 3, `LOAD_TEST.md` §7). That was over the 90 s budget. Inside the
    measured $0.82 encounter the same question, on a different run, cost
    **$0.4628** (`LOAD_TEST.md` §6); the two are separate measurements, not
    one figure rounded two ways (`docs/w2/COST_AND_LATENCY.md` §2.1).
  - **After D50,** on the host, the same question answers in **6.9–8.4 s**
    end to end on 22 rows and 12.0–13.1 s on 44, from 368–461 output tokens
    on 22 rows, for about $0.084 (`DEPLOY.md` §8.00).
  - **Before D50, from the chart,** one analyte plus one guideline topic made
    **8–12 citations in 17.2–19.5 s**, client wall clock (`docs/w2/evidence/php-wiring-e2e-run.md`).
  - Every run used one model, so this says nothing about other models.
  - `LOAD_TEST.md` §6 timed the whole-report question, before D50, as a whole
    HTTP request: 91.9 s and 130.5 s. The 130.5 s run is unexplained.
  - A guideline question is **10.5–10.6 s** (`LOAD_TEST.md` §6).
- Extraction is 15–33 s per document, and it runs at upload (D7).
- A Week 1 conversation turn is p95 6.41 s locally at 10 users.
- **On the live host at 10 users** (`main@d61f739`, `LOAD_TEST.md` §8): 125
  conversation turns in 3 minutes, **0 errors**, p50/p95/p99
  **13.25/19.47/23.58 s**, the model about 90% of the wait; agent peak
  **818 MiB** (18% of its cap); **$11.13** for the 125 model calls
  ($0.089 a turn). This is the one host load test; it drove the
  conversation route, not uploads or `/w2/ask`.

**This metric passes on the host after D50, one question at a time**
(`DEPLOY.md` §8.00).

- Before D50 it failed for whole-report questions, because the answer cited
  every extracted result.
- **Still at the edge:** "Show me all the results" on a 44-row report is
  43.4 s p50, at the 45 s alert (`LOAD_TEST.md` §7).
- **Not measured on the host:** load on uploads and on `/w2/ask`, and the
  document + guideline question after D50.

**On the deployed host at `21f1053`, before D50** (`DEPLOY.md` §8.0), one document at a time:

| Measure | Value |
|---|---|
| Document + guideline question (before D50) | 15.3–21.2 s end to end, about **$0.178** a question |
| Vision, lab page | 27.8–39.8 s and $0.11–0.13 |
| Vision, intake page | 8.3–15.0 s and about $0.05 |
| Upload → complete, as the card sees it | 20.8 s (clean intake), 35.3 s (scanned intake), 42.2 s (scanned lab) |
| Per encounter, adding Week 1's brief + 4 turns | an estimated **$0.54–1.06** |

Locally, upload to extraction complete was 34–35 s on a 1-page text-layer
report (`docs/w2/evidence/php-wiring-e2e-run.md`).

Derivations, projections and sources:
[docs/w2/COST_AND_LATENCY.md](docs/w2/COST_AND_LATENCY.md).

**Budget.** 90 s between encounters (Week 1 Metric 4). The answer-latency
alert fires at p95 > 45 s (`ALERTS.md` §1).

---

## Metrics considered and left out

- **Mean extraction confidence.** Confidence is
  `reader_confidence × tier_weight × match_score` (D35 item 7). It moves with
  the located rate, which is the number with a meaning; shown beside W2-1,
  it would add no information.
- **Judge pass rate on system output.** The judge is uncalibrated. A rate
  from an instrument with unknown agreement is not defensible to anyone
  (D43).
- **A pooled located rate across all pages as a headline.** 84.5% sounds
  like a grade and hides a 34% scan behind 98% clean pages. W2-1 shows the
  two separately instead.
