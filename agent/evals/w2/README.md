# Week 2 eval gate

One command, and its exit code is the verdict:

```bash
cd agent && python -m evals.w2.gate              # both tiers
cd agent && python -m evals.w2.gate --det-only   # deterministic tier only: no credential, no model call
cd agent && python -m evals.w2.gate -v           # print every assertion, not only the failures
```

| Exit | Meaning |
|---|---|
| 0 | **PASS** — every active case was scored and no rule fired |
| 1 | **BLOCKED** — a gate rule fired |
| 2 | **ERROR** — the gate could not run at all |
| 3 | **INCOMPLETE** — no rule fired, but a critical case was never scored |

3 exists because 0 was a lie. With the judge tier skipped, no category-M case
is scored and every one of them is critical, so the run would have exited 0
having checked none of the judge meta-checks. A green pipeline that scored no
critical case is the exact failure this gate exists to prevent, so **CI fails
when `ANTHROPIC_API_KEY` is missing** rather than printing a notice.

`--allow-incomplete` maps 3 to 0. Exactly one caller passes it: the local
pre-push hook, which deliberately runs `--det-only` and is not claiming to be
evidence. It sits at the call site so that anyone reading the hook can see the
claim being waived. CI never passes it.

Nothing parses the output to decide anything. CI runs the first form; the
pre-push hook (`scripts/install-w2-pre-push-hook.sh`) runs
`--det-only --allow-incomplete`.

## What is here

| Path | Gated | What it is |
|---|---|---|
| `cases/` | yes | One JSON file per spec. Deleting one is a deletion in the diff. |
| `case_schema.json` | yes | Every case file is validated against it before anything is scored. |
| `rubrics/rubrics.json` | yes | The 17 rubrics: 5 required, 12 sub-rubrics, each with its tier and why. |
| `rubrics/categories.json` | yes | Per category: `n_min`, thresholds, Wilson slack. |
| `judge_config.json` | yes | Pinned judge model and version, prompts, cache key, and what the judge does not cover. |
| `judge_prompts/` | yes | The two judge rubrics, as text. Their sha256 is part of the cache key. |
| `fixtures/` | yes | PHI canary bundles and the frozen judge meta-check fixtures. |
| `baseline.json` | yes | The committed scores a run is compared against. |
| `gate/` | yes | The machinery: loader, checkers, judge, statistics, verdict, runner. |
| `gate/checks_delegation.py` | yes | Checks that a `delegated` case's CI job really enforces it. |
| `factory/` | **no** | The synthetic document factory. Any session may write here. |
| `staging/` | **no** | Where a generator puts cases. Promotion into `cases/` needs `W2_GATE_SESSION=1`. |

## The five rules, in order

1. **per-category minimum-n**, over active **plus** pending specs — so deleting
   a case fails the gate before any score is read;
2. **deterministic tier at 100%** of active cases;
3. **critical-case override** — a critical case the baseline records as passing
   that now fails blocks, whatever the aggregates say;
4. **judge tier** — category threshold plus a 95% Wilson lower-bound floor;
5. the assignment's literal rule — **>5% regression vs baseline, or below
   threshold**, per category and on aggregate.

Rule 3 is the detector and rule 5 is the backstop, not the other way round: on
a 4-to-10-case category one case is worth 10 to 25 percentage points, so a 5%
band is below the resolution of a single case. See `docs/w2/DECISIONS.md`
D35.

## Case status

`active` — executes today, against the contracts, the doc-factory samples or
a merged implementation.

`pending_sut` — fully specified, waiting on something not built. A pending
case is **never** a pass. It is reported in its own block and it still counts
towards `n_min`, so a branch cannot make room by deleting the ones it has not
built yet.

