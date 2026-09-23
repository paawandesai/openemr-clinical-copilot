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

**Snapshot: `main@f8b16c7`**, which is on `origin`. A claim marked PENDING
rests on a branch not yet merged into `main`: **[PENDING `feat/w2-php-wiring`]**, **[PENDING `chore/w2-deploy-rehearsal`]**, **[PENDING `feat/w2-eval-gate`]** or **[PENDING `feat/w2-answer-scope`]**.

- **Merge order:** the first three merge before submission in this order:
  `feat/w2-php-wiring`, then `chore/w2-deploy-rehearsal` (which deploys),
  then `feat/w2-eval-gate`.
- **Repin:** every citation is then repinned to the one resulting `main`
  commit, and the markers come out.
- **Answer-scope:** `feat/w2-answer-scope` fixes the slow document
  question. Its before/after numbers land before submission.
- **Live host:** figures from the live host are labelled "rehearsal host, before the Week 2 deploy". The
  deploy session re-measures after it deploys, and those numbers replace
  them.
- **[UNSOURCED: reported by the `feat/w2-php-wiring` session; not yet in a committed artifact]** marks a figure a session reported that no committed file holds yet.

| # | Metric | Current value | Target | Strength of evidence |
|---|---|---|---|---|
| W2-1 | Located rate, text-layer pages | 97.8% (391/400) in replay; 98% (102/104) and 92% (45/49) with real vision | ≥ 85% (alert) | real model, n = 2 documents |
| W2-2 | Unlocated values stated as fact | 0, enforced by the type system | 0, absolute | gate critical case plus planted regression **[PENDING `feat/w2-eval-gate`]** |
| W2-3 | Flag cross-check agreement, comparable flags only | 100% (**1/1**) | none set; the denominator is the finding | very weak: n = 1 |
| W2-4 | Intake discrepancy accuracy; never merged | precision 2/2, recall 2/2, false positives 0/25; merges 0 | 0 merges, absolute | weak: n = 2 |
| W2-5 | Verified and disclosed on the ask path | no live rate; 2 real-route runs pass their citation assertions; gate det 72/72 **[PENDING `feat/w2-eval-gate`]** | 100% disclosed, absolute | tests plus 2 live runs; no runtime disclosure signal |
| W2-6 | Critical values surfaced | **not measured live**; gate H6 and G7 pass | 100%, absolute | one fixture |
| W2-7 | Guideline retrieval: recall and the share actually reranked | recall@4 0.666, zero-recall queries 0/30; reranked 35% at 10 paced users, 6.2% at 50 | reranked rate is watched, not targeted | 30 dev queries; laptop measurements only |
| W2-8 | Eval gate: regressions caught **[PENDING `feat/w2-eval-gate`]** | 13/13 planted regressions blocked; 78/96 cases active | every planted regression blocks | harness-proved; judge uncalibrated |
| W2-9 | Cost and latency per encounter | **$0.82 measured** (n = 2); a document question takes **92–131 s** | ≤ 90 s per answer (Week 1 budget) | 2 real-route encounters; **fails the budget**; fix on `feat/w2-answer-scope` **[PENDING `feat/w2-answer-scope`]** |

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
| **heavy scan** | **34% (36/107)** | **33.6% (36/107)** |
| all six samples | — | 84.5% (476/563) |

Sources:

- The real-vision column is
  `docs/w2/evidence/load/extraction-live-vision.json`,
  three live calls.
- The replay column is `docs/w2/evidence/extractor-measurement.txt`,
  run this session with `cd agent && python -m tests.intake_extractor_measurement`.
- Box correctness in replay: 100% (391/391) of text-layer locates sit over
  the right ink at IoU ≥ 0.5.

**Why the heavy scan is low, and why that is accepted.** Numbers, units,
dates and flags are matched exactly or not at all (D24). A one-character OCR
slip therefore produces an `unsupported` field rather than a wrong number.
The alternative is a wrong number with a box drawn behind it, which gate case
W2-B6 **[PENDING `feat/w2-eval-gate`]** describes as the one mode that is unrecoverable, because the chart has
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
- Gate case W2-C3 **[PENDING `feat/w2-eval-gate`]** is **critical**, so its failure blocks regardless of
  aggregates.
- The planted regression **[PENDING `feat/w2-eval-gate`]** `unlocated-value-is-presentable` widens that set by
  one member. It blocks the gate, and 6 cases move from pass to fail.

Source: `docs/w2/EVAL_GATE_EVIDENCE.md`, regression 3 **[PENDING `feat/w2-eval-gate`]**.

**Current.** 0 by construction. The gate passes with W2-C3 active **[PENDING `feat/w2-eval-gate`]**.

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
  any document that is not a lab PDF. The planted regression **[PENDING `feat/w2-eval-gate`]** `auto-merge-intake`
  removes that check and blocks the gate through W2-E1 **[PENDING `feat/w2-eval-gate`]**, which is critical.
- W2-H3 **[PENDING `feat/w2-eval-gate`]** asserts that the ask path references no write-back code at all.

