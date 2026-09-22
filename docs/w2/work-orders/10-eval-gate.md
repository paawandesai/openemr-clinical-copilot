WORK ORDER 10: eval gate, part 1. Branch feat/w2-eval-gate. Do not merge.
This is a DELIBERATE gate session (started with W2_GATE_SESSION=1);
it is the only session allowed to write agent/evals/w2/{cases,rubrics},
baseline, judge config, and .gitlab-ci.yml. STEP 0: confirm the
variable is set; if not, STOP.

READ FIRST: CLAUDE.md, docs/w2/DECISIONS.md (D6, D20, D22), the
contracts (agent/app/models/w2/), the factory (agent/evals/w2/factory/),
and docs/w2/EVAL_DESIGN.md — the research-backed design this work
order implements. Follow it unless this work order overrides it.
New decisions: append as "D-NEXT" (number assigned at merge).

OVERRIDES TO THE DESIGN DOC
- Case ids are prefixed W2- (W2-A1, W2-G7…) because Week 1 already
  uses categories A-I.
- Total = 81 specs: the doc's 78 (A6 B10 C6 D7 E8 F8 G9 H9 I4 J6 K5)
  plus its three gap additions: W2-G7 (lab-result cap must not drop a
  panic value; cap notice present), W2-F6 (quote_or_value is a
  substring of the cited source text/chunk), and a judge meta-check
  set (frozen known-pass/known-fail fixtures the judge must grade
  correctly).
- Judge model: a DIFFERENT CLAUDE TIER from the system-under-test
  model (not another vendor — the architecture claims a BAA-covered
  provider). Record the self-preference risk and that the meta-check
  set is the mitigation.

GOAL
The gate machinery end to end, plus every case that can execute
against components that exist today. Cases needing the not-yet-built
graph are fully specified but marked pending.

1. Case format under agent/evals/w2/cases/: id, category, critical
   (bool), status (active | pending_sut), inputs (factory scenario id /
   fixture refs / question), expected, assertions (each: rubric in
   {schema_valid, citation_present, factually_consistent,
   safe_refusal, no_phi_in_logs, plus design-doc sub-rubrics}, tier
   det|judge, check id), guards_against (the failure mode, in one
   line). Validate case files against a schema.

2. Rubric checkers as a library, per the design doc: contract/schema
   validity; citation shape + source_type separation + F6 substring
   rule; locate results; abnormal-flag cross-check with D20 semantics
   (blank = not_reported, never normal; agreement computed only over
   COMPARABLE_REPORTED_FLAGS, denominator reported); discrepancy kinds
   (rendering=derived is never a discrepancy); PHI canary scanner over
   every captured log/trace field incl. retrieval queries and error
   messages, with a no-canary control; idempotency row-delta +
   payload-mismatch rejection; routing trajectory assertions (nodes
   run, bounded visits, reason codes). Judge tier: pinned model +
   version, temperature 0, best-of-3 majority, cache keyed on (case id,
   sha256(output), judge prompt version, model version). Judge
   calibration (100-200 labelled examples per judge) is OUT of scope;
   mark judge rubrics UNCALIBRATED in outputs.

3. Gate logic, one command, exit code = verdict, in this order:
   a. per-category minimum-n (nMin = the category's spec count) over
      ACTIVE + PENDING specs, so deleting cases fails the gate;
   b. deterministic tier: 100% of active cases;
   c. critical-case override: any previously passing critical case now
      failing blocks, regardless of aggregates (critical set per the
      design doc: all of H, I3, I4, C3, D2, E1, E5, H5, all J, K1, K2,
      plus G7);
   d. judge tier: category threshold + Wilson 95% lower-bound floor;
      a verdict flips from baseline only on a 2/3 majority;
   e. the assignment's literal rule: >5% regression vs baseline or
      below threshold, per category and aggregate.
   Baseline generated and committed by this session. Pending cases are
   reported separately and never count as passes.

4. Active now: everything executable against contracts + factory
   today — W2-A (schema/citation shape), W2-D (incl. D2 blank-flag and
   D5 panic value at the contract level), W2-E (derived-not-discrepancy
   and discrepancy kinds via contract types), W2-F shape/source_type/F6,
   W2-J scanner cases with control, W2-K idempotency-key composition
   and payload-mismatch at the contract level, the judge meta-check
   fixtures. Everything else: full spec, status pending_sut.

5. Planted-regression proof: a script that applies each regression in
   the design doc's coverage map that is testable today (at least:
   blank flag -> normal; strip a citation field; mark an unlocated
   value presentable; treat a derived rendering as a discrepancy;
   break the idempotency-key composition; weaken the judge prompt vs
   the meta-check set) on a scratch copy, runs the gate, shows it FAILS
   and names the catching case, then passes on the clean tree. Save the
   output under docs/w2/ as CI evidence. List coverage-map regressions
   not yet testable and which pending case will catch each.

6. CI: .gitlab-ci.yml with an eval job tagged w2-eval, image
   python:3.12-slim, installing requirements.txt +
   requirements-dev.txt, running the gate; triggers on merge requests
   and main. The judge tier reads ANTHROPIC_API_KEY from a CI/CD
   variable (tell me to set it MASKED, NOT PROTECTED, so MR pipelines
   see it); with the variable absent, skip the judge tier with a
   visible notice (never a silent pass). Add a pre-push hook installer
   that runs the deterministic tier locally. Push THIS branch (not
   main) to the GitLab remote and show the pipeline result; tell me to
   enable "Pipelines must succeed" under Settings -> Merge requests.

ACCEPTANCE: gate passes on this branch; every planted regression fails
it (outputs shown, catching case named); the GitLab pipeline for this
branch runs on runner #235 and passes; unit suite passes; a table of
active vs pending specs per category totalling >= 81. /report. STOP.
Do not merge.