`delegated` — the behaviour is enforced by a named job in the **same
pipeline** that this gate cannot run itself. W2-K4 is the case that forced
it: "a failure mid-write leaves no ledger row and no orphaned ciphertext
file" lives in the fork's PHP module, its proof is PHPUnit, and this gate
runs on `python:3.12-slim`. Calling it `pending_sut` said nobody was checking
it, which was true and is now avoidable; calling it `active` and asserting
some weaker Python-side property would have been a lie.

What the gate scores for a delegated case is that the **delegation is real** —
`gate/checks_delegation.py` checks the job exists in `.gitlab-ci.yml`, is not
`allow_failure`, names the suite on its script, and that every named test
method is still in that file. That is what stops `delegated` becoming a way
to park a case: two planted regressions in the harness prove it, one setting
`allow_failure: true` and one renaming the test away. Neither is visible to
the PHP job — it passes either way — so only the Python gate catches them.

What it cannot prove is that the job passed: the gate runs in one job and
cannot read another's result. The pipeline joins them, which is why the
`allow_failure` check matters more than it looks.

## Adding a case

Generators write to `staging/`. Promoting a staged file into `cases/` is a gate
change and needs a session started with `W2_GATE_SESSION=1` — a human sets that
outside the session. A branch must not be able to fail on a case and delete the
case in the same commit.

A case names its checker by id. `chk.*` and `judge.*` ids must resolve in
`gate/registry.py`; `pending.*` ids have no implementation and may only appear
on a `pending_sut` case. The loader enforces both directions, so a case cannot
quietly score nothing.

## The judge tier

Pinned model and version, thinking off, schema-constrained output, best of
three with a majority of two, and a cache keyed on the case, the output, the
prompt **text** and the model version. With `ANTHROPIC_API_KEY` absent the tier
is skipped behind a loud notice, its cases are reported as not scored, and they
are not passes.

It is **UNCALIBRATED**: it has not been scored against 100–200 human-labelled
examples per rubric, so its agreement with a human is unknown. Category M
detects a judge that has *drifted*, on both rubrics. Every one of its eight
fixtures is **stable** — five independent best-of-3 gradings under the
committed prompt all agreeing with its label — and each rubric has at least
one **discriminating** fixture, which a generic one-line rubric grades the
other way on all five rounds. The rest are alive-checks: they catch a judge
that has stopped working, not one that has been loosened, and they are
labelled as such so nobody reads them as more. Both properties are measured,
never inferred; `judge_config.json` `meta_check_selection` has the numbers and
the two candidates that were rejected.

### What a run costs

Measured on a cold cache with the pinned model (`claude-haiku-4-5`, $1/$5 per
MTok): **24 calls, 15,825 input and 2,593 output tokens, $0.0288.** That is
8 judge-tier cases × best-of-3. The deterministic tier is free and offline.

**The first CI run on any runner is always a cold cache.** The cache lives in
`agent/evals/w2/.judge_cache/`, is gitignored, and is per-machine — it is a
flake and cost control within a run and across reruns on one box, never a
substitute for running the judge. A warm local rerun makes 0 calls. Budget for
a cold run on every CI job unless you add a cache path to the job, and note
that the cache key includes the prompt text, so editing a judge prompt
correctly invalidates it.

## Proving the gate catches anything

```bash
cd agent && python -m evals.w2.gate.planted_regressions --out ../docs/w2/EVAL_GATE_EVIDENCE.md
```

Copies the tree to a temporary directory, applies one regression at a time,
runs the gate there, and reads the catching case out of that run's own JSON by
diffing it against the baseline. It also runs an unpatched copy, because
"always fails" is not a gate either. Needs `ANTHROPIC_API_KEY`; it refuses to
run without one rather than produce evidence that would not mean anything.
The committed output is `docs/w2/EVAL_GATE_EVIDENCE.md`.

## Relation to the Week 1 suite

`agent/evals/` (Week 1) is live: it calls a deployed agent, OpenEMR FHIR and
the real model, and most of it SKIPs without credentials. This gate is the
opposite by design (D6): recorded fixtures, offline deterministic tier, and
one pinned judge. They do not share a harness and neither runs the other.
