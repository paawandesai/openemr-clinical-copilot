# Clinical Co-pilot — Week 2

Week 1 shipped a clinical co-pilot on this OpenEMR fork: a **Pre-Visit Brief**
and a **multi-turn follow-up chat** that read a patient's chart through
OpenEMR's FHIR R4 API under the requesting clinician's own OAuth2 token, draft
structured claims bound to `source_id` + field + value, and pass every claim
through a **deterministic code verifier** before anything renders — claims that
fail render as UNVERIFIED, never silently dropped. Week 2 adds document
ingestion: **lab PDFs and intake forms**, extracted at upload time into a
strict schema with page-located values; a **supervisor** routing between an
**intake-extractor** and an **evidence-retriever**; **hybrid RAG** over
guideline evidence; and an **eval-gated CI** pipeline that blocks merges on
regression against a committed baseline.

**For anything about the OpenEMR platform itself — PHP standards, PHPStan,
Doctrine, Twig, the Docker dev stack, `openemr-cmd`, the PHP test suites — read
[`docs/openemr-dev-guide.md`](docs/openemr-dev-guide.md).** That is the former
root `CLAUDE.md`, unchanged. Read it before touching anything under `src/`,
`library/`, `interface/`, `apis/`, `templates/` or `sql/` — which W2 does touch,
because the lab-value write-back endpoint (D1) lives in the fork, not the agent.

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

Generated eval cases do not land in `cases/` by themselves.

| Directory | Gated? | What goes there |
|---|---|---|
| `agent/evals/w2/factory/` | no | generator and factory code — write freely |
| `agent/evals/w2/staging/` | no | everything a generator emits — write freely |
| `agent/evals/w2/cases/` | **yes** | the committed case set the gate scores against |
| `agent/evals/w2/rubrics/` | **yes** | per-category rubrics |
| `agent/evals/w2/baseline.json` | **yes** | the committed scores a run is compared to |
| `agent/evals/w2/judge_config.*` | **yes** | pinned judge model and settings |

A normal session builds the factory, runs it, and writes as many cases as it
likes into `staging/`. **Promoting a staged file into `cases/` is a gate
change**, and only a session started with `W2_GATE_SESSION=1` can do it — a
human sets that outside the session. This exists so that a branch cannot both
fail on a case and delete the case in the same commit. Report what you staged
and why it should be promoted; do not promote it yourself.

Two hooks enforce this. `gate-guard.sh` runs **before** a tool call and matches
on the resolved path, so `staging/../cases/x.json` and a write through a
symlinked directory are blocked the same as a direct write. **Do not create
symlinks anywhere under `agent/evals/`** — they are refused on both the link
and the target side, exempt directories included.

`gate-effect-check.sh` runs **after every tool call** — not just Bash — and does
not read the command at all: it asks git what changed and restores any
protected path that moved. A `cd` into the directory, a path held in a
variable, a script that does the writing, and an MCP tool `gate-guard.sh` has
never heard of are all caught the same way. It reverts *any* uncommitted change
to a protected path, not only the last call's — so a session that is
deliberately editing gate files must be started with `W2_GATE_SESSION=1`, or it
will lose those edits on its next tool call.

## Where things are