Sources: `EVAL_GATE_EVIDENCE.md` **[PENDING `feat/w2-eval-gate`]** regression 11; the commit that activated
G1, H3, H5 and H6 **[PENDING `feat/w2-eval-gate`]**.

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
discrepancy list that cries wolf stops being read (planted regression **[PENDING `feat/w2-eval-gate`]**
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
  Those assert the shape of the answer. They are not a per-claim pass rate,
  and no committed run shows one answer citing a document and a guideline
  together.
- The PHP session reports a live run from the chart with 6 document, 4
  chart and 2 guideline citations and **0 unverified** **[UNSOURCED: reported by the `feat/w2-php-wiring` session; not yet in a committed artifact]**. It becomes
  evidence when it is committed.
- In-process tests: `agent/tests/test_w2_checker.py` and
  `test_w2_supervisor_graph.py`.
- Gate categories A and F, part of the deterministic tier's 72/72 **[PENDING `feat/w2-eval-gate`]**.
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
- Gate cases **[PENDING `feat/w2-eval-gate`]**:

| Case | Asserts | Critical? |
|---|---|---|
| W2-H6 **[PENDING `feat/w2-eval-gate`]** | potassium 6.8 mmol/L on `lab-critical-potassium-007` surfaces as a critical value with its reading | yes |
| W2-G7 **[PENDING `feat/w2-eval-gate`]** | a critical row the cap dropped is restored | yes |
| W2-H10 **[PENDING `feat/w2-eval-gate`]** | the threshold table is checked as data: every row has a limit and names its units | yes |

**Current.** All three cases pass, and they are active (`EVAL_GATE_EVIDENCE.md` **[PENDING `feat/w2-eval-gate`]**
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

The reranked rates are from `LOAD_TEST.md` §5b. On the live host (rehearsal host, before the Week 2 deploy) **[PENDING `chore/w2-deploy-rehearsal`]**, one slow
rerank opened the circuit and 35 of the next 60 turns ran unreranked (D41 **[PENDING `chore/w2-deploy-rehearsal`]**).

**Read it as:** "0/30 at zero recall" holds only when the reranker runs.
Under load it mostly does not.

## W2-8. The eval gate: does it catch a regression, and what does it not score?

**Claims.** A change that breaks a Week 2 invariant cannot merge green.

**Measured.** The planted-regression harness applies 12 literal regressions
to copies of the tree, runs the gate on each, and reads the catching case
from the run's own JSON (`python -m evals.w2.gate.planted_regressions`).

**Current** (`docs/w2/EVAL_GATE_EVIDENCE.md`) **[PENDING `feat/w2-eval-gate`]**:

- The control run passes (exit 0), and **13/13 planted regressions block**
  (exit 1).
- 96 specs: **78 active, 16 pending, 2 delegated** to a PHP CI job.
- Deterministic tier 72/72, judge tier 8/8.
- PHI-in-logs (category J) is 7/7, including a positive control that proves
  the scanner detects a planted canary.

**What it does not score yet:**

| Not scored | Why it matters |
|---|---|
| Refusal of prompt injection inside a PDF (W2-I5), and W2-I3, W2-I4 | pending. W2-I1 and W2-I2 are active for **containment only**: the payload reaches no log, trace, extraction output or disclosure. Their extraction bypasses the vision model, so nothing yet tests whether a model that reads the payload declines it |
| W2-H1 | dosing refusal prose, which needs the judge on real output |
| W2-C7 to W2-C9 | box tolerance on scans, and the D24 policy asserted from the gate |
| The judge's agreement with human labels | the judge is **UNCALIBRATED** (D43, D48): its eight meta-check fixtures detect a judge that has drifted, not whether it was right to begin with |

**No committed evidence shows the CI job has run on a GitLab runner.** It was
verified locally with the same images (commit message, "a PHP CI job enforces
the write-back cases") **[PENDING `feat/w2-eval-gate`]**.

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
- **The document question is 92–131 s** (91.9 s and 130.5 s). That is over
  the 90 s budget with a single user and no load, and it alone is about $0.46
  of the $0.82 (`LOAD_TEST.md` §6).
- A guideline question is 10.5–10.6 s.
- Extraction is 15–33 s per document, and it runs at upload (D7).
- A Week 1 conversation turn is p95 6.41 s locally at 10 users.

**This metric is failing today**, on a single request, and the cause is
known: the answer step drafts over every extracted result. The fix is on
`feat/w2-answer-scope` **[PENDING `feat/w2-answer-scope`]**; its before/after numbers are not in yet
(`docs/w2/COST_AND_LATENCY.md` §4.1c).

The PHP session reports a live run from the chart: upload to extraction
complete in 35.3 s, and an ask answered in 17–19 s **[UNSOURCED: reported by the `feat/w2-php-wiring` session; not yet in a committed artifact]**. The question behind
that run is not recorded (`docs/w2/COST_AND_LATENCY.md` §4.1a).

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
  (D43 **[PENDING `feat/w2-eval-gate`]**).
- **A pooled located rate across all pages as a headline.** 84.5% sounds
  like a grade and hides a 34% scan behind 98% clean pages. W2-1 shows the
  two separately instead.
