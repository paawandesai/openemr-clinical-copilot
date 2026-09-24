# The Week 2 eval gate

A merge is blocked when the evals regress. This page is what a reviewer needs
to find the prompts and the golden set, run the gate, and read a verdict.
For how the gate protects its own files see [`docs/w2/GATE.md`](docs/w2/GATE.md);
for why it is built this way see [`docs/w2/EVAL_DESIGN.md`](docs/w2/EVAL_DESIGN.md).

**Where it blocks: at push and at merge, not at commit.** A `git commit`
that regresses the evals succeeds; nothing runs on commit, by design.

- **At push:** the pre-push hook (installed once with
  `scripts/install-w2-pre-push-hook.sh`, see below) runs the deterministic
  tier and refuses the push if it fails.
- **At merge:** CI runs both tiers on every merge request, and a failing
  gate turns the pipeline red. GitLab's "Pipelines must succeed" merge
  check then refuses the merge — see the MR below.

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

(Recorded on that MR's pipeline, before W2-L1 and W2-J8 existed — 96 specs then, 98 now.)

That MR is left open with a red pipeline on purpose. It must not be merged.

All 22 planted regressions, each with the case that catches it, are in
[`docs/w2/EVAL_GATE_EVIDENCE.md`](docs/w2/EVAL_GATE_EVIDENCE.md) — regenerated
by a harness that copies the tree, applies one defect at a time, and refuses
to pass unless every one of them blocks.

## Where everything lives

Every path below is committed to the repository.

| What | Path |
|---|---|
| **Golden set** — 98 case specs | `agent/evals/w2/cases/` (`W2-<category><n>.json`) |
| Case schema (every case is validated against it) | `agent/evals/w2/case_schema.json` |
| Rubric definitions and per-category thresholds | `agent/evals/w2/rubrics/{rubrics,categories}.json` |
| Committed baseline the run is compared to | `agent/evals/w2/baseline.json` |
| **Judge prompts** | `agent/evals/w2/judge_prompts/{factually_consistent,safe_refusal}.txt` |
| Judge model pin, cache key, calibration status | `agent/evals/w2/judge_config.json` |
| **Agent drafting prompts** (pinned by W2-L1) | `agent/app/reasoning/` — `draft_answer.py`, `draft_claims.py`, `draft_conversation_turn.py`, `draft_follow_up.py` |
| **Extractor vision prompt and output schema** (pinned by W2-L1) | `agent/app/workers/intake_extractor/vision_schema.py` (`vision.py` sends them) |
| **Prompt pins** — sha256 of every prompt above | `agent/evals/w2/prompt_pins.json`; see [Prompt changes](#prompt-changes) |
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

Five cases OCR a scanned page and declare `requires: [tesseract]`: four in
categories B and C, and W2-H3. Without the binary the runner **skips** them with a
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
cases reported as unscored. That clone predates W2-L1; on
`fix/w2-prompt-pins`, with tesseract installed, the same command scores
**72/72**, and **73/73** once W2-J8 is in (neither re-verified from a cold
clone). With the delegated result artifact present, the deterministic tier is
75/75.

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
clone, so both are reported UNSCORED and the gate exits 3. To score them,
from the repository root:

```bash
# once per clone: a fresh clone has no vendor/, and the script stops without it
docker run --rm -v "$PWD":/app -w /app composer:2 composer install --ignore-platform-reqs --no-scripts

scripts/w2-writeback-result.sh      # runs the PHPUnit suite in php:8.3-cli via docker
```

**On macOS the checkout must be under `/Users`.** Docker Desktop cannot mount
`/tmp` (or `/private/tmp`) into a container, so a clone there fails both
commands above.

**Re-run the result script after any commit, rebase or branch switch.** The
artifact records the commit it was produced for, and the gate refuses one that
names a different commit — so a previously good artifact is correctly rejected
as stale and those two cases go unscored until you regenerate it. (Locally the
gate compares against `git rev-parse HEAD`; before work order 28 it compared
only in CI, and a stale local artifact was silently accepted.)

## Troubleshooting

**`scripts/w2-writeback-result.sh` exits 127.** `vendor/` exists but is
incomplete: `vendor/bin/phpunit` is not there, so the container cannot find
the command (127 is the shell's "command not found"). A missing `vendor/`
exits 2 with its own message instead; a partial one gets this far. The
script still writes `w2-writeback-php.result.json`, with `"exit_code":127,
"status":"failed"`, so if you run the gate now W2-K4 and W2-K6 **fail**
rather than go unscored. Re-run the composer install, then the script:

```bash
docker run --rm -v "$PWD":/app -w /app composer:2 composer install --ignore-platform-reqs --no-scripts
scripts/w2-writeback-result.sh
```

**W2-K4 and W2-K6 are UNSCORED with "is stale: it records commit …".** The
result artifact was produced for an earlier commit. It names the commit it
tested, and a result for another commit is not evidence about this one.
**Re-run `scripts/w2-writeback-result.sh` after every commit, rebase or
branch switch**, then run the gate. Run it on a clean tree: on a dirty one
the script warns that the artifact names a commit that is not what it
tested.

**The judge tier is skipped, and the run exits 3 with "ANTHROPIC_API_KEY is
not set".** The gate reads the key from the environment of the shell that
runs it and nowhere else. It does not read `agent/.env`. Export it in that
same shell, without printing it:

```bash
export ANTHROPIC_API_KEY=...        # in the shell you run the gate from
python -m evals.w2.gate
```

A key exported in another terminal, or in a subshell that has exited, does
not count. `--det-only` needs no key, but it exits 3 as well unless you pass
`--allow-incomplete`, because the eight category-M cases go unscored.

## What runs in CI

Three jobs in `.gitlab-ci.yml`, all tagged `w2-eval`, on merge requests and on
`main`. None has `allow_failure`, so any one of them failing turns the pipeline
red:

1. **`w2-writeback-php`** (stage `writeback`, `php:8.3-cli`) — the fork
   module's PHPUnit suite, 195 tests. Writes `w2-writeback-php.result.json`
   and its JUnit report as artifacts.
2. **`w2-eval-gate`** (stage `eval`, `python:3.12-slim`) — the gate. Declares
   `dependencies: [w2-writeback-php]`, so it receives that artifact and can
   verify the delegated job actually ran and passed **for the commit under
   test**. It is `when: always`, so a red PHP suite does not skip it: the gate
   still runs and fails those cases itself.
3. **`agent-unit-tests`** (stage `eval`, `python:3.12-slim`, `needs: []`, so it
   runs beside the other two) — `python -m pytest tests` in `agent/`, the
   offline unit suite, with tesseract installed so the OCR tests run rather
   than skip. It includes `tests/test_decisions_log.py`, the DECISIONS.md
   numbering guard (D53), and fails if that file was not collected.

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

Thirteen categories, 98 specs. `n_min` is the category's spec count, checked
first — deleting a case fails the gate before any score is computed.

| | Category | n_min | | | Category | n_min |
|---|---|---|---|---|---|---|
| A | Clean extraction & schema baseline | 6 | | H | Safe refusal & critical-value escalation | 10 |
| B | Extraction robustness (scanned, multi-page) | 10 | | I | Prompt injection in an uploaded PDF | 5 |
| C | Locate / bounding-box grounding | 9 | | J | PHI-in-logs (canary) | 8 |
| D | Abnormal-flag computation & cross-check | 7 | | K | Write-back idempotency & provenance | 6 |
| E | Med/allergy discrepancy surfacing | 8 | | L | Prompt pins | 1 |
| F | Citation faithfulness & source_type separation | 10 | | M | Judge meta-check (frozen fixtures) | 8 |
| G | Retrieval quality & supervisor routing | 10 | | | | |

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

## Prompt changes

**Any edit to a prompt fails the gate.** W2-L1 (category L, critical,
deterministic) imports the value of everything the system or the judge
sends a model, 19 values in all, and compares each one's sha256 with
`agent/evals/w2/prompt_pins.json`:

- the four drafting `SYSTEM_PROMPT`s under `agent/app/reasoning/`: the
  Pre-Visit Brief, the Week 2 answer, the follow-up and the conversation
  turn;
- the four drafters' **structured-output schemas**. The brief and follow-up
  schemas are static (`_OUTPUT_SCHEMA`). The answer and conversation-turn
  schemas are built per turn (`output_schema()`), so they are rendered from
  a fixed synthetic turn;
- the four drafters' **user-message templates** (`build_user_content()`),
  rendered from the same synthetic turn. That turn has one source of each
  kind, a lab scope that omits rows and a full page of encounters, so every
  literal line of each template, including `<retrieval_scope>` and
  `<answer_scope>`, appears in what is hashed. It holds no chart data;
- the extractor's vision `SYSTEM_PROMPT`, its structured-output schema for
  both document kinds, and its **user message** for both kinds: the page
  labels and the user instruction ("Transcribe pages … Report only what is
  drawn on these pages."). Under constrained decoding the schema is part of
  the prompt, so it is hashed without sorting keys: property order is
  generation order;
- both judge prompts, as the judge loads them.

It hashes the values, not the source files, so an unrelated edit in the same
module does not trip it. It also fails when a module under `app/reasoning/`
gains a `SYSTEM_PROMPT`, an `_OUTPUT_SCHEMA`, an `output_schema` or a
`build_user_content` that is not pinned, or `judge_config.json` a rubric
that is not pinned. The pin and its checker are gate files
(`agent/evals/w2/prompt_pins.json`, `agent/evals/w2/gate/prompt_pins.py`).
A mismatch fails with the prompt's name and file, and says that a prompt
change requires a gate session to re-validate the golden set and update the
pin. It is free and offline, so it runs in the pre-push hook as well as in CI.
The gate blocks a **push** and a **merge**; nothing blocks a local commit.

**Changing a prompt on purpose** is a gate change, like the baseline: in a
`W2_GATE_SESSION=1` session, edit the prompt, re-run the golden set against
it (the full gate; for the extractor, re-record the transcriptions too), then
`cd agent && python -m evals.w2.gate.prompt_pins --write`, re-seed the
baseline, and commit the pin with the prompt. `scripts/gate-diff.sh` then
flags the branch so a reviewer reads the pin change on its own.

**The pin is the catch-all, not a replacement for the behavioural cases.**
It proves a prompt changed, not whether the change is harmful — a pin that
matches says only that this is the prompt the golden set was last validated
against. The behavioural cases stay. Which prompt edits they also catch, from
[`docs/w2/evidence/prompt-change-check.md`](https://labs.gauntletai.com/paawandesai/openemr-clinical-copilot/-/blob/chore/w2-prompt-change-check/docs/w2/evidence/prompt-change-check.md)
(on `chore/w2-prompt-change-check`, measured on `2d12333`, before the pin):

| Prompt | Caught without the pin? |
|---|---|
| `factually_consistent` judge, loosened | **yes** — W2-M6, 3 of 3 full runs |
| `safe_refusal` judge, loosened | **unreliably** — W2-M5 in 1 of 3 runs; W2-M4 never moved |
| Extractor vision prompt or schema | **as an error, not a case**. The recorded-transcription staleness check stopped the run with exit 2 before any case was scored. It blocked, but named no case, and one edit to the ungated `agent/evals/w2/factory/record_transcriptions.py` switched it off, after which the edit passed. **Both are fixed.** The fingerprint is now computed in gated `agent/evals/w2/gate/samples.py`, and the factory imports it from there. A stale recording now fails the category-B cases that replay it, by name, instead of stopping the run |
| The four drafting prompts | **no** — no gate case sends them to a model, and the judge grades frozen fixtures |

With the pin, every row blocks with W2-L1 named. The planted-regression
harness proves it
([`docs/w2/EVAL_GATE_EVIDENCE.md`](docs/w2/EVAL_GATE_EVIDENCE.md)) for:

- the Pre-Visit Brief and Week 2 answer system prompts;
- the extractor prompt and schema. Each is planted together with the old
  staleness-check bypass, re-aimed at the moved function. Each now blocks
  on W2-L1 **and** on W2-B1–B7, W2-C2 and W2-C6, because the bypass no
  longer reaches anything the gate reads;
- a user-message template: the conversation turn's `<retrieval_scope>`
  claims the model saw every lab result;
- the extractor's user instruction, now asking for a best guess. This one
  needed no bypass at all, because the staleness fingerprint does not cover
  the user message;
- a static schema: the brief's `field` enum dropped;
- a one-line `safe_refusal` loosening, and the two whole-file judge
  replacements that were already there.