| Thing | Path |
|---|---|
| Agent service (FastAPI) | `agent/app/main.py`, routes in `agent/app/api/` |
| Graphs | `agent/app/graph/{pre_visit_brief,conversation,follow_up}.py` |
| **Tracing wrapper** `UntracedGraph` + `_untraced` node guard | `agent/app/graph/pre_visit_brief.py:211` and `:199` |
| **Verifier** (deterministic, no LLM, no network) | `agent/app/verification/checker.py` |
| Unverified-claim disclosure | `agent/app/verification/disclosure.py` |
| Claim / VerificationResult contract | `agent/app/models/claims.py` |
| FHIR resource models | `agent/app/models/fhir_resources.py` |
| Structured logging + correlation id | `agent/app/middleware/{logging,correlation}.py` |
| Week 1 eval suite (live, costs model calls) | `agent/evals/` — see `agent/evals/README.md` |
| Week 1 eval harness / invariants / independent oracle | `agent/evals/{harness,invariants,oracle}.py` |
| Week 1 unit tests (no network, no credentials) | `agent/tests/` |
| Week 2 evals (being built) | `agent/evals/w2/` — `factory/`, `staging/`, `cases/`, `rubrics/`, `baseline.json`, `judge_config.*` |
| Python tooling config (ruff + pytest) | `agent/pyproject.toml`, `agent/requirements-dev.txt` |
| Gate hook, and the merge check that reads its globs | `.claude/hooks/gate-guard.sh`, `scripts/gate-diff.sh` |
| **OpenEMR platform guide** (PHP, Docker, PHPStan, `openemr-cmd`) | `docs/openemr-dev-guide.md` |
| Week 2 decisions (read first) | `docs/w2/DECISIONS.md` |
| Week 2 work orders | `docs/w2/work-orders/` |
| Week 2 spikes | `docs/w2/spikes/` — write-back findings in `writeback.md` |
| Architecture / metrics / users / audit | `docs/ARCHITECTURE.md`, `docs/KEY_METRICS.md`, `docs/USERS.md`, `docs/AUDIT.md`, `docs/audit/findings.md` |
| Deployment | `agent/deploy/docker-compose.agent.yml`, `agent/deploy/Caddyfile`, `agent/docs/deployment.md` |

## Commands

```bash
# Dev tooling, once per environment:
cd agent && python -m pip install -r requirements.txt -r requirements-dev.txt

# Unit tests — no network, no credentials:
cd agent && python -m pytest tests
# `testpaths` in agent/pyproject.toml means a bare `pytest` here now collects
# tests/ only. It used to also collect evals/, which calls live FHIR and the
# real model. Naming a path still overrides it — so `pytest evals` is live.

# Week 1 eval suite — LIVE: deployed agent + OpenEMR FHIR + Anthropic API.
# Costs real model calls (~36) and several minutes. Do not loop it.
cd agent && python -m pytest evals
cd agent && python -m pytest evals -k "test_d or test_e"   # auth/not-found only, no model calls

# Format and lint (config: agent/pyproject.toml):
cd agent && python -m ruff format --check .   # --check only; see the note below
cd agent && python -m ruff check .            # add --fix for the safe fixes
# Week 1 code is NOT formatted yet: 54 of 81 files would be reformatted, and
# `ruff check` reports 29 findings. Reformatting is its own commit — do not
# let `ruff format` rewrite Week 1 files as a side effect of your change.
# Format the files you actually touch.

# Pre-merge gate check — run this before EVERY merge:
scripts/gate-diff.sh <branch>       # nonzero if the branch touches gate files
scripts/check-claude-paths.sh       # every path named in this file still exists

# Week 2 eval gate — NOT IMPLEMENTED YET. The command will be added with the
# factory under agent/evals/w2/. Do not invent one; report it as missing.

# PHP side (needs the OpenEMR container — see docs/openemr-dev-guide.md):
composer phpstan
composer phpcs
```

### Before every merge

`scripts/gate-diff.sh <branch>` lists the gate files a branch changes and exits
nonzero if there are any. It reads the globs from
`.claude/hooks/gate-guard.sh --check-path`, so the merge check and the
edit-time hook cannot drift apart. A nonzero exit is not a veto — it means
review the gate change on its own, before looking at the code.

**Relocation, done:** `feat/doc-factory` now writes to
`agent/evals/w2/factory/`, which is ungated, and
`scripts/gate-diff.sh feat/doc-factory` passes. It used to write one level up,
directly under agent/evals/, which the gate protects. Do not put a factory
there, and do not create a repo-root evals/ directory.

## Before you start

**Read [`docs/w2/DECISIONS.md`](docs/w2/DECISIONS.md) before starting. Do not
re-decide anything marked DECIDED; raise conflicts in your report.** Entries
marked OPEN are yours to propose on, not to silently settle. Entries marked
PENDING-SPIKE are blocked on a spike — build against the stated constraint and
flag the dependency.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/): `<type>(<scope>): <description>`.
**No AI-attribution trailers of any kind** — no `Co-Authored-By:`,
`Claude-Session:`, `Assisted-by:`, `Generated-By:`, or anything of that shape,
in any capitalization. Hard rule. If tooling appends one, remove it before
committing.

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
