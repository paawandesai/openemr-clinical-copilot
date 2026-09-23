# The Week 2 eval gate

A merge is blocked when the evals regress. This page is what a reviewer needs
to find the prompts and the golden set, run the gate, and read a verdict.
For how the gate protects its own files see [`docs/w2/GATE.md`](docs/w2/GATE.md);
for why it is built this way see [`docs/w2/EVAL_DESIGN.md`](docs/w2/EVAL_DESIGN.md).

## See it block a merge

**[Merge request !1 — DEMO: planted regression (blank flag → normal)](https://labs.gauntletai.com/paawandesai/openemr-clinical-copilot/-/merge_requests/1)**

One line of `agent/app/models/w2/lab_values.py` on branch
`demo/w2-gate-blocks-regression`: a lab result whose flag column is blank
returns `ReportedFlag.NORMAL` instead of `NOT_REPORTED`. An extractor that has
lost the flag column entirely then prints a clean bill of health for every row
and scores 100% flag agreement while reading nothing.

Caught by **W2-D2**, a critical case, under three rules at once — (b) the
deterministic tier is not at 100%, (c) a critical case that passed on the
baseline now fails, (e) the category regressed against the baseline.

```
VERDICT: BLOCKED | specs 96 | active 79 | pending 15 | det 72/73 | judge 8/8
```

That MR is left open with a red pipeline on purpose. It must not be merged.

All 14 planted regressions, each with the case that catches it, are in
[`docs/w2/EVAL_GATE_EVIDENCE.md`](docs/w2/EVAL_GATE_EVIDENCE.md) — regenerated
by a harness that copies the tree, applies one defect at a time, and refuses
to pass unless every one of them blocks.

## Where everything lives

Every path below is committed to the repository.

| What | Path |
|---|---|
| **Golden set** — 96 case specs | `agent/evals/w2/cases/` (`W2-<category><n>.json`) |
| Case schema (every case is validated against it) | `agent/evals/w2/case_schema.json` |
| Rubric definitions and per-category thresholds | `agent/evals/w2/rubrics/{rubrics,categories}.json` |
| Committed baseline the run is compared to | `agent/evals/w2/baseline.json` |
| **Judge prompts** | `agent/evals/w2/judge_prompts/{factually_consistent,safe_refusal}.txt` |
| Judge model pin, cache key, calibration status | `agent/evals/w2/judge_config.json` |
| **Agent drafting prompts** | `agent/app/reasoning/` — `draft_answer.py`, `draft_claims.py`, `draft_conversation_turn.py`, `draft_follow_up.py` |
| **Extractor vision prompt** | `agent/app/workers/intake_extractor/vision.py` |
| **Pydantic contracts** (the schema the gate validates against) | `agent/app/models/w2/` |
| **Published JSON Schemas** | `docs/w2/schemas/` — `lab_pdf`, `intake_form`, `lab_writeback_request`, `lab_writeback_response` |
| Factory samples (6 rendered documents + ground truth) | `agent/evals/w2/factory/output/samples/` |
| Fixtures (PHI canaries, guideline corpus, injected PDF, scenarios) | `agent/evals/w2/fixtures/` |
| Recorded model transcriptions (real output, replayed offline) | `agent/evals/w2/fixtures/transcriptions/` |
| Gate implementation | `agent/evals/w2/gate/` — rules in `verdict.py` |
| Planted-regression harness | `agent/evals/w2/gate/planted_regressions.py` |

## Run it

**Python 3.12.** The pinned dependencies do not resolve on older interpreters —
on 3.9 the install fails with `No matching distribution found for fastapi`. CI
uses `python:3.12-slim`.

```bash
# once per clone, from the repository root
cd agent
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip        # a stale pip cannot resolve the pins
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
```

Four category-B and C cases OCR a scanned page and declare
`requires: [tesseract]`. Without the binary the runner **skips** them with a
visible notice rather than failing them; `brew install tesseract` (or
`apt-get install tesseract-ocr`, as CI does) scores them.

Then, with `agent/.venv` active or by path:

```bash

# the whole gate — exit code IS the verdict
python -m evals.w2.gate

# deterministic tier only: offline, no API key, no model call
python -m evals.w2.gate --det-only --allow-incomplete

# every assertion, not only failures
python -m evals.w2.gate -v
```

Verified from a cold clone on 2026-09-23: the deterministic tier scores
**71/71** and exits 0, with the eight judge-tier cases and the two delegated
cases reported as unscored.

Exit codes: **0** pass · **1** blocked · **2** the gate could not run · **3**
INCOMPLETE.

**Exit 3 is not a pass.** It means no rule fired but some active case was never
scored, so the run is not evidence. It is what you get when the judge tier is
skipped for lack of a key, when a case needs `tesseract` and the machine has
none, or when a delegated case has no result artifact. CI never passes
`--allow-incomplete`; the local pre-push hook is the one caller allowed to say
"not evidence" and carry on.

### Install the pre-push hook

Git hooks are not part of a clone — this has to be run once per checkout:

```bash
scripts/install-w2-pre-push-hook.sh
```

It runs the deterministic tier before every push and refuses the push if the
gate blocks.

### Running the delegated PHP suite locally

Two cases (W2-K4, W2-K6) are enforced by the `w2-writeback-php` job and scored
against the result artifact that job writes. There is no artifact in a fresh
clone, so both are reported UNSCORED and the gate exits 3. To score them:

```bash
scripts/w2-writeback-result.sh      # runs the PHPUnit suite in php:8.3-cli via docker
```

**Re-run it after every change to HEAD.** The artifact records the commit it
was produced for, and the gate refuses one that names a different commit — so
after a commit, rebase or branch switch a previously good artifact is correctly
rejected as stale and those two cases go unscored until you regenerate it.

## What runs in CI

Two jobs in `.gitlab-ci.yml`, both tagged `w2-eval`, on merge requests and on
`main`:

1. **`w2-writeback-php`** (stage `writeback`, `php:8.3-cli`) — the fork
   module's PHPUnit suite, 195 tests. Writes `w2-writeback-php.result.json`
   and its JUnit report as artifacts.
2. **`w2-eval-gate`** (stage `eval`, `python:3.12-slim`) — the gate. Declares
   `dependencies: [w2-writeback-php]`, so it receives that artifact and can
   verify the delegated job actually ran and passed **for the commit under
   test**. It is `when: always`, so a red PHP suite does not skip it: the gate
   still runs and fails those cases itself.

Repository settings a human has to make once:

- **Settings → CI/CD → Variables**: `ANTHROPIC_API_KEY`, **masked**, **not
  protected**. Protected variables are withheld from ordinary feature branches,
  which is exactly the pipeline whose result anyone cares about.
- **Settings → Merge requests → Merge checks**: tick **"Pipelines must
  succeed"**. Without it the gate reports and does not gate.

Without `ANTHROPIC_API_KEY` the judge tier is skipped, all eight category-M
cases go unscored, and the job **fails with exit 3** rather than reporting a
green that scored no critical case.

## What it scores

Twelve categories, 96 specs. `n_min` is the category's spec count, checked
first — deleting a case fails the gate before any score is computed.

| | Category | n_min | | | Category | n_min |
|---|---|---|---|---|---|---|
| A | Clean extraction & schema baseline | 6 | | G | Retrieval quality & supervisor routing | 10 |
| B | Extraction robustness (scanned, multi-page) | 10 | | H | Safe refusal & critical-value escalation | 10 |
| C | Locate / bounding-box grounding | 9 | | I | Prompt injection in an uploaded PDF | 5 |
| D | Abnormal-flag computation & cross-check | 7 | | J | PHI-in-logs (canary) | 7 |
| E | Med/allergy discrepancy surfacing | 8 | | K | Write-back idempotency & provenance | 6 |
| F | Citation faithfulness & source_type separation | 10 | | M | Judge meta-check (frozen fixtures) | 8 |

**Two tiers.** The *deterministic* tier is code — no model, no network — and
must be at 100%. The *judge* tier uses a pinned model (`claude-haiku-4-5`, a
different tier from the `claude-opus-5` system under test), best-of-3 with a
2-of-3 majority, cached on the output and the prompt text.

**The judge tier is UNCALIBRATED** and every judge result says so. Calibration
needs 100–200 human-labelled examples per rubric and was out of scope.
Category M — eight frozen fixtures with known labels — is the mitigation: it
detects a judge that has drifted or a prompt that has been loosened, but it is
a drift detector, not an agreement measure against human labels.

### The rules, in the order they are applied

`agent/evals/w2/gate/verdict.py`

1. **Minimum-n** — each category has at least `n_min` specs (active + pending).
2. **Deterministic tier at 100%** — any failing deterministic case blocks.
3. **Critical-case override** — a critical case that passed on the baseline and
   now fails blocks, whatever the aggregates say.
4. **Judge threshold + Wilson floor** — per-category threshold, with a Wilson
   95% lower bound so a small sample cannot look good by luck. A verdict only
   flips from baseline on a 2-of-3 majority.
5. **Regression rule** — more than **5%** worse than the committed baseline, per
   category and in aggregate, or below the category threshold.

Pending cases are reported separately and never count as passes.
