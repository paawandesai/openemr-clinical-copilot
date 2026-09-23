# Clinical Co-pilot — Week 2

Week 1 shipped a clinical co-pilot on this OpenEMR fork: a **Pre-Visit Brief**
and a **multi-turn follow-up chat** that read a patient's chart over OpenEMR's
FHIR R4 API under the clinician's own OAuth2 token, draft claims bound to
`source_id` + field + value, and pass each one through a **deterministic code
verifier** before it renders — a claim that fails renders as UNVERIFIED, never
silently dropped. Week 2 adds **lab-PDF and intake-form ingestion** into a strict
schema with page-located values, a **supervisor** over an **intake-extractor**
and an **evidence-retriever**, **hybrid RAG** over guideline evidence, and
**eval-gated CI** that blocks merges on regression against a committed baseline.

**For the OpenEMR platform itself — PHP standards, PHPStan, Doctrine, Twig, the
Docker dev stack, `openemr-cmd`, the PHP test suites — read
[`docs/openemr-dev-guide.md`](docs/openemr-dev-guide.md)** (the former root
`CLAUDE.md`, unchanged) before touching `src/`, `library/`, `interface/`,
`apis/`, `templates/` or `sql/` — W2 touches them: write-back (D1) lives there.

## INVARIANTS — non-negotiable

1. **No raw PHI in logs, traces, or error messages.** Log identifiers and
   counts, never chart content. Every new graph node runs inside the existing
   tracing wrapper (`UntracedGraph`, below) — a node that can execute with a
   LangSmith tracer attached is a defect, not a style issue.
2. **Every clinical claim carries the citation contract:**
   `{source_type, source_id, page_or_section, field_or_chunk_id,
   quote_or_value}` — plus `bbox` when the source is a document.
3. **Patient-record facts and guideline evidence are distinct `source_type`s.**
   Never let a guideline citation stand in for a chart fact, or the reverse.
4. **An extracted value that cannot be located on its source page is flagged
   `unsupported`** and presented as unsupported. It is never rendered as fact
   and never counted as verified.
5. **Intake meds and allergies are never auto-merged into the chart.** They are
   document-derived facts, surfaced as discrepancies against the chart for a
   human to resolve.
6. **Every write to OpenEMR carries an idempotency key.** No exceptions, no
   "this one is read-mostly".
7. **Implementation sessions never edit eval rubrics, eval cases, the eval
   baseline, judge config, or CI config.** These are gate files; a hook blocks
   them. Generating cases is *not* blocked — see "The staging rule" below. If a
   gate file is wrong, say so in your report — do not route around it.

### The staging rule

Generated eval cases do not land in `cases/` by themselves. Generator and
factory code goes in `agent/evals/w2/factory/`, generator output in
`agent/evals/w2/staging/` — neither is gated, write freely. Gated:
`agent/evals/w2/cases/`, `agent/evals/w2/rubrics/`,
`agent/evals/w2/baseline.json`, `agent/evals/w2/judge_config.*`.

**Promoting a staged file into `cases/` is a gate change**, and only a session
started with `W2_GATE_SESSION=1` can do it — a human sets that outside the
session, so that a branch cannot both fail on a case and delete the case in the
same commit. Report what you staged and why it should be promoted; do not
promote it yourself.

Two hooks enforce this, one before and one after every tool call, and **no
symlinks anywhere under `agent/evals/`**. Gated paths, how the hooks work, what
they miss, and the merge check: [`docs/w2/GATE.md`](docs/w2/GATE.md).

## Where things are

