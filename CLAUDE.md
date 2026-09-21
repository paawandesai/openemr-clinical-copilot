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

Note: the OpenEMR platform development guide (PHP, PHPStan, Docker, worktrees)
now lives at [`docs/openemr-dev-guide.md`](docs/openemr-dev-guide.md). Read it
before touching anything under `src/`, `library/`, `interface/`, or `sql/`.

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
7. **Implementation sessions never edit eval rubrics, the eval baseline, judge
   config, or CI config.** These are gate files; a hook blocks them. If a gate
   file is wrong, say so in your report — do not route around it.

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
| Week 2 evals (factory, cases, rubrics — being built) | `evals/factory/`, `evals/cases/`, `evals/rubrics/` |
| Week 2 decisions (read first) | `docs/w2/DECISIONS.md` |
| Week 2 work orders | `docs/w2/work-orders/` |
| Week 2 spikes | `docs/w2/spikes/` |
| Architecture / metrics / users / audit | `docs/ARCHITECTURE.md`, `docs/KEY_METRICS.md`, `docs/USERS.md`, `docs/AUDIT.md`, `docs/audit/findings.md` |
| Deployment | `agent/deploy/docker-compose.agent.yml`, `agent/deploy/Caddyfile`, `agent/docs/deployment.md` |

## Commands

```bash
# Unit tests — no network, no credentials. ALWAYS name the directory:
cd agent && python -m pytest tests
# A bare `pytest` in agent/ also collects evals/, which hits live FHIR and the
# real model. Never run bare pytest there.

# Week 1 eval suite — LIVE: deployed agent + OpenEMR FHIR + Anthropic API.
# Costs real model calls (~36) and several minutes. Do not loop it.
cd agent && python -m pytest evals
cd agent && python -m pytest evals -k "test_d or test_e"   # auth/not-found only, no model calls

# Week 2 eval gate — NOT IMPLEMENTED YET. The command will be added with the
# factory under evals/. Do not invent one; report it as missing.

# Lint — there is NO Python linter configured in this repo as of Week 1.
# Adding one (ruff + mypy) is open work, not something to assume exists.
# PHP side only (needs the OpenEMR container, see docs/openemr-dev-guide.md):
composer phpstan
composer phpcs
```

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
