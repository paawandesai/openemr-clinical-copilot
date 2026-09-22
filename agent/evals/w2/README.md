# Week 2 eval gate

One command, and its exit code is the verdict:

```bash
cd agent && python -m evals.w2.gate              # 0 pass · 1 blocked · 2 the gate could not run
cd agent && python -m evals.w2.gate --det-only   # deterministic tier only: no credential, no model call
cd agent && python -m evals.w2.gate -v           # print every assertion, not only the failures
```

Nothing parses the output to decide anything. CI runs the first form; the
pre-push hook (`scripts/install-w2-pre-push-hook.sh`) runs the second.

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
D-NEXT (a).

## Case status

`active` — executes today against the contracts and the doc-factory samples.
`pending_sut` — fully specified, waiting on the supervisor graph. A pending
case is **never** a pass. It is reported in its own block and it still counts
towards `n_min`, so a branch cannot make room by deleting the ones it has not
built yet.

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
detects a judge that has *drifted* — and, measured rather than assumed, only
on the `safe_refusal` side. `judge_config.json` `known_limitation` has the
numbers.

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
