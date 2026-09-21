# WORK ORDER: <short title>

> Copy this file to `docs/w2/work-orders/NN-<slug>.md` and fill every section.
> A section with nothing in it reads `None.` — never delete a heading, and
> never leave one blank. An empty **Out of scope** is a scope bug, not a
> shortcut.

## Goal

One paragraph. What must be true when this is done, stated as an outcome, not
as a list of activities. If the goal cannot be written without an "and", it is
probably two work orders.

## Branch

`<type>/<slug>`, in its own worktree. Create it with:

```bash
scripts/new-worktree.sh <type>/<slug> docs/w2/work-orders/NN-<slug>.md
```

Do not merge. Merging is a separate, human decision.

## Environment

What the session needs to run anything: credentials (by name, never by value),
services that must be reachable, fixtures that must exist, `.env` keys, whether
the deployed host is involved. If the work can be done entirely offline, say so
explicitly — that is useful information.

## In scope

Bulleted. Concrete files, directories, or behaviours. Prefer "the X in
`path/to/file.py`" over "the X module".

## Out of scope

Bulleted, and specific. Name the adjacent things a reasonable session would be
tempted to fix on the way past, and say not to. Include the standing ones that
apply:

- Eval rubrics, eval baseline, judge config, CI config — gate files, blocked
  by hook (`CLAUDE.md` invariant 7).
- Week 1 files: do not move, rename, or restructure anything under `agent/`
  that this order does not name.

## Contracts to honor

The invariants from `CLAUDE.md` that this work actually touches — quoted, not
referenced by number, so the session reads the words. At minimum, state which
of these apply and how:

- No raw PHI in logs, traces, or error messages; every graph node inside the
  tracing wrapper (`agent/app/graph/pre_visit_brief.py:211`).
- The citation contract: `{source_type, source_id, page_or_section,
  field_or_chunk_id, quote_or_value}` (+ `bbox` for document sources).
- Patient-record facts and guideline evidence are distinct `source_type`s.
- Unlocatable extracted values are flagged `unsupported`.
- Intake meds/allergies surface as discrepancies, never auto-merged.
- Every OpenEMR write carries an idempotency key.

Plus the relevant `docs/w2/DECISIONS.md` entries by ID, with their status.

## Acceptance tests

Numbered, each one a command with an expected observable result. A test that
cannot be run as a command belongs under **Stop point**, not here.

1. `cd agent && python -m pytest tests` — passes, no new skips.
2. `<the specific new test>` — passes.
3. ...

## Timebox

Wall-clock, and what to do when it runs out: stop and report, or keep going on
a named part. "Stop and report" is the default and the safer answer.

## Deliverable

The exact artifacts. Files created or changed, and where. If the deliverable is
a decision rather than code, say which `DECISIONS.md` entry it resolves and what
form the answer takes (a spike doc under `docs/w2/spikes/`, a proposed entry
body, etc.).

## Report format

End with `/report`. It must produce:

- **What changed** — `file:line` for every edit.
- **Commands run** — real commands, real output, failures included verbatim.
- **NOT DONE / ASSUMED / UNVERIFIED** — three lists, none omitted.
- **Needs human browser verification** — what only a person in a browser can
  confirm.
- **Conflicts** — anything contradicting `docs/w2/DECISIONS.md` or a `CLAUDE.md`
  invariant.

## Stop point

Where the session stops even if it has time and ideas left. Name the things it
must not do without a human: merging, deploying, touching gate files, editing
`DECISIONS.md`, widening scope into an adjacent system. Default:

> STOP after `/report`. Do not merge. Do not deploy.