| Thing | Path |
|---|---|
| Agent service (FastAPI) | `agent/app/main.py`, routes in `agent/app/api/` |
| Graphs | `agent/app/graph/{pre_visit_brief,conversation,follow_up}.py` |
| **Tracing wrapper** `UntracedGraph` + `_untraced` node guard | `agent/app/graph/pre_visit_brief.py:211` and `:199` |
| **Verifier** (deterministic, no LLM, no network) | `agent/app/verification/checker.py` |
| **W2 ask path**: supervisor graph, routing, verifier extension, route | `agent/app/graph/supervisor.py`, `agent/app/supervisor/`, `agent/app/verification/w2_checker.py`, `agent/app/api/ask.py` (D40) |
| W2 live end-to-end run (costs one model call) | `agent/scripts/w2_e2e_ask.py` |
| Disclosure, claim contract, FHIR models | `agent/app/verification/disclosure.py`, `agent/app/models/claims.py`, `agent/app/models/fhir_resources.py` |
| Structured logging + correlation id | `agent/app/middleware/{logging,correlation}.py` |
| Week 1 eval suite (live, costs model calls) | `agent/evals/` — see `agent/evals/README.md`, harness in `agent/evals/{harness,invariants,oracle}.py` |
| Week 1 unit tests (no network, no credentials) | `agent/tests/` |
| Week 2 evals | `agent/evals/w2/` — see [`agent/evals/w2/README.md`](agent/evals/w2/README.md) |
| **Week 2 gate machinery** (loader, checkers, judge, verdict) | `agent/evals/w2/gate/` — entry point `agent/evals/w2/gate/run.py` |
| Gate rules, in the order they are applied | `agent/evals/w2/gate/verdict.py` |
| Rubric checkers | `agent/evals/w2/gate/checks_{a,c,d,e,f,j,k,m}.py`, `phi_scanner.py` |
| Judge tier (pinned model, best-of-3, cache) | `agent/evals/w2/gate/judge.py`, `agent/evals/w2/judge_config.json` |
| Planted-regression proof, and its committed output | `agent/evals/w2/gate/planted_regressions.py`, `docs/w2/EVAL_GATE_EVIDENCE.md` |
| Eval design research this implements | `docs/w2/EVAL_DESIGN.md` |
| CI pipeline (gate file) — two jobs, both tagged `w2-eval` | `.gitlab-ci.yml`: `w2-eval-gate` (Python) and `w2-writeback-php` (the module's PHPUnit suite) |
| Pre-push hook installer (deterministic tier) | `scripts/install-w2-pre-push-hook.sh` |
| Python tooling config (ruff + pytest) | `agent/pyproject.toml`, `agent/requirements-dev.txt` |
| Gate hook, and the merge check that reads its globs | `.claude/hooks/gate-guard.sh`, `scripts/gate-diff.sh` |
| **The gate in full** — gated paths, both hooks, their limits | `docs/w2/GATE.md` |
| **OpenEMR platform guide** (PHP, Docker, PHPStan, `openemr-cmd`) | `docs/openemr-dev-guide.md` |
| Week 2 decisions (read first), work orders, spikes | `docs/w2/DECISIONS.md`, `docs/w2/work-orders/`, `docs/w2/spikes/` (write-back findings in `writeback.md`) |
| Architecture / metrics / users / audit | `docs/ARCHITECTURE.md`, `docs/KEY_METRICS.md`, `docs/USERS.md`, `docs/AUDIT.md`, `docs/audit/findings.md` |
| **Lab write-back** (D1): endpoint, ledger, PDF preview | `interface/modules/custom_modules/oe-module-clinical-copilot/` — agent side in `agent/app/writeback/` |
| Deployment | `agent/deploy/docker-compose.agent.yml`, `agent/deploy/Caddyfile`, `agent/docs/deployment.md`, `docs/w2/deploy-writeback.md` |
| New worktree for a work order | `scripts/new-worktree.sh` — creates `agent/.venv` and installs requirements |

## Commands

```bash
# Dev tooling, once per environment:
cd agent && python -m pip install -r requirements.txt -r requirements-dev.txt

# Unit tests — no network, no credentials:
cd agent && python -m pytest tests
# ModuleNotFoundError at collection = stale venv; re-run the pip line above.
# `testpaths` pins a bare `pytest` to tests/; naming a path overrides it, so
# `pytest evals` is LIVE: real FHIR + Anthropic, ~36 model calls. Do not loop it.
cd agent && python -m pytest evals
cd agent && python -m pytest evals -k "test_d or test_e"   # auth/not-found only, no model calls

# Format and lint (config: agent/pyproject.toml):
cd agent && python -m ruff format --check .   # --check only
cd agent && python -m ruff check .            # add --fix for the safe fixes
# Week 1 was reformatted in one mechanical commit (.git-blame-ignore-revs); here
# format --check is clean and check reports 21 known findings — add none.

# Pre-merge gate check — run this before EVERY merge:
scripts/gate-diff.sh <branch>       # nonzero if the branch touches gate files
scripts/check-claude-paths.sh       # every path named in this file still exists

# Week 2 eval gate — one command, exit code IS the verdict:
#   0 pass · 1 blocked · 2 the gate could not run · 3 INCOMPLETE (no rule
#   fired, but a critical case was never scored — the run is not evidence).
# Deterministic tier is offline: no credential, no model call. Without
# ANTHROPIC_API_KEY the judge tier is skipped, every critical category-M case
# goes unscored, and the run exits 3 — CI fails rather than reporting green.
cd agent && python -m evals.w2.gate              # both tiers
cd agent && python -m evals.w2.gate --det-only   # deterministic only, ~1s, free; exits 3
cd agent && python -m evals.w2.gate --det-only --allow-incomplete   # ...and 3 -> 0
cd agent && python -m evals.w2.gate -v           # every assertion, not only failures
# Proof that the gate catches anything (copies the tree, plants one regression
# at a time, reruns; needs ANTHROPIC_API_KEY). Output: docs/w2/EVAL_GATE_EVIDENCE.md
cd agent && python -m evals.w2.gate.planted_regressions --out ../docs/w2/EVAL_GATE_EVIDENCE.md
# Run the deterministic tier before every push:
scripts/install-w2-pre-push-hook.sh

# PHP side (needs the OpenEMR container — see docs/openemr-dev-guide.md):
composer phpstan
composer phpcs
```

### Before every merge

`scripts/gate-diff.sh <branch>` exits nonzero if the branch touches gate files —
not a veto, but review that change on its own, before the code. The rest of the
rules, and what the hooks miss: [`docs/w2/GATE.md`](docs/w2/GATE.md).

## Before you start

**Read [`docs/w2/DECISIONS.md`](docs/w2/DECISIONS.md) first. Do not re-decide
anything marked DECIDED; raise conflicts in your report.** OPEN entries are
yours to propose on, not to silently settle; PENDING-SPIKE entries are blocked
on a spike — build against the stated constraint and flag the dependency.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/): `<type>(<scope>): <description>`.
**No AI-attribution trailers of any kind** — no `Co-Authored-By:`,
`Claude-Session:`, `Assisted-by:`, `Generated-By:`, or anything of that shape in
any capitalization. Hard rule; if tooling appends one, remove it before committing.

## Required session report

Every session ends by running `/report`, which produces exactly these sections:

1. **What changed** — every edit as `file:line`.
2. **Commands run** — the real command and its real output. Tests and evals
   pasted verbatim, including failures. Never summarize a run you did not do.
3. **NOT DONE / ASSUMED / UNVERIFIED** — three explicit lists. "Nothing" is a
   valid entry; silence is not.
4. **Needs human browser verification** — anything only a person clicking
   through the UI can confirm.
5. **Conflicts** — anything that contradicts `docs/w2/DECISIONS.md` or an
   invariant above.

A session that reports a clean run it did not execute is worse than a session
that reports a failure.
